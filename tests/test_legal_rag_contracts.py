"""Tests for the contracts-domain genre-aware buoyancy layer in legal_rag_bench.

Contracts share the same "actors on a stage" distraction problem as legal, but
the routing signal is the CONTRACT GENRE (NDA / employment / lease / ...) rather
than the offence chapter.  A query about tenant rights lands in lease-genre
sections; off-genre vocabulary (loan terms, IP licences) should sink.

Validated against synthetic contract-scenario examples that mirror the crime-
scenario-distraction pattern — genre-off chunks first by RRF, genre-on second —
and against the no-op safety guard (genre-neutral queries are unchanged).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "legal"))

from legal_rag_bench import (  # noqa: E402
    _extract_contract_genre,
    _extract_contract_roles,
    _genre_heaviness,
    contract_buoyancy_rerank,
    collapse_to_parents,
    CONTRACT_GENRES,
    CONTRACT_ACTOR_ROLES,
)


# ── genre detection ────────────────────────────────────────────────────────────

class TestExtractContractGenre:
    def test_detects_lease(self):
        assert _extract_contract_genre(
            "the tenant failed to pay rent and the landlord seeks possession"
        ) == "lease"

    def test_detects_employment(self):
        assert _extract_contract_genre(
            "the employee was dismissed without notice during probation"
        ) == "employment"

    def test_detects_nda(self):
        assert _extract_contract_genre(
            "the parties must keep all proprietary information confidential"
        ) == "nda"

    def test_detects_loan(self):
        assert _extract_contract_genre(
            "the borrower defaulted on the principal repayment to the lender"
        ) == "loan"

    def test_detects_license(self):
        assert _extract_contract_genre(
            "the licensor grants the licensee a royalty-bearing field of use"
        ) == "license"

    def test_detects_service(self):
        assert _extract_contract_genre(
            "deliverables must meet the service level agreement milestones"
        ) == "service"

    def test_no_genre_returns_none(self):
        assert _extract_contract_genre("what are liquidated damages?") is None

    def test_empty_text_returns_none(self):
        assert _extract_contract_genre("") is None


# ── genre heaviness ────────────────────────────────────────────────────────────

class TestGenreHeaviness:
    def test_same_genre_is_light(self):
        text = "the tenant pays rent to the landlord for the leased premises"
        h = _genre_heaviness(text, "lease")
        assert h < 0, f"same-genre chunk should float (negative heaviness), got {h}"

    def test_different_genre_is_heavy(self):
        text = "the borrower must repay the principal plus interest to the lender"
        h = _genre_heaviness(text, "lease")  # loan text vs lease query
        assert h > 0, f"off-genre chunk should sink (positive heaviness), got {h}"

    def test_no_genre_query_is_zero(self):
        text = "the party in default shall pay liquidated damages"
        assert _genre_heaviness(text, None) == 0.0

    def test_no_genre_vocabulary_in_text_is_zero(self):
        assert _genre_heaviness("the weather was pleasant today", "lease") == 0.0

    def test_heaviness_bounded(self):
        # heaviness must lie in [-1, +1]
        text = "rent landlord tenant lease lessee lessor premises tenancy sublease"
        h = _genre_heaviness(text, "lease")
        assert -1.0 <= h <= 1.0


# ── contract buoyancy re-rank ─────────────────────────────────────────────────

class TestContractBuoyancyRerank:
    def test_noop_when_no_genre_detected(self):
        ids = ["1.2-c1-s1", "3.4-c2-s1"]
        assert contract_buoyancy_rerank(ids, "what are liquidated damages?", {}) == ids

    def test_lease_chunk_floats_over_loan_chunk(self):
        # loan chunk ranked first by RRF; lease chunk ranked second → genre flip
        query = "the tenant wants to terminate the lease early due to landlord breach"
        pool = ["7.1-c2-s1", "4.3-c1-s1"]
        ptext = {
            "7.1-c2-s1": "The borrower shall repay the principal to the lender with "
                         "interest at the rate specified in the credit facility.",
            "4.3-c1-s1": "The tenant may terminate the lease if the landlord fails to "
                         "maintain the premises in a fit state for occupation.",
        }
        out = contract_buoyancy_rerank(pool, query, ptext, gravity=1.5)
        assert out[0] == "4.3-c1-s1", f"lease chunk should float first; got {out}"

    def test_employment_chunk_floats_over_nda_chunk(self):
        query = "the employee was dismissed without notice during probation"
        pool = ["2.5-c3-s1", "6.1-c2-s1"]
        ptext = {
            "2.5-c3-s1": "The parties shall maintain all proprietary information "
                         "as confidential; non-disclosure obligations survive termination.",
            "6.1-c2-s1": "An employer may terminate employment during the probation "
                         "period without providing the standard notice entitlement.",
        }
        out = contract_buoyancy_rerank(pool, query, ptext, gravity=1.5)
        assert out[0] == "6.1-c2-s1"

    def test_gravity_zero_preserves_order(self):
        # gravity=0 → exp(-0*h)=1 for all h → reciprocal rank order preserved
        query = "the tenant shall pay rent to the landlord"
        pool = ["3.1-c1-s1", "5.2-c2-s1"]
        ptext = {
            "3.1-c1-s1": "borrower lender principal interest credit",
            "5.2-c2-s1": "tenant landlord rent lease premises",
        }
        # pool order unchanged at gravity=0
        assert contract_buoyancy_rerank(pool, query, ptext, gravity=0.0) == pool

    def test_higher_gravity_separates_genres_more(self):
        # Both gravities should rank the same-genre chunk first; stability check
        query = "the licensee pays royalty fees to the licensor"
        pool = ["9.9-c1-s1", "2.2-c1-s1"]
        ptext = {
            "9.9-c1-s1": "the employee salary wages employer dismissal",
            "2.2-c1-s1": "licensor licensee royalty field of use sublicense",
        }
        for g in (0.5, 1.5, 3.0):
            out = contract_buoyancy_rerank(pool, query, ptext, gravity=g)
            assert out[0] == "2.2-c1-s1", f"gravity={g}: license chunk should lead"


# ── end-to-end with parent collapse ───────────────────────────────────────────

class TestContractBuoyancyThenCollapse:
    def test_lease_section_floats_above_loan_section(self):
        query = "tenant rights when landlord fails to repair the leased premises"
        pool = ["7.2-c1-s1", "7.3-c1-s1", "4.1-c2-s1"]
        ptext = {
            "7.2-c1-s1": "The borrower shall repay principal and interest; "
                         "default triggers acceleration of the credit facility.",
            "7.3-c1-s1": "Security over collateral must be registered; the lender "
                         "may enforce the promissory note on maturity.",
            "4.1-c2-s1": "The landlord is obliged to repair and maintain the "
                         "premises; the tenant may withhold rent for serious breach.",
        }
        ranked = contract_buoyancy_rerank(pool, query, ptext, gravity=1.5)
        parents = collapse_to_parents(ranked, final_k=10)
        assert parents[0] == "4.1"


# ── lexicon sanity ─────────────────────────────────────────────────────────────

class TestContractLexicons:
    def test_core_genres_present(self):
        for genre in ("nda", "service", "employment", "lease", "purchase",
                      "loan", "license", "joint_venture"):
            assert genre in CONTRACT_GENRES, f"missing genre: {genre}"

    def test_all_genres_nonempty(self):
        for genre, terms in CONTRACT_GENRES.items():
            assert terms, f"{genre} has no terms"
            assert all(isinstance(t, str) and t for t in terms)

    def test_core_roles_present(self):
        for role in ("party_offering", "party_receiving", "guarantor",
                     "breach_party", "arbitrator"):
            assert role in CONTRACT_ACTOR_ROLES, f"missing role: {role}"

    def test_all_roles_nonempty(self):
        for role, terms in CONTRACT_ACTOR_ROLES.items():
            assert terms, f"{role} has no terms"
