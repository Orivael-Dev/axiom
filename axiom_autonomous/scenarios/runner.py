"""Scenarios runner — wraps AutonomousAgent.run() with per-scenario
sandboxing, criteria checking, and signed aggregate output.

Per scenario:
  1. Copy seed/<scenario.seed>/ → fresh tmp workdir
  2. Snapshot sha256 of each must_not_modify file
  3. agent.run(task, workdir) — AutonomousAgent handles its own
     sandbox spawn / governance gate / teardown
  4. After the agent returns, run criteria checks against the
     post-run workdir (pytest, file existence, sha256 unchanged)
  5. Collect AutonomousRunResult + criteria report + wall_ms

Aggregate output is a single JSON object signed via axiom_signing —
mirrors the 5cat benchmark shape so external tools can ingest both.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, List, Optional

from .criteria import CheckResult, check_criteria, snapshot_seed_hashes
from .library import Scenario


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z",
    )


def _git_commit_short() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            stderr=subprocess.DEVNULL, text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _copy_seed(seed_dir: Path, dest: Path) -> None:
    """Copy seed → dest. dest must not yet exist."""
    if dest.exists():
        raise FileExistsError(f"workdir {dest} already exists")
    shutil.copytree(seed_dir, dest)


@dataclass(frozen=True)
class ScenarioRunReport:
    """Per-scenario result row."""

    id:               str
    title:            str
    seed:             str
    agent_success:    bool        # AutonomousRunResult.success
    criteria_passed:  bool        # external check
    aborted_reason:   str
    steps:            int
    wall_ms:          int
    run_id:           str         # chain run_id
    chain_head:       str         # chain_head_token_id
    criteria_detail:  dict        # CheckResult.to_dict()
    plan_summary:     dict        # plan.to_dict() (subgoal status etc.)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunReport:
    """Top-level aggregate output."""

    meta:       dict
    scenarios:  List[ScenarioRunReport] = field(default_factory=list)

    def summary(self) -> dict:
        n = len(self.scenarios)
        agent_ok = sum(1 for s in self.scenarios if s.agent_success)
        crit_ok  = sum(1 for s in self.scenarios if s.criteria_passed)
        wall = sum(s.wall_ms for s in self.scenarios)
        steps = sum(s.steps for s in self.scenarios)
        return {
            "total":             n,
            "agent_succeeded":   agent_ok,
            "criteria_passed":   crit_ok,
            "total_wall_ms":     wall,
            "total_steps":       steps,
            "avg_steps":         round(steps / n, 2) if n else 0.0,
        }

    def to_dict(self) -> dict:
        return {
            "meta":      self.meta,
            "summary":   self.summary(),
            "scenarios": [s.to_dict() for s in self.scenarios],
        }


def _build_meta(*, sandbox_prefer: str, backend_label: str) -> dict:
    return {
        "schema":          "axiom-autonomous-scenarios/v1",
        "axiom_commit":    _git_commit_short(),
        "started_utc":     _utc_now_iso(),  # ended_utc filled at run end
        "sandbox_prefer":  sandbox_prefer,
        "backend":         backend_label,
        "python":          sys.version.split()[0],
    }


def _backend_label(backend) -> str:
    name = getattr(backend, "name", "?")
    model = getattr(backend, "model", "?")
    return f"{name} · {model}"


def _sign_report(report_dict: dict) -> str:
    """Sign the report under the axiom-autonomous-scenarios-v1 key.

    Best-effort: if axiom_signing isn't importable (no MASTER_KEY in
    env) we leave the field empty so the runner still works in dev.
    """
    try:
        from axiom_signing import derive_key
        import hashlib, hmac
        key = derive_key(b"axiom-autonomous-scenarios-v1")
        canon = json.dumps(report_dict, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
        return "hmac-sha256:" + hmac.new(key, canon, hashlib.sha256).hexdigest()
    except Exception as e:
        return f"unsigned:{type(e).__name__}"


def run_scenarios(
    scenarios: Iterable[Scenario],
    *,
    sandbox_prefer: str = "docker_required",
    backend=None,
    workdir_root: Optional[Path] = None,
    verbose: bool = False,
) -> RunReport:
    """Execute every scenario, return a populated RunReport.

    `workdir_root` defaults to a fresh tempdir. Each scenario gets
    its own subdirectory under it (`workdir_root / <scenario.id>`)
    so the operator can inspect post-run state after the runner exits.
    """
    from axiom_autonomous import AutonomousAgent
    from axiom_event_token.backends import default_backend

    backend = backend or default_backend()
    agent = AutonomousAgent(
        backend=backend,
        sandbox_prefer=sandbox_prefer,
        record_dev_cycle=False,
    )
    workdir_root = (
        Path(workdir_root) if workdir_root is not None
        else Path(tempfile.mkdtemp(prefix="axiom_scenarios_"))
    )
    workdir_root.mkdir(parents=True, exist_ok=True)

    meta = _build_meta(
        sandbox_prefer=sandbox_prefer,
        backend_label=_backend_label(backend),
    )
    report = RunReport(meta=meta)

    for scenario in scenarios:
        if verbose:
            print(f"  → scenario {scenario.id}: {scenario.title}",
                  file=sys.stderr)
        per = _run_one(agent, scenario, workdir_root, verbose=verbose)
        report.scenarios.append(per)

    report.meta["ended_utc"]   = _utc_now_iso()
    report.meta["workdir_root"] = str(workdir_root)
    return report


def _run_one(
    agent, scenario: Scenario, workdir_root: Path, *, verbose: bool,
) -> ScenarioRunReport:
    """Run one scenario end-to-end and produce its report row."""
    workdir = workdir_root / scenario.id
    try:
        seed_dir = scenario.seed_dir()
        _copy_seed(seed_dir, workdir)
        seed_hashes = snapshot_seed_hashes(seed_dir, scenario.criteria)

        t0 = time.monotonic()
        result = agent.run(
            task=scenario.task, workdir=workdir,
            budget_steps=scenario.budget_steps,
            wall_seconds=scenario.wall_seconds,
        )
        wall_ms = int((time.monotonic() - t0) * 1000)

        criteria = check_criteria(
            scenario.criteria, workdir, seed_hashes=seed_hashes,
        )

        return ScenarioRunReport(
            id=scenario.id,
            title=scenario.title,
            seed=scenario.seed,
            agent_success=bool(result.success),
            criteria_passed=bool(criteria.passed),
            aborted_reason=result.aborted_reason or "",
            steps=int(result.steps),
            wall_ms=wall_ms,
            run_id=result.run_id,
            chain_head=result.chain_head_token_id,
            criteria_detail=criteria.to_dict(),
            plan_summary=result.plan.to_dict(),
        )
    except Exception as e:
        # A scenario-level crash (seed missing, sandbox spawn failure,
        # criteria-check exception) becomes a failed row rather than
        # killing the entire run — sibling scenarios still execute.
        if verbose:
            print(f"  ! scenario {scenario.id} crashed: {e}",
                  file=sys.stderr)
        return ScenarioRunReport(
            id=scenario.id, title=scenario.title, seed=scenario.seed,
            agent_success=False, criteria_passed=False,
            aborted_reason=f"runner-error: {type(e).__name__}: {e}",
            steps=0, wall_ms=0, run_id="", chain_head="",
            criteria_detail={}, plan_summary={},
        )


def write_report(report: RunReport, output: Path) -> Path:
    """Serialize the report to `output` with a top-level HMAC signature."""
    payload = report.to_dict()
    payload["signature"] = _sign_report(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8",
    )
    return output
