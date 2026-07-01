#!/usr/bin/env python3
"""
AXIOM pre-tool-use hook — manifest scope validator.

Claude Code calls this before every matched tool use (Bash, Edit, Write).
Reads the proposed action from stdin (JSON), checks it against the signed
session manifest, and either allows or blocks silently.

Env vars:
    AXIOM_SESSION_ID     Active session manifest to load
    AXIOM_MANIFEST_PATH  Path to manifests.jsonl store

stdin format (Claude Code PreToolUse hook):
    {"tool_name": "Bash", "tool_input": {"command": "..."}, "session_id": "..."}

stdout format to block:
    {"decision": "block", "reason": "[AXIOM manifest] ..."}

Every error path exits 0 so a missing/broken AXIOM install never blocks work.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _allow() -> None:
    sys.exit(0)


def _block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": f"[AXIOM manifest] {reason}"}))
    sys.exit(0)


def _review(reason: str, tool_name: str) -> None:
    print(json.dumps({
        "decision": "allow",
        "reason": (
            f"[AXIOM manifest — review] {reason}. "
            f"Tool: {tool_name}. Confirm with user before proceeding."
        ),
    }))
    sys.exit(0)


def main() -> None:
    # Read hook input
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        _allow()
        return

    tool_name  = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    session_id = data.get("session_id") or os.environ.get("AXIOM_SESSION_ID", "")

    if not session_id:
        _allow()
        return

    # Import AXIOM — project root on sys.path
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from axiom_action_manifest import (
            ManifestStore,
            ManifestValidator,
            ManifestVerdict,
        )
    except ImportError:
        _allow()
        return

    # Load manifest for this session
    try:
        store    = ManifestStore()
        manifest = store.load(session_id)
    except Exception:
        _allow()
        return

    if manifest is None:
        _allow()
        return

    # Extract path / command from tool input
    path = command = None
    if tool_name in ("Edit", "Write", "Read", "MultiEdit"):
        path = tool_input.get("file_path")
    elif tool_name == "Bash":
        command = tool_input.get("command", "")
    elif tool_name in ("Glob", "Grep"):
        path = tool_input.get("path")

    # Check against manifest
    try:
        validator = ManifestValidator()
        verdict, reason = validator.check_action(manifest, tool_name, path, command)
    except Exception:
        _allow()
        return

    if verdict == ManifestVerdict.BLOCK:
        _block(reason)
    elif verdict == ManifestVerdict.REVIEW:
        _review(reason, tool_name)
    else:
        _allow()


if __name__ == "__main__":
    main()
