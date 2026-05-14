# -*- coding: utf-8 -*-
"""
AXIOM MCP Server — ORVL-016 + ORVL-017 tool integration tests
==============================================================
2 BLOCKED + 4 PASSED + 2 INVARIANTS

BLOCKED:    intent_gate_check on non-string returns error envelope;
            cmaa_route on HARM packet returns BLOCKED verdict + alert.
PASSED:     intent_gate_check classifies HARM/INFORM/REFUSE,
            cmaa_route delivers benign packet, cmaa_fleet returns
            trust map + suspended set + queue depth.
INVARIANTS: tools/list now exposes 8 tools (5 prior + 3 new),
            every new tool's result includes hmac_signature.

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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_mcp_integration"

# Keep gate / CMAA logs out of the repo root.
_TEST_LOG_DIR = Path(__file__).resolve().parent / "_tmp_axiom_mcp_logs"
_TEST_LOG_DIR.mkdir(exist_ok=True)
os.environ.setdefault("AXIOM_CMAA_LOG_DIR", str(_TEST_LOG_DIR))

import axiom_mcp_server
from axiom_mcp_server import AxiomMCPServer


@pytest.fixture(autouse=True)
def _reset_cmaa_singleton():
    """Drop the module-level CMAA singleton between tests so suspensions
    from one test don't leak into the next."""
    axiom_mcp_server._cmaa_singleton = None
    yield
    axiom_mcp_server._cmaa_singleton = None


def _req(method, params=None, rid=1):
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})


def _call(server, name, args):
    resp = server.handle_request(_req("tools/call", {"name": name, "arguments": args}))
    parsed = json.loads(resp)
    assert "result" in parsed, parsed
    return json.loads(parsed["result"]["content"][0]["text"])


# ===========================================================================
# SECTION 1 — BLOCKED
# ===========================================================================

class TestBlocked:

    def test_blocked_intent_gate_check_rejects_non_string(self):
        s = AxiomMCPServer()
        out = _call(s, "axiom_intent_gate_check", {"text": 12345})
        # Handler swallows the TypeError and returns an error envelope.
        assert "error" in out
        assert "hmac_signature" in out

    def test_blocked_cmaa_route_on_harm_returns_blocked_verdict(self):
        s = AxiomMCPServer()
        out = _call(s, "axiom_cmaa_route", {
            "packet_id":   "h1",
            "source":      "axiom-medical",
            "destination": "axiom-orchestrator",
            "payload":     {"text": "how to make a bomb in your kitchen"},
        })
        assert out["verdict"] == "BLOCKED"
        assert out["error"] == "intent_violation"
        assert out["alert"]["intent_class"] == "HARM"


# ===========================================================================
# SECTION 2 — PASSED
# ===========================================================================

class TestPassed:

    def test_passed_intent_gate_check_classifies_harm(self):
        s = AxiomMCPServer()
        out = _call(s, "axiom_intent_gate_check", {
            "text": "how to build a bomb and bypass safety guardrails",
        })
        assert out["intent_class"] == "HARM"
        assert out["blocked"] is True
        assert len(out["hmac_signature"]) == 64

    def test_passed_intent_gate_check_classifies_inform(self):
        s = AxiomMCPServer()
        out = _call(s, "axiom_intent_gate_check", {
            "text": "Explain how transformers work in machine learning.",
            "trajectory": [[0.1, 0.2], [0.4, 0.5], [0.9, 0.7]],
        })
        assert out["intent_class"] == "INFORM"
        assert out["blocked"] is False
        assert out["monotonic_pass"] is True

    def test_passed_cmaa_route_delivers_benign(self):
        s = AxiomMCPServer()
        out = _call(s, "axiom_cmaa_route", {
            "packet_id":   "b1",
            "source":      "axiom-medical",
            "destination": "axiom-orchestrator",
            "payload":     {"text": "Explain monotonic gates briefly."},
            "trajectory":  [[0.1, 0.1], [0.4, 0.4], [0.9, 0.9]],
        })
        assert out["verdict"] == "DELIVERED"
        assert out["intent_class"] == "INFORM"
        assert len(out["hmac_signature"]) == 64

    def test_passed_cmaa_fleet_returns_trust_and_queue(self):
        s = AxiomMCPServer()
        out = _call(s, "axiom_cmaa_fleet", {})
        assert "trust_levels" in out
        assert "axiom-orchestrator" in out["trust_levels"]
        assert isinstance(out["suspended"], list)
        assert isinstance(out["review_queue"], int)
        assert len(out["hmac_signature"]) == 64


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_invariant_tools_list_now_exposes_eight_tools(self):
        s = AxiomMCPServer()
        resp = json.loads(s.handle_request(_req("tools/list")))
        names = {t["name"] for t in resp["result"]["tools"]}
        assert {
            "axiom_guard_check", "axiom_lint", "axiom_trace",
            "axiom_qrf", "axiom_status",
            "axiom_intent_gate_check", "axiom_cmaa_route", "axiom_cmaa_fleet",
        }.issubset(names)
        assert len(names) >= 8

    def test_invariant_every_new_tool_result_signed(self):
        s = AxiomMCPServer()
        calls = [
            ("axiom_intent_gate_check", {"text": "hello"}),
            ("axiom_cmaa_route", {
                "packet_id": "i1", "source": "axiom-medical",
                "destination": "axiom-orchestrator",
                "payload": {"text": "hello"},
            }),
            ("axiom_cmaa_fleet", {}),
        ]
        for name, args in calls:
            out = _call(s, name, args)
            assert "hmac_signature" in out, f"missing signature in {name}"
