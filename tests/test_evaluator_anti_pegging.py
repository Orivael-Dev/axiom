"""Tests for the Evaluator anti-pegging hardening.

Three protections layered together:
  1. detect_score_pegging(scores) flags N-in-a-row identical scores
     so the CLI / UI loops can abort instead of saving an inflated
     Worker prompt.
  2. EvaluatorAgent.score() demotes any >=8.0 score returned without
     supporting evidence_quotes to 7.5, with a `_inflated_score_demoted`
     audit trail in the response.
  3. format_for_prompt() injects the strict scoring bands into the
     rubric body so the Evaluator sees them in BOTH the system prompt
     and the per-call rubric.

Repro of the original failure:
  prompts/299885cde39a9bc4/worker.json had 13 iterations all scoring
  exactly 9.0. detect_score_pegging([9.0]*13) must catch that pattern;
  the CLI loop must abort instead of promoting the Worker prompt.
"""
from __future__ import annotations

import pytest


# ─── #1 detect_score_pegging ───────────────────────────────────────────

class TestPeggingDetector:
    def test_thirteen_identical_nines_flagged(self):
        """The original failure case from prompts/299885cde39a9bc4."""
        from axiom_constitutional.evolution import detect_score_pegging
        peg = detect_score_pegging([9.0] * 13)
        assert peg is not None
        assert peg["pegged_at"] == 9.0
        assert peg["window"] == 3  # default window
        assert peg["tail_scores"] == [9.0, 9.0, 9.0]

    def test_three_in_a_row_minimum(self):
        """Default window=3 — two identical scores shouldn't trip it."""
        from axiom_constitutional.evolution import detect_score_pegging
        assert detect_score_pegging([8.5, 8.5]) is None
        assert detect_score_pegging([8.5, 8.5, 8.5]) is not None

    def test_near_match_within_epsilon_flagged(self):
        """epsilon=0.1 default catches near-pegging too (9.0, 9.05, 9.0)
        — models sometimes wiggle the score slightly to look 'evolving'."""
        from axiom_constitutional.evolution import detect_score_pegging
        peg = detect_score_pegging([9.0, 9.05, 9.0])
        assert peg is not None
        assert peg["pegged_at"] == pytest.approx(9.02, abs=0.01)

    def test_real_evolution_not_flagged(self):
        """Real evolution drifts — e.g. fe75010ce20136a0's tail showed
        scores like [9.5, 9.5, 9.3, 9.0, 9.5]. The 0.5 spread on the
        trailing window prevents a false pegging flag."""
        from axiom_constitutional.evolution import detect_score_pegging
        # last 3 = [9.3, 9.0, 9.5] — spread 0.5, > epsilon (0.1)
        assert detect_score_pegging([9.5, 9.5, 9.3, 9.0, 9.5]) is None

    def test_climbing_scores_not_flagged(self):
        """Genuine improvement — score climbs — shouldn't trip the detector."""
        from axiom_constitutional.evolution import detect_score_pegging
        assert detect_score_pegging([6.0, 7.0, 8.0, 9.0]) is None

    def test_window_below_threshold_returns_none(self):
        from axiom_constitutional.evolution import detect_score_pegging
        assert detect_score_pegging([]) is None
        assert detect_score_pegging([9.0]) is None
        assert detect_score_pegging([9.0, 9.0]) is None  # default window=3

    def test_custom_window_and_epsilon(self):
        """Tightening epsilon to 0.0 should require exact equality."""
        from axiom_constitutional.evolution import detect_score_pegging
        assert detect_score_pegging([9.0, 9.05, 9.0], epsilon=0.0) is None
        assert detect_score_pegging([9.0, 9.0, 9.0], epsilon=0.0) is not None


# ─── #2 Evaluator evidence-requirement demotion ────────────────────────

class TestEvidenceRequirement:
    """The Evaluator.score() post-check demotes any inflated score
    (>=8.0 with no evidence_quotes) to 7.5. We patch _call_json so
    these tests don't need a live LLM."""

    def _agent(self, monkeypatch, fake_response):
        monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
        from axiom_constitutional.agents.evaluator import EvaluatorAgent
        agent = EvaluatorAgent(task_description="test task")
        monkeypatch.setattr(
            agent, "_call_json", lambda *_a, **_kw: dict(fake_response)
        )
        return agent

    def _minimal_rubric(self):
        return {
            "task_summary": "test",
            "dimensions": [{"name": "Correctness", "weight": 1.0,
                             "description": "is it right"}],
            "scoring_guide": "0-10",
            "anti_patterns": [],
        }

    def test_high_score_without_evidence_is_demoted(self, monkeypatch):
        """The pegging failure mode: model returns 9.0 with no quotes."""
        agent = self._agent(monkeypatch, {
            "score": 9.0,
            "reasoning": "Looks great.",
            "improvements": [],
            "dimension_scores": {"Correctness": 9.0},
            "evidence_quotes": [],
        })
        result = agent.score(task="t", output="o", rubric=self._minimal_rubric())
        assert result["score"] == 7.5
        assert "_inflated_score_demoted" in result
        assert result["_inflated_score_demoted"]["original_score"] == 9.0
        assert "DEMOTED" in result["reasoning"]

    def test_high_score_without_evidence_quotes_key_is_demoted(self, monkeypatch):
        """Missing key (not just empty list) also counts as no evidence."""
        agent = self._agent(monkeypatch, {
            "score": 8.5,
            "reasoning": "Fine.",
            "improvements": [],
            "dimension_scores": {"Correctness": 8.5},
        })
        result = agent.score(task="t", output="o", rubric=self._minimal_rubric())
        assert result["score"] == 7.5
        assert "_inflated_score_demoted" in result

    def test_high_score_with_evidence_passes_through(self, monkeypatch):
        """Real evidence => no demotion."""
        agent = self._agent(monkeypatch, {
            "score": 9.0,
            "reasoning": "Solid — see quotes.",
            "improvements": [],
            "dimension_scores": {"Correctness": 9.0},
            "evidence_quotes": ["the output correctly states X"],
        })
        result = agent.score(task="t", output="o", rubric=self._minimal_rubric())
        assert result["score"] == 9.0
        assert "_inflated_score_demoted" not in result

    def test_low_score_with_no_evidence_passes_through(self, monkeypatch):
        """The evidence requirement only kicks in at >=8.0."""
        agent = self._agent(monkeypatch, {
            "score": 5.0,
            "reasoning": "Missing dimension.",
            "improvements": ["add X"],
            "dimension_scores": {"Correctness": 5.0},
            "evidence_quotes": [],
        })
        result = agent.score(task="t", output="o", rubric=self._minimal_rubric())
        assert result["score"] == 5.0
        assert "_inflated_score_demoted" not in result

    def test_whitespace_only_quotes_count_as_no_evidence(self, monkeypatch):
        """Model returning `[" "]` doesn't satisfy the requirement."""
        agent = self._agent(monkeypatch, {
            "score": 9.5,
            "reasoning": "yes",
            "improvements": [],
            "dimension_scores": {"Correctness": 9.5},
            "evidence_quotes": ["   ", ""],
        })
        result = agent.score(task="t", output="o", rubric=self._minimal_rubric())
        assert result["score"] == 7.5

    def test_malformed_score_passes_through_untouched(self, monkeypatch):
        """If the model returns garbage in `score`, don't crash — the
        upstream caller will log and continue."""
        agent = self._agent(monkeypatch, {
            "score": "not-a-number",
            "reasoning": "x",
            "improvements": [],
        })
        result = agent.score(task="t", output="o", rubric=self._minimal_rubric())
        assert result["score"] == "not-a-number"
        assert "_inflated_score_demoted" not in result


# ─── #3 rubric format_for_prompt strict bands ──────────────────────────

class TestRubricStrictBands:
    def test_format_includes_hard_scoring_bands(self):
        """The rubric body must restate the strict bands (anti-drift)."""
        from axiom_constitutional.rubric import format_for_prompt
        rubric = {
            "task_summary": "summarise news",
            "dimensions": [{"name": "Accuracy", "weight": 1.0,
                             "description": "facts right"}],
            "scoring_guide": "be lenient", # the failure case
            "anti_patterns": ["hallucination"],
        }
        text = format_for_prompt(rubric)
        # All four band ranges must appear so the model can't cherry-pick.
        assert "9.0–10.0" in text
        assert "7.0– 8.9" in text
        assert "5.0– 6.9" in text
        assert "0.0– 4.9" in text
        # Anti-pattern cap is named.
        assert "caps the score at 6.0" in text
        # The author's possibly-lenient guide is downgraded to reference.
        assert "reference, not override" in text

    def test_anti_patterns_listed(self):
        from axiom_constitutional.rubric import format_for_prompt
        rubric = {
            "task_summary": "x",
            "dimensions": [{"name": "Y", "weight": 1.0, "description": "z"}],
            "scoring_guide": "g",
            "anti_patterns": ["hedging", "boilerplate", "vagueness"],
        }
        text = format_for_prompt(rubric)
        for ap in ("hedging", "boilerplate", "vagueness"):
            assert ap in text
