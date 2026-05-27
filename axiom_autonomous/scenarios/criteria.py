"""Post-run success-criteria checks for the scenarios harness.

The AutonomousAgent reports its OWN judgement of success (the
verifier's `verdict.kind == "success"` and plan completion). That's
about the agent's internal loop. This module's job is the EXTERNAL,
adversarial check: did the workdir actually end up in the state the
scenario demands?

Three kinds of check per scenario:

  tests_must_pass    pytest -k <selector> must report N passed, 0 failed
                     for each named test selector
  files_must_exist   each listed relative path must exist in the workdir
                     post-run (catches "agent declared success but
                     wrote nothing")
  must_not_modify    each listed relative path's sha256 must be
                     unchanged from the seed (catches "agent succeeded
                     by editing README.md and ignoring the actual task")

Optional check (when set):

  shell_must_succeed  run the listed command in the workdir; exit 0 = pass
                      (lets a scenario assert custom invariants — e.g.
                      "ruff check ." or "mypy module.py")
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class Criteria:
    """Declarative success criteria for a scenario."""

    tests_must_pass:    Tuple[str, ...] = ()
    files_must_exist:   Tuple[str, ...] = ()
    must_not_modify:    Tuple[str, ...] = ()
    shell_must_succeed: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: Mapping) -> "Criteria":
        return cls(
            tests_must_pass    = tuple(d.get("tests_must_pass", ())),
            files_must_exist   = tuple(d.get("files_must_exist", ())),
            must_not_modify    = tuple(d.get("must_not_modify", ())),
            shell_must_succeed = tuple(d.get("shell_must_succeed", ())),
        )

    def to_dict(self) -> dict:
        return {
            "tests_must_pass":    list(self.tests_must_pass),
            "files_must_exist":   list(self.files_must_exist),
            "must_not_modify":    list(self.must_not_modify),
            "shell_must_succeed": list(self.shell_must_succeed),
        }


@dataclass(frozen=True)
class CheckResult:
    """Per-check outcome. `passed` rolls up the booleans below."""

    passed:    bool
    tests:     dict           # {selector: {"passed": int, "failed": int, "ok": bool}}
    files:     dict           # {path: {"present": bool}}
    unmodified: dict          # {path: {"unchanged": bool, "reason": str}}
    shell:     dict           # {command: {"exit": int, "ok": bool}}

    def to_dict(self) -> dict:
        return {
            "passed":     self.passed,
            "tests":      self.tests,
            "files":      self.files,
            "unmodified": self.unmodified,
            "shell":      self.shell,
        }


def _sha256_file(p: Path) -> Optional[str]:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _snapshot_seed_hashes(
    seed_dir: Path, paths: Iterable[str],
) -> dict:
    """Hash the listed files in the seed BEFORE the agent runs.

    Returns {relpath: sha256_hex_or_None}. None means the seed didn't
    have the file in the first place — `unmodified` then trivially
    passes (you can't have modified what wasn't there).
    """
    return {p: _sha256_file(seed_dir / p) for p in paths}


def _check_tests(
    workdir: Path, selectors: Iterable[str], timeout_s: int = 120,
) -> dict:
    """Run pytest in `workdir` once per selector and report counts."""
    out: dict = {}
    for sel in selectors:
        try:
            proc = subprocess.run(
                ["python3", "-m", "pytest", "-q", "-k", sel],
                cwd=str(workdir), capture_output=True, text=True,
                timeout=timeout_s,
            )
            stdout = proc.stdout + proc.stderr
            passed = _grep_count(stdout, " passed")
            failed = _grep_count(stdout, " failed")
            out[sel] = {
                "passed": passed,
                "failed": failed,
                "ok":     proc.returncode == 0 and passed >= 1 and failed == 0,
                "exit":   proc.returncode,
            }
        except subprocess.TimeoutExpired:
            out[sel] = {"passed": 0, "failed": 0, "ok": False,
                        "exit": -1, "error": "timeout"}
        except FileNotFoundError as e:
            out[sel] = {"passed": 0, "failed": 0, "ok": False,
                        "exit": -1, "error": f"pytest not found: {e}"}
    return out


def _grep_count(text: str, needle: str) -> int:
    """Parse '3 passed' / '1 failed' off pytest's summary line."""
    import re
    for line in reversed(text.splitlines()):
        m = re.search(rf"(\d+){re.escape(needle)}", line)
        if m:
            return int(m.group(1))
    return 0


def _check_files(workdir: Path, paths: Iterable[str]) -> dict:
    return {p: {"present": (workdir / p).exists()} for p in paths}


def _check_unmodified(
    workdir: Path, seed_hashes: Mapping[str, Optional[str]],
) -> dict:
    out: dict = {}
    for path, original in seed_hashes.items():
        current = _sha256_file(workdir / path)
        if original is None:
            # File wasn't in the seed — "must not modify" vacuously OK.
            out[path] = {"unchanged": True, "reason": "absent in seed"}
        elif current is None:
            out[path] = {"unchanged": False, "reason": "deleted by agent"}
        elif current == original:
            out[path] = {"unchanged": True, "reason": "sha256 match"}
        else:
            out[path] = {"unchanged": False, "reason": "sha256 changed"}
    return out


def _check_shell(
    workdir: Path, commands: Iterable[str], timeout_s: int = 60,
) -> dict:
    out: dict = {}
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(workdir),
                capture_output=True, text=True, timeout=timeout_s,
            )
            out[cmd] = {"exit": proc.returncode, "ok": proc.returncode == 0}
        except subprocess.TimeoutExpired:
            out[cmd] = {"exit": -1, "ok": False, "error": "timeout"}
    return out


def check_criteria(
    criteria: Criteria,
    workdir: Path,
    *,
    seed_hashes: Optional[Mapping[str, Optional[str]]] = None,
) -> CheckResult:
    """Run every declared check against the post-run workdir.

    `seed_hashes` carries the pre-run sha256 of every must_not_modify
    file (taken via `_snapshot_seed_hashes` BEFORE the agent ran).
    Pass an empty dict to skip the unmodified check.
    """
    tests = _check_tests(workdir, criteria.tests_must_pass) \
            if criteria.tests_must_pass else {}
    files = _check_files(workdir, criteria.files_must_exist) \
            if criteria.files_must_exist else {}
    unmod = _check_unmodified(workdir, seed_hashes or {}) \
            if criteria.must_not_modify else {}
    shell = _check_shell(workdir, criteria.shell_must_succeed) \
            if criteria.shell_must_succeed else {}

    overall = (
        all(r["ok"] for r in tests.values())
        and all(r["present"] for r in files.values())
        and all(r["unchanged"] for r in unmod.values())
        and all(r["ok"] for r in shell.values())
    )
    return CheckResult(
        passed=overall, tests=tests, files=files,
        unmodified=unmod, shell=shell,
    )


def snapshot_seed_hashes(seed_dir: Path, criteria: Criteria) -> dict:
    """Public helper: pre-run hash snapshot for must_not_modify files."""
    return _snapshot_seed_hashes(seed_dir, criteria.must_not_modify)
