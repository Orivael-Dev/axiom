"""Shell tools: run_shell, run_tests, finish."""
from __future__ import annotations

import re
import shlex
import time
from typing import List

from ..models import Observation, ToolCall
from ..sandbox import Sandbox, SandboxError
from . import Tool


def _split_command(raw) -> List[str]:
    """Coerce the model's `command` argument into argv form.

    Accepts a list directly (preferred — least ambiguity) or a string
    that will be parsed with shlex. Raises ValueError on garbage.
    """
    if isinstance(raw, list):
        cmd = [str(x) for x in raw]
        if not cmd:
            raise ValueError("command list is empty")
        return cmd
    if isinstance(raw, str):
        if not raw.strip():
            raise ValueError("command string is empty")
        return shlex.split(raw)
    raise ValueError("'command' must be a list of strings or a string")


def _run_shell_dispatch(call: ToolCall, sandbox: Sandbox) -> Observation:
    t0 = time.monotonic()
    raw = call.args.get("command")
    timeout_s = call.args.get("timeout_s", 60)
    try:
        cmd = _split_command(raw)
        timeout_int = int(timeout_s)
    except (ValueError, TypeError) as e:
        return Observation(ok=False, output="", error=str(e),
                           duration_ms=int((time.monotonic() - t0) * 1000))
    timeout_int = max(1, min(300, timeout_int))
    try:
        result = sandbox.run_shell(cmd, timeout_s=timeout_int)
    except SandboxError as e:
        return Observation(ok=False, output="", error=str(e),
                           duration_ms=int((time.monotonic() - t0) * 1000))
    combined = result.stdout
    if result.stderr:
        combined = (combined + "\n--- STDERR ---\n" + result.stderr).strip()
    return Observation(
        ok=result.ok,
        output=combined or ("(no output)" if result.ok else "(failed silently)"),
        structured={
            "argv": cmd,
            "returncode": result.returncode,
        },
        duration_ms=result.duration_ms,
        error="" if result.ok else (
            result.stderr.strip() or f"non-zero exit {result.returncode}"
        ),
    )


# ── run_tests — pytest specialisation ─────────────────────────────────


_PYTEST_SUMMARY_RE = re.compile(
    r"==+\s*"
    r"(?:(\d+)\s+failed,?\s*)?"
    r"(?:(\d+)\s+passed,?\s*)?"
    r"(?:(\d+)\s+skipped,?\s*)?"
    r"(?:(\d+)\s+error,?\s*)?"
    r".*?in\s+[\d.]+s",
)


def _parse_pytest_counts(text: str) -> dict:
    """Pull pass/fail/skip/error counts from pytest's summary line.

    Returns zeros if no summary is found (e.g. pytest crashed before
    running anything). Always returns the expected keys.
    """
    fail = passed = skipped = error = 0
    for line in reversed(text.splitlines()):
        m = _PYTEST_SUMMARY_RE.search(line)
        if m:
            if m.group(1): fail    = int(m.group(1))
            if m.group(2): passed  = int(m.group(2))
            if m.group(3): skipped = int(m.group(3))
            if m.group(4): error   = int(m.group(4))
            break
    return {"passed": passed, "failed": fail,
            "skipped": skipped, "errors": error}


def _extract_first_failures(text: str, k: int = 3) -> List[str]:
    """Pull the first k failure tracebacks out of pytest output.

    Hunts for the `FAILED test_x.py::test_y` lines + the FAILURES
    section header — keeps the model's observation focused on the
    actionable failures, not the full noisy output.
    """
    failures: List[str] = []
    lines = text.splitlines()
    in_section = False
    current: List[str] = []
    for line in lines:
        if "=== FAILURES ===" in line or "____ test_" in line:
            in_section = True
        if in_section:
            current.append(line)
            if line.startswith("===") and "FAILURES" not in line and current:
                failures.append("\n".join(current[:-1]).strip())
                current = []
                in_section = False
                if len(failures) >= k:
                    break
    if current and in_section:
        failures.append("\n".join(current).strip())
    return failures[:k]


def _run_tests_dispatch(call: ToolCall, sandbox: Sandbox) -> Observation:
    t0 = time.monotonic()
    args = call.args.get("args", [])
    if isinstance(args, str):
        args = shlex.split(args)
    if not isinstance(args, list):
        return Observation(ok=False, output="",
                           error="run_tests 'args' must be a list or string",
                           duration_ms=int((time.monotonic() - t0) * 1000))
    timeout_s = max(1, min(600, int(call.args.get("timeout_s", 300))))
    cmd = ["python", "-m", "pytest", "-v"] + [str(a) for a in args]
    try:
        result = sandbox.run_shell(cmd, timeout_s=timeout_s)
    except SandboxError as e:
        return Observation(ok=False, output="", error=str(e),
                           duration_ms=int((time.monotonic() - t0) * 1000))
    combined = result.stdout
    if result.stderr:
        combined = combined + "\n--- STDERR ---\n" + result.stderr
    counts = _parse_pytest_counts(combined)
    failures = _extract_first_failures(combined, k=3)
    # pytest returns 0 if all passed, 1 if any failed, 5 if no tests.
    # Treat 0 as success and any-non-zero as failure for our ok bit.
    return Observation(
        ok=(result.returncode == 0),
        output=combined.strip() or "(no pytest output)",
        structured={
            "argv": cmd,
            "returncode": result.returncode,
            **counts,
            "first_failures": failures,
        },
        duration_ms=result.duration_ms,
        error="" if result.returncode == 0 else (
            f"pytest exit {result.returncode}; "
            f"{counts['failed']} failed, {counts['errors']} errored"
        ),
    )


def _finish_dispatch(call: ToolCall, sandbox: Sandbox) -> Observation:
    """No-op tool — signals 'I am done, no more steps needed'.

    The verifier's "success" verdict is the canonical termination
    signal; `finish` lets the executor explicitly say "I have nothing
    more to do" when there's no work left even though subgoals remain
    (e.g. the user gave an over-ambitious plan that turned out to be
    one step).
    """
    t0 = time.monotonic()
    reason = call.args.get("reason", "")
    if not isinstance(reason, str):
        reason = ""
    return Observation(
        ok=True,
        output=f"finish signalled: {reason}" if reason else "finish signalled",
        structured={"finish": True, "reason": reason},
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


TOOLS = [
    Tool(
        name="run_shell",
        description=(
            "Run a shell command inside the sandbox. Pass argv as a "
            "list of strings ('command': ['ls', '-la']) or a single "
            "string that will be shlex-split. Returns combined stdout "
            "+ stderr (truncated). Governance gates BLOCK certain "
            "binaries and patterns before dispatch."
        ),
        json_schema={
            "command": "list[str] | str",
            "timeout_s": "int (optional, default 60, max 300)",
        },
        dispatch=_run_shell_dispatch,
        risk="high",
    ),
    Tool(
        name="run_tests",
        description=(
            "Run pytest inside the sandbox. The observation includes "
            "parsed pass/fail/skip/error counts and the first 3 "
            "failure tracebacks. Use this rather than run_shell for "
            "test execution so the orchestrator can detect the "
            "terminal 'tests pass' state."
        ),
        json_schema={
            "args": "list[str] (optional pytest args, e.g. ['-k', 'pattern'])",
            "timeout_s": "int (optional, default 300, max 600)",
        },
        dispatch=_run_tests_dispatch,
        risk="medium",
    ),
    Tool(
        name="finish",
        description=(
            "Signal that no further actions are needed. Use sparingly: "
            "the verifier's 'success' verdict is the normal way to end "
            "the loop."
        ),
        json_schema={"reason": "str (optional)"},
        dispatch=_finish_dispatch,
        risk="low",
    ),
]
