"""Filesystem tools: write_file, read_file, list_dir, apply_patch."""
from __future__ import annotations

import difflib
import re
import time
from typing import List

from ..models import Observation, ToolCall
from ..sandbox import Sandbox, SandboxError
from . import Tool


def _write_file_dispatch(call: ToolCall, sandbox: Sandbox) -> Observation:
    t0 = time.monotonic()
    path = call.args.get("path")
    content = call.args.get("content")
    if not isinstance(path, str) or not path.strip():
        return Observation(ok=False, output="",
                           error="write_file requires string 'path'",
                           duration_ms=int((time.monotonic() - t0) * 1000))
    if not isinstance(content, str):
        return Observation(ok=False, output="",
                           error="write_file requires string 'content'",
                           duration_ms=int((time.monotonic() - t0) * 1000))
    try:
        sandbox.write_file(path, content)
    except SandboxError as e:
        return Observation(ok=False, output="", error=str(e),
                           duration_ms=int((time.monotonic() - t0) * 1000))
    return Observation(
        ok=True,
        output=f"wrote {len(content)} bytes to {path}",
        structured={"path": path, "bytes": len(content)},
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


def _read_file_dispatch(call: ToolCall, sandbox: Sandbox) -> Observation:
    t0 = time.monotonic()
    path = call.args.get("path")
    if not isinstance(path, str) or not path.strip():
        return Observation(ok=False, output="",
                           error="read_file requires string 'path'",
                           duration_ms=int((time.monotonic() - t0) * 1000))
    try:
        content = sandbox.read_file(path)
    except SandboxError as e:
        return Observation(ok=False, output="", error=str(e),
                           duration_ms=int((time.monotonic() - t0) * 1000))
    if content is None:
        return Observation(
            ok=False, output="",
            error=f"file not found: {path}",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    return Observation(
        ok=True, output=content, structured={"path": path},
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


def _list_dir_dispatch(call: ToolCall, sandbox: Sandbox) -> Observation:
    t0 = time.monotonic()
    path = call.args.get("path", ".")
    if not isinstance(path, str):
        return Observation(ok=False, output="",
                           error="list_dir 'path' must be a string",
                           duration_ms=int((time.monotonic() - t0) * 1000))
    try:
        entries = sandbox.list_dir(path)
    except SandboxError as e:
        return Observation(ok=False, output="", error=str(e),
                           duration_ms=int((time.monotonic() - t0) * 1000))
    return Observation(
        ok=True,
        output="\n".join(entries) if entries else "(empty)",
        structured={"path": path, "entries": entries},
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


# ── apply_patch (unified diff) ────────────────────────────────────────

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
)


def _apply_unified_diff(original: str, diff: str) -> str:
    """Minimal unified-diff applier — context + add + remove lines.

    Not as full-featured as `patch(1)`; sufficient for single-file
    hunks the executor will produce. Raises ValueError on malformed
    hunks so the dispatcher returns a clean error.
    """
    lines = original.splitlines(keepends=True) if original else []
    out: List[str] = []
    i = 0
    diff_lines = diff.splitlines()
    di = 0
    # Skip the file-header lines (--- / +++) if present.
    while di < len(diff_lines) and (
        diff_lines[di].startswith("---") or diff_lines[di].startswith("+++")
    ):
        di += 1
    while di < len(diff_lines):
        header = diff_lines[di]
        m = _HUNK_HEADER_RE.match(header)
        if not m:
            di += 1
            continue
        orig_start = int(m.group(1))
        # Copy original lines up to this hunk's start.
        while i < orig_start - 1 and i < len(lines):
            out.append(lines[i])
            i += 1
        di += 1
        while di < len(diff_lines) and not diff_lines[di].startswith("@@"):
            hl = diff_lines[di]
            if hl.startswith("+"):
                addition = hl[1:]
                out.append(addition + ("\n" if not addition.endswith("\n") else ""))
            elif hl.startswith("-"):
                if i >= len(lines):
                    raise ValueError(
                        f"hunk wants to delete line {i+1} past end-of-file"
                    )
                expected = hl[1:]
                actual = lines[i].rstrip("\n")
                if actual != expected:
                    raise ValueError(
                        f"hunk context mismatch at line {i+1}: "
                        f"expected {expected!r}, got {actual!r}"
                    )
                i += 1
            elif hl.startswith(" "):
                # Context line — must match.
                if i >= len(lines):
                    raise ValueError(
                        f"hunk context wants line {i+1} past end-of-file"
                    )
                expected = hl[1:]
                actual = lines[i].rstrip("\n")
                if actual != expected:
                    raise ValueError(
                        f"hunk context mismatch at line {i+1}: "
                        f"expected {expected!r}, got {actual!r}"
                    )
                out.append(lines[i])
                i += 1
            else:
                # Blank or comment line inside a hunk — skip.
                pass
            di += 1
    # Trailing original lines after the last hunk.
    while i < len(lines):
        out.append(lines[i])
        i += 1
    return "".join(out)


def _apply_patch_dispatch(call: ToolCall, sandbox: Sandbox) -> Observation:
    t0 = time.monotonic()
    path = call.args.get("path")
    diff = call.args.get("diff")
    if not isinstance(path, str) or not path.strip():
        return Observation(ok=False, output="",
                           error="apply_patch requires string 'path'",
                           duration_ms=int((time.monotonic() - t0) * 1000))
    if not isinstance(diff, str) or not diff.strip():
        return Observation(ok=False, output="",
                           error="apply_patch requires string 'diff'",
                           duration_ms=int((time.monotonic() - t0) * 1000))
    try:
        original = sandbox.read_file(path) or ""
        patched = _apply_unified_diff(original, diff)
        sandbox.write_file(path, patched)
    except (SandboxError, ValueError) as e:
        return Observation(ok=False, output="", error=str(e),
                           duration_ms=int((time.monotonic() - t0) * 1000))
    return Observation(
        ok=True,
        output=f"patched {path} ({len(patched)} bytes)",
        structured={"path": path, "bytes": len(patched)},
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


TOOLS = [
    Tool(
        name="write_file",
        description=(
            "Create or overwrite a file inside the sandbox workdir. "
            "Use this for new files; use apply_patch for surgical edits "
            "to existing files."
        ),
        json_schema={"path": "str", "content": "str"},
        dispatch=_write_file_dispatch,
        risk="medium",
    ),
    Tool(
        name="read_file",
        description=(
            "Read a file from the sandbox workdir. Returns its full "
            "contents (truncated at 16KB) or an error if missing."
        ),
        json_schema={"path": "str"},
        dispatch=_read_file_dispatch,
        risk="low",
    ),
    Tool(
        name="list_dir",
        description=(
            "List entries in a directory inside the sandbox workdir. "
            "Defaults to the workdir root."
        ),
        json_schema={"path": "str (optional, default '.')"},
        dispatch=_list_dir_dispatch,
        risk="low",
    ),
    Tool(
        name="apply_patch",
        description=(
            "Apply a unified diff to an existing file. The diff must "
            "use standard ``@@`` hunk headers; context lines must match "
            "the current file or the patch is rejected."
        ),
        json_schema={"path": "str", "diff": "str"},
        dispatch=_apply_patch_dispatch,
        risk="medium",
    ),
]
