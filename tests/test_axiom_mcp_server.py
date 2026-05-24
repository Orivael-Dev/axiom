# -*- coding: utf-8 -*-
"""
AXIOM MCP Server Tests — JSON-RPC 2.0 over stdio
=================================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

BLOCKED: VERSION immutable, unknown tool returns error, invalid JSON returns parse error
PASSED:  tools/list returns 5 tools, guard_check returns verdict+sig, lint returns health_score
INVARIANTS: every response valid JSON-RPC, every tool result has hmac_signature, errors have code+message

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_mcp_server"


def _req(method, params=None, rid=1):
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})


def _parse(resp_str):
    return json.loads(resp_str)


# ===========================================================================
# SECTION 1 — BLOCKED
# ===========================================================================

class TestBlocked:

    def test_blocked_version_cannot_mutate(self):
        """BLOCKED: VERSION reassignment must raise AttributeError."""
        import axiom_mcp_server as m
        assert m.VERSION == "1.8.8"
        with pytest.raises(AttributeError):
            m.VERSION = "0.0.0"

    def test_blocked_unknown_tool_returns_error(self):
        """BLOCKED: tools/call with unknown tool returns JSON-RPC error, not crash."""
        from axiom_mcp_server import AxiomMCPServer
        server = AxiomMCPServer()
        resp = server.handle_request(_req("tools/call",
            {"name": "nonexistent_tool", "arguments": {}}))
        parsed = _parse(resp)
        assert "error" in parsed
        assert parsed["error"]["code"] == -32000
        assert "Unknown tool" in parsed["error"]["message"]

    def test_blocked_invalid_json_returns_parse_error(self):
        """BLOCKED: Invalid JSON returns JSON-RPC parse error code -32700."""
        from axiom_mcp_server import AxiomMCPServer
        server = AxiomMCPServer()
        resp = server.handle_request("this is not json {{{")
        parsed = _parse(resp)
        assert "error" in parsed
        assert parsed["error"]["code"] == -32700
        assert "Parse error" in parsed["error"]["message"]


# ===========================================================================
# SECTION 2 — PASSED
# ===========================================================================

class TestPassed:

    def test_passed_tools_list_returns_core_five(self):
        """PASSED: tools/list exposes the original 5 core tools (others may
        be added by ORVL-016/017 wiring)."""
        from axiom_mcp_server import AxiomMCPServer
        server = AxiomMCPServer()
        resp = server.handle_request(_req("tools/list"))
        parsed = _parse(resp)
        tools = parsed["result"]["tools"]
        names = {t["name"] for t in tools}
        # Core five must always be present.
        assert {"axiom_guard_check", "axiom_lint", "axiom_trace",
                "axiom_qrf", "axiom_status"}.issubset(names)
        for t in tools:
            assert "inputSchema" in t
            assert "description" in t

    def test_passed_guard_check_returns_verdict_and_signature(self):
        """PASSED: axiom_guard_check returns verdict and hmac_signature."""
        from axiom_mcp_server import AxiomMCPServer
        server = AxiomMCPServer()
        resp = server.handle_request(_req("tools/call",
            {"name": "axiom_guard_check", "arguments": {"input": "The weather is nice today"}}))
        parsed = _parse(resp)
        content = parsed["result"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        result = json.loads(content[0]["text"])
        assert result["verdict"] in ("PASSED", "BLOCKED")
        assert "hmac_signature" in result
        assert len(result["hmac_signature"]) == 64

    def test_passed_lint_returns_health_score(self):
        """PASSED: axiom_lint returns health_score from spec content."""
        from axiom_mcp_server import AxiomMCPServer
        server = AxiomMCPServer()
        spec = "AGENT TestAgent\nVERSION 1.0\nPURPOSE test\nGOAL test\n"
        resp = server.handle_request(_req("tools/call",
            {"name": "axiom_lint", "arguments": {"spec_content": spec}}))
        parsed = _parse(resp)
        result = json.loads(parsed["result"]["content"][0]["text"])
        assert "health_score" in result
        assert isinstance(result["health_score"], float)
        assert "hmac_signature" in result


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_every_response_valid_jsonrpc(self):
        """Every response must be valid JSON-RPC 2.0 with jsonrpc and id."""
        from axiom_mcp_server import AxiomMCPServer
        server = AxiomMCPServer()
        cases = [
            _req("initialize"),
            _req("tools/list"),
            _req("tools/call", {"name": "axiom_status", "arguments": {}}, rid=42),
            "broken json!!!",
            _req("unknown/method", rid=99),
        ]
        for case in cases:
            resp = server.handle_request(case)
            if not resp:
                continue
            parsed = _parse(resp)
            assert parsed["jsonrpc"] == "2.0"
            assert "id" in parsed

    def test_every_tool_result_has_hmac(self):
        """Every tool result must include hmac_signature."""
        from axiom_mcp_server import AxiomMCPServer
        server = AxiomMCPServer()
        calls = [
            {"name": "axiom_guard_check", "arguments": {"input": "hello"}},
            {"name": "axiom_lint", "arguments": {"spec_content": "AGENT T\nVERSION 1\nPURPOSE t\nGOAL t\n"}},
            {"name": "axiom_status", "arguments": {}},
        ]
        for call in calls:
            resp = server.handle_request(_req("tools/call", call))
            parsed = _parse(resp)
            result = json.loads(parsed["result"]["content"][0]["text"])
            assert "hmac_signature" in result, f"Missing hmac in {call['name']}"

    def test_error_responses_have_code_and_message(self):
        """Error responses must include error.code and error.message."""
        from axiom_mcp_server import AxiomMCPServer
        server = AxiomMCPServer()
        error_cases = [
            "not json",
            _req("tools/call", {"name": "fake_tool", "arguments": {}}),
            _req("nonexistent/method"),
        ]
        for case in error_cases:
            resp = server.handle_request(case)
            parsed = _parse(resp)
            assert "error" in parsed
            assert "code" in parsed["error"]
            assert "message" in parsed["error"]
            assert isinstance(parsed["error"]["code"], int)


class TestSelfHeal:
    """Regression: MCP client must not see error -32000 just because the
    spawning harness didn't inherit the user's AXIOM_MASTER_KEY env var.

    Before the self-heal patch, the server's `from axiom_signing import
    derive_key` line raised RuntimeError at import time when the env
    var was missing → JSON-RPC transport never came up → client surfaced
    error -32000 ("connection failed"). After the patch, the server
    generates an ephemeral key on stderr-warning and boots normally.
    """

    def test_server_boots_with_missing_master_key(self):
        import subprocess
        repo_root = Path(__file__).resolve().parents[1]
        env = {k: v for k, v in os.environ.items() if k != "AXIOM_MASTER_KEY"}
        proc = subprocess.Popen(
            [sys.executable, "axiom_mcp_server.py"],
            cwd=str(repo_root),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        init_msg = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "regtest", "version": "0"}},
        }) + "\n"
        try:
            out, err = proc.communicate(input=init_msg.encode("utf-8"),
                                          timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        finally:
            if proc.poll() is None:
                proc.kill()

        err_text = err.decode("utf-8", errors="replace")
        out_text = out.decode("utf-8", errors="replace")
        # Self-heal warning visible to the client (helps debugging).
        assert "AXIOM_MASTER_KEY missing" in err_text, \
            f"warning not emitted; stderr was: {err_text[:400]}"
        # The fatal import-time crash must NOT happen.
        assert "RuntimeError" not in err_text, \
            f"server still crashes at import; stderr was: {err_text[:400]}"
        # And the JSON-RPC initialize response actually came back.
        resp_line = next((line for line in out_text.splitlines() if line.strip()),
                         "")
        assert resp_line, f"no JSON-RPC response on stdout; got: {out_text[:200]}"
        parsed = json.loads(resp_line)
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["id"] == 1
        assert "result" in parsed
        assert parsed["result"]["serverInfo"]["name"] == "axiom"
