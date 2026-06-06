# -*- coding: utf-8 -*-
"""
Workspace assembler + AUI render tests — pure, no Axiom server.
==============================================================
Uses a fake bridge so the goal→workspace mapping and the adaptive layout
are tested deterministically.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workspace.assembler import open_workspace, AssembledWorkspace  # noqa: E402
from aui.render import render  # noqa: E402


class FakeBridge:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def assemble_workspace(self, goal, domain=None):
        self.calls.append((goal, domain))
        return {"goal": goal, **self._response}


def test_allowed_with_context_maps_and_renders():
    bridge = FakeBridge({
        "allowed": True, "intent_class": "INFORM", "intent_confidence": 0.55,
        "blocked_reason": "", "recall_hit": True,
        "recalled": {"domain": "general",
                     "active_constraints": ["local_first"],
                     "resolution": "approved_for_demo",
                     "packet_signature": "abc123def456ghi789"},
        "hmac_signature": "sig0123456789abcdef",
    })
    ws = open_workspace(bridge, "work on the launch demo", domain="general")
    assert ws.status == "recalled"
    assert ws.allowed and ws.has_context
    assert bridge.calls == [("work on the launch demo", "general")]

    panel = render(ws)
    assert "AX OS workspace" in panel
    assert "recalled context" in panel
    assert "local_first" in panel
    assert "approved_for_demo" in panel


def test_allowed_without_context_is_fresh():
    bridge = FakeBridge({
        "allowed": True, "intent_class": "INFORM", "intent_confidence": 0.6,
        "blocked_reason": "", "recall_hit": False, "recalled": None,
        "hmac_signature": "sigfresh000000000",
    })
    ws = open_workspace(bridge, "a brand new project")
    assert ws.status == "fresh"
    panel = render(ws)
    assert "fresh workspace" in panel
    assert "no prior local context" in panel


def test_refused_goal_renders_refusal():
    bridge = FakeBridge({
        "allowed": False, "intent_class": "HARM", "intent_confidence": 0.5,
        "blocked_reason": "intent_gate: harm", "recall_hit": False,
        "recalled": None, "hmac_signature": "sigblocked00000000",
    })
    ws = open_workspace(bridge, "do something harmful")
    assert ws.status == "refused"
    assert not ws.allowed
    panel = render(ws)
    assert "refused" in panel
    assert "intent_gate: harm" in panel
    assert "no workspace assembled" in panel
