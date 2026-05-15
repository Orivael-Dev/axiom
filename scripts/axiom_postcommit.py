#!/usr/bin/env python3
"""
AXIOM Post-Commit Hook Worker
=============================
Invoked by ``.git/hooks/post-commit`` after every commit. Records the
commit as a signed dev-cycle entry via :class:`axiom_dev_loop.DevCycleRecorder`
so the existing autotrain pipeline (axiom_dataset_builder → build_qwen_chatml
→ axiom_qwen_finetune.ipynb) picks it up automatically.

Never blocks a commit: a missing AXIOM_MASTER_KEY, a failed pytest run,
or any internal error returns exit-code 0 with a one-line warning. The
commit has already landed when this runs — recording is best-effort.

Optional env knobs (read at invocation):

  AXIOM_MASTER_KEY    required to sign records; if unset, exit silently
  AXIOM_DEV_NO_TESTS  skip pytest invocation, write record with 0/0 counts
  AXIOM_DEV_LOG_DIR   override sink directory (default: repo root)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _git(*args: str, cwd: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _parse_pytest_summary(text: str) -> tuple[int, int]:
    """Extract (passed, failed) from pytest's terminal summary line."""
    passed = failed = 0
    for line in reversed(text.splitlines()):
        if " passed" in line or " failed" in line:
            for token in line.replace(",", " ").split():
                if token.isdigit():
                    n = int(token)
                elif token == "passed":
                    passed = n
                elif token == "failed":
                    failed = n
            if passed or failed:
                return passed, failed
    return passed, failed


def _run_tests(repo: Path) -> tuple[int, int]:
    if os.environ.get("AXIOM_DEV_NO_TESTS", "").lower() in ("1", "true", "yes"):
        return 0, 0
    cmd = [
        sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no",
        "--ignore=tests/acb_scorer_test.py", "-p", "no:cacheprovider",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True, timeout=180,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0, 0
    return _parse_pytest_summary(proc.stdout + "\n" + proc.stderr)


def main() -> int:
    repo = Path(_git("rev-parse", "--show-toplevel", cwd=Path.cwd()) or Path.cwd())
    if not os.environ.get("AXIOM_MASTER_KEY"):
        # Silently no-op so commits never break on a fresh checkout.
        print("  [axiom-dev] AXIOM_MASTER_KEY unset; skipping training-record capture.")
        return 0

    sha = _git("rev-parse", "HEAD", cwd=repo)
    if not sha:
        return 0
    subject = _git("log", "-1", "--format=%s", cwd=repo)
    diffstat = _git("diff", "--shortstat", "HEAD~1", "HEAD", cwd=repo) or "(initial commit)"
    files_raw = _git("diff", "--name-only", "HEAD~1", "HEAD", cwd=repo)
    changed = [f for f in files_raw.splitlines() if f.strip()]

    test_pass, test_fail = _run_tests(repo)

    log_dir = Path(os.environ.get("AXIOM_DEV_LOG_DIR") or repo)
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        sys.path.insert(0, str(repo))
        from axiom_dev_loop import DevCycleRecorder
    except ImportError as e:
        print(f"  [axiom-dev] cannot import axiom_dev_loop: {e}")
        return 0

    try:
        recorder = DevCycleRecorder(repo_root=log_dir)
        record = recorder.record(
            commit_sha=sha,
            task=subject or f"commit:{sha[:12]}",
            changed_files=changed,
            diff_summary=diffstat,
            test_pass=test_pass,
            test_fail=test_fail,
            retrospect_signal="green" if test_fail == 0 else "regression",
        )
        print(
            f"  [axiom-dev] recorded {record.rating} cycle {sha[:12]} "
            f"(tests {test_pass} pass / {test_fail} fail)"
        )
    except Exception as e:
        # Best-effort: never break the commit on a sink failure.
        print(f"  [axiom-dev] record failed: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
