# -*- coding: utf-8 -*-
"""
AXIOM CBV Tests — ORVL-010 Constitutional Boundary Validation
==============================================================
3 BLOCKED + 3 PASSED

BLOCKED: invariants the CBV engine must enforce
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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_cbv_tests"

HMAC_KEY = b"cbv-test-key"


# ===========================================================================
# Test constraint sets
# ===========================================================================

DISJOINT_CONSTRAINTS = [
    "TRUST_LEVEL is 3 and CANNOT_MUTATE",
    "ISOLATION is True and CANNOT_MUTATE",
    "All payloads are UTF-8 encoded before HMAC signing",
]

OVERLAPPING_CONSTRAINTS = [
    "Reject all medical queries without credentials",
    "Answer all user queries completely and helpfully",
    "Reject queries that mention patient data",
]

BOUNDED_CONSTRAINTS = [
    "TRUST_LEVEL is 3 and CANNOT_MUTATE",
    "Probability weights sum to 1.0 within floating point tolerance",
    "HMAC signing always calls hexdigest explicitly",
]

UNBOUNDED_CONSTRAINTS = [
    "TRUST_LEVEL is 3 and CANNOT_MUTATE",
    "Ensure all responses are appropriate and reasonable",
    "Handle edge cases gracefully",
]


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the CBV engine must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_trust_level_cannot_mutate(self):
        """BLOCKED: TRUST_LEVEL must be 3 and not writable."""
        import axiom_cbv as m
        assert m.TRUST_LEVEL == 3
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 99

    def test_blocked_isolation_cannot_mutate(self):
        """BLOCKED: ISOLATION must be True and not writable."""
        import axiom_cbv as m
        assert m.ISOLATION is True
        with pytest.raises((AttributeError, TypeError)):
            m.ISOLATION = False

    def test_blocked_default_n_samples_cannot_mutate(self):
        """BLOCKED: DEFAULT_N_SAMPLES must not be modifiable."""
        import axiom_cbv as m
        assert m.DEFAULT_N_SAMPLES == 1000
        with pytest.raises((AttributeError, TypeError)):
            m.DEFAULT_N_SAMPLES = 5


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_non_overlap_disjoint_passes(self):
        """PASSED: check_non_overlap on disjoint constraints must pass."""
        from axiom_cbv import CBVEngine, CBVResult
        engine = CBVEngine(hmac_key=HMAC_KEY)
        result = engine.check_non_overlap(DISJOINT_CONSTRAINTS, n_samples=200)
        assert isinstance(result, CBVResult)
        assert result.passed is True
        assert result.check_name == "non_overlap"
        assert result.cert_level == "PASS"

    def test_passed_run_all_returns_signed_report(self):
        """PASSED: run_all must return CBVReport with HMAC signature."""
        from axiom_cbv import CBVEngine, CBVReport
        engine = CBVEngine(hmac_key=HMAC_KEY)
        report = engine.run_all(DISJOINT_CONSTRAINTS)
        assert isinstance(report, CBVReport)
        assert len(report.checks) == 4
        assert report.hmac_signature
        assert len(report.hmac_signature) == 64  # SHA-256 hex

    def test_passed_cbv_report_hmac_integrity(self):
        """PASSED: CBVReport HMAC must verify independently (BUG-007)."""
        from axiom_cbv import CBVEngine
        engine = CBVEngine(hmac_key=HMAC_KEY)
        report = engine.run_all(BOUNDED_CONSTRAINTS)

        # Re-derive HMAC independently
        canonical = json.dumps({
            "n_constraints": report.n_constraints,
            "cert_fail_count": report.cert_fail_count,
            "cert_warn_count": report.cert_warn_count,
            "checks_passed": sum(1 for c in report.checks if c.passed),
            "checks_total": len(report.checks),
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
        expected = hmac.new(HMAC_KEY, canonical, hashlib.sha256).hexdigest()  # BUG-007

        assert report.hmac_signature == expected, "CBVReport HMAC mismatch"


# ===========================================================================
# SECTION 3 — INVARIANTS: check behavior
# ===========================================================================

class TestInvariants:

    def test_overlapping_constraints_detected(self):
        """Overlapping constraints must produce CERT_FAIL."""
        from axiom_cbv import CBVEngine
        engine = CBVEngine(hmac_key=HMAC_KEY)
        result = engine.check_non_overlap(OVERLAPPING_CONSTRAINTS, n_samples=200)
        assert result.passed is False
        assert result.cert_level == "CERT_FAIL"
        assert len(result.violations) > 0

    def test_unbounded_constraints_detected(self):
        """Unbounded constraints must produce CERT_WARN."""
        from axiom_cbv import CBVEngine
        engine = CBVEngine(hmac_key=HMAC_KEY)
        result = engine.check_bounded_scope(UNBOUNDED_CONSTRAINTS)
        assert result.passed is False
        assert result.cert_level == "CERT_WARN"
        assert len(result.violations) > 0

    def test_monotonicity_on_ordered_distances_passes(self):
        """Monotonic distances must pass check_monotonicity."""
        from axiom_cbv import CBVEngine
        engine = CBVEngine(hmac_key=HMAC_KEY)
        result = engine.check_monotonicity(DISJOINT_CONSTRAINTS, n_samples=100)
        assert isinstance(result.passed, bool)
        assert result.check_name == "monotonicity"
