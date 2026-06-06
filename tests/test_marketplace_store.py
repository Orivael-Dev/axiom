# -*- coding: utf-8 -*-
"""
Marketplace store + install-review render tests — pure (fake bridge).
=====================================================================
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marketplace import AgentStore  # noqa: E402
from aui.render import render_install_review  # noqa: E402


class FakeBridge:
    """Stateful fake of the marketplace bridge surface."""
    def __init__(self, *, valid=True):
        self._valid = valid
        self._state = {}        # pair_id -> authorized bool / revoked
        self.events = []

    def mkt_verify(self, manifest):
        return {"valid": self._valid, "name": manifest["name"],
                "version": manifest["version"],
                "error": None if self._valid else "signature invalid"}

    def mkt_install(self, manifest):
        pid = "bp-fake123"
        self._state[pid] = {"authorized": False, "revoked": False}
        return {"installed": True, "agent": manifest["name"],
                "version": manifest["version"], "pair_id": pid, "authorized": False}

    def mkt_review(self, manifest, pair_id):
        return {"requested_access": {"additional_block_patterns": 1,
                                     "disabled_default_classes": [],
                                     "allow_only_classes": None, "tags": ["demo"]}}

    def mkt_approve(self, pair_id, actor="human"):
        self._state[pair_id]["authorized"] = True
        return {"authorized": True, "state": "ACTIVE_VALIDATED"}

    def mkt_revoke(self, pair_id, actor="human"):
        self._state[pair_id] = {"authorized": False, "revoked": True}
        return {"authorized": False, "state": "REVOKED"}

    def mkt_authority(self, pair_id):
        st = self._state.get(pair_id, {})
        state = "REVOKED" if st.get("revoked") else (
            "ACTIVE_VALIDATED" if st.get("authorized") else "ACTIVE_PENDING")
        return {"authorized": st.get("authorized", False), "state": state}

    def log_event(self, event_type, **kw):
        self.events.append((event_type, kw.get("outcome")))
        return {"logged": True}


_MAN = {"name": "demo-agent", "version": "0.1.0"}


def test_install_review_approve_revoke_flow_and_logging():
    b = FakeBridge(valid=True)
    store = AgentStore(b)

    review = store.install_for_review(_MAN)
    assert review.valid_signature and review.installed
    assert review.authorized is False           # sandboxed
    pid = review.pair_id
    assert store.can_act(pid) is False           # gate blocks pre-approval

    store.approve(pid, agent=review.agent)
    assert store.can_act(pid) is True            # gate opens after approval

    store.revoke(pid, agent=review.agent)
    assert store.can_act(pid) is False           # cut instantly

    kinds = [e for e, _ in b.events]
    assert kinds == ["agent_sandboxed", "agent_approved", "agent_revoked"]


def test_invalid_signature_refused_and_logged():
    b = FakeBridge(valid=False)
    review = AgentStore(b).install_for_review(_MAN)
    assert review.valid_signature is False
    assert review.installed is False
    assert ("agent_rejected", "bad_signature") in b.events


def test_render_install_review_panels():
    b = FakeBridge(valid=True)
    review = AgentStore(b).install_for_review(_MAN)
    panel = render_install_review(review)
    assert "install review" in panel
    assert "signature: VALID" in panel
    assert "awaiting human approval" in panel

    bad = AgentStore(FakeBridge(valid=False)).install_for_review(_MAN)
    assert "rejected" in render_install_review(bad)
