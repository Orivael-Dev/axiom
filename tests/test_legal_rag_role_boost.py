"""Tests for the actor-role boost layer in legal_rag_bench.

The crime-scenario-distraction misses (Group A, ~20 of 31) share a structure:
the query narrates an offence but the answer is a procedure/evidence section
keyed on the ACTOR on the trial stage (juror, child witness, alibi, view).
These tests validate role extraction, offence-chapter detection, and that the
role-boost re-rank lifts the procedural section above the offence section —
checked against the five real examples the user reported (Q2/Q3/Q7/Q15/Q40).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "legal"))

from legal_rag_bench import (  # noqa: E402
    _extract_roles,
    _chapter_of,
    _is_offence_chunk,
    role_boost_rerank,
    collapse_to_parents,
    ACTOR_ROLES,
)


# ── role extraction ─────────────────────────────────────────────────────────────

class TestExtractRoles:
    def test_juror(self):
        assert "juror" in _extract_roles("Harry is serving as a juror in a trial")

    def test_jury_variant(self):
        assert "juror" in _extract_roles("should the jury be told the sentence")

    def test_view(self):
        assert "view" in _extract_roles("the jury wants a view of the backyard")

    def test_child_witness(self):
        roles = _extract_roles("Paul is 12 years old and is a witness")
        assert "child_witness" in roles
        assert "witness" in roles

    def test_alibi(self):
        assert "alibi" in _extract_roles("he says he has an alibi for that night")

    def test_no_false_positive_on_view_inside_word(self):
        # "interview"/"review" must NOT trip the \bview\b boundary
        assert "view" not in _extract_roles("the interview and the review process")

    def test_empty_text(self):
        assert _extract_roles("") == set()

    def test_pure_offence_query_has_no_role(self):
        # an offence-element question with no trial-actor → no procedural role
        assert _extract_roles("what are the elements of carjacking") == set()


# ── chapter / offence detection ─────────────────────────────────────────────────

class TestChapter:
    def test_chapter_of_offence(self):
        assert _chapter_of("7.5.10-c2-s1") == "7"

    def test_chapter_of_procedure(self):
        assert _chapter_of("1.5-c5-s1") == "1"

    def test_offence_chunk_true(self):
        assert _is_offence_chunk("7.2.1A-c1-s1") is True
        assert _is_offence_chunk("8.15-c2-s2") is True

    def test_offence_chunk_false_for_procedure(self):
        assert _is_offence_chunk("1.5-c6-s1") is False
        assert _is_offence_chunk("4.25-c2-s2") is False


# ── role-boost re-rank ───────────────────────────────────────────────────────────

class TestRoleBoostRerank:
    def test_noop_without_query_role(self):
        # offence-element query → no procedural role → order unchanged
        ids = ["7.5.8-c1-s1", "7.5.9-c1-s1", "4.12.2-c1-s1"]
        out = role_boost_rerank(ids, "elements of carjacking", {})
        assert out == ids

    def test_q2_juror_lifts_procedure_over_offence(self):
        # Q2: juror using locksmith expertise. Gold 1.5 (juror conduct);
        # distractors 7.5.10 / 7.5.4 (offence). RRF ranked offence first.
        query = "Harry is a juror and a locksmith; he examines a lock in the jury room"
        pool = ["7.5.10-c2-s1", "7.5.4-c4-s3", "1.5-c5-s1"]
        ptext = {
            "7.5.10-c2-s1": "Handling of goods. The accused handled stolen goods.",
            "7.5.4-c4-s3":  "Burglary. Intention to commit an offence.",
            "1.5-c5-s1":    "The jury must decide solely on the evidence. Jurors "
                            "must not conduct their own experiments.",
        }
        out = role_boost_rerank(pool, query, ptext, boost=1.0, penalty=0.5)
        # the juror-conduct section must now outrank both offence sections
        assert out[0] == "1.5-c5-s1"

    def test_q7_view_lifts_over_drug_offence(self):
        query = "the jury wants a view of the backyard where plants grew"
        pool = ["7.6.2-c2-s1", "7.6.2.6-c1-s2", "2.1-c1-s1"]
        ptext = {
            "7.6.2-c2-s1":   "Cultivation of narcotic plants. Elements of the offence.",
            "7.6.2.6-c1-s2": "Checklist: cultivation of a narcotic plant.",
            "2.1-c1-s1":     "Views. The court may order a demonstration, experiment "
                             "or inspection of premises by the jury.",
        }
        out = role_boost_rerank(pool, query, ptext, boost=1.0, penalty=0.5)
        assert out[0] == "2.1-c1-s1"

    def test_q40_alibi_lifts_over_robbery(self):
        query = "Alex had dinner with friends across town — an alibi for the robbery"
        pool = ["7.5.3.2-c7-s1", "7.5.3-c2-s1", "4.25-c2-s2"]
        ptext = {
            "7.5.3.2-c7-s1": "Robbery. The accused stole property using force.",
            "7.5.3-c2-s1":   "The accused committed robbery.",
            "4.25-c2-s2":    "Directions about alibi evidence. The accused adduced "
                             "evidence that he was elsewhere at the time.",
        }
        out = role_boost_rerank(pool, query, ptext, boost=1.0, penalty=0.5)
        assert out[0] == "4.25-c2-s2"

    def test_offence_query_not_demoted(self):
        # When the query has NO procedural role, an offence-element answer must
        # stay on top (guards against over-demotion of legit offence questions).
        query = "what must the prosecution prove for aggravated carjacking"
        pool = ["7.5.9-c1-s1", "4.12.2-c1-s1"]
        ptext = {"7.5.9-c1-s1": "Aggravated carjacking elements.",
                 "4.12.2-c1-s1": "Photographic identification charge."}
        out = role_boost_rerank(pool, query, ptext)
        assert out == pool  # unchanged (no-op)


# ── end-to-end: re-rank then collapse to parents ─────────────────────────────────

class TestRoleBoostThenCollapse:
    def test_q15_child_witness_section_recovered(self):
        # Q15: 12-year-old witness. Gold 4.2 (child witnesses); offence distractors.
        query = "Paul is 12 years old and a witness in a riot trial"
        pool = ["7.3.12-c3-s2", "7.4.18-c2-s1", "4.2-c3-s1", "4.2-c1-s1"]
        ptext = {
            "7.3.12-c3-s2": "Statutory defences and exclusions for the offence.",
            "7.4.18-c2-s1": "Taking away a child. The accused took a person away.",
            "4.2-c3-s1":    "Prohibited statements about child witnesses and their "
                            "reliability as children give evidence.",
            "4.2-c1-s1":    "Child witnesses. Special procedures for children.",
        }
        ranked = role_boost_rerank(query=query, merged_ids=pool,
                                   passage_text=ptext, boost=1.0, penalty=0.5)
        parents = collapse_to_parents(ranked, final_k=10)
        # the child-witness section (4.2) must be the top parent
        assert parents[0] == "4.2"


# ── lexicon sanity ───────────────────────────────────────────────────────────────

class TestLexicon:
    def test_core_roles_present(self):
        for tag in ("juror", "view", "child_witness", "alibi", "witness"):
            assert tag in ACTOR_ROLES

    def test_all_terms_nonempty(self):
        for tag, terms in ACTOR_ROLES.items():
            assert terms, f"{tag} has no terms"
            assert all(isinstance(t, str) and t for t in terms)
