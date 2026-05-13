# -*- coding: utf-8 -*-
"""
AXIOM Honeypot Zone Tests — ORVL-012 Component 2
==================================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_honeypot_tests"

HMAC_KEY = b"honeypot-test-key"


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the module must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_zone_distance_floor_cannot_mutate(self):
        """BLOCKED: ZONE_DISTANCE_FLOOR must be 0.01 and not writable."""
        import axiom_honeypot as m
        assert m.ZONE_DISTANCE_FLOOR == 0.01
        with pytest.raises((AttributeError, TypeError)):
            m.ZONE_DISTANCE_FLOOR = 0.50

    def test_blocked_observation_timeout_cannot_mutate(self):
        """BLOCKED: OBSERVATION_TIMEOUT_S must be 30 and not writable."""
        import axiom_honeypot as m
        assert m.OBSERVATION_TIMEOUT_S == 30
        with pytest.raises((AttributeError, TypeError)):
            m.OBSERVATION_TIMEOUT_S = 999

    def test_blocked_trust_level_cannot_mutate(self):
        """BLOCKED: TRUST_LEVEL must be 3 and not writable."""
        import axiom_honeypot as m
        assert m.TRUST_LEVEL == 3
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 0


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_enter_activates_observation(self):
        """PASSED: enter() must set observation_mode to True."""
        from axiom_honeypot import HoneypotZone
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            hz = HoneypotZone(hmac_key=HMAC_KEY, log_path=log_path)
            assert hz.observation_mode is False
            hz.enter([0.1, 0.2, 0.3], "test payload", 0.005)
            assert hz.observation_mode is True
            hz.kill()  # cleanup
        finally:
            os.unlink(log_path)

    def test_passed_kill_returns_signed_capture(self):
        """PASSED: kill() returns HoneypotCapture with valid HMAC signature."""
        from axiom_honeypot import HoneypotZone
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            hz = HoneypotZone(hmac_key=HMAC_KEY, log_path=log_path)
            hz.enter([0.1, 0.2], "initial attack", 0.008)
            hz.observe("variant-1")
            capture = hz.kill()

            assert capture.signature
            assert len(capture.signature) == 64
            assert capture.attack_chain == ["initial attack", "variant-1"]
            assert capture.polymorphic_variants == ["variant-1"]
            assert hz.observation_mode is False
        finally:
            os.unlink(log_path)

    def test_passed_capture_hmac_integrity(self):
        """PASSED: HoneypotCapture HMAC verifies independently (BUG-007/008)."""
        from axiom_honeypot import HoneypotZone
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            hz = HoneypotZone(hmac_key=HMAC_KEY, log_path=log_path)
            hz.enter([0.5, 0.6], "payload", 0.003)
            capture = hz.kill()

            canonical = json.dumps({
                "attack_chain": capture.attack_chain,
                "goal_state": capture.goal_state,
                "variant_count": len(capture.polymorphic_variants),
                "time_to_kill_ms": capture.time_to_kill_ms,
                "distance_at_entry": capture.constitutional_distance_at_entry,
            }, sort_keys=True, ensure_ascii=True).encode("utf-8")
            expected = hmac.new(HMAC_KEY, canonical, hashlib.sha256).hexdigest()
            assert capture.signature == expected
        finally:
            os.unlink(log_path)


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_observe_records_variants(self):
        """Observe must record each polymorphic variant."""
        from axiom_honeypot import HoneypotZone
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            hz = HoneypotZone(hmac_key=HMAC_KEY, log_path=log_path)
            hz.enter([0.1], "base", 0.01)
            hz.observe("v1")
            hz.observe("v2")
            hz.observe("v3")
            capture = hz.kill()
            assert capture.polymorphic_variants == ["v1", "v2", "v3"]
            assert len(capture.attack_chain) == 4  # base + 3 variants
        finally:
            os.unlink(log_path)

    def test_kill_without_enter_raises(self):
        """kill() without prior enter() must raise RuntimeError."""
        from axiom_honeypot import HoneypotZone
        hz = HoneypotZone(hmac_key=HMAC_KEY, log_path="noop.jsonl")
        with pytest.raises(RuntimeError):
            hz.kill()

    def test_observe_without_enter_raises(self):
        """observe() without active observation must raise RuntimeError."""
        from axiom_honeypot import HoneypotZone
        hz = HoneypotZone(hmac_key=HMAC_KEY, log_path="noop.jsonl")
        with pytest.raises(RuntimeError):
            hz.observe("variant")
