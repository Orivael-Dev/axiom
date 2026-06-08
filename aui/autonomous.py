"""Dedicated autonomous-agent workspace backend.

Drives Axiom's autonomous coding agent (the ``axiom_autonomous`` package,
maintained in the Axiom main repo) through its CLI as a background subprocess —
so AX OS uses the upstream agent as-is, no fork. Each run is a blocking job (up
to ``wall_seconds``); it's launched in a worker thread and tracked here, and the
dedicated workspace submits a task then polls status + result.

The agent enforces its own constitutional gates (intent gate, sandbox review,
per-action governance); the AX OS route additionally screens the task through
axiom_immune before launching. Reachability needs the Axiom repo on disk
(AXIOM_REPO — the same var the bridge uses) and AXIOM_MASTER_KEY in the env.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _axiom_repo() -> Optional[str]:
    return os.environ.get("AXIOM_REPO")


def available() -> dict:
    """Whether the upstream autonomous agent is reachable (its repo on disk)."""
    repo = _axiom_repo()
    ok = bool(repo) and Path(repo, "axiom_autonomous_agent.py").is_file()
    return {"available": ok, "repo": repo or ""}


def _public(job: dict) -> dict:
    return {k: job[k] for k in ("id", "task", "status", "started_at",
                                "finished_at", "result", "error")}


def list_runs() -> list:
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j["started_at"], reverse=True)
        return [_public(j) for j in jobs]


def get_run(run_id: str) -> Optional[dict]:
    with _LOCK:
        j = _JOBS.get(run_id)
        return _public(j) if j else None


def _parse_result(stdout: str) -> Optional[dict]:
    """Pull the AutonomousRunResult JSON out of the CLI's --json output (the whole
    stream, else the last JSON object line)."""
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except Exception:  # noqa: BLE001
        pass
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:  # noqa: BLE001
                continue
    return None


def _launch(cmd: list, *, cwd: str, timeout: int) -> tuple:
    """Run the agent CLI to completion. Seam for tests. Returns (rc, out, err)."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ})
    return proc.returncode, proc.stdout, proc.stderr


def _run_job(run_id: str, repo: str, task: str, workdir: Path, *,
             budget_steps: int, wall_seconds: int, sandbox: str) -> None:
    cmd = [sys.executable, "-m", "axiom_autonomous_agent", "run",
           "--task", task, "--workdir", str(workdir),
           "--budget-steps", str(budget_steps), "--wall-seconds", str(wall_seconds),
           "--sandbox", sandbox, "--json"]
    status, result, error = "error", None, ""
    try:
        _rc, out, err = _launch(cmd, cwd=repo, timeout=wall_seconds + 60)
        result = _parse_result(out)
        if result is None:
            error = (err or out or "no result").strip()[-2000:]
        elif result.get("success"):
            status = "done"
        elif result.get("aborted_reason"):
            status = "blocked"      # governance / intent gate / budget exhausted
        else:
            status = "failed"
    except subprocess.TimeoutExpired:
        status, error = "timeout", f"exceeded {wall_seconds}s"
    except Exception as e:  # noqa: BLE001
        status, error = "error", f"{type(e).__name__}: {e}"
    with _LOCK:
        j = _JOBS.get(run_id)
        if j:
            j.update(status=status, result=result, error=error,
                     finished_at=time.time())


def submit(task: str, *, budget_steps: int = 30, wall_seconds: int = 900,
           sandbox: str = "local") -> dict:
    """Launch an autonomous run in the background; returns a run handle. Fails
    soft when the agent isn't reachable."""
    task = (task or "").strip()
    if not task:
        return {"ok": False, "reason": "empty_task"}
    if not available()["available"]:
        return {"ok": False, "reason": "autonomous_unavailable",
                "detail": "set AXIOM_REPO to the Axiom repo on disk"}
    run_id = "job_" + uuid.uuid4().hex[:12]
    workdir = Path(tempfile.mkdtemp(prefix=f"ax_auto_{run_id}_"))
    job = {"id": run_id, "task": task, "status": "running", "workdir": str(workdir),
           "started_at": time.time(), "finished_at": None, "result": None, "error": ""}
    with _LOCK:
        _JOBS[run_id] = job
    threading.Thread(
        target=_run_job, args=(run_id, _axiom_repo(), task, workdir),
        kwargs={"budget_steps": budget_steps, "wall_seconds": wall_seconds,
                "sandbox": sandbox}, daemon=True).start()
    return {"ok": True, "run_id": run_id, "status": "running"}
