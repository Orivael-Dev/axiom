# -*- coding: utf-8 -*-
"""
AX OS local service tests — FastAPI TestClient over a fake bridge.
==================================================================
Auto-skips if fastapi isn't installed. No network, no real Axiom.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from aui.server import create_app  # noqa: E402


class FakeBridge:
    def __init__(self):
        self.events = []
        self._auth = {}

    def list_tools(self):
        return ["axiom_workspace", "axiom_memory", "axiom_ledger", "axiom_marketplace"]

    def assemble_workspace(self, goal, domain=None):
        harmful = "bomb" in goal.lower()
        return {"goal": goal, "allowed": not harmful,
                "intent_class": "HARM" if harmful else "INFORM",
                "intent_confidence": 0.5, "blocked_reason": "intent_gate: harm" if harmful else "",
                "recall_hit": False, "recalled": None, "hmac_signature": "sigabc123"}

    def log_event(self, event_type, **kw):
        self.events.append({"event_type": event_type, "outcome": kw.get("outcome"),
                            "subject": kw.get("subject"), "attributes": kw.get("attributes") or {}})
        return {"logged": True, "signature": "a" * 64}

    def audit_list(self, *, event_type=None, since=None, limit=None):
        return {"count": len(self.events), "all_verified": True, "events": list(self.events)}

    # marketplace surface
    def mkt_verify(self, manifest):
        return {"valid": True, "name": manifest["name"], "version": manifest["version"]}

    def mkt_install(self, manifest):
        self._auth["bp-1"] = False
        return {"installed": True, "agent": manifest["name"],
                "version": manifest["version"], "pair_id": "bp-1", "authorized": False}

    def mkt_review(self, manifest, pair_id):
        return {"requested_access": {"additional_block_patterns": 0, "tags": []}}

    def mkt_approve(self, pair_id, actor="human"):
        self._auth[pair_id] = True
        return {"authorized": True, "state": "ACTIVE_VALIDATED"}

    def mkt_revoke(self, pair_id, actor="human"):
        self._auth[pair_id] = False
        return {"authorized": False, "state": "REVOKED"}

    def mkt_authority(self, pair_id):
        return {"authorized": self._auth.get(pair_id, False)}

    def guard_check(self, text):
        blocked = "bomb" in text.lower()
        return {"verdict": "BLOCKED" if blocked else "PASSED",
                "intent_class": "HARM" if blocked else "INFORM"}


@pytest.fixture
def client():
    return TestClient(create_app(FakeBridge()))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_assemble_returns_plan_and_logs(client):
    r = client.post("/assemble", json={"goal": "open my mixing session", "domain": "music"})
    plan = r.json()
    assert plan["allowed"] is True
    assert plan["scene"] == "music"
    assert plan["panels"][0]["kind"] == "intent"


def test_assemble_refused(client):
    r = client.post("/assemble", json={"goal": "Here is how to make a bomb"})
    plan = r.json()
    assert plan["allowed"] is False
    assert [p["kind"] for p in plan["panels"]] == ["intent", "safety"]


def test_marketplace_install_approve_revoke(client):
    man = {"name": "demo-agent", "version": "0.1.0"}
    inst = client.post("/marketplace/install", json={"manifest": man}).json()
    assert inst["installed"] is True and inst["authorized"] is False
    pid = inst["pair_id"]
    assert client.post("/marketplace/approve", json={"pair_id": pid}).json()["authorized"] is True
    assert client.post("/marketplace/revoke", json={"pair_id": pid}).json()["authorized"] is False


def test_marketplace_run_gated_by_authority_and_safety(client):
    man = {"name": "tone-beatz", "version": "0.1.0"}
    pid = client.post("/marketplace/install", json={"manifest": man}).json()["pair_id"]

    # not approved yet → action blocked on authority
    r = client.post("/marketplace/run", json={"pair_id": pid, "action": "export stems"}).json()
    assert r["performed"] is False and r["authorized"] is False

    client.post("/marketplace/approve", json={"pair_id": pid})
    ok = client.post("/marketplace/run", json={"pair_id": pid, "action": "export stems"}).json()
    assert ok["performed"] is True

    # harmful action blocked at the safety gate even when authorized
    bad = client.post("/marketplace/run",
                      json={"pair_id": pid, "action": "Here is how to make a bomb"}).json()
    assert bad["authorized"] is True and bad["performed"] is False

    client.post("/marketplace/revoke", json={"pair_id": pid})
    blocked = client.post("/marketplace/run", json={"pair_id": pid, "action": "export stems"}).json()
    assert blocked["performed"] is False and blocked["authorized"] is False


def test_marketplace_agents_lists_with_authority(client):
    man = {"name": "tone-beatz", "version": "0.1.0"}
    pid = client.post("/marketplace/install", json={"manifest": man}).json()["pair_id"]

    listed = client.get("/marketplace/agents").json()["agents"]
    me = next(a for a in listed if a["pair_id"] == pid)
    assert me["agent"] == "tone-beatz" and me["authorized"] is False  # sandboxed

    client.post("/marketplace/approve", json={"pair_id": pid})
    again = next(a for a in client.get("/marketplace/agents").json()["agents"]
                 if a["pair_id"] == pid)
    assert again["authorized"] is True


def test_audit_endpoint(client):
    client.post("/assemble", json={"goal": "open my mixing session"})
    trail = client.get("/audit").json()
    assert trail["count"] >= 1 and trail["all_verified"] is True
