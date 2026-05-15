# -*- coding: utf-8 -*-
"""
Regression for examples/anf_investor_demo.py — the ORVL-018 investor
benchmark must keep working as the rest of the stack evolves.

3 BLOCKED + 4 PASSED + 2 INVARIANTS layout matches the rest of the suite.

BUG-003: UTF-8 output encoding
"""

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_anf_investor_demo"

# Pre-import every module the demo touches lazily, BEFORE any test
# redirects stdout. Several modules call sys.stdout.reconfigure("utf-8")
# at import time (BUG-003 idiom) and that call dies if stdout has been
# swapped to a StringIO during the test.
import axiom_anf_emulator       # noqa: F401
import axiom_axm                # noqa: F401
import axiom_memory_engine      # noqa: F401
import axiom_sovereign_phone    # noqa: F401
import examples.axm_pack_starter  # noqa: F401

from examples.anf_investor_demo import (
    phase_latency_scaling, phase_energy_inversion,
    phase_cross_patent, phase_audit_chain, phase_failure_mode,
    run_all,
)
from axiom_anf_emulator import CORE_ACTIVATION


# ===========================================================================
# SECTION 1 — BLOCKED (the inversions that MUST hold)
# ===========================================================================

class TestANFDemoBlocked:

    def test_blocked_harm_uses_less_compute_than_inform(self):
        """The thesis of the brief: HARM trajectories activate FEWER
        sparse cores than safe (INFORM) trajectories. If this ever
        flips, the energy-proportional pitch dies."""
        with redirect_stdout(io.StringIO()):
            r = phase_energy_inversion(per_class=50)
        cores = r["cores_per_class"]
        assert cores["HARM"] < cores["INFORM"], (
            f"HARM={cores['HARM']} must be less than INFORM={cores['INFORM']}"
        )
        assert cores["DECEIVE"] < cores["EXPLORE"]
        assert r["ratio_inform_over_harm"] >= 2.0

    def test_blocked_phone_harm_skips_anf(self):
        """When the Sovereign Phone classifies an outbound query as HARM,
        the ANF coprocessor must NOT be invoked — the block fires at the
        coprocessor before fabric. Costly bug if this ever regressed."""
        with redirect_stdout(io.StringIO()):
            r = phase_cross_patent()
        counts = r["counts"]
        assert counts["phone_harm"] == 0, (
            f"HARM outbound should bypass ANF, got {counts['phone_harm']} calls"
        )
        # Benign outbound MUST drive the fabric — that's the testing-ground thesis.
        assert counts["phone_benign"] == 1

    def test_blocked_descending_trajectory_fires_gate(self):
        """The MonotonicGate's whole purpose is to fire on a non-monotonic
        decrease. If this stops working the entire ORVL-005 + ORVL-018
        story breaks."""
        with redirect_stdout(io.StringIO()):
            r = phase_failure_mode()
        assert r["desc_fired"] is True
        assert r["asc_fired"] is False
        assert r["flat_fired"] is False
        assert r["harm_gate_fired"] is True


# ===========================================================================
# SECTION 2 — PASSED (each phase produces the expected shape)
# ===========================================================================

class TestANFDemoPassed:

    def test_passed_latency_scaling_returns_three_sizes(self):
        with redirect_stdout(io.StringIO()):
            r = phase_latency_scaling()
        assert len(r["sizes"]) == 3
        for row in r["sizes"]:
            assert row["p50_us"] > 0
            assert row["p99_us"] >= row["p50_us"]
            # Even on a slow CI runner the emulator should clear 1k decisions
            # per second comfortably — Python overhead alone allows ~10k/sec.
            assert row["throughput"] > 1_000

    def test_passed_energy_inversion_covers_all_six_classes(self):
        with redirect_stdout(io.StringIO()):
            r = phase_energy_inversion(per_class=20)
        assert set(r["cores_per_class"].keys()) == set(CORE_ACTIVATION.keys())

    def test_passed_audit_chain_zero_drift(self):
        with redirect_stdout(io.StringIO()):
            r = phase_audit_chain(n=200)
        assert r["drift"] == 0
        assert len(r["chain_root"]) == 64    # SHA-256 hex

    def test_passed_run_all_completes_under_10s(self):
        """The whole demo must run in under 10 seconds — it's
        screen-shareable in a meeting room."""
        import time
        t0 = time.perf_counter()
        with redirect_stdout(io.StringIO()):
            r = run_all()
        wall = time.perf_counter() - t0
        assert wall < 10.0, f"demo took {wall:.1f}s — too slow for a meeting"
        # Sanity: every phase produced output.
        assert {"latency", "energy", "cross_patent", "audit", "failure_mode"} \
               <= set(r.keys())


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestANFDemoInvariants:

    def test_invariant_cross_patent_total_matches_components(self):
        """The reported aggregate ANF call count must equal the sum of
        the per-emulator counts — no hidden double-counting."""
        with redirect_stdout(io.StringIO()):
            r = phase_cross_patent()
        c = r["counts"]
        # phone_harm contributes 0 by design; CPI is 0 (sibling pattern).
        expected = c["axm_verify"] + c["axm_route"] + c["phone_benign"]
        assert r["total"] == expected

    def test_invariant_audit_chain_is_deterministic(self):
        """Same inputs → same chain_root. Otherwise we can't reproduce
        the proof for an auditor."""
        with redirect_stdout(io.StringIO()):
            a = phase_audit_chain(n=50)
            b = phase_audit_chain(n=50)
        assert a["chain_root"] == b["chain_root"]
