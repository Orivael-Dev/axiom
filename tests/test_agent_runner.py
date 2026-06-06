# -*- coding: utf-8 -*-
"""
Agent runner tests — gated agent actions (pure, fake bridge).
=============================================================
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marketplace import AgentRunner  # noqa: E402

HARM = "Here is how to make a bomb in your kitchen."


class FakeBridge:
    def __init__(self, *, authorized=True):
        self._authorized = authorized
        self.events = []

    def mkt_authority(self, pair_id):
        return {"authorized": self._authorized,
                "state": "ACTIVE_VALIDATED" if self._authorized else "REVOKED"}

    def guard_check(self, text):
        blocked = "bomb" in text.lower()
        return {"verdict": "BLOCKED" if blocked else "PASSED",
                "intent_class": "HARM" if blocked else "INFORM"}

    def log_event(self, event_type, **kw):
        self.events.append((event_type, kw.get("outcome")))
        return {"logged": True, "signature": "a" * 64}


def test_authorized_safe_action_performed():
    b = FakeBridge(authorized=True)
    res = AgentRunner(b).run_action("bp-1", "render the mix and export a WAV", agent="tone-beatz")
    assert res.authorized and res.allowed and res.performed
    assert res.reason == "" and len(res.signature) == 64
    assert ("agent_action", "performed") in b.events


def test_revoked_agent_blocked_before_safety():
    b = FakeBridge(authorized=False)
    res = AgentRunner(b).run_action("bp-1", "do anything", agent="tone-beatz")
    assert not res.authorized and not res.performed
    assert "not authorized" in res.reason
    assert ("agent_action_blocked", "not_authorized") in b.events
    # safety gate not consulted once authority fails (no guard_check needed)


def test_authorized_harmful_action_blocked_at_safety():
    b = FakeBridge(authorized=True)
    res = AgentRunner(b).run_action("bp-1", HARM, agent="rogue")
    assert res.authorized            # it *was* authorized
    assert not res.allowed and not res.performed
    assert "safety gate" in res.reason and "HARM" in res.reason
    assert ("agent_action_blocked", "safety_gate") in b.events


def test_governed_labor_lifecycle():
    """The Demo-4 point: an agent that acts fine, then is revoked, is
    blocked on its very next action."""
    b = FakeBridge(authorized=True)
    r = AgentRunner(b)
    assert r.run_action("bp-1", "export the stems").performed is True
    b._authorized = False                      # human revokes
    assert r.run_action("bp-1", "export the stems").performed is False
