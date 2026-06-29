# -*- coding: utf-8 -*-
"""
EU AI Act Art. 9 / ISO 42001 risk-register generator tests — seeded risks across
the rights/safety/security taxonomy, each mapped to an Axiom treatment control,
residuals left for the deployer, signed + tamper-evident.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_riskreg"

from axiom_risk_register import (
    build_register, render_markdown, sign, verify_markdown, RISK_LIBRARY,
)

NOW = "2026-06-28T00:00:00+00:00"
SYS = {"name": "TriageBot", "intended_purpose": "clinical triage decision support"}


class TestRiskRegisterStructure:

    def test_every_seeded_risk_present(self):
        reg = build_register(SYS, now=NOW)
        assert len(reg["risks"]) == len(RISK_LIBRARY)
        ids = [r["id"] for r in reg["risks"]]
        assert ids == sorted(ids)                    # stable ordering
        assert len(set(ids)) == len(ids)             # unique

    def test_every_risk_has_a_treatment_control(self):
        reg = build_register(SYS, now=NOW)
        for r in reg["risks"]:
            assert r["treatment_controls"], f"{r['id']} has no treatment"

    def test_taxonomy_covers_rights_safety_security(self):
        reg = build_register(SYS, now=NOW)
        cats = {r["category"] for r in reg["risks"]}
        assert {"Fundamental rights", "Safety", "Security"} <= cats

    def test_residuals_are_deployer_pending(self):
        reg = build_register(SYS, now=NOW)
        for r in reg["risks"]:
            assert r["residual"]["likelihood"] == "[DEPLOYER]"
            assert r["residual"]["accepted_by"].startswith("[DEPLOYER]")

    def test_summary_counts(self):
        reg = build_register(SYS, now=NOW)
        sm = reg["summary"]
        assert sm["total_risks"] == len(RISK_LIBRARY)
        assert sm["with_axiom_treatment"] == len(RISK_LIBRARY)   # all seeded risks treated
        assert sm["high_inherent"] >= 1


class TestRiskRegisterMapping:

    def test_provenance_marking_treats_transparency_risk(self):
        reg = build_register(SYS, now=NOW)
        r04 = next(r for r in reg["risks"] if r["id"] == "R04")
        assert "Transparency" in r04["category"]
        assert any("provenance" in c for c in r04["treatment_controls"])

    def test_audit_gap_risk_maps_to_signed_ledgers(self):
        reg = build_register(SYS, now=NOW)
        r12 = next(r for r in reg["risks"] if r["id"] == "R12")
        assert any("ledger" in c.lower() for c in r12["treatment_controls"])


class TestRiskRegisterSigning:

    def test_render_signed_and_verifies(self):
        reg = build_register(SYS, now=NOW)
        md = render_markdown(reg)
        assert "<!-- RISK-REGISTER-SIG" in md
        assert verify_markdown(md, reg) is True

    def test_tampered_register_fails_verify(self):
        reg = build_register(SYS, now=NOW)
        md = render_markdown(reg)
        other = build_register({**SYS, "name": "OtherBot"}, now=NOW)
        assert verify_markdown(md, other) is False

    def test_signing_key_not_in_output(self):
        import axiom_risk_register as rr
        md = render_markdown(build_register(SYS, now=NOW))
        assert rr._KEY.hex() not in md
