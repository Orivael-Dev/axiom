# -*- coding: utf-8 -*-
"""
AXIOM CAS Orchestrator Tests — ORVL-008 Component 3
====================================================
3 BLOCKED + 3 PASSED

BLOCKED: invariants the orchestrator must enforce
PASSED:  functional and structural checks that must succeed

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import hashlib
import hmac
import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# BUG-003: UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_cas_tests"

HMAC_KEY = b"cas-orchestrator-test-key"


def _make_blocked_response():
    """Mock requests.Response returning BLOCKED verdict."""
    r = MagicMock()
    r.status_code = 200
    body = {"verdict": "BLOCKED", "reason": "guard_triggered"}
    r.content = json.dumps(body).encode("utf-8")
    r.json.return_value = body
    return r


def _make_passed_response():
    """Mock requests.Response returning PASSED verdict (attack bypassed)."""
    r = MagicMock()
    r.status_code = 200
    body = {"verdict": "PASSED", "reason": "no_violation"}
    r.content = json.dumps(body).encode("utf-8")
    r.json.return_value = body
    return r


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the orchestrator must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_dbscan_eps_cannot_mutate(self):
        """BLOCKED: DBSCAN_EPS must not be modifiable."""
        import axiom_cas_orchestrator as m
        assert m.DBSCAN_EPS == 0.15
        with pytest.raises((AttributeError, TypeError)):
            m.DBSCAN_EPS = 0.5

    def test_blocked_sovereign_threshold_cannot_mutate(self):
        """BLOCKED: SOVEREIGN_CONSECUTIVE_THRESHOLD must not be modifiable."""
        import axiom_cas_orchestrator as m
        assert m.SOVEREIGN_CONSECUTIVE_THRESHOLD == 2
        with pytest.raises((AttributeError, TypeError)):
            m.SOVEREIGN_CONSECUTIVE_THRESHOLD = 99

    def test_blocked_sovereign_alert_on_consecutive_red_wins(self):
        """BLOCKED: 2 consecutive red wins must trigger SovereignAlert."""
        from axiom_cas_orchestrator import CASOrchestrator
        from axiom_red_agent import RedAgent
        from axiom_blue_agent import BlueAgent

        red = RedAgent(hmac_key=HMAC_KEY)
        blue = BlueAgent(hmac_key=HMAC_KEY)
        orch = CASOrchestrator(hmac_key=HMAC_KEY, red_agent=red,
                               blue_agent=blue, log_path=None)

        # Two consecutive bypassed attacks should trigger sovereign alert
        passed_resp = _make_passed_response()
        with patch("axiom_red_agent.requests.post", return_value=passed_resp):
            report = orch.run_rounds(2)

        assert len(report.sovereign_alerts) >= 1
        assert any("consecutive" in a.reason.lower()
                    for a in report.sovereign_alerts)


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_single_blocked_round(self):
        """PASSED: single round with BLOCKED verdict emits 1 blue_win."""
        from axiom_cas_orchestrator import CASOrchestrator
        from axiom_red_agent import RedAgent
        from axiom_blue_agent import BlueAgent

        red = RedAgent(hmac_key=HMAC_KEY)
        blue = BlueAgent(hmac_key=HMAC_KEY)
        orch = CASOrchestrator(hmac_key=HMAC_KEY, red_agent=red,
                               blue_agent=blue, log_path=None)

        blocked_resp = _make_blocked_response()
        with patch("axiom_red_agent.requests.post", return_value=blocked_resp):
            report = orch.run_rounds(1)

        assert report.blue_wins == 1
        assert report.red_wins == 0
        assert len(report.rounds) == 1
        assert report.signature  # HMAC signed

    def test_passed_single_bypassed_round(self):
        """PASSED: single round with PASSED verdict emits 1 red_win."""
        from axiom_cas_orchestrator import CASOrchestrator
        from axiom_red_agent import RedAgent
        from axiom_blue_agent import BlueAgent

        red = RedAgent(hmac_key=HMAC_KEY)
        blue = BlueAgent(hmac_key=HMAC_KEY)
        orch = CASOrchestrator(hmac_key=HMAC_KEY, red_agent=red,
                               blue_agent=blue, log_path=None)

        passed_resp = _make_passed_response()
        with patch("axiom_red_agent.requests.post", return_value=passed_resp):
            report = orch.run_rounds(1)

        assert report.red_wins == 1
        assert report.blue_wins == 0
        assert len(report.rounds) == 1

    def test_passed_cas_report_hmac_integrity(self):
        """PASSED: CASReport HMAC must verify independently (BUG-007)."""
        from axiom_cas_orchestrator import CASOrchestrator, CASReport
        from axiom_red_agent import RedAgent
        from axiom_blue_agent import BlueAgent

        red = RedAgent(hmac_key=HMAC_KEY)
        blue = BlueAgent(hmac_key=HMAC_KEY)
        orch = CASOrchestrator(hmac_key=HMAC_KEY, red_agent=red,
                               blue_agent=blue, log_path=None)

        blocked_resp = _make_blocked_response()
        with patch("axiom_red_agent.requests.post", return_value=blocked_resp):
            report = orch.run_rounds(2)

        # Re-derive HMAC independently over round signatures
        body = json.dumps(
            [r.signature for r in report.rounds],
            sort_keys=True, ensure_ascii=True,
        ).encode("utf-8")
        expected = hmac.new(HMAC_KEY, body, hashlib.sha256).hexdigest()

        assert report.signature == expected, "CASReport HMAC mismatch"


# ===========================================================================
# SECTION 3 — IMMUTABILITY: CANNOT_MUTATE contracts
# ===========================================================================

class TestInvariants:

    def test_trust_level_cannot_mutate(self):
        """CANNOT_MUTATE: TRUST_LEVEL must be 4 and not writable."""
        import axiom_cas_orchestrator as m
        assert m.TRUST_LEVEL == 4
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 99

    def test_isolation_cannot_mutate(self):
        """CANNOT_MUTATE: ISOLATION must be True and not writable."""
        import axiom_cas_orchestrator as m
        assert m.ISOLATION is True
        with pytest.raises((AttributeError, TypeError)):
            m.ISOLATION = False
