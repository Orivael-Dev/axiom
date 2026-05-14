# -*- coding: utf-8 -*-
"""
AXM (ORVL-023) — REST + MCP integration tests
==============================================
1 BLOCKED + 1 PASSED + 1 INVARIANT per surface.

BUG-003: UTF-8 output encoding
"""

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_axm_integration"

from fastapi.testclient import TestClient

import axiom_server
import axiom_mcp_server
from axiom_axm import AXMContainer
from examples.axm_pack_starter import STARTER_SPEC


@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear the path-keyed container caches between tests so they don't
    leak state across runs."""
    axiom_server._axm_cache.clear()
    axiom_mcp_server._axm_cache_mcp.clear()
    yield
    axiom_server._axm_cache.clear()
    axiom_mcp_server._axm_cache_mcp.clear()


@pytest.fixture()
def packed(tmp_path):
    """Pack a starter container and return its absolute path."""
    cpath = tmp_path / "starter.axm"
    AXMContainer.pack(STARTER_SPEC, str(cpath))
    return str(cpath)


@pytest.fixture()
def client():
    return TestClient(axiom_server.app)


# ===========================================================================
# REST surface
# ===========================================================================

class TestAXMRest:

    def test_blocked_route_on_missing_container(self, client):
        r = client.post("/axm/route", json={
            "container_path": "/nonexistent/path.axm",
            "task": "anything",
        })
        assert r.status_code == 400

    def test_passed_inspect_then_verify_then_route(self, client, packed):
        ins = client.post("/axm/inspect", json={"container_path": packed})
        assert ins.status_code == 200
        assert ins.json()["delegate_count"] == 3

        ver = client.post("/axm/verify", json={"container_path": packed})
        assert ver.status_code == 200
        assert ver.json()["verified"] is True

        rt = client.post("/axm/route", json={
            "container_path": packed,
            "task": "Explain monotonic gates briefly",
        })
        assert rt.status_code == 200
        body = rt.json()
        assert body["intent_class"] == "INFORM"
        assert "pii_redactor" in body["loaded_skills"]
        assert len(body["signature"]) == 64

    def test_invariant_repeated_routes_reuse_container(self, client, packed):
        for _ in range(3):
            r = client.post("/axm/route", json={
                "container_path": packed,
                "task": "explain transformers briefly",
            })
            assert r.status_code == 200
        # All requests hit the same cached container singleton.
        assert packed in axiom_server._axm_cache


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


class TestAXMMcp:

    def test_blocked_route_without_task_argument(self):
        s = axiom_mcp_server.AxiomMCPServer()
        out = _mcp_call(s, "axiom_axm",
                        {"action": "route", "container_path": "/tmp/nope"})
        assert "error" in out

    def test_passed_inspect_verify_route(self, packed):
        s = axiom_mcp_server.AxiomMCPServer()
        ins = _mcp_call(s, "axiom_axm",
                        {"action": "inspect", "container_path": packed})
        assert ins["delegate_count"] == 3

        ver = _mcp_call(s, "axiom_axm",
                        {"action": "verify", "container_path": packed})
        assert ver["verified"] is True

        rt = _mcp_call(s, "axiom_axm", {
            "action": "route", "container_path": packed,
            "task":   "Explain monotonic gates briefly",
        })
        assert rt["intent_class"] == "INFORM"
        assert "pii_redactor" in rt["loaded_skills"]

    def test_invariant_tools_list_exposes_axm(self):
        s = axiom_mcp_server.AxiomMCPServer()
        resp = json.loads(s.handle_request(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })))
        names = {t["name"] for t in resp["result"]["tools"]}
        assert "axiom_axm" in names
