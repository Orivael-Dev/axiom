# -*- coding: utf-8 -*-
"""
Regression for examples/orivael_pitch_deck.py — locks in the five
investor-meeting talking points so a regression never silently kills
the demo before a screen-share.

3 BLOCKED + 4 PASSED + 2 INVARIANTS, same layout as the per-patent
investor demos.

BUG-003: UTF-8 output encoding
"""

import io
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_pitch_deck"

# Pre-import everything the deck touches lazily, BEFORE any test redirects
# stdout. Several modules call sys.stdout.reconfigure("utf-8") at import
# time (BUG-003 idiom) and that call dies if stdout has been swapped.
import axiom_anf_emulator           # noqa: F401
import axiom_axm                    # noqa: F401
import axiom_sovereign_phone        # noqa: F401
import axiom_cpi                    # noqa: F401
import axiom_vulnguard              # noqa: F401
import axiom_memory_engine          # noqa: F401
import examples.axm_pack_starter    # noqa: F401
import examples.anf_investor_demo   # noqa: F401
import examples.vulnguard_investor_demo  # noqa: F401

from examples.orivael_pitch_deck import (
    chapter_anf, chapter_axm, chapter_phone, chapter_cpi, chapter_vulnguard,
    run_all,
)


# ===========================================================================
# SECTION 1 — BLOCKED (the headline guarantees per chapter)
# ===========================================================================

class TestPitchDeckBlocked:

    def test_blocked_vulnguard_refuses_exploit_boundary(self):
        """Chapter 5's kicker: probe(intensity=1.0) raises. If this
        breaks the entire non-weaponization pitch dies on stage."""
        with redirect_stdout(io.StringIO()):
            r = chapter_vulnguard()
        assert r["boundary_crossed"] is False
        assert r["mutation_blocked"] is True

    def test_blocked_phone_scam_call_lands_l3(self):
        """Chapter 3's kicker: the Hello Operator trajectory must
        graduate through three blocks within one session_id."""
        with redirect_stdout(io.StringIO()):
            r = chapter_phone()
        assert r["blocks"] == 3

    def test_blocked_cpi_glass_clamps_to_fragile_ceiling(self):
        """Chapter 4's kicker: the glass pickup demo must clamp the
        applied torque to the FRAGILE class CANNOT_EXCEED limit."""
        from axiom_cpi import TORQUE_LIMIT_FRAGILE
        with redirect_stdout(io.StringIO()):
            r = chapter_cpi()
        assert r["glass_applied_nm"] == TORQUE_LIMIT_FRAGILE
        # And the stability trajectory must hit L4 emergency on
        # the below-floor frame.
        assert 4 in r["stability_levels"]


# ===========================================================================
# SECTION 2 — PASSED (the architecture surfaces what it should)
# ===========================================================================

class TestPitchDeckPassed:

    def test_passed_anf_chapter_returns_latency_and_ratio(self):
        with redirect_stdout(io.StringIO()):
            r = chapter_anf()
        assert r["latency"]["p50_us"] > 0
        # The energy-inversion ratio must remain visible (HARM < INFORM)
        assert r["ratio_inform_over_harm"] >= 2.0

    def test_passed_axm_chapter_lazy_loads_subset(self):
        """The AXM chapter must show the lazy-load discipline — not
        every delegate is loaded for a single task."""
        with redirect_stdout(io.StringIO()):
            r = chapter_axm()
        assert r["verified"] is True
        assert 0 < len(r["loaded"]) < r["delegates"]

    def test_passed_vulnguard_finds_candidates_across_surfaces(self):
        """6 surfaces should produce at least 24 candidates (4 categories ×
        a handful per surface). If this drops near zero the math drifted."""
        with redirect_stdout(io.StringIO()):
            r = chapter_vulnguard()
        assert r["surfaces"] == 6
        assert r["candidates"] >= 24

    def test_passed_run_all_completes_under_5s(self):
        """The whole pitch deck must run in under 5 seconds — it's a
        screen-share, not a CI job. If it ever crosses 5s, the
        meeting cadence breaks and we need to cut a chapter."""
        t0 = time.perf_counter()
        with redirect_stdout(io.StringIO()):
            r = run_all()
        wall = time.perf_counter() - t0
        assert wall < 5.0, f"pitch deck took {wall:.1f}s — too slow for a screen-share"
        # All five chapters must produce a return value.
        assert {"anf", "axm", "phone", "cpi", "vulnguard"} <= set(r.keys())


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestPitchDeckInvariants:

    def test_invariant_five_chapters_present(self):
        """The deck has exactly five chapter functions — if anyone adds
        a sixth without updating the runner, this catches it."""
        from examples import orivael_pitch_deck as deck
        chapters = [name for name in dir(deck) if name.startswith("chapter_")]
        assert set(chapters) == {
            "chapter_anf", "chapter_axm", "chapter_phone",
            "chapter_cpi", "chapter_vulnguard",
        }

    def test_invariant_no_chapter_leaves_global_state(self):
        """Each chapter must be safely re-runnable (tests AND the
        deck calls each one once, but a customer asking 'run that
        again' must not blow up on stale singletons)."""
        with redirect_stdout(io.StringIO()):
            # Run AXM twice — the second call uses a fresh tempdir,
            # so this checks we're not leaving any path-state behind.
            r1 = chapter_axm()
            r2 = chapter_axm()
        assert r1["verified"] is True
        assert r2["verified"] is True
