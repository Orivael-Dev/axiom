"""Tests for KnowledgeCookie — fragment tracking, promotion, signing, and CRUD."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "b" * 64)

from axiom_knowledge_cookie import (
    KnowledgeCookie,
    KnowledgeCookieStore,
    KnowledgeFragment,
    DEFAULT_COOKIE_PATH,
    from_env,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _store(tmp: Path) -> KnowledgeCookieStore:
    return KnowledgeCookieStore(tmp / "test.knowledge.json")


def _cookie_with_hits(sessions: int = 3, hit_count: int = 5) -> KnowledgeCookie:
    cookie = KnowledgeCookie()
    for i in range(sessions):
        cookie.record_hit(
            "Section 12: liability capped at $1M USD",
            f"legal/contracts.db:chunk_{i}",
            session_id=f"sess-{i:04d}",
        )
    for _ in range(hit_count - sessions):
        cookie.record_hit(
            "Section 12: liability capped at $1M USD",
            "legal/contracts.db:chunk_0",
            session_id="sess-0000",  # same session — only hit_count goes up
        )
    cookie.promote()
    return cookie


# ── record_hit ────────────────────────────────────────────────────────────────

class TestRecordHit:
    def test_creates_new_fragment(self):
        c = KnowledgeCookie()
        frag = c.record_hit("hello world", "src:1", session_id="s1")
        assert frag.content == "hello world"
        assert frag.content_hash in c.fragments

    def test_hit_count_increments(self):
        c = KnowledgeCookie()
        c.record_hit("hello", "src:1", session_id="s1")
        frag = c.record_hit("hello", "src:1", session_id="s1")
        assert frag.hit_count == 2

    def test_sessions_seen_increments_on_new_session(self):
        c = KnowledgeCookie()
        c.record_hit("hello", "src:1", session_id="s1")
        frag = c.record_hit("hello", "src:1", session_id="s2")
        assert frag.sessions_seen == 2

    def test_sessions_seen_not_incremented_on_same_session(self):
        c = KnowledgeCookie()
        c.record_hit("hello", "src:1", session_id="s1")
        frag = c.record_hit("hello", "src:1", session_id="s1")
        assert frag.sessions_seen == 1

    def test_finetune_emitted_at_threshold(self):
        c = KnowledgeCookie()
        for i in range(10):
            frag = c.record_hit("hello", "src:1", session_id=f"s{i}")
        assert frag.finetune_emitted is True

    def test_finetune_not_emitted_below_threshold(self):
        c = KnowledgeCookie()
        for i in range(9):
            frag = c.record_hit("hello", "src:1", session_id=f"s{i}")
        assert frag.finetune_emitted is False

    def test_first_seen_set_on_creation(self):
        c = KnowledgeCookie()
        frag = c.record_hit("hello", "src:1", session_id="s1")
        assert frag.first_seen != ""

    def test_last_seen_updated_on_hit(self):
        c = KnowledgeCookie()
        c.record_hit("hello", "src:1", session_id="s1")
        frag = c.record_hit("hello", "src:1", session_id="s2")
        assert frag.last_seen != ""

    def test_different_content_creates_separate_fragments(self):
        c = KnowledgeCookie()
        c.record_hit("alpha", "src:1", session_id="s1")
        c.record_hit("beta", "src:2", session_id="s1")
        assert len(c.fragments) == 2

    def test_sessions_list_capped_at_20(self):
        c = KnowledgeCookie()
        for i in range(25):
            c.record_hit("hello", "src:1", session_id=f"s{i:03d}")
        frag = list(c.fragments.values())[0]
        assert len(frag.sessions_list) <= 20
        assert frag.sessions_seen == 25   # count keeps going


# ── promote ───────────────────────────────────────────────────────────────────

class TestPromote:
    def test_promotes_at_threshold(self):
        c = KnowledgeCookie()
        for i in range(3):
            c.record_hit("legal text", "src:1", session_id=f"s{i}")
        c.promote()
        assert len(c.hot_knowledge) == 1
        assert c.hot_knowledge[0].promoted is True

    def test_not_promoted_below_threshold(self):
        c = KnowledgeCookie()
        for i in range(2):
            c.record_hit("legal text", "src:1", session_id=f"s{i}")
        c.promote()
        assert len(c.hot_knowledge) == 0

    def test_hot_knowledge_sorted_by_hit_count(self):
        c = KnowledgeCookie()
        for i in range(3):
            c.record_hit("high hit", "src:1", session_id=f"s{i}")
        for _ in range(5):
            c.record_hit("high hit", "src:1", session_id="s0")  # boosts hit_count
        for i in range(3):
            c.record_hit("low hit", "src:2", session_id=f"s{i}")
        c.promote()
        assert c.hot_knowledge[0].source_uri == "src:1"   # higher hit_count first

    def test_promote_is_idempotent(self):
        c = KnowledgeCookie()
        for i in range(3):
            c.record_hit("text", "src:1", session_id=f"s{i}")
        c.promote()
        c.promote()
        assert len(c.hot_knowledge) == 1


# ── extra_context ─────────────────────────────────────────────────────────────

class TestExtraContext:
    def test_empty_when_no_hot_fragments(self):
        c = KnowledgeCookie()
        assert c.to_extra_context() == {}

    def test_returns_hot_knowledge_key(self):
        c = _cookie_with_hits(sessions=3)
        ctx = c.to_extra_context()
        assert "hot_knowledge" in ctx

    def test_content_in_extra_context(self):
        c = _cookie_with_hits(sessions=3)
        ctx = c.to_extra_context()
        assert "Section 12" in ctx["hot_knowledge"]

    def test_max_fragments_limits_output(self):
        c = KnowledgeCookie()
        for j in range(5):
            for i in range(3):
                c.record_hit(f"fragment {j}", f"src:{j}", session_id=f"s{i}")
        c.promote()
        ctx = c.to_extra_context(max_fragments=2)
        # Two fragments joined by ---; count separators
        assert ctx["hot_knowledge"].count("---") == 1

    def test_to_prompt_prefix_empty_when_no_hot(self):
        assert KnowledgeCookie().to_prompt_prefix() == ""

    def test_to_prompt_prefix_starts_with_tag(self):
        c = _cookie_with_hits(sessions=3)
        assert c.to_prompt_prefix().startswith("[Hot knowledge]")


# ── serialisation ─────────────────────────────────────────────────────────────

class TestSerialisation:
    def test_round_trip_preserves_fragments(self):
        c = _cookie_with_hits(sessions=3)
        d = c.to_dict()
        c2 = KnowledgeCookie.from_dict(d)
        assert len(c2.fragments) == len(c.fragments)

    def test_hot_knowledge_not_in_dict(self):
        c = _cookie_with_hits(sessions=3)
        d = c.to_dict()
        assert "hot_knowledge" not in d

    def test_from_dict_calls_promote(self):
        c = _cookie_with_hits(sessions=3)
        d = c.to_dict()
        c2 = KnowledgeCookie.from_dict(d)
        assert len(c2.hot_knowledge) == 1

    def test_empty_cookie_serialises(self):
        d = KnowledgeCookie().to_dict()
        assert d["fragments"] == {}
        assert d["version"] == 1


# ── signing ───────────────────────────────────────────────────────────────────

class TestSigning:
    def test_sign_produces_signature(self):
        c = _cookie_with_hits(sessions=3).sign()
        assert len(c.signature) == 64

    def test_verify_passes_on_untampered(self):
        c = _cookie_with_hits(sessions=3).sign()
        assert c.verify()

    def test_verify_fails_on_no_signature(self):
        assert not KnowledgeCookie().verify()

    def test_verify_fails_on_tampered_session_count(self):
        c = _cookie_with_hits(sessions=3).sign()
        d = c.to_dict()
        d["session_count"] = 999
        c2 = KnowledgeCookie.from_dict(d)
        c2.signature = c.signature
        assert not c2.verify()


# ── KnowledgeCookieStore ──────────────────────────────────────────────────────

class TestKnowledgeCookieStore:
    def test_record_hit_saves_to_disk(self, tmp_path):
        store = _store(tmp_path)
        store.record_hit("text", "src:1", session_id="s1")
        assert (tmp_path / "test.knowledge.json").exists()

    def test_load_verifies_signature(self, tmp_path):
        store = _store(tmp_path)
        store.record_hit("text", "src:1", session_id="s1")
        c = store.load()
        assert c is not None
        assert c.verify()

    def test_load_returns_none_when_missing(self, tmp_path):
        assert _store(tmp_path).load() is None

    def test_load_returns_none_on_tampered_file(self, tmp_path):
        store = _store(tmp_path)
        store.record_hit("text", "src:1", session_id="s1")
        path = tmp_path / "test.knowledge.json"
        data = json.loads(path.read_text())
        data["session_count"] = 999
        path.write_text(json.dumps(data))
        assert store.load() is None

    def test_record_hit_cross_session_accumulates(self, tmp_path):
        store = _store(tmp_path)
        for sess in ["s1", "s2", "s3"]:
            store.record_hit("text", "src:1", session_id=sess)
        c = store.load()
        frag = list(c.fragments.values())[0]
        assert frag.sessions_seen == 3
        assert frag.hit_count == 3

    def test_record_hit_same_session_does_not_dup_sessions(self, tmp_path):
        store = _store(tmp_path)
        for _ in range(3):
            store.record_hit("text", "src:1", session_id="same-session")
        c = store.load()
        frag = list(c.fragments.values())[0]
        assert frag.sessions_seen == 1
        assert frag.hit_count == 3

    def test_promote_and_save_builds_hot_knowledge(self, tmp_path):
        store = _store(tmp_path)
        for sess in ["s1", "s2", "s3"]:
            store.record_hit("hot fragment", "src:1", session_id=sess)
        c = store.promote_and_save()
        assert len(c.hot_knowledge) == 1

    def test_promote_and_save_returns_verified_cookie(self, tmp_path):
        store = _store(tmp_path)
        for sess in ["s1", "s2", "s3"]:
            store.record_hit("hot fragment", "src:1", session_id=sess)
        c = store.promote_and_save()
        assert c.verify()

    def test_purge_removes_old_fragments(self, tmp_path):
        store = _store(tmp_path)
        store.record_hit("old fragment", "src:1", session_id="s1")
        # Forcibly backdate the last_seen to trigger purge
        c = store.load_or_empty()
        frag = list(c.fragments.values())[0]
        frag.last_seen = "2000-01-01T00:00:00Z"
        store.save(c)
        removed = store.purge(older_than_days=1)
        assert removed == 1
        c2 = store.load()
        assert len(c2.fragments) == 0

    def test_purge_keeps_recent_fragments(self, tmp_path):
        store = _store(tmp_path)
        store.record_hit("recent fragment", "src:1", session_id="s1")
        removed = store.purge(older_than_days=90)
        assert removed == 0

    def test_forget_all_deletes_file(self, tmp_path):
        store = _store(tmp_path)
        store.record_hit("text", "src:1", session_id="s1")
        store.forget_all()
        assert not (tmp_path / "test.knowledge.json").exists()
        assert store.load() is None


# ── from_env ─────────────────────────────────────────────────────────────────

class TestFromEnv:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("AXIOM_KNOWLEDGE_COOKIE", raising=False)
        assert from_env() is None

    def test_returns_none_when_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AXIOM_KNOWLEDGE_COOKIE", str(tmp_path / "no.json"))
        assert from_env() is None

    def test_returns_cookie_when_valid(self, monkeypatch, tmp_path):
        path = tmp_path / "test.json"
        store = KnowledgeCookieStore(path)
        for sess in ["s1", "s2", "s3"]:
            store.record_hit("hot text", "src:1", session_id=sess)
        store.promote_and_save()
        monkeypatch.setenv("AXIOM_KNOWLEDGE_COOKIE", str(path))
        c = from_env()
        assert c is not None
        assert c.verify()
        assert len(c.hot_knowledge) == 1

    def test_returns_none_on_tampered(self, monkeypatch, tmp_path):
        path = tmp_path / "test.json"
        store = KnowledgeCookieStore(path)
        store.record_hit("text", "src:1", session_id="s1")
        data = json.loads(path.read_text())
        data["session_count"] = 999
        path.write_text(json.dumps(data))
        monkeypatch.setenv("AXIOM_KNOWLEDGE_COOKIE", str(path))
        assert from_env() is None
