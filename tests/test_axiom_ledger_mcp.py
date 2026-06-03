# -*- coding: utf-8 -*-
"""
AXIOM Audit Ledger Tests — building block + axiom_ledger MCP tool.
=================================================================
PASSED:     log appends a signed event; list returns it; query filters.
BLOCKED:    unknown action errors; log without event_type errors.
INVARIANTS: every result signed; verify detects a tampered row; the file
            is append-only (log does not rewrite prior rows).
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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_ledger"


# ── building block ───────────────────────────────────────────
class TestAuditLedger:

    def test_append_read_verify(self, tmp_path):
        from axiom_audit_ledger import AuditLedger
        led = AuditLedger(str(tmp_path / "audit.jsonl"))
        ev = led.append("workspace_opened", actor="aui", subject="goal: ship demo",
                        outcome="allowed", attributes={"recall_hit": True})
        assert ev.signature and ev.verify()
        rows = led.read()
        assert len(rows) == 1
        assert rows[0].event_type == "workspace_opened"
        assert rows[0].attributes == {"recall_hit": True}
        assert rows[0].verify()

    def test_query_filters_and_append_only(self, tmp_path):
        from axiom_audit_ledger import AuditLedger
        led = AuditLedger(str(tmp_path / "audit.jsonl"))
        led.append("a", outcome="x")
        led.append("b", outcome="y")
        led.append("a", outcome="z")
        assert len(led.read()) == 3  # append-only: all three retained
        only_a = led.query(event_type="a")
        assert len(only_a) == 2 and all(e.event_type == "a" for e in only_a)
        assert led.query(limit=1)[0].event_type == "a"  # last 1

    def test_tamper_breaks_verify(self, tmp_path):
        from axiom_audit_ledger import AuditLedger
        path = tmp_path / "audit.jsonl"
        led = AuditLedger(str(path))
        led.append("login", outcome="allowed")
        rec = json.loads(path.read_text().splitlines()[0])
        rec["outcome"] = "DENIED"  # flip without re-signing
        path.write_text(json.dumps(rec) + "\n")
        assert led.read()[0].verify() is False


# ── MCP tool ─────────────────────────────────────────────────
@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_AUDIT_LEDGER", str(tmp_path / "audit.jsonl"))
    import axiom_mcp_server as m
    m._ledger_singleton = None
    m._ledger_path = None
    yield m.AxiomMCPServer()
    m._ledger_singleton = None
    m._ledger_path = None


def _call(server, name, args):
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": name, "arguments": args}})
    resp = json.loads(server.handle_request(req))
    assert "result" in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


class TestLedgerTool:

    def test_log_then_list(self, server):
        out = _call(server, "axiom_ledger",
                    {"action": "log", "event_type": "branch_loaded",
                     "actor": "aui", "subject": "claude/feature",
                     "outcome": "allowed", "attributes": {"files": 12}})
        assert out["logged"] is True
        assert out["event_type"] == "branch_loaded"
        assert out["signature"]          # entry HMAC
        assert out["hmac_signature"]     # MCP envelope

        listed = _call(server, "axiom_ledger", {"action": "list"})
        assert listed["count"] == 1
        assert listed["all_verified"] is True
        assert listed["events"][0]["attributes"] == {"files": 12}

    def test_in_tools_list(self, server):
        resp = json.loads(server.handle_request(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})))
        assert "axiom_ledger" in {t["name"] for t in resp["result"]["tools"]}

    def test_verify_action(self, server):
        _call(server, "axiom_ledger", {"action": "log", "event_type": "e1"})
        _call(server, "axiom_ledger", {"action": "log", "event_type": "e2"})
        v = _call(server, "axiom_ledger", {"action": "verify"})
        assert v["count"] == 2 and v["all_verified"] is True
        assert v["tampered_indices"] == []

    def test_unknown_action_and_missing_type_error(self, server):
        bad = _call(server, "axiom_ledger", {"action": "delete"})
        assert "error" in bad and "hmac_signature" in bad
        no_type = _call(server, "axiom_ledger", {"action": "log"})
        assert "error" in no_type
