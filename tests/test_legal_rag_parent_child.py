"""Tests for parent-child (section-level) retrieval in legal_rag_bench.

Validates the chunk→parent collapse logic and the section-level recovery that
turns "right section, wrong chunk" misses (Group B in the miss analysis) into
hits — using ids that mirror the real legal-rag-bench format (e.g. 4.13.2-c4-s2).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

# legal_rag_bench lives under research/legal/ and inserts repo root on import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "legal"))

from legal_rag_bench import (  # noqa: E402
    _parent_of,
    collapse_to_parents,
    rr,
    hit_at,
    _aggregate,
)


# ── _parent_of ──────────────────────────────────────────────────────────────────

class TestParentOf:
    def test_strips_chunk_suffix(self):
        assert _parent_of("4.13.2-c4-s2") == "4.13.2"

    def test_strips_multi_segment_section(self):
        assert _parent_of("7.3.1.2-c4-s10") == "7.3.1.2"

    def test_short_section(self):
        assert _parent_of("1.5-c5-s1") == "1.5"

    def test_idempotent_on_bare_section(self):
        # applying twice must not change a section id
        assert _parent_of(_parent_of("4.13.2-c4-s2")) == "4.13.2"

    def test_bare_section_unchanged(self):
        assert _parent_of("7.5.10") == "7.5.10"

    def test_handles_trailing_text(self):
        # the suffix regex tolerates a trailing description after the chunk marker
        assert _parent_of("4.13.2-c4-s2 Jury Directions") == "4.13.2"


# ── collapse_to_parents ──────────────────────────────────────────────────────────

class TestCollapseToParents:
    def test_dedupes_siblings(self):
        chunks = ["4.13.2-c1-s1", "4.13.2-c2-s2", "4.13.2-c2-s3", "7.2.1-c1-s1"]
        assert collapse_to_parents(chunks) == ["4.13.2", "7.2.1"]

    def test_preserves_best_rank_order(self):
        chunks = ["7.5.10-c2-s3", "4.13.2-c1-s1", "7.5.10-c3-s2"]
        # 7.5.10 first seen at rank 0, 4.13.2 at rank 1
        assert collapse_to_parents(chunks) == ["7.5.10", "4.13.2"]

    def test_truncates_to_final_k(self):
        chunks = [f"{i}.0-c1-s1" for i in range(20)]
        assert len(collapse_to_parents(chunks, final_k=5)) == 5

    def test_empty_list(self):
        assert collapse_to_parents([]) == []


# ── Group B recovery (the core claim) ────────────────────────────────────────────

class TestSectionLevelRecovery:
    def test_sibling_chunk_miss_recovered_at_section_level(self):
        # Mirrors Q31/Q32: gold 4.13.2-c4-s2; retriever returns sibling chunks
        # of the SAME section but not the gold chunk itself.
        gold = "4.13.2-c4-s2"
        retrieved = ["4.13.2-c1-s1", "4.13.2-c2-s2", "4.13.2-c2-s3",
                     "7.2.1-c1-s1", "8.15-c4-s3"]
        # chunk-level: miss (gold chunk not present)
        assert hit_at(retrieved, gold, 10) is False
        # section-level: hit (parent 4.13.2 is present)
        sec = collapse_to_parents(retrieved)
        assert hit_at(sec, _parent_of(gold), 10) is True

    def test_true_routing_miss_not_recovered(self):
        # Mirrors Q3: gold section 1.5 (procedure); retriever went entirely to
        # offence sections 7.x. Parent-child must NOT rescue this — different
        # section, so it stays a miss. Guards against the metric over-counting.
        gold = "1.5-c6-s1"
        retrieved = ["7.4.9-c1-s1", "7.2.1A-c1-s1", "7.4.9-c5-s1"]
        sec = collapse_to_parents(retrieved)
        assert hit_at(sec, _parent_of(gold), 10) is False

    def test_near_miss_chunk_recovered(self):
        # Mirrors Q63: gold 7.3.1.2-c4-s10, got 7.3.1.2-c4-s5 (same section).
        gold = "7.3.1.2-c4-s10"
        retrieved = ["7.3.1.2-c4-s5", "6.4.2-c4-s2", "7.7.1-c12-s3"]
        assert hit_at(retrieved, gold, 10) is False
        sec = collapse_to_parents(retrieved)
        assert hit_at(sec, _parent_of(gold), 10) is True


# ── _aggregate section metrics ───────────────────────────────────────────────────

class TestAggregateSectionMetrics:
    def _row(self, qid, gold, retrieved):
        return {"id": qid, "relevant_id": gold, "retrieved": retrieved,
                "rr": rr(retrieved, gold), "fts5_ms": 5.0}

    def test_section_metrics_present(self):
        results = [self._row("q1", "4.13.2-c4-s2",
                             ["4.13.2-c1-s1", "7.2.1-c1-s1"])]
        agg = _aggregate(results, 10, latency_field="fts5_ms")
        assert "Hit@10_sec" in agg
        assert "MRR_sec" in agg
        assert "n_misses_sec" in agg

    def test_recovery_counted(self):
        # Two queries: one sibling-chunk miss (recoverable), one true miss.
        results = [
            self._row("recoverable", "4.13.2-c4-s2",
                      ["4.13.2-c1-s1", "4.13.2-c2-s2"]),       # right section
            self._row("true_miss", "1.5-c6-s1",
                      ["7.4.9-c1-s1", "7.2.1A-c1-s1"]),        # wrong section
        ]
        agg = _aggregate(results, 10, latency_field="fts5_ms")
        # chunk-level: both miss → 2 misses
        assert agg["n_misses"] == 2
        # section-level: the recoverable one flips → 1 miss
        assert agg["n_misses_sec"] == 1
        # recovered = 2 - 1 = 1
        assert agg["n_misses"] - agg["n_misses_sec"] == 1

    def test_section_hit_at_one_for_exact_chunk(self):
        # When the gold chunk itself is rank 0, section Hit@1 must also be 1.
        results = [self._row("q1", "7.5.10-c2-s1",
                             ["7.5.10-c2-s1", "8.15-c4-s3"])]
        agg = _aggregate(results, 10, latency_field="fts5_ms")
        assert agg["Hit@1"] == 1.0
        assert agg["Hit@1_sec"] == 1.0

    def test_section_mrr_at_least_chunk_mrr(self):
        # Collapsing can only promote the gold section's rank (or keep it),
        # never demote it — so section MRR >= chunk MRR for every query.
        results = [
            self._row("q1", "4.13.2-c4-s2",
                      ["7.2.1-c1-s1", "4.13.2-c1-s1", "4.13.2-c4-s2"]),
        ]
        agg = _aggregate(results, 10, latency_field="fts5_ms")
        assert agg["MRR_sec"] >= agg["MRR"]
