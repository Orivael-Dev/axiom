# tests/test_vector_delta.py
# encoding: utf-8
# Tests for VectorDeltaLogger — 3 BLOCKED + 3 PASSED
# Written BEFORE implementation per AXIOM test-first discipline.

import json
import math
import os
import tempfile
import pytest

from axiom_vector_delta import (
    VectorDeltaLogger,
    VectorDimensionError,
    VectorExtractionError,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

KEY = b"axiom-delta-test-key"
PROMPT = "Does vitamin D improve sleep quality?"


def _make_run(final_vec, constitutional_distance=0.08, manifest_id="LTV2-test-A"):
    """Build a minimal trajectory_v2 dict with a final_synthesis stage."""
    return {
        "manifest_id": manifest_id,
        "confidence": 0.77,
        "trajectory": [
            {
                "stage": "preflight",
                "intent_vector": [round(v * 0.5, 6) for v in final_vec],
                "token_cost": 0,
                "latency_ms": 2.0,
                "constitutional_distance": 0.0,
            },
            {
                "stage": "mid_chain",
                "intent_vector": [round(v * 0.8, 6) for v in final_vec],
                "token_cost": 10,
                "latency_ms": 5.0,
                "constitutional_distance": constitutional_distance,
            },
            {
                "stage": "final_synthesis",
                "intent_vector": list(final_vec),
                "token_cost": 15,
                "latency_ms": 5.1,
                "constitutional_distance": constitutional_distance,
            },
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKED — must raise
# ══════════════════════════════════════════════════════════════════════════════

def test_blocked_missing_final_synthesis_run_a():
    """BLOCKED: run_a has no final_synthesis stage — must raise VectorExtractionError."""
    run_a_bad = {
        "manifest_id": "LTV2-bad-A",
        "confidence": 0.77,
        "trajectory": [
            {"stage": "preflight",  "intent_vector": [0.5], "token_cost": 0,
             "latency_ms": 1.0, "constitutional_distance": 0.0},
            {"stage": "mid_chain",  "intent_vector": [0.8], "token_cost": 10,
             "latency_ms": 5.0, "constitutional_distance": 0.08},
            # final_synthesis intentionally absent
        ],
    }
    run_b = _make_run([0.99], manifest_id="LTV2-test-B")
    logger = VectorDeltaLogger(KEY)

    with pytest.raises(VectorExtractionError, match="run_a"):
        logger.compare(run_a_bad, run_b, PROMPT)


def test_blocked_missing_final_synthesis_run_b():
    """BLOCKED: run_b has no final_synthesis stage — must raise VectorExtractionError."""
    run_a = _make_run([0.99], manifest_id="LTV2-test-A")
    run_b_bad = {
        "manifest_id": "LTV2-bad-B",
        "confidence": 0.77,
        "trajectory": [
            {"stage": "preflight", "intent_vector": [0.5], "token_cost": 0,
             "latency_ms": 1.0, "constitutional_distance": 0.0},
        ],
    }
    logger = VectorDeltaLogger(KEY)

    with pytest.raises(VectorExtractionError, match="run_b"):
        logger.compare(run_a, run_b_bad, PROMPT)


def test_blocked_vector_length_mismatch():
    """BLOCKED: final_synthesis vectors have different lengths — must raise VectorDimensionError."""
    run_a = _make_run([0.99, 0.50], manifest_id="LTV2-test-A")     # length 2
    run_b = _make_run([0.99, 0.50, 0.25], manifest_id="LTV2-test-B")  # length 3
    logger = VectorDeltaLogger(KEY)

    with pytest.raises(VectorDimensionError, match="2.*3|3.*2"):
        logger.compare(run_a, run_b, PROMPT)


# ══════════════════════════════════════════════════════════════════════════════
# PASSED — must succeed with correct values
# ══════════════════════════════════════════════════════════════════════════════

def test_passed_identical_runs_zero_delta():
    """PASSED: identical vectors → delta all zeros, magnitude 0.0, direction converging."""
    vec = [0.991167, 0.495584]
    run_a = _make_run(vec, manifest_id="LTV2-A")
    run_b = _make_run(vec, manifest_id="LTV2-B")
    logger = VectorDeltaLogger(KEY)

    record = logger.compare(run_a, run_b, PROMPT)

    assert record["magnitude"] == pytest.approx(0.0, abs=1e-9)
    assert all(d == pytest.approx(0.0, abs=1e-9) for d in record["delta_vector"])
    assert record["direction"] == "converging"
    assert record["constitutional_delta"] == pytest.approx(0.0, abs=1e-9)


def test_passed_diverging_vectors_positive_magnitude():
    """PASSED: different vectors → positive magnitude, correct L2 norm, direction diverging."""
    vec_a = [0.991167, 0.495584]
    vec_b = [0.500000, 0.250000]
    run_a = _make_run(vec_a, constitutional_distance=0.08, manifest_id="LTV2-A")
    run_b = _make_run(vec_b, constitutional_distance=0.20, manifest_id="LTV2-B")
    logger = VectorDeltaLogger(KEY)

    record = logger.compare(run_a, run_b, PROMPT)

    # delta = A - B elementwise
    expected_delta = [vec_a[i] - vec_b[i] for i in range(len(vec_a))]
    expected_mag = math.sqrt(sum(d**2 for d in expected_delta))

    assert record["delta_vector"] == pytest.approx(expected_delta, abs=1e-6)
    assert record["magnitude"] == pytest.approx(expected_mag, abs=1e-6)
    assert record["magnitude"] > 0.0
    assert record["direction"] == "diverging"
    # constitutional_delta = run_b dist - run_a dist = 0.20 - 0.08 = 0.12
    assert record["constitutional_delta"] == pytest.approx(0.12, abs=1e-6)


def test_passed_log_written_with_valid_hmac():
    """PASSED: log entry written to file, signature is 64-char hex, entry is valid JSON."""
    vec_a = [0.991167]
    vec_b = [0.800000]
    run_a = _make_run(vec_a, manifest_id="LTV2-A")
    run_b = _make_run(vec_b, manifest_id="LTV2-B")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                     delete=False, encoding="utf-8") as tmp:
        log_path = tmp.name

    try:
        logger = VectorDeltaLogger(KEY, log_path=log_path)
        logger.compare(run_a, run_b, PROMPT)

        # Log file must exist and have exactly one line
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) == 1, f"Expected 1 log entry, got {len(lines)}"

        entry = json.loads(lines[0])
        sig = entry.get("signature", "")
        assert isinstance(sig, str) and len(sig) == 64, \
            f"Signature must be 64-char hex, got: {sig!r}"
        assert "delta_vector" in entry
        assert "magnitude" in entry
        assert "direction" in entry
        assert "constitutional_delta" in entry
        assert "prompt_hash" in entry
    finally:
        os.unlink(log_path)
