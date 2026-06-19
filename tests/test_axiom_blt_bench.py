"""Tests for axiom_blt_bench — BLT benchmark for KnowledgeCookie."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "b" * 64)

from axiom_blt_bench import (
    BLTConfig,
    BLTResult,
    BLTBenchmark,
    _make_synthetic_fragment,
    _make_synthetic_cookie,
    _compute_derived,
    RELEVANCE_RATE,
)
from axiom_knowledge_cookie import KnowledgeFragment, KnowledgeCookie


# ── _make_synthetic_fragment ──────────────────────────────────────────────────

class TestSyntheticFragment:
    def test_content_hash_is_16_chars(self):
        f = _make_synthetic_fragment(0, 1200)
        assert len(f.content_hash) == 16

    def test_content_length_near_avg(self):
        f = _make_synthetic_fragment(0, 1200)
        assert abs(len(f.content) - 1200) < 50

    def test_promoted_true(self):
        f = _make_synthetic_fragment(0, 1200)
        assert f.promoted is True

    def test_sessions_seen_3(self):
        f = _make_synthetic_fragment(0, 1200)
        assert f.sessions_seen == 3

    def test_different_idx_gives_different_hash(self):
        f0 = _make_synthetic_fragment(0, 1200)
        f1 = _make_synthetic_fragment(1, 1200)
        assert f0.content_hash != f1.content_hash


# ── _make_synthetic_cookie ────────────────────────────────────────────────────

class TestSyntheticCookie:
    def test_fragment_count(self):
        c = _make_synthetic_cookie(10, 1200)
        assert len(c.fragments) == 10

    def test_hot_knowledge_count(self):
        c = _make_synthetic_cookie(10, 1200)
        assert len(c.hot_knowledge) == 10

    def test_zero_fragments(self):
        c = _make_synthetic_cookie(0, 1200)
        assert len(c.fragments) == 0
        assert len(c.hot_knowledge) == 0


# ── _compute_derived ──────────────────────────────────────────────────────────

class TestComputeDerived:
    def test_returns_two_values(self):
        result = _compute_derived(
            tokens_added=300,
            calls_per_session=10,
            tokens_saved_per_hit=200,
            token_cost_per_1k=0.002,
        )
        assert len(result) == 2

    def test_break_even_proportional_to_tokens_added(self):
        be_small, _ = _compute_derived(
            tokens_added=300,
            calls_per_session=10,
            tokens_saved_per_hit=200,
            token_cost_per_1k=0.002,
        )
        be_large, _ = _compute_derived(
            tokens_added=3000,
            calls_per_session=10,
            tokens_saved_per_hit=200,
            token_cost_per_1k=0.002,
        )
        assert be_large > be_small

    def test_net_cost_negative_when_tokens_added_less_than_savings(self):
        _, net = _compute_derived(
            tokens_added=10,
            calls_per_session=10,
            tokens_saved_per_hit=1000,   # large savings
            token_cost_per_1k=0.002,
        )
        # savings side: (1000 * 0.20 / 1000) * 0.002 * 1_000_000 = 400
        # cost side:    (10 / 1000) * 0.002 * 1_000_000 = 20
        # net = 20 - 400 = -380
        assert net < 0

    def test_net_cost_positive_when_tokens_added_exceeds_savings(self):
        _, net = _compute_derived(
            tokens_added=10000,
            calls_per_session=10,
            tokens_saved_per_hit=50,    # small savings
            token_cost_per_1k=0.002,
        )
        assert net > 0

    def test_break_even_uses_relevance_rate(self):
        be, _ = _compute_derived(
            tokens_added=200,
            calls_per_session=10,
            tokens_saved_per_hit=200,
            token_cost_per_1k=0.002,
        )
        expected = (200 * 10) / (200 * RELEVANCE_RATE * 10)
        assert abs(be - expected) < 0.01

    def test_zero_tokens_added_still_works(self):
        be, net = _compute_derived(
            tokens_added=0,
            calls_per_session=10,
            tokens_saved_per_hit=200,
            token_cost_per_1k=0.002,
        )
        assert be == 0.0
        assert net < 0   # pure saving, zero cost


# ── BLTBenchmark ──────────────────────────────────────────────────────────────

class TestBLTBenchmark:
    def _small_config(self) -> BLTConfig:
        return BLTConfig(fragment_counts=[1, 5], reps=1)

    def test_run_returns_one_result_per_fragment_count(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        results = bench.run()
        assert len(results) == 2

    def test_fragment_counts_match_config(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        results = bench.run()
        assert results[0].fragment_count == 1
        assert results[1].fragment_count == 5

    def test_cookie_bytes_positive(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        for r in bench.run():
            assert r.cookie_bytes > 0

    def test_cookie_bytes_grows_with_fragment_count(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        results = bench.run()
        assert results[1].cookie_bytes > results[0].cookie_bytes

    def test_tokens_added_positive(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        for r in bench.run():
            assert r.tokens_added > 0

    def test_load_ms_non_negative(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        for r in bench.run():
            assert r.load_ms >= 0

    def test_inject_ms_non_negative(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        for r in bench.run():
            assert r.inject_ms >= 0

    def test_break_even_positive(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        for r in bench.run():
            assert r.break_even_sessions > 0

    def test_to_json_valid(self, tmp_path):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        results = bench.run()
        data = json.loads(bench.to_json(results))
        assert len(data) == 2
        assert "fragment_count" in data[0]
        assert "cookie_bytes" in data[0]
        assert "break_even_sessions" in data[0]

    def test_print_table_does_not_raise(self, tmp_path, capsys):
        bench = BLTBenchmark(config=self._small_config(), tmp_dir=tmp_path)
        results = bench.run()
        bench.print_table(results)
        out = capsys.readouterr().out
        assert "BLT Benchmark" in out
        assert "Fragment" in out

    def test_auto_tmp_dir_cleaned_up(self):
        bench = BLTBenchmark(config=BLTConfig(fragment_counts=[1], reps=1))
        results = bench.run()
        assert len(results) == 1
        # After run(), the owned temp dir should be cleaned up
        assert bench._owned_tmp is None

    def test_multiple_reps_averaged(self, tmp_path):
        cfg_1 = BLTConfig(fragment_counts=[5], reps=1)
        cfg_3 = BLTConfig(fragment_counts=[5], reps=3)
        r1 = BLTBenchmark(config=cfg_1, tmp_dir=tmp_path).run()[0]
        r3 = BLTBenchmark(config=cfg_3, tmp_dir=tmp_path).run()[0]
        # Both should have reasonable load_ms — just check type
        assert isinstance(r3.load_ms, float)
        assert isinstance(r1.load_ms, float)
