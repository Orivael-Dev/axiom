"""Tests for VerifiedAnswerCache — fingerprinting, hit-tracking, hot-path promotion."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "dd" * 32)

from axiom_verified_answer_cache import (
    CachedAnswer,
    VerifiedAnswerCache,
    fingerprint,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cache(tmp_path: Path, threshold: int = 3, ttl_days: int = 30) -> VerifiedAnswerCache:
    return VerifiedAnswerCache(
        db_path=tmp_path / "cache.db",
        promotion_threshold=threshold,
        default_ttl_days=ttl_days,
    )


# ── fingerprint() ─────────────────────────────────────────────────────────────

def test_fingerprint_order_invariant():
    assert fingerprint("fix error 1099 how") == fingerprint("how fix 1099 error")


def test_fingerprint_punctuation_stripped():
    assert fingerprint("fix error 1099?") == fingerprint("fix error 1099")


def test_fingerprint_stopwords_removed():
    fp_full  = fingerprint("how do i fix the error 1099")
    fp_clean = fingerprint("fix error 1099")
    assert fp_full == fp_clean


def test_fingerprint_case_insensitive():
    assert fingerprint("Fix Error 1099") == fingerprint("fix error 1099")


def test_fingerprint_context_key_differentiates():
    assert fingerprint("balance", context_key="user:1") != \
           fingerprint("balance", context_key="user:2")


def test_fingerprint_no_context_same_as_empty_context():
    assert fingerprint("balance") == fingerprint("balance", context_key="")


def test_fingerprint_deterministic():
    fp1 = fingerprint("what is the boiling point of water")
    fp2 = fingerprint("what is the boiling point of water")
    assert fp1 == fp2


def test_fingerprint_returns_sha256_hex():
    fp = fingerprint("hello world")
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_different_queries_differ():
    assert fingerprint("error 1099") != fingerprint("error 1100")


# ── record() and get() ────────────────────────────────────────────────────────

def test_record_creates_entry(tmp_path):
    c  = _cache(tmp_path)
    fp = fingerprint("what is BM25")
    c.record(fp, "BM25 is a ranking function.")
    ca = c.get(fp)
    assert ca is not None
    assert ca.answer_text == "BM25 is a ranking function."
    assert ca.hits == 1
    assert ca.verified_hits == 0
    assert ca.promoted is False


def test_record_increments_hits(tmp_path):
    c  = _cache(tmp_path)
    fp = fingerprint("what is BM25")
    c.record(fp, "BM25 is a ranking function.")
    c.record(fp, "BM25 is a ranking function.")
    ca = c.get(fp)
    assert ca.hits == 2


def test_record_verified_flag_counts_verified_hit(tmp_path):
    c  = _cache(tmp_path)
    fp = fingerprint("what is BM25")
    c.record(fp, "BM25 is a ranking function.", verified=True)
    ca = c.get(fp)
    assert ca.verified_hits == 1


def test_record_answer_change_resets_promotion(tmp_path):
    c  = _cache(tmp_path, threshold=2)
    fp = fingerprint("capital france")
    c.record(fp, "Paris", verified=True)
    c.record(fp, "Paris", verified=True)
    assert c.get(fp).promoted is True

    # New answer → demote
    c.record(fp, "Paris (capital of France)")
    assert c.get(fp).promoted is False


# ── verify() and auto-promotion ───────────────────────────────────────────────

def test_verify_increments_verified_hits(tmp_path):
    c  = _cache(tmp_path)
    fp = fingerprint("speed of light")
    c.record(fp, "299,792,458 m/s")
    c.verify(fp)
    assert c.get(fp).verified_hits == 1


def test_auto_promote_at_threshold(tmp_path):
    c  = _cache(tmp_path, threshold=3)
    fp = fingerprint("capital germany")
    c.record(fp, "Berlin")
    for _ in range(3):
        c.verify(fp)
    assert c.get(fp).promoted is True


def test_not_promoted_below_threshold(tmp_path):
    c  = _cache(tmp_path, threshold=5)
    fp = fingerprint("capital italy")
    c.record(fp, "Rome")
    for _ in range(4):
        c.verify(fp)
    assert c.get(fp).promoted is False


def test_verify_returns_false_for_unknown(tmp_path):
    c  = _cache(tmp_path)
    assert c.verify("nonexistent_fingerprint") is False


# ── lookup() — hot-path ───────────────────────────────────────────────────────

def test_lookup_returns_none_before_promotion(tmp_path):
    c  = _cache(tmp_path, threshold=3)
    fp = fingerprint("what is SRD")
    c.record(fp, "Stochastic Residual Dithering")
    c.verify(fp)
    assert c.lookup(fp) is None   # only 1 verify, threshold=3


def test_lookup_returns_answer_after_promotion(tmp_path):
    c  = _cache(tmp_path, threshold=2)
    fp = fingerprint("what is SRD")
    c.record(fp, "Stochastic Residual Dithering")
    c.verify(fp)
    c.verify(fp)
    assert c.lookup(fp) == "Stochastic Residual Dithering"


def test_lookup_increments_hits(tmp_path):
    c  = _cache(tmp_path, threshold=1)
    fp = fingerprint("boiling point water")
    c.record(fp, "100°C")
    c.verify(fp)
    assert c.get(fp).promoted is True

    hits_before = c.get(fp).hits
    c.lookup(fp)
    assert c.get(fp).hits == hits_before + 1


def test_lookup_returns_none_for_unknown(tmp_path):
    c = _cache(tmp_path)
    assert c.lookup("unknown_fp_xyz") is None


# ── promote() and invalidate() ────────────────────────────────────────────────

def test_explicit_promote(tmp_path):
    c  = _cache(tmp_path, threshold=10)
    fp = fingerprint("speed sound air")
    c.record(fp, "343 m/s at 20°C")
    c.promote(fp)
    assert c.get(fp).promoted is True
    assert c.lookup(fp) == "343 m/s at 20°C"


def test_invalidate_demotes(tmp_path):
    c  = _cache(tmp_path, threshold=1)
    fp = fingerprint("capital japan")
    c.record(fp, "Tokyo")
    c.verify(fp)
    assert c.lookup(fp) == "Tokyo"

    c.invalidate(fp)
    assert c.get(fp).promoted is False
    assert c.lookup(fp) is None


def test_invalidate_resets_verified_hits(tmp_path):
    c  = _cache(tmp_path, threshold=2)
    fp = fingerprint("capital japan")
    c.record(fp, "Tokyo")
    c.verify(fp)
    c.verify(fp)
    c.invalidate(fp)
    assert c.get(fp).verified_hits == 0


def test_invalidate_returns_false_for_unknown(tmp_path):
    c = _cache(tmp_path)
    assert c.invalidate("bad_fp") is False


def test_promote_returns_false_for_unknown(tmp_path):
    c = _cache(tmp_path)
    assert c.promote("bad_fp") is False


# ── TTL / sweep_expired() ────────────────────────────────────────────────────

def test_sweep_expired_demotes_old_entries(tmp_path):
    c  = _cache(tmp_path, threshold=1, ttl_days=0)
    fp = fingerprint("old query")
    c.record(fp, "old answer")
    c.verify(fp)
    assert c.get(fp).promoted is True

    # ttl_days=0 → immediately expired
    n = c.sweep_expired()
    assert n >= 1
    assert c.get(fp).promoted is False


def test_sweep_leaves_fresh_entries(tmp_path):
    c  = _cache(tmp_path, threshold=1, ttl_days=365)
    fp = fingerprint("fresh query")
    c.record(fp, "fresh answer")
    c.verify(fp)
    n = c.sweep_expired()
    assert n == 0
    assert c.get(fp).promoted is True


def test_lookup_auto_demotes_expired(tmp_path):
    c  = _cache(tmp_path, threshold=1, ttl_days=0)
    fp = fingerprint("expiry test query")
    c.record(fp, "expiry test answer")
    c.verify(fp)

    result = c.lookup(fp)
    assert result is None
    assert c.get(fp).promoted is False


# ── HMAC integrity ────────────────────────────────────────────────────────────

def test_signature_valid_on_new_entry(tmp_path):
    c  = _cache(tmp_path, threshold=1)
    fp = fingerprint("hmac test")
    c.record(fp, "signed answer")
    c.verify(fp)
    ca = c.get(fp)
    assert ca.is_valid() is True


def test_lookup_rejects_tampered_answer(tmp_path):
    c  = _cache(tmp_path, threshold=1)
    fp = fingerprint("hmac tamper test")
    c.record(fp, "correct answer")
    c.verify(fp)
    assert c.lookup(fp) == "correct answer"

    # Tamper directly via SQL
    c._conn.execute(
        "UPDATE answer_cache SET answer_text = 'TAMPERED' WHERE fingerprint = ?", (fp,)
    )
    c._conn.commit()
    assert c.lookup(fp) is None   # signature mismatch → hot-path rejected


# ── stats() ───────────────────────────────────────────────────────────────────

def test_stats_empty_cache(tmp_path):
    c = _cache(tmp_path)
    s = c.stats()
    assert s["total_fingerprints"] == 0
    assert s["promoted_hot"] == 0


def test_stats_counts_correctly(tmp_path):
    c  = _cache(tmp_path, threshold=1)
    fp1 = fingerprint("promoted query one")
    fp2 = fingerprint("cold query two")
    c.record(fp1, "answer one")
    c.verify(fp1)                 # promotes
    c.record(fp2, "answer two")   # cold

    s = c.stats()
    assert s["total_fingerprints"] == 2
    assert s["promoted_hot"] == 1
    assert s["cold_warm"] == 1


# ── Context-key discrimination ────────────────────────────────────────────────

def test_context_key_separate_entries(tmp_path):
    c    = _cache(tmp_path, threshold=1)
    fp1  = fingerprint("my balance", context_key="user:alice")
    fp2  = fingerprint("my balance", context_key="user:bob")
    assert fp1 != fp2

    c.record(fp1, "Alice: $1,000")
    c.verify(fp1)
    c.record(fp2, "Bob: $500")
    c.verify(fp2)

    assert c.lookup(fp1) == "Alice: $1,000"
    assert c.lookup(fp2) == "Bob: $500"


# ── record() with verified=True ───────────────────────────────────────────────

def test_record_verified_true_auto_promotes_at_threshold(tmp_path):
    c  = _cache(tmp_path, threshold=2)
    fp = fingerprint("record verified threshold")
    c.record(fp, "the answer", verified=True)
    assert c.get(fp).promoted is False   # 1 < threshold=2
    c.record(fp, "the answer", verified=True)
    assert c.get(fp).promoted is True    # 2 >= threshold=2


# ── CachedAnswer helpers ──────────────────────────────────────────────────────

def test_cached_answer_is_expired_uses_ttl():
    from datetime import datetime, timezone, timedelta
    from axiom_verified_answer_cache import _sign
    old_dt = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    sig = _sign("fp", "ans", "", old_dt)
    ca = CachedAnswer(
        fingerprint="fp", answer_text="ans",
        hits=1, verified_hits=1, promoted=True,
        context_key="", created_at=old_dt,
        last_seen=old_dt, ttl_days=30, signature=sig,
    )
    assert ca.is_expired() is True


def test_cached_answer_not_expired_when_fresh():
    from axiom_verified_answer_cache import _sign
    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    sig = _sign("fp", "ans", "", now)
    ca = CachedAnswer(
        fingerprint="fp", answer_text="ans",
        hits=1, verified_hits=1, promoted=True,
        context_key="", created_at=now,
        last_seen=now, ttl_days=30, signature=sig,
    )
    assert ca.is_expired() is False
