"""Tests for the buoyancy re-rank in legal_rag_bench.

Buoyancy generalises role-boost's binary chapter penalty to a continuous
distance-from-actor weight: offence-dominated chunks are heavy and sink,
actor-aligned chunks are light and float. Validated against the reported
crime-scenario-distraction examples and the no-op safety guard.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "legal"))

from legal_rag_bench import (  # noqa: E402
    _actor_heaviness,
    _extract_roles,
    buoyancy_rerank,
    collapse_to_parents,
    OFFENCE_TERMS,
)


# ── heaviness ─────────────────────────────────────────────────────────────────────

class TestActorHeaviness:
    def test_pure_offence_is_heavy(self):
        roles = {"juror"}
        h = _actor_heaviness("burglary robbery arson theft murder", roles)
        assert h == pytest.approx(1.0)

    def test_pure_actor_is_light(self):
        roles = {"juror"}
        h = _actor_heaviness("the jury and the jurors in the jury room", roles)
        assert h == pytest.approx(-1.0)

    def test_balanced_near_zero(self):
        roles = {"juror"}
        # one offence term, one actor term
        h = _actor_heaviness("the jury considered the burglary", roles)
        assert h == pytest.approx(0.0)

    def test_no_role_is_zero(self):
        assert _actor_heaviness("burglary murder arson", set()) == 0.0

    def test_no_terms_is_zero(self):
        # neither actor nor offence vocabulary present
        assert _actor_heaviness("the weather was pleasant", {"juror"}) == 0.0

    def test_empty_text(self):
        assert _actor_heaviness("", {"juror"}) == 0.0


# ── buoyancy re-rank ───────────────────────────────────────────────────────────────

class TestBuoyancyRerank:
    def test_noop_without_role(self):
        ids = ["7.5.8-c1-s1", "4.12.2-c1-s1"]
        assert buoyancy_rerank(ids, "elements of carjacking", {}) == ids

    def test_offence_chunk_sinks_below_actor_chunk(self):
        # offence chunk ranked first by RRF; actor chunk second → buoyancy flips
        query = "Harry is a juror examining a lock in the jury room"
        pool = ["7.5.4-c4-s3", "1.5-c5-s1"]
        ptext = {
            "7.5.4-c4-s3": "Burglary. The accused committed burglary and theft "
                           "of goods during a robbery.",
            "1.5-c5-s1":   "The jury must decide solely on the evidence; jurors "
                           "must not conduct experiments in the jury room.",
        }
        out = buoyancy_rerank(pool, query, ptext, gravity=1.5)
        assert out[0] == "1.5-c5-s1"

    def test_passing_offence_mention_stays_buoyant(self):
        # a chunk centred on the actor but mentioning the offence once should
        # NOT sink as hard as a chunk that is all offence vocabulary
        query = "juror conduct in the jury room"
        mostly_actor = "jury jurors jury room deliberation verdict burglary"  # 5 actor,1 offence
        all_offence  = "burglary robbery theft arson"                          # 0 actor,4 offence
        h_actor = _actor_heaviness(mostly_actor, {"juror"})
        h_off   = _actor_heaviness(all_offence, {"juror"})
        assert h_actor < h_off          # actor-centred is lighter
        assert h_actor < 0 < h_off      # floats vs sinks

    def test_gravity_scales_effect(self):
        # higher gravity pushes a heavy chunk further down (lower adjusted score)
        query = "juror jury room"
        pool = ["7.5.4-c4-s3", "1.5-c5-s1"]
        ptext = {"7.5.4-c4-s3": "burglary robbery theft",
                 "1.5-c5-s1":   "jury jurors deliberation"}
        # both gravities should rank the actor chunk first; check it's stable
        for g in (0.5, 1.5, 3.0):
            out = buoyancy_rerank(pool, query, ptext, gravity=g)
            assert out[0] == "1.5-c5-s1"


# ── end-to-end with parent collapse ───────────────────────────────────────────────

class TestBuoyancyThenCollapse:
    def test_q40_alibi_section_floats(self):
        query = "Alex had dinner across town — an alibi for the robbery"
        pool = ["7.5.3.2-c7-s1", "7.5.3-c2-s1", "4.25-c2-s2"]
        ptext = {
            "7.5.3.2-c7-s1": "Robbery. The accused stole property by force, theft.",
            "7.5.3-c2-s1":   "The accused committed robbery and theft.",
            "4.25-c2-s2":    "Directions about alibi evidence: the accused was "
                             "elsewhere at the time, an alibi.",
        }
        ranked = buoyancy_rerank(pool, query, ptext, gravity=1.5)
        parents = collapse_to_parents(ranked, final_k=10)
        assert parents[0] == "4.25"


# ── lexicon sanity ───────────────────────────────────────────────────────────────

class TestOffenceLexicon:
    def test_core_offences_present(self):
        joined = " ".join(OFFENCE_TERMS)
        for term in ("murder", "arson", "burglary", "robbery", "traffick"):
            assert term in joined
