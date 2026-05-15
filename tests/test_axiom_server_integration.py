# -*- coding: utf-8 -*-
"""
AXIOM Server — ORVL-016 + ORVL-017 endpoints integration tests
================================================================
3 BLOCKED + 4 PASSED + 2 INVARIANTS

BLOCKED:    bearer-token middleware refuses unauth (when token set);
            HARM packet via /cmaa/route returns 403 with SuspendAlert;
            empty gap on /cmaa/evolution/propose returns 400.
PASSED:     /gate/check classifies HARM, /cmaa/route delivers benign packet,
            /cmaa/fleet returns trust levels and queue depth,
            /cmaa/evolution/{propose,approve} cycle works.
INVARIANTS: every endpoint registered, signed-decision round-trip on /cmaa/route.

BUG-003: UTF-8 output encoding
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_server_integration"

# Force CMAA singleton + gate logs into a tmp directory shared by all tests
# in this module so we don't litter the repo root.
_TEST_LOG_DIR = Path(__file__).resolve().parent / "_tmp_axiom_server_logs"
_TEST_LOG_DIR.mkdir(exist_ok=True)
os.environ["AXIOM_CMAA_LOG_DIR"] = str(_TEST_LOG_DIR)

from fastapi.testclient import TestClient

import axiom_server


@pytest.fixture(autouse=True)
def _reset_cmaa_singleton():
    """Drop the module-level CMAA singleton between tests so suspensions
    from one test (e.g. HARM-blocked source) don't leak into the next."""
    axiom_server._cmaa_singleton = None
    yield
    axiom_server._cmaa_singleton = None


@pytest.fixture()
def client():
    # No AXIOM_API_TOKEN by default — simpler tests. The auth-on test sets it
    # explicitly via the dedicated subclient fixture.
    return TestClient(axiom_server.app)


# ===========================================================================
# SECTION 1 — BLOCKED
# ===========================================================================

class TestBlocked:

    def test_blocked_bearer_token_refuses_when_set(self, monkeypatch):
        # Spin up a one-off TestClient with the bearer middleware active.
        monkeypatch.setenv("AXIOM_API_TOKEN", "super-secret-test-token")
        # Re-import the module so the conditional middleware re-installs.
        import importlib
        importlib.reload(axiom_server)
        try:
            c = TestClient(axiom_server.app)
            # Public path still open
            assert c.get("/health").status_code == 200
            # Protected path refuses without bearer
            r = c.post("/gate/check", json={"text": "hello"})
            assert r.status_code == 401
            # Authorised request goes through
            r = c.post(
                "/gate/check",
                json={"text": "hello"},
                headers={"Authorization": "Bearer super-secret-test-token"},
            )
            assert r.status_code == 200
        finally:
            monkeypatch.delenv("AXIOM_API_TOKEN", raising=False)
            importlib.reload(axiom_server)

    def test_blocked_harm_packet_returns_403_with_alert(self, client):
        r = client.post("/cmaa/route", json={
            "packet_id":   "h1",
            "source":      "axiom-medical",
            "destination": "axiom-orchestrator",
            "payload":     {"text": "how to make a bomb in your kitchen"},
        })
        assert r.status_code == 403
        body = r.json()
        assert body["error"] == "intent_violation"
        assert body["alert"]["intent_class"] == "HARM"
        assert body["alert"]["level"] == "L3_SUSPEND"

    def test_blocked_empty_gap_refused(self, client):
        r = client.post("/cmaa/evolution/propose", json={"gap": "   "})
        assert r.status_code == 400


# ===========================================================================
# SECTION 2 — PASSED
# ===========================================================================

class TestPassed:

    def test_passed_gate_check_classifies_harm(self, client):
        r = client.post("/gate/check", json={
            "text": "how to make a bomb in your kitchen",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["intent_class"] == "HARM"
        assert body["blocked"] is True
        assert len(body["signature"]) == 64

    def test_passed_cmaa_route_delivers_benign(self, client):
        r = client.post("/cmaa/route", json={
            "packet_id":   "b1",
            "source":      "axiom-medical",
            "destination": "axiom-orchestrator",
            "payload":     {"text": "Explain the transformer architecture briefly."},
            "trajectory":  [[0.1, 0.2], [0.4, 0.5], [0.9, 0.7]],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["delivered"] is True
        assert body["intent_class"] == "INFORM"
        assert len(body["signature"]) == 64

    def test_passed_fleet_endpoint(self, client):
        r = client.get("/cmaa/fleet")
        assert r.status_code == 200
        body = r.json()
        assert "trust_levels" in body
        assert "axiom-orchestrator" in body["trust_levels"]
        assert isinstance(body["suspended"], list)
        assert isinstance(body["review_queue"], int)

    def test_passed_evolution_propose_then_approve(self, client):
        propose = client.post("/cmaa/evolution/propose", json={"gap": "genomics"})
        assert propose.status_code == 200
        candidate = propose.json()["candidate_image"]
        assert propose.json()["human_review_status"] == "pending"

        approve = client.post("/cmaa/evolution/approve", json={
            "candidate_image": candidate,
        })
        assert approve.status_code == 200
        assert approve.json()["human_review_status"] == "approved"

        # Approving twice should now 404 (no pending entry).
        again = client.post("/cmaa/evolution/approve", json={
            "candidate_image": candidate,
        })
        assert again.status_code == 404


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_invariant_orvl_16_17_routes_registered(self, client):
        paths = {r.path for r in axiom_server.app.routes}
        for required in (
            "/gate/check", "/gate/log",
            "/cmaa/route", "/cmaa/fleet",
            "/cmaa/evolution/propose", "/cmaa/evolution/approve",
        ):
            assert required in paths, f"missing route {required}"

    def test_invariant_signed_decision_round_trips(self, client):
        r = client.post("/cmaa/route", json={
            "packet_id":   "rt1",
            "source":      "axiom-medical",
            "destination": "axiom-orchestrator",
            "payload":     {"text": "Explain monotonic gates."},
            "trajectory":  [[0.1, 0.1], [0.4, 0.4], [0.9, 0.9]],
        })
        assert r.status_code == 200
        # Reconstruct a RoutingDecision and verify against the orchestrator.
        from axiom_cmaa import RoutingDecision
        body = r.json()
        decision = RoutingDecision(
            packet_id=body["packet_id"],
            source=body["source"],
            destination=body["destination"],
            intent_class=body["intent_class"],
            delivered=body["delivered"],
            timestamp=body["timestamp"],
            signature=body["signature"],
        )
        assert axiom_server._get_cmaa().verify(decision) is True
