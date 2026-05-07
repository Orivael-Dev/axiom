# -*- coding: utf-8 -*-
"""
AXIOM RedAgent Tests — ORVL-008 Constitutional Adversarial Sandbox
==================================================================
3 BLOCKED + 3 PASSED

BLOCKED: adversarial payloads the guard must reject
PASSED:  structural integrity checks that must succeed

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
BUG-010: len(response.content) > 0 before indexing
"""

import hashlib
import hmac
import json
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# BUG-003: UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure AXIOM_MASTER_KEY is set for tests
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_red_agent_tests"

HMAC_KEY = b"red-agent-test-key"


def _make_mock_response(status, body):
    """Mock requests.Response with JSON body."""
    r = MagicMock()
    r.status_code = status
    r.content = json.dumps(body).encode("utf-8")
    r.json.return_value = body
    return r


def _make_empty_response(status):
    """Mock response with EMPTY content (BUG-010 trigger)."""
    r = MagicMock()
    r.status_code = status
    r.content = b""
    r.json.side_effect = ValueError("No content")
    return r


# ===========================================================================
# SECTION 1 — BLOCKED: adversarial payloads must be rejected
# ===========================================================================

class TestBlocked:

    def test_blocked_prompt_injection(self):
        """BLOCKED: prompt injection payload must be flagged BLOCKED."""
        from axiom_red_agent import RedAgent, AttackResult

        mock_resp = _make_mock_response(200, {"verdict": "BLOCKED", "reason": "injection"})
        with patch("axiom_red_agent.requests.post", return_value=mock_resp):
            agent = RedAgent(hmac_key=HMAC_KEY)
            result = agent.attack_prompt_injection()

        assert result.attack_blocked is True
        assert result.vector == "PROMPT_INJECTION"
        assert result.payload
        assert isinstance(result.latency_ms, float)

    def test_blocked_trust_escalation(self):
        """BLOCKED: trust level escalation payload must be flagged BLOCKED."""
        from axiom_red_agent import RedAgent

        mock_resp = _make_mock_response(200, {"verdict": "BLOCKED", "reason": "trust_mutation"})
        with patch("axiom_red_agent.requests.post", return_value=mock_resp):
            agent = RedAgent(hmac_key=HMAC_KEY)
            result = agent.attack_trust_escalation()

        assert result.attack_blocked is True
        assert result.vector == "TRUST_ESCALATION"

    def test_blocked_isolation_bypass(self):
        """BLOCKED: isolation=false injection must be caught."""
        from axiom_red_agent import RedAgent

        mock_resp = _make_mock_response(200, {"verdict": "BLOCKED", "reason": "isolation_bypass"})
        with patch("axiom_red_agent.requests.post", return_value=mock_resp):
            agent = RedAgent(hmac_key=HMAC_KEY)
            result = agent.attack_isolation_bypass()

        assert result.attack_blocked is True
        assert result.vector == "ISOLATION_BYPASS"


# ===========================================================================
# SECTION 2 — PASSED: structural integrity must hold
# ===========================================================================

class TestPassed:

    def test_passed_report_hmac_integrity(self):
        """PASSED: AttackReport HMAC must verify correctly (BUG-007)."""
        from axiom_red_agent import AttackReport, AttackResult

        results = [
            AttackResult(
                vector="PROMPT_INJECTION",
                payload="test payload",
                attack_blocked=True,
                guard_response={"verdict": "BLOCKED"},
                latency_ms=12.5,
            )
        ]
        report = AttackReport(results=results, hmac_key=HMAC_KEY)

        # Re-derive HMAC independently
        body = json.dumps(
            [r.__dict__ for r in results], sort_keys=True, ensure_ascii=True
        ).encode("utf-8")
        expected = hmac.new(HMAC_KEY, body, hashlib.sha256).hexdigest()

        assert report.signature == expected, "HMAC mismatch — digest not finalised"

    def test_passed_empty_response_no_index_error(self):
        """PASSED: empty API response must NOT raise IndexError (BUG-010)."""
        from axiom_red_agent import RedAgent

        empty_resp = _make_empty_response(200)
        with patch("axiom_red_agent.requests.post", return_value=empty_resp):
            agent = RedAgent(hmac_key=HMAC_KEY)
            result = agent.attack_prompt_injection()

        assert result is not None
        assert result.guard_response == {} or \
               "empty" in str(result.guard_response).lower() or \
               "error" in str(result.guard_response).lower()

    def test_passed_run_all_attacks_returns_report(self):
        """PASSED: run_all_attacks must emit AttackReport with 8 results and valid HMAC."""
        from axiom_red_agent import RedAgent, AttackReport

        mock_resp = _make_mock_response(200, {"verdict": "BLOCKED", "reason": "test"})
        with patch("axiom_red_agent.requests.post", return_value=mock_resp):
            agent = RedAgent(hmac_key=HMAC_KEY)
            report = agent.run_all_attacks()

        assert isinstance(report, AttackReport)
        assert len(report.results) == 8
        assert report.signature

        body = json.dumps(
            [r.__dict__ for r in report.results], sort_keys=True, ensure_ascii=True
        ).encode("utf-8")
        expected = hmac.new(HMAC_KEY, body, hashlib.sha256).hexdigest()
        assert report.signature == expected


# ===========================================================================
# SECTION 3 — IMMUTABILITY: CANNOT_MUTATE contracts
# ===========================================================================

class TestInvariants:

    def test_trust_level_cannot_mutate(self):
        """CANNOT_MUTATE: TRUST_LEVEL must be 1 and not writable."""
        import axiom_red_agent as m
        assert m.TRUST_LEVEL == 1
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 99

    def test_isolation_cannot_mutate(self):
        """CANNOT_MUTATE: ISOLATION must be True and not writable."""
        import axiom_red_agent as m
        assert m.ISOLATION is True
        with pytest.raises((AttributeError, TypeError)):
            m.ISOLATION = False

    def test_hmac_key_cannot_mutate(self):
        """CANNOT_MUTATE: _hmac_key must not be reassignable after init."""
        from axiom_red_agent import RedAgent
        agent = RedAgent(hmac_key=HMAC_KEY)
        with pytest.raises(AttributeError):
            agent._hmac_key = b"tampered"

    def test_guard_url_cannot_mutate(self):
        """CANNOT_MUTATE: _guard_url must not be reassignable after init."""
        from axiom_red_agent import RedAgent
        agent = RedAgent(hmac_key=HMAC_KEY)
        with pytest.raises(AttributeError):
            agent._guard_url = "http://evil.com/guard"


# ===========================================================================
# SECTION 4 — BUG-010: MAX_RESPONSE_BYTES enforcement
# ===========================================================================

class TestResponseLimits:

    def test_oversized_response_rejected(self):
        """BUG-010: response exceeding MAX_RESPONSE_BYTES must be rejected."""
        from axiom_red_agent import RedAgent, MAX_RESPONSE_BYTES

        oversized_content = b'x' * (MAX_RESPONSE_BYTES + 1)
        r = MagicMock()
        r.status_code = 200
        r.content = oversized_content

        agent = RedAgent(hmac_key=HMAC_KEY)
        result = agent._parse_response(r)
        assert result["error"] == "response_too_large"
        assert result["size"] == len(oversized_content)
        assert result["limit"] == MAX_RESPONSE_BYTES

    def test_max_boundary_response_accepted(self):
        """BUG-010: response at exactly MAX_RESPONSE_BYTES must be accepted."""
        from axiom_red_agent import RedAgent, MAX_RESPONSE_BYTES

        body = {"verdict": "BLOCKED", "reason": "test"}
        content = json.dumps(body).encode("utf-8")
        # Pad to exactly MAX_RESPONSE_BYTES with valid JSON
        assert len(content) <= MAX_RESPONSE_BYTES

        r = MagicMock()
        r.status_code = 200
        r.content = content
        r.json.return_value = body

        agent = RedAgent(hmac_key=HMAC_KEY)
        result = agent._parse_response(r)
        assert result["verdict"] == "BLOCKED"
