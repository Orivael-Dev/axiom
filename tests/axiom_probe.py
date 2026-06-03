# -*- coding: utf-8 -*-
"""
Shared Axiom-readiness probe for AX OS e2e tests.
=================================================
e2e tests need a *current* Axiom MCP server — not just any Axiom. A stale
install (one predating axiom_workspace / memory / ledger / marketplace)
launches fine but returns "Unknown tool: …", which used to surface as a
red failure. ``axiom_ready()`` launches the server once and checks the
tool surface, so those suites **skip with a clear message** instead —
treating a stale build the same as an absent one.
"""
from __future__ import annotations

import functools
import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

REQUIRED_TOOL = "axiom_workspace"


def axiom_command() -> Tuple[Optional[list], Optional[str]]:
    """How to launch Axiom: (command, cwd) — or (None, None) if unavailable."""
    repo = os.environ.get("AXIOM_REPO")
    if repo and (Path(repo) / "axiom_mcp_server.py").exists():
        return ["python", "axiom_mcp_server.py"], repo
    if importlib.util.find_spec("axiom_mcp_server") is not None:
        return ["python", "-m", "axiom_mcp_server"], None
    return None, None


def axiom_repo() -> Optional[str]:
    return axiom_command()[1]


@functools.lru_cache(maxsize=1)
def axiom_ready() -> Tuple[bool, str]:
    """(ready, skip_reason). Launches the server once and checks the tools."""
    cmd, cwd = axiom_command()
    if not cmd:
        return (False, "Axiom MCP server unavailable (set AXIOM_REPO or pip install axiom)")
    try:
        from bridge import AxiomBridge
        env = {
            "AXIOM_MASTER_KEY": os.environ.get("AXIOM_MASTER_KEY", "probe-key"),
            "AXIOM_MEMORY_STORE": tempfile.mktemp(suffix=".jsonl"),
            "AXIOM_AUDIT_LEDGER": tempfile.mktemp(suffix=".jsonl"),
            "AXIOM_MARKETPLACE_LEDGER": tempfile.mktemp(suffix=".jsonl"),
        }
        with AxiomBridge(command=cmd, cwd=cwd, env=env) as ax:
            tools = ax.list_tools()
    except Exception as e:  # noqa: BLE001
        return (False, f"could not launch Axiom MCP server: {e}")
    if REQUIRED_TOOL not in tools:
        return (False, f"stale Axiom build: {REQUIRED_TOOL!r} missing from tools/list "
                       f"({len(tools)} tools) — update your Axiom install to current main")
    return (True, "")
