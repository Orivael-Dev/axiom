# -*- coding: utf-8 -*-
"""
Regression for examples/vulnguard_investor_demo.py — pins the
ORVL-021 zero-day discovery story.

3 BLOCKED + 4 PASSED + 2 INVARIANTS, same layout as the rest of the
suite.

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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_vulnguard_investor_demo"

# Pre-import everything the demo touches, before any test redirects stdout
# (BUG-003: sys.stdout.reconfigure dies on StringIO).
import axiom_vulnguard          # noqa: F401

from examples.vulnguard_investor_demo import (
    phase_map_surfaces, phase_full_scan, phase_category_coverage,
    phase_boundary, phase_cannot_mutate, phase_audit_chain, run_all,
    SYSTEM,
)
from axiom_vulnguard import (
    ConstitutionalVulnGuard, ConstitutionalViolation, ProbeCategory,
    MAX_INTENSITY,
)


# ===========================================================================
# SECTION 1 — BLOCKED (the non-weaponization promises)
# ===========================================================================

class TestVulnGuardDemoBlocked:

    def test_blocked_boundary_refuses_intensity_one(self):
        """probe(intensity=1.0) MUST raise. If this ever regressed,
        the entire 'non-weaponization in code' pitch evaporates."""
        with redirect_stdout(io.StringIO()):
            surfaces = phase_map_surfaces()
        vg = ConstitutionalVulnGuard()
        with pytest.raises(ConstitutionalViolation):
            vg.probe(surfaces[0], ProbeCategory.NETWORK, 1.0)

    def test_blocked_max_intensity_cannot_mutate(self):
        """MAX_INTENSITY is in axiom_vulnguard._FROZEN. Reassigning it
        must raise AttributeError — the boundary is fused, not policy."""
        with redirect_stdout(io.StringIO()):
            result = phase_cannot_mutate()
        assert result["mutation_blocked"] is True

    def test_blocked_report_always_says_no_exploits(self):
        """The signed report MUST always carry no_exploits_generated=True
        and no_boundaries_crossed=True. These are claims we put in writing
        to customers; they cannot ever be False."""
        with redirect_stdout(io.StringIO()):
            r = run_all()
        report = r["report"]
        assert report["no_exploits_generated"] is True
        assert report["no_boundaries_crossed"] is True
        assert len(report["hmac_signature"]) == 64


# ===========================================================================
# SECTION 2 — PASSED (the architecture surfaces what it should)
# ===========================================================================

class TestVulnGuardDemoPassed:

    def test_passed_six_surfaces_mapped(self):
        with redirect_stdout(io.StringIO()):
            surfaces = phase_map_surfaces()
        assert len(surfaces) == len(SYSTEM)
        for s in surfaces:
            assert len(s.hmac_signature) == 64
            assert s.baseline_distance > 1.0   # realistic, not the default 1.0

    def test_passed_full_scan_finds_candidates(self):
        """With baselines in the 2.5-4.0 range every surface should
        produce at least some cliffs across the four categories."""
        with redirect_stdout(io.StringIO()):
            surfaces = phase_map_surfaces()
            cands = phase_full_scan(ConstitutionalVulnGuard(), surfaces)
        assert len(cands) > 0
        # Every candidate has a non-empty fix proposal — the whole point
        # of constitutional vuln discovery vs raw cliff reporting.
        for c in cands:
            assert c.fix_proposal
            assert len(c.hmac_signature) == 64

    def test_passed_all_four_categories_covered(self):
        """PRIVILEGE / DATA / NETWORK / ANCESTRY each produce candidates
        across the demo surface set — proves the architecture spans the
        four constitutional vulnerability classes, not just one."""
        with redirect_stdout(io.StringIO()):
            surfaces = phase_map_surfaces()
            per_cat = phase_category_coverage(ConstitutionalVulnGuard(),
                                                surfaces)
        for cat in ("PRIVILEGE", "DATA", "NETWORK", "ANCESTRY"):
            assert per_cat[cat] > 0, f"{cat} produced 0 candidates"

    def test_passed_run_all_completes_under_3s(self):
        """The whole demo must run in under 3 seconds — it's a meeting-
        room demo, not a CI job."""
        import time
        t0 = time.perf_counter()
        with redirect_stdout(io.StringIO()):
            r = run_all()
        wall = time.perf_counter() - t0
        assert wall < 3.0, f"demo took {wall:.1f}s — too slow"
        assert "audit" in r and r["audit"]["drift"] == 0


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestVulnGuardDemoInvariants:

    def test_invariant_audit_chain_zero_drift(self):
        """Every probe signature recomputes from the canonical payload.
        Any drift means signatures aren't reproducible — which means an
        auditor can't verify the trail. Zero drift is non-negotiable."""
        with redirect_stdout(io.StringIO()):
            surfaces = phase_map_surfaces()
            audit = phase_audit_chain(ConstitutionalVulnGuard(), surfaces)
        assert audit["drift"] == 0
        assert audit["total_probes"] > 0
        assert len(audit["chain_root"]) == 64

    def test_invariant_max_intensity_below_one(self):
        """MAX_INTENSITY (0.9) must stay below 1.0 — the exploit boundary.
        If anyone ever raised it past 1.0 the non-weaponization story
        would break since probe(intensity >= 1.0) raises but a constant
        of 1.0 would be unreachable."""
        assert MAX_INTENSITY < 1.0
        assert MAX_INTENSITY >= 0.5   # but high enough to do useful probing
