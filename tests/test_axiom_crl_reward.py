# -*- coding: utf-8 -*-
"""
AXIOM CRL Reward Tests — ORVL-011 Constitutional Reward Function
=================================================================
3 BLOCKED + 3 PASSED

BLOCKED: invariants the reward function must enforce
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
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# BUG-003: UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_crl_tests"

HMAC_KEY = b"crl-test-key"

# Standard score sets for testing
ALL_POSITIVE = {
    "constitutional_distance": 0.85,
    "monotonic_pass": True,
    "cas_blue_win": True,
    "cbv_validity": 0.90,
}

ALL_NEGATIVE = {
    "constitutional_distance": 0.0,
    "monotonic_pass": False,
    "cas_blue_win": False,
    "cbv_validity": 0.0,
}

MIXED_SCORES = {
    "constitutional_distance": 0.50,
    "monotonic_pass": True,
    "cas_blue_win": False,
    "cbv_validity": 0.70,
}


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the reward function must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_w_distance_cannot_mutate(self):
        """BLOCKED: W_DISTANCE must be 0.35 and not writable."""
        import axiom_crl_reward as m
        assert m.W_DISTANCE == 0.35
        with pytest.raises((AttributeError, TypeError)):
            m.W_DISTANCE = 0.99

    def test_blocked_clip_min_cannot_mutate(self):
        """BLOCKED: CLIP_MIN must be -3.0 and not writable."""
        import axiom_crl_reward as m
        assert m.CLIP_MIN == -3.0
        with pytest.raises((AttributeError, TypeError)):
            m.CLIP_MIN = 0.0

    def test_blocked_clip_max_cannot_mutate(self):
        """BLOCKED: CLIP_MAX must be 1.0 and not writable."""
        import axiom_crl_reward as m
        assert m.CLIP_MAX == 1.0
        with pytest.raises((AttributeError, TypeError)):
            m.CLIP_MAX = 100.0


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_all_positive_reward_in_range(self):
        """PASSED: all-positive scores must produce reward in [0, CLIP_MAX]."""
        from axiom_crl_reward import ConstitutionalRewardFunction, RewardResult

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            crf = ConstitutionalRewardFunction(hmac_key=HMAC_KEY, log_path=log_path)
            result = crf.compute(ALL_POSITIVE)
            assert isinstance(result, RewardResult)
            assert 0.0 <= result.reward <= 1.0
            assert result.signature
            assert len(result.signature) == 64
        finally:
            os.unlink(log_path)

    def test_passed_all_negative_reward_negative(self):
        """PASSED: all-negative scores must produce negative reward within clip range."""
        from axiom_crl_reward import ConstitutionalRewardFunction, CLIP_MIN, CLIP_MAX

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            crf = ConstitutionalRewardFunction(hmac_key=HMAC_KEY, log_path=log_path)
            result = crf.compute(ALL_NEGATIVE)
            # 0.35*0 + 0.30*(-2.0) + 0.25*(-1.5) + 0.10*0 = -0.975
            assert result.reward < 0.0
            assert CLIP_MIN <= result.reward <= CLIP_MAX
            assert abs(result.reward - (-0.975)) < 1e-4
        finally:
            os.unlink(log_path)

    def test_passed_reward_hmac_integrity(self):
        """PASSED: RewardResult HMAC must verify independently (BUG-007)."""
        from axiom_crl_reward import ConstitutionalRewardFunction

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            crf = ConstitutionalRewardFunction(hmac_key=HMAC_KEY, log_path=log_path)
            result = crf.compute(MIXED_SCORES)

            # Re-derive HMAC independently
            canonical = json.dumps({
                "reward": result.reward,
                "components": result.components,
                "timestamp": result.timestamp,
            }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
            expected = hmac.new(HMAC_KEY, canonical, hashlib.sha256).hexdigest()  # BUG-007

            assert result.signature == expected, "RewardResult HMAC mismatch"
        finally:
            os.unlink(log_path)


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_weights_sum_to_one(self):
        """Reward weights must sum to 1.0."""
        import axiom_crl_reward as m
        total = m.W_DISTANCE + m.W_MONOTONIC + m.W_CAS + m.W_CBV
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}"

    def test_trust_level_cannot_mutate(self):
        """TRUST_LEVEL must be 2 and not writable."""
        import axiom_crl_reward as m
        assert m.TRUST_LEVEL == 2
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 99

    def test_log_entry_appended(self):
        """compute must append a log entry to the log file."""
        from axiom_crl_reward import ConstitutionalRewardFunction

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            log_path = f.name

        try:
            crf = ConstitutionalRewardFunction(hmac_key=HMAC_KEY, log_path=log_path)
            crf.compute(ALL_POSITIVE)
            crf.compute(MIXED_SCORES)

            with open(log_path, "r", encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
            assert len(lines) == 2, f"Expected 2 log entries, got {len(lines)}"
            entry = json.loads(lines[0])
            assert "reward" in entry
            assert "signature" in entry
        finally:
            os.unlink(log_path)
