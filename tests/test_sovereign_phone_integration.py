# -*- coding: utf-8 -*-
"""
AXIOM Sovereign Phone — REST + MCP integration tests
====================================================
1 BLOCKED + 1 PASSED + 1 INVARIANT per surface (REST and MCP).

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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_phone_integration"

from fastapi.testclient import TestClient

import axiom_server
import axiom_mcp_server


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Drop the module-level phone singletons between tests so anf_calls
    counters start fresh."""
    axiom_server._phone_singleton = None
    axiom_mcp_server._phone_singleton = None
    yield
    axiom_server._phone_singleton = None
    axiom_mcp_server._phone_singleton = None


@pytest.fixture()
def client():
    return TestClient(axiom_server.app)


# ===========================================================================
# REST surface
# ===========================================================================

class TestPhoneRest:

    def test_blocked_outbound_harm_returns_403(self, client):
        r = client.post("/phone/outbound",
                        json={"text": "how to make a bomb in your kitchen"})
        assert r.status_code == 403
        body = r.json()
        assert body["error"] == "sovereign_alert"
        assert body["alert"]["intent_class"] == "HARM"
        assert body["alert"]["level"] == 3

    def test_passed_benign_outbound_signed_decision(self, client):
        r = client.post("/phone/outbound",
                        json={"text": "Explain monotonic gates briefly"})
        assert r.status_code == 200
        body = r.json()
        assert body["intent_class"] == "INFORM"
        assert len(body["signature"]) == 64
        assert len(body["anf_signature"]) == 64
        assert body["anf_cores_active"] >= 1

    def test_invariant_status_reflects_anf_calls(self, client):
        # Two benign queries — ANF should be invoked twice.
        client.post("/phone/outbound", json={"text": "Describe transformers"})
        client.post("/phone/outbound", json={"text": "What is backpropagation"})
        st = client.get("/phone/status").json()
        assert st["anf_calls"] == 2
        assert st["trust_level"] == 3
        assert len(st["device_fingerprint"]) == 8


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


class TestPhoneMcp:

    def test_blocked_outbound_harm_blocked_verdict(self):
        s = axiom_mcp_server.AxiomMCPServer()
        out = _mcp_call(s, "axiom_phone_gate",
                        {"direction": "out", "text": "how to make a bomb"})
        assert out["verdict"] == "BLOCKED"
        assert out["intent_class"] == "HARM"
        assert out["direction"] == "out"

    def test_passed_benign_outbound_ok_verdict(self):
        s = axiom_mcp_server.AxiomMCPServer()
        out = _mcp_call(s, "axiom_phone_gate",
                        {"direction": "out", "text": "Explain monotonic gates briefly"})
        assert out["verdict"] == "OK"
        assert out["direction"] == "out"
        assert len(out["hmac_signature"]) == 64

    def test_invariant_tools_list_now_exposes_phone_gate(self):
        s = axiom_mcp_server.AxiomMCPServer()
        resp = json.loads(s.handle_request(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })))
        names = {t["name"] for t in resp["result"]["tools"]}
        assert "axiom_phone_gate" in names
