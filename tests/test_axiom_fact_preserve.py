# -*- coding: utf-8 -*-
"""
Fact-preservation loop — one verified fact → many paraphrases, keep only the ones that
carry the Entity–Relation–Value relationship intact. Tests the deterministic validator
(reversal / negation / dropped-role rejection), generality across relations, and the
generate→verify→keep loop with a signed, tamper-evident kept set.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_fact_preserve"

from axiom_fact_preserve import (Fact, FactValidator, TruthPreservationLoop,
                                 template_generator)

FR = Fact("France", "has capital", "Paris")


class TestValidator:

    def test_correct_paraphrases_kept(self):
        v = FactValidator()
        for s in ["Paris is the capital of France.",
                  "France's capital is Paris.",
                  "The capital of France is Paris.",
                  "France has Paris as its capital."]:
            assert v.check(FR, s).ok, s

    def test_reversed_relationship_rejected(self):
        v = FactValidator()
        r = v.check(FR, "France is the capital of Paris.")
        assert not r.ok
        assert any("reversed" in x for x in r.reasons)

    def test_reversed_possessive_rejected(self):
        assert not FactValidator().check(FR, "Paris's capital is France.").ok

    def test_negation_rejected(self):
        r = FactValidator().check(FR, "Paris is not the capital of France.")
        assert not r.ok and any("negated" in x for x in r.reasons)

    def test_dropped_value_rejected(self):
        r = FactValidator().check(FR, "The capital of France is Berlin.")
        assert not r.ok and any("value" in x for x in r.reasons)

    def test_dropped_entity_rejected(self):
        assert not FactValidator().check(FR, "Paris is a large city.").ok

    def test_checks_trail_is_auditable(self):
        r = FactValidator().check(FR, "France is the capital of Paris.")
        assert r.checks["entity_present"] and r.checks["value_present"]
        assert r.checks["not_reversed"] is False       # the specific failed gate


class TestGenerality:

    def test_authorship_with_synonym_nouns(self):
        book = Fact("Nineteen Eighty-Four", "was written by", "George Orwell",
                    value_aliases=("Orwell",), rel_nouns=("author", "writer", "written"))
        v = FactValidator()
        assert v.check(book, "George Orwell wrote Nineteen Eighty-Four.").ok
        assert v.check(book, "George Orwell is the author of Nineteen Eighty-Four.").ok
        assert not v.check(book, "Nineteen Eighty-Four is the author of George Orwell.").ok

    def test_ceo_relation(self):
        ceo = Fact("Apple", "has CEO", "Tim Cook", value_aliases=("Cook",))
        v = FactValidator()
        assert v.check(ceo, "Tim Cook is the CEO of Apple.").ok
        assert not v.check(ceo, "Apple is the CEO of Tim Cook.").ok

    def test_aliases_count_as_present(self):
        f = Fact("United States", "has capital", "Washington",
                 entity_aliases=("the US", "USA"), value_aliases=("Washington, D.C.",))
        assert FactValidator().check(f, "The capital of the US is Washington.").ok


class TestSemanticFloor:

    def test_floor_keeps_on_topic(self):
        # a low floor should not reject faithful paraphrases
        v = FactValidator(semantic_floor=0.20)
        assert v.check(FR, "Paris is the capital of France.").ok


class TestLoop:

    def test_template_expansion_all_clean_and_signed(self):
        loop = TruthPreservationLoop()
        out = loop.expand(FR, n=8)
        assert len(out.kept) == 8 and out.rejected == []
        assert loop.verify(out) is True

    def test_adversarial_generator_only_clean_kept(self):
        # a generator that also emits mutations — the loop must discard exactly those
        def adversarial(fact, n):
            return [
                "Paris is the capital of France.",           # keep
                "France's capital is Paris.",                # keep
                "France is the capital of Paris.",           # drop: reversed
                "Paris is not the capital of France.",       # drop: negated
                "The capital of France is Berlin.",          # drop: wrong value
            ]
        loop = TruthPreservationLoop()
        out = loop.expand(FR, n=5, generator=adversarial)
        assert set(out.kept) == {"Paris is the capital of France.", "France's capital is Paris."}
        assert len(out.rejected) == 3
        assert loop.verify(out) is True

    def test_training_examples_shape(self):
        out = TruthPreservationLoop().expand(FR, n=3)
        rows = out.training_examples()
        assert rows and all(r["label"] == "truth_preserving" for r in rows)
        assert all(r["entity"] == "France" and r["value"] == "Paris" for r in rows)

    def test_tampered_kept_fails_verify(self):
        loop = TruthPreservationLoop()
        out = loop.expand(FR, n=4)
        out.kept.append("France is the capital of Paris.")   # sneak in a mutation
        assert loop.verify(out) is False

    def test_dedup(self):
        def dupes(fact, n):
            return ["Paris is the capital of France."] * 4
        out = TruthPreservationLoop().expand(FR, n=4, generator=dupes)
        assert len(out.kept) == 1


def test_template_generator_count():
    assert len(template_generator(FR, 3)) == 3
    assert len(template_generator(FR, 20)) == 20        # wraps to fill n


def test_cli_smoke(capsys):
    from axiom_fact_preserve import _main
    assert _main(["--entity", "France", "--relation", "has capital", "--value", "Paris"]) == 0
    out = capsys.readouterr().out
    assert "KEPT" in out and "verify=True" in out
