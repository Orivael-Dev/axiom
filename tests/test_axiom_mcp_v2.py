"""Tests for axiom_mcp_v2 — axiom-mcp-v2 signed envelope transport.

Covers:
  - CANNOT_MUTATE sentinels
  - McpV2Scope flag composition and header serialisation
  - EnvelopeSigner round-trip (build_headers → parse_and_verify)
  - Replay detection (nonce reuse)
  - Timestamp window rejection (expired / future)
  - Body tampering detection (HMAC mismatch)
  - Missing / malformed header detection
  - Response signing + verification
  - ScopeGate per-tool enforcement
  - tool_scope() helper
  - _NonceCache TTL eviction
"""
from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# Ensure AXIOM_MASTER_KEY is set before importing the module
os.environ.setdefault("AXIOM_MASTER_KEY", "a" * 64)

from axiom_mcp_v2 import (
    MCP_V2_VERSION,
    NONCE_TTL_S,
    TIMESTAMP_WINDOW_S,
    EnvelopeError,
    EnvelopeSigner,
    McpV2Envelope,
    McpV2Scope,
    ScopeError,
    ScopeGate,
    _NonceCache,
    tool_scope,
)


# ── CANNOT_MUTATE ─────────────────────────────────────────────────────────────

class TestCannotMutate(unittest.TestCase):

    def test_version_immutable(self):
        import axiom_mcp_v2 as m
        with self.assertRaises(AttributeError):
            m.MCP_V2_VERSION = "99"

    def test_timestamp_window_immutable(self):
        import axiom_mcp_v2 as m
        with self.assertRaises(AttributeError):
            m.TIMESTAMP_WINDOW_S = 0

    def test_nonce_ttl_immutable(self):
        import axiom_mcp_v2 as m
        with self.assertRaises(AttributeError):
            m.NONCE_TTL_S = 0

    def test_sentinel_values(self):
        self.assertEqual(MCP_V2_VERSION, "2")
        self.assertEqual(TIMESTAMP_WINDOW_S, 300)
        self.assertGreaterEqual(NONCE_TTL_S, TIMESTAMP_WINDOW_S)


# ── McpV2Scope ────────────────────────────────────────────────────────────────

class TestMcpV2Scope(unittest.TestCase):

    def test_read_flag_value(self):
        self.assertEqual(int(McpV2Scope.READ), 1)

    def test_write_implies_read(self):
        self.assertTrue(McpV2Scope.WRITE & McpV2Scope.READ)

    def test_execute_implies_write_and_read(self):
        self.assertTrue(McpV2Scope.EXECUTE & McpV2Scope.WRITE)
        self.assertTrue(McpV2Scope.EXECUTE & McpV2Scope.READ)

    def test_from_header_single_read(self):
        s = McpV2Scope.from_header("tool:read")
        self.assertEqual(s, McpV2Scope.READ)

    def test_from_header_write(self):
        s = McpV2Scope.from_header("tool:write")
        self.assertIn(McpV2Scope.READ, s)

    def test_from_header_execute(self):
        s = McpV2Scope.from_header("tool:execute")
        self.assertIn(McpV2Scope.WRITE, s)
        self.assertIn(McpV2Scope.READ, s)

    def test_from_header_combined(self):
        s = McpV2Scope.from_header("tool:read tool:write")
        self.assertIn(McpV2Scope.READ, s)

    def test_from_header_unknown_token_ignored(self):
        s = McpV2Scope.from_header("tool:unknown")
        self.assertEqual(s, McpV2Scope.NONE)

    def test_from_header_case_insensitive(self):
        s = McpV2Scope.from_header("Tool:Read Tool:Write")
        self.assertIn(McpV2Scope.READ, s)

    def test_to_header_read(self):
        hdr = McpV2Scope.READ.to_header()
        self.assertIn("tool:read", hdr)

    def test_to_header_execute_contains_all(self):
        hdr = McpV2Scope.EXECUTE.to_header()
        self.assertIn("tool:read", hdr)
        self.assertIn("tool:write", hdr)
        self.assertIn("tool:execute", hdr)

    def test_to_header_none_defaults_to_read(self):
        hdr = McpV2Scope.NONE.to_header()
        self.assertEqual(hdr, "tool:read")

    def test_roundtrip(self):
        for scope in (McpV2Scope.READ, McpV2Scope.WRITE, McpV2Scope.EXECUTE):
            self.assertEqual(McpV2Scope.from_header(scope.to_header()), scope)


# ── tool_scope() ──────────────────────────────────────────────────────────────

class TestToolScope(unittest.TestCase):

    def test_read_tool(self):
        self.assertEqual(tool_scope("axiom_guard_check"), McpV2Scope.READ)
        self.assertEqual(tool_scope("axiom_lint"), McpV2Scope.READ)
        self.assertEqual(tool_scope("axiom_trace"), McpV2Scope.READ)
        self.assertEqual(tool_scope("axiom_qrf"), McpV2Scope.READ)
        self.assertEqual(tool_scope("axiom_status"), McpV2Scope.READ)

    def test_write_tool(self):
        self.assertEqual(tool_scope("axiom_memory"), McpV2Scope.WRITE)
        self.assertEqual(tool_scope("axiom_ledger"), McpV2Scope.WRITE)
        self.assertEqual(tool_scope("axiom_mkb"), McpV2Scope.WRITE)

    def test_execute_tool(self):
        self.assertEqual(tool_scope("axiom_cmaa_route"), McpV2Scope.EXECUTE)
        self.assertEqual(tool_scope("axiom_shield"), McpV2Scope.EXECUTE)
        self.assertEqual(tool_scope("axiom_phone_gate"), McpV2Scope.EXECUTE)

    def test_unknown_tool_defaults_to_read(self):
        self.assertEqual(tool_scope("axiom_nonexistent_tool"), McpV2Scope.READ)


# ── EnvelopeSigner ────────────────────────────────────────────────────────────

def _make_signer() -> tuple[EnvelopeSigner, _NonceCache]:
    """Return a signer with a fresh nonce cache (no cross-test contamination)."""
    cache = _NonceCache(ttl_s=NONCE_TTL_S)
    signer = EnvelopeSigner(nonce_cache=cache)
    return signer, cache


class TestEnvelopeSigner(unittest.TestCase):

    def _roundtrip(
        self,
        method: str = "POST",
        body: bytes = b'{"jsonrpc":"2.0"}',
        model_id: str = "claude-opus-4-8",
        tenant_id: str = "tenant-abc",
        scope: McpV2Scope = McpV2Scope.READ,
    ) -> McpV2Envelope:
        signer, _ = _make_signer()
        hdrs = signer.build_headers(method, body, model_id, tenant_id, scope)
        return signer.parse_and_verify(method, body, hdrs)

    def test_roundtrip_read(self):
        env = self._roundtrip()
        self.assertEqual(env.model_id, "claude-opus-4-8")
        self.assertEqual(env.tenant_id, "tenant-abc")
        self.assertIn(McpV2Scope.READ, env.scope)

    def test_roundtrip_execute(self):
        env = self._roundtrip(scope=McpV2Scope.EXECUTE)
        self.assertTrue(env.granted(McpV2Scope.EXECUTE))

    def test_granted_sufficient(self):
        env = self._roundtrip(scope=McpV2Scope.WRITE)
        self.assertTrue(env.granted(McpV2Scope.READ))
        self.assertTrue(env.granted(McpV2Scope.WRITE))
        self.assertFalse(env.granted(McpV2Scope.EXECUTE))

    def test_has_v2_headers_true(self):
        signer, _ = _make_signer()
        hdrs = signer.build_headers("POST", b"body", "m", "t")
        self.assertTrue(signer.has_v2_headers(hdrs))

    def test_has_v2_headers_false(self):
        signer, _ = _make_signer()
        self.assertFalse(signer.has_v2_headers({"Authorization": "Bearer tok"}))

    def test_response_sign_verify_roundtrip(self):
        signer, _ = _make_signer()
        body = b'{"result":"ok"}'
        sig = signer.sign_response(body)
        self.assertTrue(signer.verify_response(body, sig))

    def test_response_sig_rejects_tampered_body(self):
        signer, _ = _make_signer()
        sig = signer.sign_response(b"original")
        self.assertFalse(signer.verify_response(b"tampered", sig))


# ── Replay detection ──────────────────────────────────────────────────────────

class TestReplayDetection(unittest.TestCase):

    def test_nonce_reuse_rejected(self):
        signer, cache = _make_signer()
        body = b'{"jsonrpc":"2.0"}'
        hdrs = signer.build_headers("POST", body, "m", "t")
        # First call succeeds
        signer.parse_and_verify("POST", body, hdrs)
        # Second call with same nonce fails
        with self.assertRaises(EnvelopeError) as ctx:
            signer.parse_and_verify("POST", body, hdrs)
        self.assertIn("Replayed", str(ctx.exception))

    def test_different_nonces_both_accepted(self):
        signer, _ = _make_signer()
        body = b'{"jsonrpc":"2.0"}'
        hdrs1 = signer.build_headers("POST", body, "m", "t")
        hdrs2 = signer.build_headers("POST", body, "m", "t")
        self.assertNotEqual(hdrs1["X-Axiom-Nonce"], hdrs2["X-Axiom-Nonce"])
        signer.parse_and_verify("POST", body, hdrs1)
        signer.parse_and_verify("POST", body, hdrs2)   # must not raise


# ── Timestamp window ──────────────────────────────────────────────────────────

class TestTimestampWindow(unittest.TestCase):

    def _build_with_ts(self, delta_s: int) -> tuple[EnvelopeSigner, dict, bytes]:
        signer, _ = _make_signer()
        body = b'{"jsonrpc":"2.0"}'
        hdrs = signer.build_headers("POST", body, "model", "tenant")
        # Override timestamp + re-sign
        ts = (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat(timespec="seconds")
        hdrs["X-Axiom-Timestamp"] = ts
        # Re-compute sig so it matches the new timestamp
        sig = signer._sign(
            "POST", body,
            hdrs["X-Axiom-Nonce"], ts,
            hdrs["X-Axiom-Model-Id"], hdrs["X-Axiom-Tenant-Id"],
            McpV2Scope.from_header(hdrs["X-Axiom-Scope"]),
        )
        hdrs["X-Axiom-Sig"] = sig
        return signer, hdrs, body

    def test_fresh_request_accepted(self):
        signer, hdrs, body = self._build_with_ts(0)
        env = signer.parse_and_verify("POST", body, hdrs)
        self.assertIsInstance(env, McpV2Envelope)

    def test_expired_request_rejected(self):
        signer, hdrs, body = self._build_with_ts(-400)
        with self.assertRaises(EnvelopeError) as ctx:
            signer.parse_and_verify("POST", body, hdrs)
        self.assertIn("window", str(ctx.exception))

    def test_future_request_rejected(self):
        signer, hdrs, body = self._build_with_ts(400)
        with self.assertRaises(EnvelopeError) as ctx:
            signer.parse_and_verify("POST", body, hdrs)
        self.assertIn("window", str(ctx.exception))

    def test_invalid_timestamp_format_rejected(self):
        signer, _ = _make_signer()
        body = b'body'
        hdrs = signer.build_headers("POST", body, "m", "t")
        hdrs["X-Axiom-Timestamp"] = "not-a-date"
        hdrs["X-Axiom-Sig"] = signer._sign(
            "POST", body, hdrs["X-Axiom-Nonce"], "not-a-date",
            hdrs["X-Axiom-Model-Id"], hdrs["X-Axiom-Tenant-Id"],
            McpV2Scope.from_header(hdrs["X-Axiom-Scope"]),
        )
        with self.assertRaises(EnvelopeError) as ctx:
            signer.parse_and_verify("POST", body, hdrs)
        self.assertIn("timestamp", str(ctx.exception).lower())


# ── Body / header tampering ───────────────────────────────────────────────────

class TestTampering(unittest.TestCase):

    def _build(self) -> tuple[EnvelopeSigner, dict, bytes]:
        signer, _ = _make_signer()
        body = b'{"jsonrpc":"2.0","method":"tools/call"}'
        hdrs = signer.build_headers("POST", body, "model", "tenant")
        return signer, hdrs, body

    def test_tampered_body_rejected(self):
        signer, hdrs, _ = self._build()
        with self.assertRaises(EnvelopeError) as ctx:
            signer.parse_and_verify("POST", b"tampered", hdrs)
        self.assertIn("tampered", str(ctx.exception))

    def test_tampered_model_id_rejected(self):
        signer, hdrs, body = self._build()
        hdrs["X-Axiom-Model-Id"] = "evil-model"
        with self.assertRaises(EnvelopeError):
            signer.parse_and_verify("POST", body, hdrs)

    def test_tampered_tenant_id_rejected(self):
        signer, hdrs, body = self._build()
        hdrs["X-Axiom-Tenant-Id"] = "evil-tenant"
        with self.assertRaises(EnvelopeError):
            signer.parse_and_verify("POST", body, hdrs)

    def test_tampered_scope_rejected(self):
        signer, hdrs, body = self._build()
        hdrs["X-Axiom-Scope"] = "tool:execute"
        with self.assertRaises(EnvelopeError):
            signer.parse_and_verify("POST", body, hdrs)

    def test_truncated_sig_rejected(self):
        signer, hdrs, body = self._build()
        hdrs["X-Axiom-Sig"] = hdrs["X-Axiom-Sig"][:10]
        with self.assertRaises(EnvelopeError):
            signer.parse_and_verify("POST", body, hdrs)


# ── Missing / malformed headers ───────────────────────────────────────────────

class TestMissingHeaders(unittest.TestCase):

    def _base(self) -> tuple[EnvelopeSigner, dict, bytes]:
        signer, _ = _make_signer()
        body = b'body'
        hdrs = signer.build_headers("POST", body, "model", "tenant")
        return signer, hdrs, body

    def test_missing_sig(self):
        signer, hdrs, body = self._base()
        del hdrs["X-Axiom-Sig"]
        with self.assertRaises(EnvelopeError) as ctx:
            signer.parse_and_verify("POST", body, hdrs)
        self.assertIn("Missing", str(ctx.exception))

    def test_missing_model_id(self):
        signer, hdrs, body = self._base()
        del hdrs["X-Axiom-Model-Id"]
        with self.assertRaises(EnvelopeError):
            signer.parse_and_verify("POST", body, hdrs)

    def test_missing_nonce(self):
        signer, hdrs, body = self._base()
        del hdrs["X-Axiom-Nonce"]
        with self.assertRaises(EnvelopeError):
            signer.parse_and_verify("POST", body, hdrs)

    def test_short_nonce_rejected(self):
        signer, hdrs, body = self._base()
        hdrs["X-Axiom-Nonce"] = "abc"  # < 8 chars
        hdrs["X-Axiom-Sig"] = signer._sign(
            "POST", body, "abc",
            hdrs["X-Axiom-Timestamp"],
            hdrs["X-Axiom-Model-Id"],
            hdrs["X-Axiom-Tenant-Id"],
            McpV2Scope.from_header(hdrs["X-Axiom-Scope"]),
        )
        with self.assertRaises(EnvelopeError) as ctx:
            signer.parse_and_verify("POST", body, hdrs)
        self.assertIn("Nonce", str(ctx.exception))


# ── ScopeGate ─────────────────────────────────────────────────────────────────

class TestScopeGate(unittest.TestCase):

    def _envelope(self, scope: McpV2Scope) -> McpV2Envelope:
        return McpV2Envelope(
            model_id="m", tenant_id="t", scope=scope,
            nonce="n" * 32, timestamp="2026-01-01T00:00:00+00:00", sig="s",
        )

    def test_read_scope_allows_read_tool(self):
        gate = ScopeGate()
        env = self._envelope(McpV2Scope.READ)
        gate.check(env, "axiom_lint")  # must not raise

    def test_read_scope_denies_write_tool(self):
        gate = ScopeGate()
        env = self._envelope(McpV2Scope.READ)
        with self.assertRaises(ScopeError) as ctx:
            gate.check(env, "axiom_memory")
        self.assertIn("requires scope", str(ctx.exception))

    def test_write_scope_allows_write_tool(self):
        gate = ScopeGate()
        env = self._envelope(McpV2Scope.WRITE)
        gate.check(env, "axiom_ledger")  # must not raise

    def test_write_scope_denies_execute_tool(self):
        gate = ScopeGate()
        env = self._envelope(McpV2Scope.WRITE)
        with self.assertRaises(ScopeError):
            gate.check(env, "axiom_shield")

    def test_execute_scope_allows_all_tools(self):
        gate = ScopeGate()
        env = self._envelope(McpV2Scope.EXECUTE)
        for tool in ("axiom_lint", "axiom_memory", "axiom_shield"):
            gate.check(env, tool)  # must not raise

    def test_error_message_names_tool_and_required_scope(self):
        gate = ScopeGate()
        env = self._envelope(McpV2Scope.READ)
        try:
            gate.check(env, "axiom_axm")
        except ScopeError as exc:
            self.assertIn("axiom_axm", str(exc))
            self.assertIn("tool:execute", str(exc))
        else:
            self.fail("Expected ScopeError")


# ── _NonceCache ────────────────────────────────────────────────────────────────

class TestNonceCache(unittest.TestCase):

    def test_new_nonce_not_seen(self):
        cache = _NonceCache(ttl_s=60)
        self.assertFalse(cache.seen("nonce-a"))

    def test_seen_nonce_returns_true(self):
        cache = _NonceCache(ttl_s=60)
        cache.seen("nonce-b")
        self.assertTrue(cache.seen("nonce-b"))

    def test_cache_length_increments(self):
        cache = _NonceCache(ttl_s=60)
        self.assertEqual(len(cache), 0)
        cache.seen("n1")
        cache.seen("n2")
        self.assertEqual(len(cache), 2)

    def test_ttl_eviction(self):
        cache = _NonceCache(ttl_s=1)
        cache.seen("evict-me")
        self.assertTrue(cache.seen("evict-me"))  # still there
        time.sleep(1.1)
        # Trigger eviction by calling seen() with a new nonce
        cache.seen("new-nonce")
        self.assertFalse(cache.seen("evict-me"))  # evicted


# ── McpV2Envelope.granted() ───────────────────────────────────────────────────

class TestEnvelopeGranted(unittest.TestCase):

    def _env(self, scope: McpV2Scope) -> McpV2Envelope:
        return McpV2Envelope("m", "t", scope, "n" * 32, "2026-01-01T00:00:00+00:00", "s")

    def test_read_grants_read(self):
        self.assertTrue(self._env(McpV2Scope.READ).granted(McpV2Scope.READ))

    def test_read_does_not_grant_write(self):
        self.assertFalse(self._env(McpV2Scope.READ).granted(McpV2Scope.WRITE))

    def test_execute_grants_read(self):
        self.assertTrue(self._env(McpV2Scope.EXECUTE).granted(McpV2Scope.READ))

    def test_none_grants_nothing(self):
        self.assertFalse(self._env(McpV2Scope.NONE).granted(McpV2Scope.READ))


if __name__ == "__main__":
    unittest.main()
