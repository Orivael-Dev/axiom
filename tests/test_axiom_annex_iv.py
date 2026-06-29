# -*- coding: utf-8 -*-
"""
EU AI Act Annex IV generator tests — all 9 sections present, Axiom-known items
pre-filled, deployer placeholders marked, output signed + tamper-evident.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_annexiv"

from axiom_annex_iv import (
    build_annex_iv, render_markdown, sign, verify_markdown,
    AXIOM, PARTIAL, DEPLOYER,
)

NOW = "2026-06-28T00:00:00+00:00"
SYS = {"name": "TriageBot", "provider": "Acme Health", "version": "2.1",
       "intended_purpose": "clinical triage decision support", "model": "claude-opus-4-8"}


class TestAnnexIVStructure:

    def test_all_nine_sections_present(self):
        doc = build_annex_iv(SYS, now=NOW)
        ns = [s["n"] for s in doc["sections"]]
        assert ns == [1, 2, 3, 4, 5, 6, 7, 8, 9]

    def test_axiom_known_items_prefilled(self):
        doc = build_annex_iv(SYS, now=NOW)
        items = {it["ref"]: it for s in doc["sections"] for it in s["items"]}
        # Human oversight, cybersecurity, testing, lifecycle are Axiom-substantiated.
        assert items["2(e)"]["status"] == AXIOM and "HUMAN_REVIEW" in items["2(e)"]["body"]
        assert items["2(h)"]["status"] == AXIOM and "HMAC" in items["2(h)"]["body"]
        assert items["2(g)"]["status"] == AXIOM and "ledger" in items["2(g)"]["body"].lower()
        assert items["6"]["status"] == AXIOM

    def test_deployer_only_items_marked(self):
        doc = build_annex_iv(SYS, now=NOW)
        items = {it["ref"]: it for s in doc["sections"] for it in s["items"]}
        # Declaration of conformity + harmonised standards can't be Axiom-filled.
        assert items["8"]["status"] == DEPLOYER          # EU declaration of conformity
        assert items["7"]["status"] == DEPLOYER          # harmonised standards
        for ref in ("7", "8"):
            assert "[DEPLOYER]" in items[ref]["body"]

    def test_system_fields_flow_in(self):
        doc = build_annex_iv(SYS, now=NOW)
        assert doc["system"]["name"] == "TriageBot"
        assert "Acme Health" in doc["sections"][0]["items"][0]["body"]
        assert "clinical triage" in doc["sections"][0]["items"][0]["body"]

    def test_summary_counts_consistent(self):
        doc = build_annex_iv(SYS, now=NOW)
        sm = doc["summary"]
        total = sum(len(s["items"]) for s in doc["sections"])
        assert sm["total_items"] == total
        assert sm["axiom_filled"] + sm["partial"] + sm["deployer_required"] == total
        assert 0 <= sm["axiom_prefilled_pct"] <= 100


class TestAnnexIVCertIngest:

    def test_cert_fills_real_values(self):
        cert = {"agent": "Evaluator", "agent_version": "1.2", "axiom_version": "1.8.7",
                "conformance_level": "STANDARD",
                "steps": [{"name": "Benchmark Evidence", "status": "PASS"},
                          {"name": "Audit Trail", "status": "PASS"}]}
        # No name in system → cert agent name is used.
        doc = build_annex_iv({"provider": "Acme"}, cert=cert, now=NOW)
        assert doc["system"]["name"] == "Evaluator"
        assert doc["system"]["version"] == "1.2"
        assert doc["system"]["conformance_level"] == "STANDARD"
        items = {it["ref"]: it for s in doc["sections"] for it in s["items"]}
        assert "Benchmark Evidence (PASS)" in items["2(g)"]["body"]


class TestAnnexIVSigning:

    def test_render_is_signed_and_verifies(self):
        doc = build_annex_iv(SYS, now=NOW)
        md = render_markdown(doc)
        assert "<!-- ANNEX-IV-SIG" in md
        assert verify_markdown(md, doc) is True

    def test_tampered_markdown_fails_verify(self):
        doc = build_annex_iv(SYS, now=NOW)
        md = render_markdown(doc)
        # Alter the system after signing → recomputed signature differs.
        doc2 = build_annex_iv({**SYS, "provider": "Evil Corp"}, now=NOW)
        assert verify_markdown(md, doc2) is False

    def test_signing_key_not_in_output(self):
        import axiom_annex_iv as a
        md = render_markdown(build_annex_iv(SYS, now=NOW))
        assert a._KEY.hex() not in md
