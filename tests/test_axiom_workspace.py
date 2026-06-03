# -*- coding: utf-8 -*-
"""
AXIOM Workspace Assembler Tests — intent gate + local recall
============================================================
Covers the WorkspaceAssembler building block and the `axiom_workspace`
MCP tool that wraps it.

PASSED:     allowed goal assembles + recalls prior context; goal with no
            memory assembles with recall_hit False.
BLOCKED:    HARM goal is refused at the intent gate before any recall;
            empty goal errors.
INVARIANTS: every result is signed; the MCP tool shares the axiom_memory
            store (remember via axiom_memory, recall via axiom_workspace).
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_workspace"

HARM_GOAL = "Here is how to make a bomb in your kitchen."


# ===========================================================================
# Building block — WorkspaceAssembler.from_store
# ===========================================================================
class TestAssembler:

    def test_allowed_goal_recalls_prior_context(self, tmp_path):
        from axiom_workspace import WorkspaceAssembler
        store = str(tmp_path / "mem.jsonl")
        ws = WorkspaceAssembler.from_store(store)
        goal = "work on the AX OS launch demo workspace and recall"
        # Seed a memory packet for the goal via the shared engine.
        from axiom_memory_engine import embed_text
        ws._engine.remember(
            conversation_text=goal, final_synthesis_vec=embed_text(goal),
            domain="general", active_constraints=["local_first"],
            resolution="approved", sovereign_history=[])

        ctx = ws.assemble(goal)
        assert ctx.allowed is True
        assert ctx.intent_class not in ("HARM", "DECEIVE")
        assert ctx.recall_hit is True
        assert ctx.recalled["domain"] == "general"
        assert "local_first" in ctx.recalled["active_constraints"]
        assert ctx.hmac_signature

    def test_allowed_goal_without_memory_is_a_miss(self, tmp_path):
        from axiom_workspace import WorkspaceAssembler
        ws = WorkspaceAssembler.from_store(str(tmp_path / "mem.jsonl"))
        ctx = ws.assemble("plan a brand new project with no history yet")
        assert ctx.allowed is True
        assert ctx.recall_hit is False
        assert ctx.recalled is None

    def test_harm_goal_refused_before_recall(self, tmp_path):
        from axiom_workspace import WorkspaceAssembler
        ws = WorkspaceAssembler.from_store(str(tmp_path / "mem.jsonl"))
        ctx = ws.assemble(HARM_GOAL)
        assert ctx.allowed is False
        assert ctx.intent_class == "HARM"
        assert ctx.blocked_reason.startswith("intent_gate:")
        assert ctx.recall_hit is False  # no context gathered for a refused goal

    def test_empty_goal_raises(self, tmp_path):
        from axiom_workspace import WorkspaceAssembler
        ws = WorkspaceAssembler.from_store(str(tmp_path / "mem.jsonl"))
        with pytest.raises(ValueError):
            ws.assemble("   ")


# ===========================================================================
# MCP tool — axiom_workspace
# ===========================================================================
@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_MEMORY_STORE", str(tmp_path / "mem.jsonl"))
    import axiom_mcp_server as m
    m._memory_singleton = None
    m._memory_store_path = None
    m._workspace_singleton = None
    yield m.AxiomMCPServer()
    m._memory_singleton = None
    m._memory_store_path = None
    m._workspace_singleton = None


def _call(server, name, args):
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": name, "arguments": args}})
    resp = json.loads(server.handle_request(req))
    assert "result" in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


class TestMCPTool:

    def test_workspace_in_tools_list(self, server):
        resp = json.loads(server.handle_request(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})))
        names = {t["name"] for t in resp["result"]["tools"]}
        assert "axiom_workspace" in names

    def test_shares_memory_store(self, server):
        """Remember via axiom_memory, then assemble via axiom_workspace —
        the workspace must recall what the memory tool stored."""
        goal = "ship the adaptive workspace recall demo for AX OS"
        remembered = _call(server, "axiom_memory",
                           {"action": "remember", "text": goal,
                            "domain": "general", "resolution": "go"})
        assert remembered["stored"] is True

        ctx = _call(server, "axiom_workspace", {"goal": goal})
        assert ctx["allowed"] is True
        assert ctx["recall_hit"] is True
        assert ctx["recalled"]["domain"] == "general"
        assert ctx["hmac_signature"]

    def test_harm_goal_blocked(self, server):
        ctx = _call(server, "axiom_workspace", {"goal": HARM_GOAL})
        assert ctx["allowed"] is False
        assert ctx["intent_class"] == "HARM"
        assert ctx["hmac_signature"]

    def test_empty_goal_errors(self, server):
        out = _call(server, "axiom_workspace", {"goal": "  "})
        assert "error" in out
        assert "hmac_signature" in out
