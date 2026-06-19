# -*- coding: utf-8 -*-
"""
AXIOM Memory MCP Tool Tests — ORVL-015 over JSON-RPC 2.0
=========================================================
Covers the `axiom_memory` tool (remember / recall / stats) and the
building-block helpers it wraps (embed_text, load_store).

PASSED:     remember stores a signed packet; recall hits on matching text;
            stats reports the store.
BLOCKED:    unknown action errors; remember without text errors.
INVARIANTS: every result is signed; recall survives a process restart
            (LSH rebuilt from the store); tampered store rows are skipped;
            embed_text is deterministic.
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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_memory_mcp"


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Fresh MCP server with an isolated, empty memory store per test."""
    monkeypatch.setenv("AXIOM_MEMORY_STORE", str(tmp_path / "mem.jsonl"))
    import axiom_mcp_server as m
    # Reset the lazy singleton so it re-reads AXIOM_MEMORY_STORE.
    m._memory_singleton = None
    m._memory_store_path = None
    yield m.AxiomMCPServer()
    m._memory_singleton = None
    m._memory_store_path = None


def _call(server, name, args):
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": name, "arguments": args}})
    resp = json.loads(server.handle_request(req))
    assert "result" in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


# ===========================================================================
# PASSED
# ===========================================================================
class TestPassed:

    def test_remember_then_recall_hits(self, server):
        text = "Planning the AX OS launch demo with adaptive workspace and recall"
        out = _call(server, "axiom_memory",
                    {"action": "remember", "text": text, "domain": "general",
                     "resolution": "approved", "constraints": ["local_first"]})
        assert out["stored"] is True
        assert out["domain"] == "general"
        assert out["packet_signature"]

        hit = _call(server, "axiom_memory",
                    {"action": "recall", "query": text})
        assert hit["hit"] is True
        assert hit["domain"] == "general"
        assert hit["resolution"] == "approved"
        assert "local_first" in hit["active_constraints"]

    def test_recall_miss_on_unrelated_query(self, server):
        _call(server, "axiom_memory",
              {"action": "remember", "text": "quarterly tax filing notes",
               "domain": "financial"})
        miss = _call(server, "axiom_memory",
                     {"action": "recall",
                      "query": "humanoid robot torque clamp stability"})
        assert miss["hit"] is False

    def test_stats_reports_store(self, server):
        _call(server, "axiom_memory", {"action": "remember", "text": "one"})
        _call(server, "axiom_memory", {"action": "remember", "text": "two"})
        stats = _call(server, "axiom_memory", {"action": "stats"})
        assert stats["packet_count"] == 2
        assert stats["vector_dimensions"] == 32
        assert stats["similarity_threshold"] == 0.75


# ===========================================================================
# BLOCKED
# ===========================================================================
class TestBlocked:

    def test_unknown_action_errors(self, server):
        out = _call(server, "axiom_memory", {"action": "forget"})
        assert "error" in out
        assert "remember" in out["error"]

    def test_remember_without_text_errors(self, server):
        out = _call(server, "axiom_memory", {"action": "remember", "text": "  "})
        assert "error" in out


# ===========================================================================
# INVARIANTS
# ===========================================================================
class TestInvariants:

    def test_every_result_is_signed(self, server):
        for args in ({"action": "stats"},
                     {"action": "remember", "text": "signed payload check"},
                     {"action": "recall", "query": "signed payload check"},
                     {"action": "bogus"}):
            out = _call(server, "axiom_memory", args)
            assert "hmac_signature" in out and out["hmac_signature"]

    def test_recall_survives_restart(self, server, monkeypatch):
        """A new server process (singleton reset) rebuilds the LSH from the
        persisted store and still recalls — the core of local-first memory."""
        import axiom_mcp_server as m
        text = "remember this across a server restart please"
        _call(server, "axiom_memory", {"action": "remember", "text": text})

        # Simulate a fresh process: drop the in-memory engine + LSH.
        m._memory_singleton = None
        m._memory_store_path = None
        fresh = m.AxiomMCPServer()
        hit = _call(fresh, "axiom_memory", {"action": "recall", "query": text})
        assert hit["hit"] is True

    def test_tampered_store_row_is_skipped(self, server, monkeypatch, tmp_path):
        import axiom_mcp_server as m
        _call(server, "axiom_memory",
              {"action": "remember", "text": "authentic memory packet"})
        store = Path(os.environ["AXIOM_MEMORY_STORE"])
        rows = store.read_text(encoding="utf-8").splitlines()
        rec = json.loads(rows[0])
        rec["resolution"] = "TAMPERED — signature no longer matches"
        store.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        from axiom_memory_engine import LSHIndex, load_store, count_verified
        lsh = LSHIndex()
        assert load_store(str(store), lsh) == 0  # tampered row not indexed
        assert count_verified(str(store)) == 0  # ...and not counted

    def test_stats_excludes_tampered_rows(self, server, monkeypatch):
        """stats.packet_count reflects only authentic packets, matching what
        recall can serve — not raw lines (Codex review #61)."""
        import axiom_mcp_server as m
        _call(server, "axiom_memory",
              {"action": "remember", "text": "authentic packet for stats"})
        store = Path(os.environ["AXIOM_MEMORY_STORE"])
        rec = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
        rec["resolution"] = "TAMPERED"
        store.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        m._memory_singleton = None  # simulate restart
        m._memory_store_path = None
        fresh = m.AxiomMCPServer()
        stats = _call(fresh, "axiom_memory", {"action": "stats"})
        assert stats["packet_count"] == 0

    def test_embed_text_is_deterministic(self):
        from axiom_memory_engine import embed_text, VECTOR_DIMENSIONS
        a = embed_text("the same text yields the same vector")
        b = embed_text("the same text yields the same vector")
        assert a == b
        assert len(a) == VECTOR_DIMENSIONS
        assert embed_text("different text entirely") != a
