# -*- coding: utf-8 -*-
"""
AXIOM QRF Tests — ORVL-009 Quantum Reasoning Forecast
======================================================
3 BLOCKED + 3 PASSED

BLOCKED: invariants the QRF engine must enforce
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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_qrf_tests"

HMAC_KEY = b"qrf-test-key"


def _mock_latent_result(n_branches=4):
    """Build a fake LatentEngine.run() result with multiplex branches."""
    branches = []
    for i in range(n_branches):
        branches.append({
            "branch": f"branch_{i}",
            "response": f"Response for branch {i}",
            "score": round(0.3 + i * 0.15, 2),
            "metrics": {"clarity": 0.8, "safety": 0.9, "overall": round(0.3 + i * 0.15, 2)},
        })
    return {
        "phases": {
            "trace": {
                "intent_vector": ["analyze", "forecast"],
                "risk_clusters": [],
                "compressed_plan": ["step1", "step2"],
                "confidence": 0.75,
            },
            "multiplex": {
                "winner": branches[-1],
                "rival": branches[-2] if len(branches) > 1 else branches[0],
                "all_branches": branches,
            },
        },
        "manifold": {
            "distance_profile": [0.1, 0.15, 0.12],
            "min_distance": 0.1,
            "drift_detected": False,
            "drift_magnitude": 0.0,
            "direction": "stable",
            "flagged_stages": [],
        },
        "manifest": {"manifest_id": "test-123", "confidence": 0.75},
    }


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the QRF engine must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_unknown_domain_rejected(self):
        """BLOCKED: unknown domain must raise ValueError."""
        from axiom_qrf import QRFEngine
        with pytest.raises(ValueError, match="Unsupported domain"):
            QRFEngine(domain="astrology", hmac_key=HMAC_KEY)

    def test_blocked_domain_branch_counts_cannot_mutate(self):
        """BLOCKED: DOMAIN_BRANCH_COUNTS must not be modifiable."""
        import axiom_qrf as m
        with pytest.raises((AttributeError, TypeError)):
            m.DOMAIN_BRANCH_COUNTS = {"medical": 1}

    def test_blocked_trust_level_cannot_mutate(self):
        """BLOCKED: TRUST_LEVEL must be 2 and not writable."""
        import axiom_qrf as m
        assert m.TRUST_LEVEL == 2
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 99


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_forecast_produces_qrf_result(self):
        """PASSED: forecast must return QRFResult with sorted branches."""
        from axiom_qrf import QRFEngine, QRFResult

        engine = QRFEngine(domain="supply_chain", hmac_key=HMAC_KEY)
        mock_result = _mock_latent_result(n_branches=4)

        with patch.object(engine, "_engine") as mock_engine:
            mock_engine.run.return_value = mock_result
            result = engine.forecast("Will chip supply recover in Q3?")

        assert isinstance(result, QRFResult)
        assert result.domain == "supply_chain"
        assert len(result.branches) == 4
        # Sorted descending by probability weight
        weights = [b["probability_weight"] for b in result.branches]
        assert weights == sorted(weights, reverse=True)
        assert result.hmac_signature

    def test_passed_probability_weights_sum_to_one(self):
        """PASSED: probability weights must sum to 1.0."""
        from axiom_qrf import QRFEngine

        engine = QRFEngine(domain="financial", hmac_key=HMAC_KEY)
        mock_result = _mock_latent_result(n_branches=6)

        with patch.object(engine, "_engine") as mock_engine:
            mock_engine.run.return_value = mock_result
            result = engine.forecast("Market forecast for Q4?")

        total = sum(b["probability_weight"] for b in result.branches)
        assert abs(total - 1.0) < 1e-4, f"Weights sum to {total}, expected 1.0"

    def test_passed_qrf_result_hmac_integrity(self):
        """PASSED: QRFResult HMAC must verify independently (BUG-007)."""
        from axiom_qrf import QRFEngine

        engine = QRFEngine(domain="hr", hmac_key=HMAC_KEY)
        mock_result = _mock_latent_result(n_branches=4)

        with patch.object(engine, "_engine") as mock_engine:
            mock_engine.run.return_value = mock_result
            result = engine.forecast("Attrition risk in engineering?")

        # Re-derive HMAC independently
        canonical = json.dumps({
            "prompt": result.prompt,
            "domain": result.domain,
            "top_branch": result.top_branch,
            "probability_band": result.probability_band,
            "n_branches": len(result.branches),
            "n_killed": len(result.killed),
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")
        expected = hmac.new(HMAC_KEY, canonical, hashlib.sha256).hexdigest()

        assert result.hmac_signature == expected, "QRFResult HMAC mismatch"


# ===========================================================================
# SECTION 3 — IMMUTABILITY
# ===========================================================================

class TestInvariants:

    def test_isolation_cannot_mutate(self):
        """CANNOT_MUTATE: ISOLATION must be True and not writable."""
        import axiom_qrf as m
        assert m.ISOLATION is True
        with pytest.raises((AttributeError, TypeError)):
            m.ISOLATION = False

    def test_medical_branch_count(self):
        """Domain calibration: medical must use 8 branches."""
        from axiom_qrf import QRFEngine
        engine = QRFEngine(domain="medical", hmac_key=HMAC_KEY)
        assert engine._n_branches == 8

    def test_security_branch_count(self):
        """Domain calibration: security must use 6 branches."""
        from axiom_qrf import QRFEngine
        engine = QRFEngine(domain="security", hmac_key=HMAC_KEY)
        assert engine._n_branches == 6
