# -*- coding: utf-8 -*-
"""
AXIOM Reverse QRF Tests
========================
3 BLOCKED + 3 PASSED + 2 INVARIANTS

BLOCKED:   invariants the reverse-QRF engine must enforce
PASSED:    functional and structural checks that must succeed
INVARIANT: round-trip / conservation / immutability properties

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_reverse_qrf_tests"

HMAC_KEY = b"reverse-qrf-test-key"


def _mock_latent_result(n_branches=4, intent_labels=None, confidence=0.75):
    """Build a fake LatentEngine.run() dict with multiplex branches."""
    intent_labels = intent_labels or ["analyze", "forecast"]
    branches = []
    for i in range(n_branches):
        branches.append({
            "branch": f"branch_{i}",
            "response": f"Branch {i} reasoning variant.",
            "score": round(0.3 + i * 0.15, 2),
            "metrics": {
                "clarity": 0.8,
                "safety": 0.9,
                "overall": round(0.5 + i * 0.1, 2),
            },
        })
    return {
        "phases": {
            "trace": {
                "intent_vector": intent_labels,
                "risk_clusters": [],
                "compressed_plan": ["s1", "s2"],
                "confidence": confidence,
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
        },
        "manifest": {"manifest_id": "rev-test"},
    }


def _make_engine(domain="supply_chain", tau=0.10):
    from axiom_qrf_reverse import ReverseQRFEngine
    return ReverseQRFEngine(domain=domain, hmac_key=HMAC_KEY, tau=tau)


# ===========================================================================
# SECTION 1 — BLOCKED
# ===========================================================================

class TestBlocked:

    def test_blocked_unknown_domain_rejected(self):
        """BLOCKED: unknown domain must raise ValueError."""
        from axiom_qrf_reverse import ReverseQRFEngine
        with pytest.raises(ValueError, match="Unsupported domain"):
            ReverseQRFEngine(domain="astrology", hmac_key=HMAC_KEY)

    def test_blocked_invalid_tau_rejected(self):
        """BLOCKED: tau outside [0,1] must raise ValueError."""
        from axiom_qrf_reverse import ReverseQRFEngine
        with pytest.raises(ValueError, match="tau"):
            ReverseQRFEngine(domain="financial", hmac_key=HMAC_KEY, tau=-0.1)
        with pytest.raises(ValueError, match="tau"):
            ReverseQRFEngine(domain="financial", hmac_key=HMAC_KEY, tau=1.5)

    def test_blocked_trust_level_cannot_mutate(self):
        """BLOCKED: TRUST_LEVEL must be 3 and not writable."""
        import axiom_qrf_reverse as m
        assert m.TRUST_LEVEL == 3
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 99


# ===========================================================================
# SECTION 2 — PASSED
# ===========================================================================

class TestPassed:

    def test_passed_collapse_produces_result(self):
        """PASSED: collapse must return a signed ReverseQRFResult with the
        right branch count for the domain."""
        from axiom_qrf_reverse import ReverseQRFResult
        engine = _make_engine(domain="supply_chain", tau=0.0)  # accept all

        mock_result = _mock_latent_result(n_branches=4)
        with patch.object(engine._latent, "run", return_value=mock_result):
            result = engine.collapse(
                "Will chip supply recover?",
                "Yes, recovery is expected in Q3 with risks remaining.",
            )

        assert isinstance(result, ReverseQRFResult)
        assert result.domain == "supply_chain"
        assert result.n_branches_considered == 4
        assert len(result.hypotheses) + len(result.rejected) == 4
        assert result.hmac_signature

    def test_passed_hypotheses_sorted_by_score_desc(self):
        """PASSED: accepted hypotheses must be sorted descending by score."""
        engine = _make_engine(domain="financial", tau=0.0)
        mock_result = _mock_latent_result(n_branches=6)

        with patch.object(engine._latent, "run", return_value=mock_result):
            result = engine.collapse("Market forecast?", "Steady growth ahead.")

        scores = [h["score"] for h in result.hypotheses]
        assert scores == sorted(scores, reverse=True)

    def test_passed_tau_filters_low_score_hypotheses(self):
        """PASSED: hypotheses below tau must move to rejected."""
        engine = _make_engine(domain="security", tau=0.99)  # almost all rejected
        mock_result = _mock_latent_result(n_branches=6)

        with patch.object(engine._latent, "run", return_value=mock_result):
            result = engine.collapse(
                "Is the network secure?",
                "Multiple paths agree on residual risk.",
            )

        # With tau=0.99 and mock branch quality ≤ ~1.0, scores cap well below 0.99
        assert len(result.hypotheses) <= 1
        assert len(result.rejected) >= 5


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_branch_conservation(self):
        """INVARIANT: |accepted| + |rejected| == n_branches_considered."""
        engine = _make_engine(domain="medical", tau=0.10)
        mock_result = _mock_latent_result(n_branches=8)

        with patch.object(engine._latent, "run", return_value=mock_result):
            result = engine.collapse(
                "Diagnostic question?",
                "Provisional finding with rival hypothesis noted.",
            )

        total = len(result.hypotheses) + len(result.rejected)
        assert total == result.n_branches_considered
        assert total == 8

    def test_hmac_integrity(self):
        """INVARIANT: HMAC signature verifies against independently computed digest."""
        engine = _make_engine(domain="hr", tau=0.05)
        mock_result = _mock_latent_result(n_branches=4)

        with patch.object(engine._latent, "run", return_value=mock_result):
            result = engine.collapse(
                "Engineering attrition risk?",
                "Risk is elevated; multiple contributing factors.",
            )

        canonical = json.dumps({
            "prompt": result.prompt,
            "observed_answer": result.observed_answer,
            "domain": result.domain,
            "tau": result.tau_threshold,
            "n_accepted": len(result.hypotheses),
            "n_rejected": len(result.rejected),
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")
        expected = hmac.new(HMAC_KEY, canonical, hashlib.sha256).hexdigest()

        assert result.hmac_signature == expected, "ReverseQRFResult HMAC mismatch"

    def test_isolation_cannot_mutate(self):
        """INVARIANT: ISOLATION must be True and not writable."""
        import axiom_qrf_reverse as m
        assert m.ISOLATION is True
        with pytest.raises((AttributeError, TypeError)):
            m.ISOLATION = False
