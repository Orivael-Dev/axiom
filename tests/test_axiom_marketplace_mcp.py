# -*- coding: utf-8 -*-
"""
AXIOM Marketplace Tests — signed-agent install + bonded authority.
==================================================================
Covers the Marketplace building block and the axiom_marketplace MCP tool
across the full Demo 3 / Demo 4 lifecycle:
  verify -> sandbox_install -> review -> approve -> (authorized) -> revoke
  -> (blocked, and cannot re-approve).
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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_marketplace"


def _signed_manifest(name="demo-agent"):
    from axiom_firewall.skill_pack import sign_first_party
    body = {
        "format_version": "1.0", "name": name, "title": "Demo Agent",
        "description": "A demo signed agent.", "version": "0.1.0",
        "author": "Orivael Dev", "license": "MIT",
        "tags": ["demo"], "tested_against": ["axiom-firewall>=0.1.0"],
        "policy": {"version": 1,
                   "additional_block_patterns": [{"class": "HARM", "regex": "rm\\s+-rf"}],
                   "disabled_default_classes": [], "allow_only_classes": None},
    }
    body["signature"] = sign_first_party(body)
    return body


# ── building block ───────────────────────────────────────────
class TestMarketplace:

    def test_full_lifecycle(self, tmp_path):
        from axiom_marketplace import Marketplace, MarketplaceError
        mkt = Marketplace(str(tmp_path / "mkt.jsonl"))
        man = _signed_manifest()

        assert mkt.verify(man)["valid"] is True

        inst = mkt.sandbox_install(man)
        assert inst["installed"] is True
        assert inst["authorized"] is False         # sandboxed, not yet authorized
        pair_id = inst["pair_id"]

        rev = mkt.review(man, pair_id)
        assert rev["authorized"] is False
        assert rev["requested_access"]["additional_block_patterns"] == 1

        assert mkt.approve(pair_id)["authorized"] is True     # Demo 3 approve
        assert mkt.authority(pair_id)["authorized"] is True
        assert mkt.authority(pair_id)["chain_verified"] is True

        assert mkt.revoke(pair_id)["authorized"] is False     # Demo 4 revoke
        assert mkt.authority(pair_id)["state"] == "REVOKED"

        # cannot re-authorize a revoked agent (terminal state)
        with pytest.raises(MarketplaceError):
            mkt.approve(pair_id)

    def test_tampered_manifest_rejected(self, tmp_path):
        from axiom_marketplace import Marketplace, MarketplaceError
        mkt = Marketplace(str(tmp_path / "mkt.jsonl"))
        man = _signed_manifest()
        man["author"] = "Attacker"   # invalidates signature
        assert mkt.verify(man)["valid"] is False
        with pytest.raises(MarketplaceError):
            mkt.sandbox_install(man)


# ── MCP tool ─────────────────────────────────────────────────
@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_MARKETPLACE_LEDGER", str(tmp_path / "mkt.jsonl"))
    import axiom_mcp_server as m
    m._marketplace_singleton = None
    yield m.AxiomMCPServer()
    m._marketplace_singleton = None


def _call(server, args):
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "axiom_marketplace", "arguments": args}})
    resp = json.loads(server.handle_request(req))
    assert "result" in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


class TestMarketplaceTool:

    def test_install_approve_revoke_over_mcp(self, server):
        man = _signed_manifest("mcp-agent")
        inst = _call(server, {"action": "sandbox_install", "manifest": man})
        assert inst["installed"] is True and inst["authorized"] is False
        pid = inst["pair_id"]

        gated = _call(server, {"action": "authority", "pair_id": pid})
        assert gated["authorized"] is False     # blocked before approval

        appr = _call(server, {"action": "approve", "pair_id": pid, "actor": "alice"})
        assert appr["authorized"] is True
        assert _call(server, {"action": "authority", "pair_id": pid})["authorized"] is True

        rev = _call(server, {"action": "revoke", "pair_id": pid, "actor": "alice"})
        assert rev["authorized"] is False
        assert _call(server, {"action": "authority", "pair_id": pid})["state"] == "REVOKED"

    def test_in_tools_list_and_signed(self, server):
        resp = json.loads(server.handle_request(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})))
        assert "axiom_marketplace" in {t["name"] for t in resp["result"]["tools"]}
        out = _call(server, {"action": "verify", "manifest": _signed_manifest()})
        assert out["valid"] is True and out["hmac_signature"]

    def test_error_paths_signed(self, server):
        bad = _call(server, {"action": "explode"})
        assert "error" in bad and bad["hmac_signature"]
        missing = _call(server, {"action": "approve"})   # no pair_id
        assert "error" in missing
