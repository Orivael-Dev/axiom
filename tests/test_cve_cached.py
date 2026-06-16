"""Tests for CachedCVERetriever — CVERetriever + VerifiedAnswerCache wiring."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "a" * 64)

from axiom_cve_retriever import CVERetriever, CachedCVERetriever
from axiom_verified_answer_cache import (
    VerifiedAnswerCache,
    PROMOTION_THRESHOLD,
    fingerprint,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_db(rows: list[dict]) -> tuple[str, str]:
    """Create a temp FTS5 db with the given rows. Returns (db_path, jsonl_path)."""
    tmp = tempfile.mkdtemp()
    db_path   = str(Path(tmp) / "cve.db")
    jsonl_path = str(Path(tmp) / "cve.jsonl")
    with open(jsonl_path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    r = CVERetriever(db_path)
    r.build_from_jsonl(jsonl_path)
    return db_path, jsonl_path


def _make_cache() -> tuple[VerifiedAnswerCache, str]:
    tmp = tempfile.mkdtemp()
    db  = str(Path(tmp) / "cache.db")
    return VerifiedAnswerCache(db_path=Path(db)), db


_SAMPLE_ROWS = [
    {
        "User": "What is CVE-2021-44228?",
        "Assistant": "CVE-2021-44228 is Log4Shell, a critical RCE in Apache Log4j.",
    },
    {
        "User": "Explain CVE-2014-0160.",
        "Assistant": "CVE-2014-0160 is Heartbleed, a buffer over-read in OpenSSL.",
    },
    {
        "User": "What is the CVSS score of CVE-2021-44228?",
        "Assistant": "CVE-2021-44228 has a CVSS v3.1 base score of 10.0 (Critical).",
    },
]


# ── cold path ─────────────────────────────────────────────────────────────────

class TestColdPath:
    def setup_method(self):
        db_path, _ = _make_db(_SAMPLE_ROWS)
        self.retriever = CVERetriever(db_path)
        self.cache, _ = _make_cache()
        self.r = CachedCVERetriever(self.retriever, self.cache)

    def test_cold_returns_answer_and_false(self):
        text, from_cache = self.r.answer("CVE-2021-44228 log4j")
        assert from_cache is False
        assert text is not None
        assert "Log4Shell" in text or "44228" in text

    def test_cold_records_in_cache(self):
        query = "CVE-2021-44228 log4j"
        self.r.answer(query)
        fp = fingerprint(query, context_key="cve")
        entry = self.cache.get(fp)
        assert entry is not None
        assert entry.hits == 1
        assert entry.promoted == 0

    def test_cold_no_match_returns_none(self):
        text, from_cache = self.r.answer("zzz unknown query xyz")
        assert from_cache is False
        assert text is None

    def test_cold_no_match_nothing_recorded(self):
        query = "zzz unknown query xyz"
        self.r.answer(query)
        fp = fingerprint(query, context_key="cve")
        assert self.cache.get(fp) is None


# ── verify → promote ─────────────────────────────────────────────────────────

class TestVerifyAndPromote:
    def setup_method(self):
        db_path, _ = _make_db(_SAMPLE_ROWS)
        self.retriever = CVERetriever(db_path)
        self.cache, _ = _make_cache()
        self.r = CachedCVERetriever(self.retriever, self.cache)
        self.query = "CVE-2021-44228 log4j"
        self.r.answer(self.query)   # cold hit; records

    def test_verify_increments_count(self):
        self.r.verify(self.query)
        fp = fingerprint(self.query, context_key="cve")
        entry = self.cache.get(fp)
        assert entry.verified_hits == 1

    def test_verify_returns_true_when_entry_exists(self):
        assert self.r.verify(self.query) is True

    def test_verify_returns_false_when_no_entry(self):
        assert self.r.verify("does not exist in cache xyz") is False

    def test_auto_promotes_at_threshold(self):
        for _ in range(PROMOTION_THRESHOLD):
            self.r.verify(self.query)
        fp = fingerprint(self.query, context_key="cve")
        entry = self.cache.get(fp)
        assert entry.promoted == 1

    def test_not_promoted_below_threshold(self):
        for _ in range(PROMOTION_THRESHOLD - 1):
            self.r.verify(self.query)
        fp = fingerprint(self.query, context_key="cve")
        entry = self.cache.get(fp)
        assert entry.promoted == 0


# ── hot path ─────────────────────────────────────────────────────────────────

class TestHotPath:
    def setup_method(self):
        db_path, _ = _make_db(_SAMPLE_ROWS)
        self.retriever = CVERetriever(db_path)
        self.cache, _ = _make_cache()
        self.r = CachedCVERetriever(self.retriever, self.cache)
        self.query = "CVE-2021-44228 log4j"
        # Populate and promote
        self.r.answer(self.query)
        for _ in range(PROMOTION_THRESHOLD):
            self.r.verify(self.query)

    def test_hot_returns_true(self):
        _, from_cache = self.r.answer(self.query)
        assert from_cache is True

    def test_hot_answer_matches_original(self):
        first_text, _ = self.r.answer(self.query)   # already promoted; hot
        second_text, _ = self.r.answer(self.query)
        assert first_text == second_text

    def test_order_invariant_query_hits_hot_path(self):
        """Different word order → same fingerprint → same hot-path hit."""
        alt_query = "log4j CVE-2021-44228"
        _, from_cache = self.r.answer(alt_query)
        assert from_cache is True

    def test_retriever_not_called_on_hot_hit(self):
        """CVERetriever.answer_for() should not be called on a cache hit."""
        with patch.object(self.retriever, "answer_for") as mock_af:
            self.r.answer(self.query)
            mock_af.assert_not_called()


# ── invalidate ───────────────────────────────────────────────────────────────

class TestInvalidate:
    def setup_method(self):
        db_path, _ = _make_db(_SAMPLE_ROWS)
        self.retriever = CVERetriever(db_path)
        self.cache, _ = _make_cache()
        self.r = CachedCVERetriever(self.retriever, self.cache)
        self.query = "CVE-2021-44228 log4j"
        self.r.answer(self.query)
        for _ in range(PROMOTION_THRESHOLD):
            self.r.verify(self.query)

    def test_invalidate_demotes(self):
        self.r.invalidate(self.query)
        fp = fingerprint(self.query, context_key="cve")
        entry = self.cache.get(fp)
        assert entry is None or entry.promoted == 0

    def test_after_invalidate_next_answer_is_cold(self):
        self.r.invalidate(self.query)
        _, from_cache = self.r.answer(self.query)
        assert from_cache is False

    def test_invalidate_nonexistent_returns_false(self):
        assert self.r.invalidate("this was never cached xyz") is False


# ── context_key ──────────────────────────────────────────────────────────────

class TestContextKey:
    def setup_method(self):
        db_path, _ = _make_db(_SAMPLE_ROWS)
        self.retriever = CVERetriever(db_path)
        self.cache, _ = _make_cache()
        self.r = CachedCVERetriever(self.retriever, self.cache)
        self.query = "CVE-2021-44228 log4j"

    def test_different_context_keys_are_independent(self):
        self.r.answer(self.query, context_key="cve")
        for _ in range(PROMOTION_THRESHOLD):
            self.r.verify(self.query, context_key="cve")
        # Different key — not promoted there
        _, from_cache = self.r.answer(self.query, context_key="other")
        assert from_cache is False

    def test_default_context_key_is_cve(self):
        self.r.answer(self.query)
        fp_default = fingerprint(self.query, context_key="cve")
        assert self.cache.get(fp_default) is not None

    def test_custom_default_context_key(self):
        r = CachedCVERetriever(self.retriever, self.cache, context_key="sec")
        r.answer(self.query)
        fp_sec = fingerprint(self.query, context_key="sec")
        assert self.cache.get(fp_sec) is not None
        fp_cve = fingerprint(self.query, context_key="cve")
        assert self.cache.get(fp_cve) is None


# ── retrieve passthrough ──────────────────────────────────────────────────────

class TestRetrievePassthrough:
    def setup_method(self):
        db_path, _ = _make_db(_SAMPLE_ROWS)
        self.retriever = CVERetriever(db_path)
        self.cache, _ = _make_cache()
        self.r = CachedCVERetriever(self.retriever, self.cache)

    def test_retrieve_returns_sources(self):
        hits = self.r.retrieve("CVE-2021-44228 log4j", k=3)
        assert len(hits) >= 1
        assert all(hasattr(h, "score") for h in hits)

    def test_retrieve_is_always_live(self):
        """retrieve() never consults the cache."""
        with patch.object(self.cache, "lookup") as mock_lu:
            self.r.retrieve("CVE-2021-44228 log4j")
            mock_lu.assert_not_called()


# ── stats ─────────────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_has_both_keys(self):
        db_path, _ = _make_db(_SAMPLE_ROWS)
        cache, _ = _make_cache()
        r = CachedCVERetriever(CVERetriever(db_path), cache)
        s = r.stats()
        assert "retriever" in s
        assert "cache" in s
        assert s["retriever"]["rows"] == len(_SAMPLE_ROWS)
