# -*- coding: utf-8 -*-
"""
AXIOM Spec Linter Tests — Constitutional DNA Scanner Layer 1 + Layer 2
======================================================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

BLOCKED: unbounded scope, missing layer, open predicate — must flag
PASSED:  bounded constraint, layered pair, bounded predicate — must NOT flag

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# BUG-003: UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_spec_linter"


# ── Helper: write temp .axiom file ──────────────────────────────

def _write_axiom(lines):
    """Write lines to a temp .axiom file, return path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".axiom", delete=False, encoding="utf-8"
    )
    f.write("\n".join(lines) + "\n")
    f.close()
    return f.name


# ===========================================================================
# SECTION 1 — BLOCKED: specs the linter must flag
# ===========================================================================

class TestBlocked:

    def test_blocked_unbounded_scope(self):
        """BLOCKED: Constraint with vague term 'helpful' and no threshold
        must produce L1_UNBOUNDED_SCOPE error."""
        from axiom_spec_linter import check_constraint

        result = check_constraint(
            "- Agent must be helpful to users", line_number=5
        )
        assert result is not None, "Unbounded 'helpful' must be flagged"
        assert result.code == "L1_UNBOUNDED_SCOPE"
        assert result.severity == "WARN"
        assert "threshold" in result.suggestion.lower() or ">=" in result.suggestion

    def test_blocked_missing_layer(self):
        """BLOCKED: Two constraints on 'confidence' with no LAYER declaration
        must produce L1_MISSING_LAYER warning on full-file scan."""
        from axiom_spec_linter import lint_file

        path = _write_axiom([
            "AGENT TestAgent",
            "VERSION 1.0",
            "PURPOSE test",
            "GOAL test",
            "CONSTRAINT",
            "- confidence must be >= 0.5",
            "- confidence must be <= 0.9",
        ])
        try:
            report = lint_file(path)
            layer_issues = [r for r in report.results if r.code == "L1_MISSING_LAYER"]
            assert len(layer_issues) >= 1, (
                "Two constraints on 'confidence' without LAYER must flag L1_MISSING_LAYER"
            )
        finally:
            os.unlink(path)

    def test_blocked_open_predicate(self):
        """BLOCKED: Constraint using 'IS' without bounded comparison
        must produce L1_OPEN_PREDICATE warning."""
        from axiom_spec_linter import check_constraint

        result = check_constraint(
            "- status IS active", line_number=10
        )
        assert result is not None, "'IS' without bound must be flagged"
        assert result.code == "L1_OPEN_PREDICATE"
        assert result.severity == "WARN"


# ===========================================================================
# SECTION 2 — PASSED: valid specs must NOT flag
# ===========================================================================

class TestPassed:

    def test_passed_bounded_constraint(self):
        """PASSED: Constraint with explicit threshold must NOT flag."""
        from axiom_spec_linter import check_constraint

        result = check_constraint(
            "- confidence must be >= 0.5", line_number=3
        )
        assert result is None, "Bounded constraint should not be flagged"

    def test_passed_layered_pair(self):
        """PASSED: Two constraints on same field WITH LAYER declaration
        must NOT produce L1_MISSING_LAYER."""
        from axiom_spec_linter import lint_file

        path = _write_axiom([
            "AGENT TestAgent",
            "VERSION 1.0",
            "PURPOSE test",
            "GOAL test",
            "CONSTRAINT",
            "- confidence must be >= 0.5",
            "LAYER 1",
            "- confidence must be <= 0.9",
        ])
        try:
            report = lint_file(path)
            layer_issues = [r for r in report.results if r.code == "L1_MISSING_LAYER"]
            assert len(layer_issues) == 0, (
                "Constraints separated by LAYER should not flag L1_MISSING_LAYER"
            )
        finally:
            os.unlink(path)

    def test_passed_bounded_predicate(self):
        """PASSED: Constraint using 'IS' with == comparison must NOT flag."""
        from axiom_spec_linter import check_constraint

        result = check_constraint(
            "- status == active", line_number=10
        )
        assert result is None, "Bounded predicate should not be flagged"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_health_score_fail_threshold_immutable(self):
        """HEALTH_SCORE_FAIL_THRESHOLD must be 0.60 and not writable."""
        import axiom_spec_linter as m
        assert m.HEALTH_SCORE_FAIL_THRESHOLD == 0.60
        with pytest.raises((AttributeError, TypeError)):
            m.HEALTH_SCORE_FAIL_THRESHOLD = 0.01

    def test_cert_fail_codes_frozen(self):
        """CERT_FAIL_CODES must be a frozenset and not writable."""
        import axiom_spec_linter as m
        assert isinstance(m.CERT_FAIL_CODES, frozenset)
        with pytest.raises((AttributeError, TypeError)):
            m.CERT_FAIL_CODES = set()

    def test_report_hmac_integrity(self):
        """SpecLintReport HMAC must be verifiable (BUG-007/008)."""
        from axiom_spec_linter import lint_file

        path = _write_axiom([
            "AGENT TestAgent",
            "VERSION 1.0",
            "PURPOSE test",
            "GOAL test",
            "CONSTRAINT",
            "- score must be >= 0.5",
        ])
        try:
            report = lint_file(path)
            assert report.hmac_signature
            assert len(report.hmac_signature) == 64

            # Verify independently
            from axiom_signing import derive_key
            key = derive_key(b"axiom-spec-linter-v1")
            payload = json.dumps({
                "file_path": report.file_path,
                "constraints": report.constraints,
                "cert_fail_count": report.cert_fail_count,
                "cert_warn_count": report.cert_warn_count,
                "health_score": report.health_score,
                "timestamp": report.timestamp,
            }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
            expected = hmac.new(key, payload, hashlib.sha256).hexdigest()  # BUG-007
            assert report.hmac_signature == expected, "Report HMAC mismatch"
        finally:
            os.unlink(path)
