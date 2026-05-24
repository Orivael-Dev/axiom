#!/usr/bin/env python3
"""Trivial in-container tool runner.

The orchestrator currently dispatches every tool call via direct
`docker exec` of the tool's argv (e.g. `docker exec <cid> python -m
pytest`) and reads the workdir through the bind mount, so this script
is reserved for tool implementations that need an in-container helper
beyond what the registered tools do today.

It reads JSON args from /work/.axiom/in.json and writes
{"ok": bool, "stdout": str, "stderr": str, "returncode": int}
to /work/.axiom/out.json.

Kept intentionally tiny — no imports beyond the stdlib so the image
build stays fast and the attack surface stays minimal.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


IN_PATH  = Path("/work/.axiom/in.json")
OUT_PATH = Path("/work/.axiom/out.json")


def _write_out(payload: dict) -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload), encoding="utf-8")
    return 0 if payload.get("ok") else 1


def main(argv: list) -> int:
    if not IN_PATH.exists():
        return _write_out({
            "ok": False, "stdout": "", "stderr":
            f"no input file at {IN_PATH}", "returncode": -1,
        })
    try:
        spec = json.loads(IN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return _write_out({
            "ok": False, "stdout": "", "stderr":
            f"in.json unreadable: {e}", "returncode": -1,
        })
    cmd = spec.get("argv")
    if not isinstance(cmd, list) or not cmd:
        return _write_out({
            "ok": False, "stdout": "", "stderr":
            "in.json must include 'argv': [str, ...]", "returncode": -1,
        })
    timeout_s = int(spec.get("timeout_s", 60))
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(x) for x in cmd],
            cwd="/work", capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as e:
        return _write_out({
            "ok": False, "stdout": e.stdout or "",
            "stderr": (e.stderr or "") + f"\n[timed out after {timeout_s}s]",
            "returncode": -1,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
    except (OSError, FileNotFoundError) as e:
        return _write_out({
            "ok": False, "stdout": "", "stderr": str(e), "returncode": -1,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
    return _write_out({
        "ok": proc.returncode == 0,
        "stdout": proc.stdout, "stderr": proc.stderr,
        "returncode": int(proc.returncode),
        "duration_ms": int((time.monotonic() - t0) * 1000),
    })


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
