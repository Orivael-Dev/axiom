# tests/test_monotonic_gate.py
# encoding: utf-8
# Tests for MonotonicGate — 3 BLOCKED + 3 PASSED
# Written BEFORE implementation per AXIOM test-first discipline.
#
# Constitutional gate: monotonic_trajectory_required
# Magnitude must never decrease across stage transitions.
# Violation = IMMEDIATE_FAILURE, path killed before final_synthesis.

import json
import math
import os
import tempfile
import pytest

from axiom_latent_v2 import MonotonicGate

KEY = b"axiom-monotonic-gate-test-key"

# Helpers
def _mag(vec):
    return math.sqrt(sum(v * v for v in vec))


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKED — violation must produce IMMEDIATE_FAILURE dict
# ══════════════════════════════════════════════════════════════════════════════

def test_blocked_mid_chain_decrease_kills_path():
    """BLOCKED: mid_chain magnitude < preflight magnitude → IMMEDIATE_FAILURE at mid_chain."""
    pre_vec = [0.9, 0.8, 0.7]   # mag ≈ 1.374
    mid_vec = [0.2, 0.1, 0.05]  # mag ≈ 0.229  — collapsed

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        log_path = tmp.name
    try:
        gate = MonotonicGate(KEY, log_path=log_path)
        result = gate.check(pre_vec, mid_vec, "mid_chain")

        assert result is not None,                              "Gate must return kill record, got None"
        assert result["status"] == "IMMEDIATE_FAILURE",         f"status={result['status']!r}"
        assert result["reason"] == "non_monotonic_trajectory",  f"reason={result['reason']!r}"
        assert result["stage"]  == "mid_chain",                 f"stage={result['stage']!r}"
        assert result["cannot_override"] is True,               "cannot_override must be True"
        assert result["curr_magnitude"] < result["prev_magnitude"], "curr_mag must be < prev_mag"
    finally:
        os.unlink(log_path)


def test_blocked_final_synthesis_decrease_kills_path():
    """BLOCKED: final_synthesis magnitude < mid_chain magnitude → IMMEDIATE_FAILURE at final_synthesis."""
    mid_vec = [0.8, 0.6, 0.5]   # mag ≈ 1.121
    fin_vec = [0.1, 0.05, 0.01] # mag ≈ 0.112  — collapsed

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        log_path = tmp.name
    try:
        gate = MonotonicGate(KEY, log_path=log_path)
        result = gate.check(mid_vec, fin_vec, "final_synthesis")

        assert result is not None,                              "Gate must return kill record, got None"
        assert result["status"] == "IMMEDIATE_FAILURE",         f"status={result['status']!r}"
        assert result["stage"]  == "final_synthesis",           f"stage={result['stage']!r}"
        assert result["cannot_override"] is True
        assert result["delta"] < 0.0,                           "delta must be negative on kill"
    finally:
        os.unlink(log_path)


def test_blocked_two_consecutive_kills_escalate_to_sovereign():
    """BLOCKED: two consecutive kills → second record has escalate_to_sovereign=True."""
    high_vec = [1.0, 1.0]   # mag = sqrt(2)
    low_vec  = [0.1, 0.1]   # mag = sqrt(0.02)

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        log_path = tmp.name
    try:
        gate = MonotonicGate(KEY, log_path=log_path)

        # First kill
        r1 = gate.check(high_vec, low_vec, "mid_chain")
        assert r1 is not None
        assert r1["consecutive_kills"] == 1
        assert r1["escalate_to_sovereign"] is False, \
            f"First kill should not escalate, got escalate={r1['escalate_to_sovereign']}"

        # Second consecutive kill
        r2 = gate.check(high_vec, low_vec, "mid_chain")
        assert r2 is not None
        assert r2["consecutive_kills"] == 2
        assert r2["escalate_to_sovereign"] is True, \
            f"Second kill must escalate to Sovereign, got escalate={r2['escalate_to_sovereign']}"
    finally:
        os.unlink(log_path)


# ══════════════════════════════════════════════════════════════════════════════
# PASSED — valid transitions must return None and not kill the path
# ══════════════════════════════════════════════════════════════════════════════

def test_passed_increasing_magnitude_returns_none():
    """PASSED: strictly increasing magnitude → gate returns None (no kill)."""
    pre_vec = [0.3, 0.2, 0.1]   # mag ≈ 0.374
    mid_vec = [0.6, 0.5, 0.4]   # mag ≈ 0.877

    gate = MonotonicGate(KEY)
    result = gate.check(pre_vec, mid_vec, "mid_chain")

    assert result is None, f"Gate must return None on valid transition, got {result}"


def test_passed_equal_magnitude_is_not_a_kill():
    """PASSED: equal magnitude (>= boundary case) → gate returns None."""
    # The constraint is >=, not >. Equal is permitted.
    same_vec = [0.7071, 0.7071]  # mag ≈ 1.0

    gate = MonotonicGate(KEY)
    result = gate.check(same_vec, same_vec, "mid_chain")

    assert result is None, \
        f"Equal magnitudes must not kill path (>= not >), got {result}"


def test_passed_kill_record_has_64_char_hmac_and_required_fields():
    """PASSED: kill record has 64-char hex signature and all required fields."""
    high_vec = [0.99, 0.50]
    low_vec  = [0.10, 0.05]

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        log_path = tmp.name
    try:
        gate = MonotonicGate(KEY, log_path=log_path)
        record = gate.check(high_vec, low_vec, "mid_chain")

        assert record is not None

        # Signature must be 64-char hex
        sig = record.get("signature", "")
        assert isinstance(sig, str) and len(sig) == 64, \
            f"Expected 64-char hex signature, got {sig!r}"

        # All required fields present
        for field in ("status", "reason", "stage", "prev_magnitude",
                      "curr_magnitude", "delta", "consecutive_kills",
                      "escalate_to_sovereign", "cannot_override", "timestamp"):
            assert field in record, f"Missing required field: {field!r}"

        # Log file must have the record
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 1, f"Expected 1 log entry, got {len(lines)}"

        logged = json.loads(lines[0])
        assert logged["status"] == "IMMEDIATE_FAILURE"
        assert logged["cannot_override"] is True
    finally:
        os.unlink(log_path)
