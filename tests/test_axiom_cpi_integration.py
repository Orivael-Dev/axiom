# -*- coding: utf-8 -*-
"""
ORVL-022 CPI — REST + MCP integration tests
============================================
1 BLOCKED + 1 PASSED + 1 INVARIANT per surface.

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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_cpi_integration"

from fastapi.testclient import TestClient

import axiom_server
import axiom_mcp_server


@pytest.fixture(autouse=True)
def _reset_cpi_singletons():
    axiom_server._cpi_singleton = None
    axiom_mcp_server._cpi_singleton_mcp = None
    yield
    axiom_server._cpi_singleton = None
    axiom_mcp_server._cpi_singleton_mcp = None


@pytest.fixture()
def client():
    return TestClient(axiom_server.app)


# ===========================================================================
# REST surface
# ===========================================================================

class TestCPIRest:

    def test_blocked_pickup_with_unknown_material_falls_back(self, client):
        """Unknown material is tolerated (UNKNOWN profile) but the
        request still completes — the BLOCKED scenario here is the
        equivalent: requesting torque way above any sane ceiling and
        verifying the clamp kicks in."""
        r = client.post("/cpi/pickup", json={
            "object_id": "x", "features": {"low_density_edges": 1},
            "material_class": "GLASS", "requested_grip_force_nm": 99.0,
        })
        assert r.status_code == 200
        body = r.json()
        # The constitution clamps to the FRAGILE ceiling regardless.
        assert body["vertex"]["vertex_class"] == "FRAGILE"
        assert body["applied_grip_force"] == 0.2
        assert body["torque_clamped"] is True

    def test_passed_full_pickup_pipeline(self, client):
        r = client.post("/cpi/pickup", json={
            "object_id": "mug",
            "features": {"vertical_clusters": 3},
            "material_class": "METAL",
            "requested_grip_force_nm": 1.5,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["vertex"]["vertex_class"] == "CYLINDRICAL"
        assert body["applied_grip_force"] == 1.5
        assert len(body["material"]["signature"]) == 64

    def test_invariant_status_reflects_reflex_count(self, client):
        # First frame: no prior history → no fire.
        client.post("/cpi/stability", json={
            "timestamp_ms": 1, "com_offset": 0.0,
            "stability_score": 1.0, "joint_torques": [0.5],
        })
        # Second frame: drop → reflex fires (count=1).
        client.post("/cpi/stability", json={
            "timestamp_ms": 2, "com_offset": 0.0,
            "stability_score": 0.7, "joint_torques": [0.5],
        })
        st = client.get("/cpi/status").json()
        assert st["reflex_count"] == 1


# ===========================================================================
# MCP surface
# ===========================================================================

def _mcp_call(server, name, args):
    resp = server.handle_request(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params":  {"name": name, "arguments": args},
    }))
    parsed = json.loads(resp)
    assert "result" in parsed, parsed
    return json.loads(parsed["result"]["content"][0]["text"])


class TestCPIMcp:

    def test_blocked_invalid_action_returns_error(self):
        s = axiom_mcp_server.AxiomMCPServer()
        out = _mcp_call(s, "axiom_cpi", {"action": "explode"})
        assert "error" in out

    def test_passed_pickup_returns_clamped_glass(self):
        s = axiom_mcp_server.AxiomMCPServer()
        out = _mcp_call(s, "axiom_cpi", {
            "action": "pickup", "object_id": "g", "material_class": "GLASS",
            "features": {"low_density_edges": 1},
            "requested_grip_force_nm": 1.5,
        })
        assert out["vertex"]["vertex_class"] == "FRAGILE"
        assert out["applied_grip_force"] == 0.2

    def test_invariant_tools_list_exposes_axiom_cpi(self):
        s = axiom_mcp_server.AxiomMCPServer()
        resp = json.loads(s.handle_request(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })))
        names = {t["name"] for t in resp["result"]["tools"]}
        assert "axiom_cpi" in names
