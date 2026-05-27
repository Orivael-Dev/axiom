"""Sandbox lifecycle for the autonomous loop.

Two implementations behind a common Protocol:

  DockerSandbox  — one container per run. `--network none --read-only`,
                   workdir bind-mounted at /work, repo bind-mounted
                   read-only at /repo. Tool dispatch via `docker exec`
                   into a baked /usr/local/bin/axiom_tool_runner script.

  LocalSandbox   — direct subprocess execution in the workdir on the
                   host. No container isolation. Used by tests so
                   CI doesn't need docker, AND as a documented escape
                   hatch when docker isn't available — but the
                   orchestrator logs a prominent warning so the
                   reduced isolation is visible in the ledger.

Both share the Sandbox Protocol: spawn / read_file / write_file /
list_dir / run_shell / snapshot / diff_hash / export_workdir_to_host /
teardown.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Protocol, Sequence


# Maximum bytes the sandbox will return as part of an Observation
# output field. Larger files / outputs get truncated so a single
# step token's payload stays bounded.
MAX_OBSERVATION_BYTES = 16 * 1024


class SandboxError(RuntimeError):
    """Sandbox setup, teardown, or dispatch failed."""


@dataclass(frozen=True)
class ExecResult:
    ok:          bool
    stdout:      str
    stderr:      str
    returncode:  int
    duration_ms: int


class Sandbox(Protocol):
    """Minimum contract every sandbox implements."""
    workdir_host: Path
    kind: str               # "docker" | "local"

    def read_file(self, relpath: str) -> Optional[str]: ...
    def write_file(self, relpath: str, content: str) -> None: ...
    def list_dir(self, relpath: str = ".") -> List[str]: ...
    def run_shell(
        self, command: Sequence[str], *, timeout_s: int = 60,
    ) -> ExecResult: ...
    def snapshot(self) -> dict: ...
    def diff_hash(self) -> str: ...
    def export_workdir_to_host(self) -> None: ...
    def teardown(self) -> None: ...


# ── LocalSandbox ───────────────────────────────────────────────────────


class LocalSandbox:
    """In-process / on-host sandbox.

    No container isolation — this is for tests and for the "docker not
    available" fallback. The orchestrator records `kind="local"` in
    every step token so the reduced isolation is auditable.
    """
    kind: str = "local"

    def __init__(self, workdir_host: Path) -> None:
        self.workdir_host = Path(workdir_host)
        self.workdir_host.mkdir(parents=True, exist_ok=True)

    def read_file(self, relpath: str) -> Optional[str]:
        path = self._safe_path(relpath)
        if not path.exists() or not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError as e:
            raise SandboxError(f"read_file failed: {e}") from e
        if len(data) > MAX_OBSERVATION_BYTES:
            head = data[:MAX_OBSERVATION_BYTES].decode("utf-8", errors="replace")
            return head + f"\n[…truncated, {len(data)} bytes total]"
        return data.decode("utf-8", errors="replace")

    def write_file(self, relpath: str, content: str) -> None:
        path = self._safe_path(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def list_dir(self, relpath: str = ".") -> List[str]:
        path = self._safe_path(relpath)
        if not path.exists() or not path.is_dir():
            return []
        return sorted(p.name for p in path.iterdir())

    def run_shell(
        self, command: Sequence[str], *, timeout_s: int = 60,
    ) -> ExecResult:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                list(command),
                cwd=str(self.workdir_host),
                capture_output=True, text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            return ExecResult(
                ok=False, stdout=e.stdout or "", stderr=e.stderr or "",
                returncode=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except (OSError, FileNotFoundError) as e:
            return ExecResult(
                ok=False, stdout="", stderr=str(e),
                returncode=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        stdout = _truncate(proc.stdout or "")
        stderr = _truncate(proc.stderr or "")
        return ExecResult(
            ok=(proc.returncode == 0),
            stdout=stdout, stderr=stderr,
            returncode=int(proc.returncode),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    def snapshot(self) -> dict:
        """Light directory snapshot — just the top-level entries.

        Used in the planner's first prompt to give the model a
        non-LLM-derived view of the starting workdir.
        """
        return {
            "kind": "local",
            "workdir": str(self.workdir_host),
            "entries": self.list_dir("."),
        }

    def diff_hash(self) -> str:
        """SHA-256 of (relpath, content_hash) tuples across the workdir.

        Stable, deterministic — included in every step token so two
        consecutive steps with no filesystem change are visibly the
        same diff_hash, which makes replay forensics easier.
        """
        h = hashlib.sha256()
        for root, _dirs, files in os.walk(self.workdir_host):
            for name in sorted(files):
                p = Path(root) / name
                rel = str(p.relative_to(self.workdir_host))
                try:
                    blob = p.read_bytes()
                except OSError:
                    continue
                h.update(rel.encode("utf-8") + b"\0")
                h.update(hashlib.sha256(blob).digest())
        return "sha256:" + h.hexdigest()

    def export_workdir_to_host(self) -> None:
        """LocalSandbox already lives on the host. No-op."""
        return None

    def teardown(self) -> None:
        """LocalSandbox owns the workdir but does not delete it — the
        caller may want to inspect the result. Override the workdir
        path between runs to avoid cross-contamination.
        """
        return None

    def _safe_path(self, relpath: str) -> Path:
        """Resolve `relpath` and reject anything that escapes workdir.

        Reused as the canonical "inside the sandbox" check from
        governance.py too.
        """
        if os.path.isabs(relpath):
            raise SandboxError(f"absolute paths not allowed: {relpath!r}")
        candidate = (self.workdir_host / relpath).resolve()
        root = self.workdir_host.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as e:
            raise SandboxError(
                f"path escapes sandbox workdir: {relpath!r}"
            ) from e
        return candidate


# ── DockerSandbox ─────────────────────────────────────────────────────


class DockerSandbox:
    """One container per run.

    Spawn: docker run -d --rm --network none --read-only
                       --memory 1g --cpus 2 --pids-limit 256
                       --tmpfs /tmp:size=256m
                       -v <workdir>:/work -v <repo>:/repo:ro
                       <image> sleep infinity

    Dispatch: every read_file / write_file / list_dir / run_shell
    funnels through `docker exec <cid> /usr/local/bin/axiom_tool_runner`
    which reads JSON args from /work/.axiom/in.json and writes the
    observation to /work/.axiom/out.json.

    The bind-mount means the host can write the argsfile + read the
    outfile directly — no RPC server, no docker-in-docker.
    """
    kind: str = "docker"

    DEFAULT_IMAGE = "orivael/axiom-autonomous:local"

    def __init__(
        self,
        workdir_host: Path,
        *,
        image: Optional[str] = None,
        repo_root: Optional[Path] = None,
        memory: str = "1g",
        cpus: str = "2",
        pids_limit: int = 256,
    ) -> None:
        self.workdir_host = Path(workdir_host)
        self.workdir_host.mkdir(parents=True, exist_ok=True)
        self.image = image or os.environ.get(
            "AXIOM_AUTONOMOUS_IMAGE", self.DEFAULT_IMAGE,
        )
        self.repo_root = (
            Path(repo_root) if repo_root else Path(__file__).resolve().parent.parent
        )
        self._memory = memory
        self._cpus = cpus
        self._pids_limit = pids_limit
        self._cid: Optional[str] = None
        (self.workdir_host / ".axiom").mkdir(exist_ok=True)

    @classmethod
    def spawn(cls, workdir_host: Path, **kwargs) -> "DockerSandbox":
        """Spawn a fresh container + return a ready-to-use sandbox."""
        instance = cls(workdir_host, **kwargs)
        instance._start_container()
        return instance

    def _start_container(self) -> None:
        # Resolve absolute mount paths so docker doesn't choke on
        # symlinks or relative paths the caller passed in.
        workdir_abs = str(self.workdir_host.resolve())
        repo_abs    = str(self.repo_root.resolve())
        cmd = [
            "docker", "run", "-d", "--rm",
            "--network", "none",
            "--read-only",
            "--memory", self._memory,
            "--cpus",   self._cpus,
            "--pids-limit", str(self._pids_limit),
            "--tmpfs", "/tmp:size=256m",
            "--tmpfs", "/var/tmp:size=64m",
            "-v", f"{workdir_abs}:/work",
            "-v", f"{repo_abs}:/repo:ro",
            "-w", "/work",
            self.image,
            "sleep", "infinity",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=60,
            )
        except FileNotFoundError as e:
            raise SandboxError("docker binary not found on PATH") from e
        except subprocess.CalledProcessError as e:
            raise SandboxError(
                f"docker run failed (exit {e.returncode}): {e.stderr.strip()}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise SandboxError(f"docker run timed out: {e}") from e
        self._cid = proc.stdout.strip()
        if not self._cid:
            raise SandboxError("docker run returned empty container id")

    def _exec(self, argv: Sequence[str], *, timeout_s: int = 60) -> ExecResult:
        if not self._cid:
            raise SandboxError("sandbox container not started")
        cmd = ["docker", "exec", self._cid, *argv]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            return ExecResult(
                ok=False, stdout=e.stdout or "", stderr=e.stderr or "",
                returncode=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        return ExecResult(
            ok=(proc.returncode == 0),
            stdout=_truncate(proc.stdout or ""),
            stderr=_truncate(proc.stderr or ""),
            returncode=int(proc.returncode),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    def read_file(self, relpath: str) -> Optional[str]:
        # Reads happen on the host through the bind mount — no docker
        # round-trip needed. Path safety check still applies.
        path = self._safe_path(relpath)
        if not path.exists() or not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError as e:
            raise SandboxError(f"read_file failed: {e}") from e
        if len(data) > MAX_OBSERVATION_BYTES:
            head = data[:MAX_OBSERVATION_BYTES].decode("utf-8", errors="replace")
            return head + f"\n[…truncated, {len(data)} bytes total]"
        return data.decode("utf-8", errors="replace")

    def write_file(self, relpath: str, content: str) -> None:
        path = self._safe_path(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def list_dir(self, relpath: str = ".") -> List[str]:
        path = self._safe_path(relpath)
        if not path.exists() or not path.is_dir():
            return []
        return sorted(p.name for p in path.iterdir())

    def run_shell(
        self, command: Sequence[str], *, timeout_s: int = 60,
    ) -> ExecResult:
        # Real run_shell goes THROUGH docker exec so the command
        # executes under the container's isolation.
        return self._exec(list(command), timeout_s=timeout_s)

    def snapshot(self) -> dict:
        return {
            "kind": "docker",
            "container_id": self._cid or "",
            "image": self.image,
            "workdir": str(self.workdir_host),
            "repo_root": str(self.repo_root),
            "entries": self.list_dir("."),
        }

    def diff_hash(self) -> str:
        # Same algorithm as LocalSandbox — operates on the host-side
        # bind-mounted workdir, so the docker container doesn't need
        # to expose any hashing helper.
        h = hashlib.sha256()
        for root, _dirs, files in os.walk(self.workdir_host):
            for name in sorted(files):
                p = Path(root) / name
                rel = str(p.relative_to(self.workdir_host))
                try:
                    blob = p.read_bytes()
                except OSError:
                    continue
                h.update(rel.encode("utf-8") + b"\0")
                h.update(hashlib.sha256(blob).digest())
        return "sha256:" + h.hexdigest()

    def export_workdir_to_host(self) -> None:
        # The bind mount means writes inside the container already
        # land on the host. Nothing to copy.
        return None

    def teardown(self) -> None:
        if not self._cid:
            return
        cid = self._cid
        self._cid = None
        # SIGTERM grace, then SIGKILL via `docker rm -f`. --rm on
        # the original `docker run` cleans up after the kill.
        try:
            subprocess.run(
                ["docker", "kill", "--signal=SIGTERM", cid],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        try:
            subprocess.run(
                ["docker", "rm", "-f", cid],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _safe_path(self, relpath: str) -> Path:
        if os.path.isabs(relpath):
            raise SandboxError(f"absolute paths not allowed: {relpath!r}")
        candidate = (self.workdir_host / relpath).resolve()
        root = self.workdir_host.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as e:
            raise SandboxError(
                f"path escapes sandbox workdir: {relpath!r}"
            ) from e
        return candidate


# ── factory ────────────────────────────────────────────────────────────


def spawn_sandbox(
    workdir_host: Path,
    *,
    prefer: str = "docker",
    **kwargs,
) -> Sandbox:
    """Build a sandbox. Falls back to LocalSandbox if docker isn't
    available and `prefer != "docker_required"`.

    Set `prefer="docker_required"` to raise SandboxError when docker
    is missing — the right setting for production runs.
    """
    if prefer == "local":
        return LocalSandbox(workdir_host)
    try:
        return DockerSandbox.spawn(workdir_host, **kwargs)
    except SandboxError:
        if prefer == "docker_required":
            raise
        return LocalSandbox(workdir_host)


def _truncate(text: str) -> str:
    if len(text) <= MAX_OBSERVATION_BYTES:
        return text
    return text[:MAX_OBSERVATION_BYTES] + f"\n[…truncated, {len(text)} chars total]"
