# -*- coding: utf-8 -*-
"""
Axiom Triad — Child / Mom / Best Friend with the Bounce loop. The Mom is the Layer-4
logic gate (FactGuard + rules); she reflects a hard boundary instead of rewriting, and
the Child recalculates. Tests the gate, the bounce/recovery, the give-up path, and the
signed trace.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_triad"

from axiom_fact_preserve import Fact
from axiom_triad import MomGate, TriadLoop, _default_best_friend

FR = Fact("France", "has capital", "Paris")


class TestMomGate:

    def test_clean_output_passes(self):
        assert MomGate(facts=[FR]).review("Paris is the capital of France.").ok

    def test_reversed_is_bounced_with_boundary(self):
        v = MomGate(facts=[FR]).review("France is the capital of Paris.")
        assert not v.ok
        assert any("reversed" in b for b in v.boundaries)

    def test_wrong_value_bounced(self):
        v = MomGate(facts=[FR]).review("The capital of France is Berlin.")
        assert not v.ok and any("wrong_value" in b for b in v.boundaries)

    def test_extra_rule_composes(self):
        # a second Layer-4 rule (e.g. a safety gate) bounces a banned token
        def no_secrets(text):
            return ("password" not in text.lower(),
                    "leaks a secret — try another way.")
        mom = MomGate(facts=[FR]).add_rule(no_secrets)
        assert mom.review("Paris is the capital of France.").ok
        v = mom.review("Paris is the capital of France; the password is hunter2.")
        assert not v.ok and any("secret" in b for b in v.boundaries)


class TestBounceLoop:

    def test_child_recovers_after_bounce(self):
        drafts = ["France is the capital of Paris.",          # broken → bounce
                  "Paris is the capital of France."]           # valid → accept
        child = lambda task, bnds, i: drafts[min(i, len(drafts) - 1)]
        res = TriadLoop(child=child, mom=MomGate(facts=[FR])).run("state the capital")
        assert res.accepted and res.output == "Paris is the capital of France."
        assert len(res.bounces) == 1
        assert res.bounces[0].boundaries    # Mom reflected a boundary

    def test_boundaries_are_fed_back_to_child(self):
        seen = {}
        def child(task, boundaries, i):
            seen[i] = list(boundaries)                          # capture what Child received
            return "France is the capital of Paris." if i == 0 else "Paris is the capital of France."
        TriadLoop(child=child, mom=MomGate(facts=[FR])).run("go")
        assert seen[0] == []                                    # first attempt: no feedback yet
        assert seen[1] and "reversed" in seen[1][0]             # recalculation sees the boundary

    def test_never_recovers_gives_up(self):
        child = lambda task, bnds, i: "France is the capital of Paris."   # always broken
        res = TriadLoop(child=child, mom=MomGate(facts=[FR]), max_bounces=3).run("go")
        assert not res.accepted and res.output == ""
        assert len(res.bounces) == 4                            # max_bounces + 1 attempts

    def test_clean_first_try_no_bounces(self):
        child = lambda task, bnds, i: "Paris is the capital of France."
        res = TriadLoop(child=child, mom=MomGate(facts=[FR])).run("go")
        assert res.accepted and res.bounces == [] and res.attempts == 1


class TestEvaluatorAndIntegrity:

    def test_best_friend_scores_accepted(self):
        child = lambda task, bnds, i: "Paris, the City of Light, is the capital of France."
        res = TriadLoop(child=child, mom=MomGate(facts=[FR])).run("go")
        assert res.score > 0.0

    def test_custom_best_friend_used(self):
        child = lambda task, bnds, i: "Paris is the capital of France."
        res = TriadLoop(child=child, mom=MomGate(facts=[FR]),
                        best_friend=lambda c: 0.99).run("go")
        assert res.score == pytest.approx(0.99)

    def test_signed_trace_verifies_and_tamper_fails(self):
        child = lambda task, bnds, i: "Paris is the capital of France."
        loop = TriadLoop(child=child, mom=MomGate(facts=[FR]))
        res = loop.run("go")
        assert loop.verify(res) is True
        res.output = "France is the capital of Paris."          # tamper
        assert loop.verify(res) is False


class TestRealChildAndEvaluator:

    def test_llm_child_feeds_boundaries_back(self):
        from axiom_triad import llm_child
        seen = {}
        def call(prompt):
            seen["prompt"] = prompt
            return "France is the capital of Paris." if "BOUNCED" not in prompt \
                   else "Paris is the capital of France."
        child = llm_child(call)
        # first attempt: no boundaries
        child("say the capital", [], 0)
        assert "BOUNCED" not in seen["prompt"]
        # after a bounce: the boundary is injected into the prompt
        out = child("say the capital", ["[reversed] keep the fact intact."], 1)
        assert "BOUNCED" in seen["prompt"] and "reversed" in seen["prompt"]
        assert out == "Paris is the capital of France."

    def test_llm_child_recovers_in_loop(self):
        from axiom_triad import llm_child
        call = lambda p: ("Paris is the capital of France." if "BOUNCED" in p
                          else "France is the capital of Paris.")
        res = TriadLoop(child=llm_child(call), mom=MomGate(facts=[FR])).run("go")
        assert res.accepted and len(res.bounces) == 1

    def test_anthropic_child_with_stub_client(self):
        from axiom_triad import anthropic_child
        class _Msg:
            content = [type("B", (), {"type": "text", "text": "Paris is the capital of France."})()]
        class _Client:
            class messages:
                @staticmethod
                def create(**kw): return _Msg()
        res = TriadLoop(child=anthropic_child(client=_Client()), mom=MomGate(facts=[FR])).run("go")
        assert res.accepted

    def test_crl_best_friend_scores(self):
        from axiom_triad import crl_best_friend
        bf = crl_best_friend()
        child = lambda task, bnds, i: "Paris is the capital of France."
        res = TriadLoop(child=child, mom=MomGate(facts=[FR]), best_friend=bf).run("capital?")
        assert isinstance(res.score, float)


def test_default_best_friend_rewards_variety():
    assert _default_best_friend("") == 0.0
    assert _default_best_friend("a b c d e") > _default_best_friend("a a a a a")


def test_cli_smoke(capsys):
    from axiom_triad import _main
    assert _main([]) == 0
    out = capsys.readouterr().out
    assert "Mom:" in out and "accepted" in out and "verify=True" in out
