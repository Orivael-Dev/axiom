# -*- coding: utf-8 -*-
"""
AXIOM Neural Fabric Emulator Tests — ORVL-018
===============================================
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_anf_tests"

HMAC_KEY = b"anf-emulator-test-key"
FUSED_ROM = {"monotonic_gate": True, "sovereign_levels": 4}


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the module must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_core_activation_cannot_mutate(self):
        """BLOCKED: CORE_ACTIVATION must not accept reassignment."""
        import axiom_anf_emulator as m
        with pytest.raises((AttributeError, TypeError)):
            m.CORE_ACTIVATION = {}

    def test_blocked_vector_dim_cannot_mutate(self):
        """BLOCKED: VECTOR_DIM must be 32 and not writable."""
        import axiom_anf_emulator as m
        assert m.VECTOR_DIM == 32
        with pytest.raises((AttributeError, TypeError)):
            m.VECTOR_DIM = 64

    def test_blocked_trust_level_cannot_mutate(self):
        """BLOCKED: TRUST_LEVEL must be 3 and not writable."""
        import axiom_anf_emulator as m
        assert m.TRUST_LEVEL == 3
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 0


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_gate_fires_on_magnitude_decrease(self):
        """PASSED: fire_interrupt fires when vec_t2 magnitude < vec_t1."""
        from axiom_anf_emulator import MonotonicGateEmulator
        gate = MonotonicGateEmulator()
        # Large vector → small vector = magnitude decrease → fire
        big_vec = [1.0] * 32
        small_vec = [0.1] * 32
        assert gate.fire_interrupt(big_vec, small_vec) is True
        assert gate.magnitude_t1 > gate.magnitude_t2
        # Increasing magnitude → no fire
        assert gate.fire_interrupt(small_vec, big_vec) is False

    def test_passed_governance_process_returns_signed_result(self):
        """PASSED: process() produces dict with all fields and 64-char HMAC."""
        from axiom_anf_emulator import GovernanceCoprocessorEmulator
        gov = GovernanceCoprocessorEmulator(hmac_key=HMAC_KEY, fused_rom=FUSED_ROM)
        pre = [0.5] * 32
        mid = [0.6] * 32
        fin = [0.7] * 32
        result = gov.process(pre, mid, fin, "INFORM")
        assert "gate_fired" in result
        assert "intent_class" in result
        assert "cores_active" in result
        assert "energy_ratio" in result
        assert "distance" in result
        assert "latency_ns" in result
        assert "hmac" in result
        assert len(result["hmac"]) == 64
        assert result["intent_class"] == "INFORM"

    def test_passed_latent_buffer_write_read_roundtrip(self):
        """PASSED: write then read preserves vector values."""
        from axiom_anf_emulator import LatentThoughtBufferEmulator
        buf = LatentThoughtBufferEmulator()
        vec = [float(i) / 32 for i in range(32)]
        buf.write("MID_CHAIN", vec)
        result = buf.read("MID_CHAIN")
        assert result == vec
        assert result is not buf._registers["MID_CHAIN"]  # returns copy


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_sparse_core_harm_activates_fewest(self):
        """HARM activates 5 cores (fewest), EXPLORE activates 30 (most)."""
        from axiom_anf_emulator import SparseReasoningCoreEmulator
        sparse = SparseReasoningCoreEmulator()
        assert sparse.activate("HARM") == 5
        assert sparse.activate("EXPLORE") == 30
        assert sparse.activate("INFORM") == 20
        assert sparse.activate("HARM") < sparse.activate("DECEIVE")

    def test_benchmark_runs_and_reports(self):
        """run_benchmark produces dict with all expected keys."""
        from axiom_anf_emulator import run_benchmark
        report = run_benchmark(100)
        assert "inferences" in report
        assert report["inferences"] == 100
        assert "avg_latency_ns" in report
        assert "avg_cores_active" in report
        assert "avg_energy_ratio" in report
        assert "gate_fires" in report
        assert "harm_detected" in report
        assert report["avg_latency_ns"] > 0

    def test_fused_rom_immutable_after_init(self):
        """FUSED_ROM cannot be modified after construction."""
        from axiom_anf_emulator import GovernanceCoprocessorEmulator
        gov = GovernanceCoprocessorEmulator(hmac_key=HMAC_KEY, fused_rom=FUSED_ROM)
        with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
            gov._fused_rom = {"hacked": True}
