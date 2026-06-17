"""Tests for the deep-recall diagnostic (gold_rank_report) in legal_rag_bench.

The bucketing decides whether a wider --pc-pool helps a miss or whether only
query expansion (HyDE) can reach it. The bucket-assignment logic is tested
directly (deterministic); the FTS5-backed report is checked for structure only,
since exact ranks depend on BM25 internals that belong to integration testing.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "legal"))

from legal_rag_bench import (  # noqa: E402
    _rank_bucket,
    gold_rank_report,
    print_gold_rank_report,
    build_legal_index,
)


# ── bucket assignment (deterministic) ────────────────────────────────────────────

class TestRankBucket:
    def test_absent_when_none(self):
        # gold section never appears in the pool → true recall gap
        assert _rank_bucket(None, pool_threshold=30) == "absent"

    def test_in_pool_below_threshold(self):
        assert _rank_bucket(5, pool_threshold=30) == "in_pool"
        assert _rank_bucket(29, pool_threshold=30) == "in_pool"

    def test_reachable_between_threshold_and_100(self):
        # a wider --pc-pool would admit these
        assert _rank_bucket(30, pool_threshold=30) == "reachable"
        assert _rank_bucket(99, pool_threshold=30) == "reachable"

    def test_deep_at_or_above_100(self):
        assert _rank_bucket(100, pool_threshold=30) == "deep"
        assert _rank_bucket(180, pool_threshold=30) == "deep"

    def test_threshold_is_exclusive_lower_bound(self):
        # with a tiny threshold, rank 0 is in_pool, rank == threshold is reachable
        assert _rank_bucket(0, pool_threshold=1) == "in_pool"
        assert _rank_bucket(1, pool_threshold=1) == "reachable"


# ── report structure (FTS5-backed, lenient) ─────────────────────────────────────

def _make_db(tmp: Path) -> Path:
    db = tmp / "mini.db"
    corpus = [
        {"cve_id": "1.5-c5-s1",
         "answer": "The jury must decide solely on the evidence; jurors must not "
                   "conduct private experiments during deliberation."},
        {"cve_id": "7.5.10-c2-s1", "answer": "Handling of stolen goods burglary."},
        {"cve_id": "2.1-c1-s1",    "answer": "Views: the court may order an inspection."},
    ]
    build_legal_index(db, corpus)
    return db


class TestGoldRankReportStructure:
    def test_returns_bucket_structure(self, tmp_path):
        db = _make_db(tmp_path)
        qa = [{"id": "q1", "question": "jury deliberation evidence",
               "relevant_passage_id": "1.5-c5-s1"}]
        rep = gold_rank_report(db, qa, dense=None, deep_pool=50, pool_threshold=10)
        assert set(rep["buckets"]) == {"in_pool", "reachable", "deep", "absent"}
        assert rep["n"] == 1
        assert sum(rep["buckets"].values()) == 1

    def test_every_query_lands_in_one_bucket(self, tmp_path):
        db = _make_db(tmp_path)
        qa = [{"id": "q1", "question": "jury evidence", "relevant_passage_id": "1.5-c5-s1"},
              {"id": "q2", "question": "inspection premises", "relevant_passage_id": "2.1-c1-s1"},
              {"id": "q3", "question": "burglary goods", "relevant_passage_id": "7.5.10-c2-s1"}]
        rep = gold_rank_report(db, qa, dense=None, deep_pool=50, pool_threshold=10)
        assert sum(rep["buckets"].values()) == 3

    def test_row_records_gold_section(self, tmp_path):
        db = _make_db(tmp_path)
        qa = [{"id": "q1", "question": "jury evidence", "relevant_passage_id": "1.5-c5-s1"}]
        rep = gold_rank_report(db, qa, dense=None, deep_pool=50, pool_threshold=10)
        assert rep["rows"][0]["gold_section"] == "1.5"

    def test_print_does_not_raise(self, tmp_path, capsys):
        db = _make_db(tmp_path)
        qa = [{"id": "q1", "question": "jury evidence", "relevant_passage_id": "1.5-c5-s1"}]
        rep = gold_rank_report(db, qa, dense=None, deep_pool=50, pool_threshold=10)
        print_gold_rank_report(rep)
        out = capsys.readouterr().out
        assert "Deep-recall diagnostic" in out
        assert "absent" in out
