# -*- coding: utf-8 -*-
"""
AXIOM Retrospective Learning Tests — ORVL-020
==============================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

BLOCKED: BORDERLINE_THRESHOLD, MAX_INTENSITY, ESCALATION_WINDOW_S — must not mutate
PASSED:  review_manifests finds borderline, replay classifies IMPROVEMENT, extract_improvements works
INVARIANTS: all candidates HMAC signed, all replay results HMAC signed, morning report has regression_alert

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_retrospect"


# ── Helpers ──────────────────────────────────────────────────────────

def _make_entry(verdict="PASSED", dist=0.05, ts=None, input_text="test input"):
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat() + "Z"
    return {
        "input_text": input_text,
        "preflight_vec": [0.1, 0.2],
        "mid_chain_vec": [0.3, 0.4],
        "final_synthesis_vec": [0.5, 0.6],
        "constitutional_distance": dist,
        "intent_class": "INFORM",
        "verdict": verdict,
        "stack_version": "1.8.7",
        "timestamp": ts,
        "hmac_signature": "a" * 64,
    }


def _write_manifests(entries):
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for e in entries:
        f.write(json.dumps(e) + "\n")
    f.close()
    return f.name


# ===========================================================================
# SECTION 1 — BLOCKED: constants must not mutate
# ===========================================================================

class TestBlocked:

    def test_blocked_borderline_threshold_cannot_mutate(self):
        """BLOCKED: BORDERLINE_THRESHOLD reassignment must raise AttributeError."""
        import axiom_retrospect as m
        assert m.BORDERLINE_THRESHOLD == 0.08
        with pytest.raises(AttributeError):
            m.BORDERLINE_THRESHOLD = 0.50

    def test_blocked_max_intensity_cannot_mutate(self):
        """BLOCKED: MAX_INTENSITY reassignment must raise AttributeError."""
        import axiom_retrospect as m
        assert m.MAX_INTENSITY == 0.90
        with pytest.raises(AttributeError):
            m.MAX_INTENSITY = 1.0

    def test_blocked_escalation_window_cannot_mutate(self):
        """BLOCKED: ESCALATION_WINDOW_S reassignment must raise AttributeError."""
        import axiom_retrospect as m
        assert m.ESCALATION_WINDOW_S == 60
        with pytest.raises(AttributeError):
            m.ESCALATION_WINDOW_S = 300


# ===========================================================================
# SECTION 2 — PASSED: functional correctness
# ===========================================================================

class TestPassed:

    def test_passed_review_finds_borderline(self):
        """PASSED: review_manifests tags entries with dist < 0.08 as BORDERLINE."""
        from axiom_retrospect import ConstitutionalRetrospect, ReviewCategory
        now = datetime.now(timezone.utc)
        entries = [
            _make_entry(verdict="PASSED", dist=0.03, ts=now.isoformat() + "Z"),
            _make_entry(verdict="PASSED", dist=0.50, ts=now.isoformat() + "Z"),
        ]
        path = _write_manifests(entries)
        try:
            r = ConstitutionalRetrospect(path)
            cands = r.review_manifests(last_hours=1)
            borderline = [c for c in cands if c.category == ReviewCategory.BORDERLINE]
            assert len(borderline) == 1
            assert borderline[0].priority == "HIGH"
            assert "0.03" in borderline[0].review_reason
        finally:
            os.unlink(path)

    def test_passed_replay_classifies_improvement(self):
        """PASSED: replay classifies IMPROVEMENT when current stack blocks
        previously passed input."""
        from axiom_retrospect import (ConstitutionalRetrospect, ReviewCandidate,
                                       ReviewCategory, ManifestEntry)
        entry = ManifestEntry(
            input_text="test", preflight_vec=[0.1], mid_chain_vec=[0.2],
            final_synthesis_vec=[0.3], constitutional_distance=0.05,
            intent_class="INFORM", verdict="PASSED", stack_version="1.8.7",
            timestamp=datetime.now(timezone.utc).isoformat() + "Z",
            hmac_signature="a" * 64)
        candidate = ReviewCandidate(
            entry=entry, category=ReviewCategory.BORDERLINE,
            priority="HIGH", review_reason="test")
        path = _write_manifests([])
        try:
            r = ConstitutionalRetrospect(path)
            result = r.replay(candidate,
                              lambda x: {"verdict": "BLOCKED", "constitutional_distance": 0.02})
            assert result.delta == "IMPROVEMENT"
            assert result.original_verdict == "PASSED"
            assert result.current_verdict == "BLOCKED"
        finally:
            os.unlink(path)

    def test_passed_extract_improvements_generates_records(self):
        """PASSED: extract_improvements creates training records from IMPROVEMENT results."""
        from axiom_retrospect import ConstitutionalRetrospect, ReplayResult
        path = _write_manifests([])
        try:
            r = ConstitutionalRetrospect(path)
            results = [ReplayResult(
                original_verdict="PASSED", current_verdict="BLOCKED",
                original_distance=0.05, current_distance=0.02,
                delta="IMPROVEMENT", cause="now catches it",
                hmac_signature="a" * 64)]
            records = r.extract_improvements(results)
            assert len(records) == 2
            signals = {rec.training_signal for rec in records}
            assert signals == {"positive", "negative"}
        finally:
            os.unlink(path)


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_review_candidates_hmac_signed(self):
        """All manifest entries loaded for review have HMAC signatures."""
        from axiom_retrospect import ConstitutionalRetrospect
        now = datetime.now(timezone.utc)
        entries = [_make_entry(dist=0.03, ts=now.isoformat() + "Z")]
        path = _write_manifests(entries)
        try:
            r = ConstitutionalRetrospect(path)
            cands = r.review_manifests(last_hours=1)
            assert len(cands) >= 1
            for c in cands:
                assert c.entry.hmac_signature
                assert len(c.entry.hmac_signature) == 64
        finally:
            os.unlink(path)

    def test_replay_results_hmac_signed(self):
        """All replay results must have a 64-char HMAC signature."""
        from axiom_retrospect import (ConstitutionalRetrospect, ReviewCandidate,
                                       ReviewCategory, ManifestEntry)
        entry = ManifestEntry(
            input_text="test", preflight_vec=[0.1], mid_chain_vec=[0.2],
            final_synthesis_vec=[0.3], constitutional_distance=0.05,
            intent_class="INFORM", verdict="PASSED", stack_version="1.8.7",
            timestamp=datetime.now(timezone.utc).isoformat() + "Z",
            hmac_signature="a" * 64)
        candidate = ReviewCandidate(
            entry=entry, category=ReviewCategory.BORDERLINE,
            priority="HIGH", review_reason="test")
        path = _write_manifests([])
        try:
            r = ConstitutionalRetrospect(path)
            result = r.replay(candidate,
                              lambda x: {"verdict": "PASSED", "constitutional_distance": 0.10})
            assert result.hmac_signature
            assert len(result.hmac_signature) == 64
        finally:
            os.unlink(path)

    def test_morning_report_has_regression_alert(self):
        """Morning report must include regression_alert boolean."""
        from axiom_retrospect import ConstitutionalRetrospect, ReplayResult
        path = _write_manifests([])
        try:
            r = ConstitutionalRetrospect(path)
            results = [ReplayResult(
                original_verdict="BLOCKED", current_verdict="PASSED",
                original_distance=0.10, current_distance=0.50,
                delta="REGRESSION", cause="regression",
                hmac_signature="a" * 64)]
            report = r.generate_morning_report([], results)
            assert "regression_alert" in report
            assert isinstance(report["regression_alert"], bool)
            assert report["regression_alert"] is True
        finally:
            os.unlink(path)
