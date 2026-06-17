"""Tests for UserContextCookie — signing, CRUD, extra_context injection."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "a" * 64)

from axiom_user_cookie import (
    UserContextCookie,
    CookieStore,
    DEFAULT_COOKIE_PATH,
    from_env,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _store(tmp: Path) -> CookieStore:
    return CookieStore(tmp / "test.cookie.json")


def _full_cookie() -> UserContextCookie:
    return UserContextCookie(
        style="terse, no preamble",
        response_format="markdown",
        language="en",
        domain_expertise={"security": "expert", "legal": "beginner"},
        active_project="legal RAG benchmark",
        active_goals=["improve Hit@10", "reduce latency"],
        topics_blocked=["politics"],
        created_at="2026-06-17T00:00:00Z",
        updated_at="2026-06-17T00:00:00Z",
    )


# ── signing ───────────────────────────────────────────────────────────────────

class TestSigning:
    def test_sign_produces_non_empty_signature(self):
        c = _full_cookie().sign()
        assert c.signature and len(c.signature) == 64

    def test_verify_passes_on_untampered_cookie(self):
        c = _full_cookie().sign()
        assert c.verify()

    def test_verify_fails_on_tampered_style(self):
        c = _full_cookie().sign()
        tampered = UserContextCookie(**{**c.__dict__, "style": "verbose"})
        assert not tampered.verify()

    def test_verify_fails_on_tampered_active_project(self):
        c = _full_cookie().sign()
        tampered = UserContextCookie(**{**c.__dict__, "active_project": "HACKED"})
        assert not tampered.verify()

    def test_verify_fails_with_empty_signature(self):
        c = _full_cookie()   # not signed
        assert not c.verify()

    def test_signature_not_in_signing_payload(self):
        c1 = _full_cookie().sign()
        # Changing only the signature field should not break re-verification
        # (signature is excluded from the signable payload)
        c2 = UserContextCookie(**{**c1.__dict__, "signature": "00" * 32})
        # c2 has a wrong signature — verify should fail because "00"*32 != real sig
        assert not c2.verify()


# ── serialisation ─────────────────────────────────────────────────────────────

class TestSerialisation:
    def test_round_trip_preserves_all_fields(self):
        c = _full_cookie().sign()
        d = c.to_dict()
        c2 = UserContextCookie.from_dict(d)
        assert c2.style == c.style
        assert c2.domain_expertise == c.domain_expertise
        assert c2.active_goals == c.active_goals
        assert c2.signature == c.signature

    def test_from_dict_ignores_unknown_keys(self):
        d = _full_cookie().sign().to_dict()
        d["unknown_future_field"] = "ignored"
        c = UserContextCookie.from_dict(d)
        assert c.style == "terse, no preamble"

    def test_empty_cookie_serialises(self):
        c = UserContextCookie()
        d = c.to_dict()
        assert d["style"] == ""
        assert d["domain_expertise"] == {}
        assert d["active_goals"] == []


# ── extra_context injection ───────────────────────────────────────────────────

class TestExtraContext:
    def test_non_empty_fields_present(self):
        c = _full_cookie()
        ctx = c.to_extra_context()
        assert "user_style" in ctx
        assert "terse" in ctx["user_style"]

    def test_expertise_formatted_as_pairs(self):
        c = _full_cookie()
        ctx = c.to_extra_context()
        assert "user_expertise" in ctx
        assert "security=expert" in ctx["user_expertise"]
        assert "legal=beginner" in ctx["user_expertise"]

    def test_active_project_present(self):
        c = _full_cookie()
        ctx = c.to_extra_context()
        assert ctx["active_project"] == "legal RAG benchmark"

    def test_active_goals_semicolon_joined(self):
        c = _full_cookie()
        ctx = c.to_extra_context()
        assert "improve Hit@10" in ctx["active_goals"]
        assert ";" in ctx["active_goals"]

    def test_empty_cookie_produces_empty_context(self):
        assert UserContextCookie().to_extra_context() == {}

    def test_default_language_not_emitted(self):
        c = UserContextCookie(language="en")
        ctx = c.to_extra_context()
        assert "user_language" not in ctx

    def test_non_english_language_emitted(self):
        c = UserContextCookie(language="fr")
        ctx = c.to_extra_context()
        assert ctx.get("user_language") == "fr"

    def test_topics_blocked_not_in_context(self):
        # topics_blocked is private — should NOT be sent to the LLM
        c = _full_cookie()
        ctx = c.to_extra_context()
        assert "topics_blocked" not in ctx


# ── system_prompt_prefix ──────────────────────────────────────────────────────

class TestSystemPromptPrefix:
    def test_non_empty_for_full_cookie(self):
        assert _full_cookie().to_system_prompt_prefix() != ""

    def test_empty_for_bare_cookie(self):
        assert UserContextCookie().to_system_prompt_prefix() == ""

    def test_starts_with_user_context_tag(self):
        p = _full_cookie().to_system_prompt_prefix()
        assert p.startswith("[User context]")


# ── CookieStore save / load ───────────────────────────────────────────────────

class TestCookieStoreSaveLoad:
    def test_save_creates_file(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        assert (tmp_path / "test.cookie.json").exists()

    def test_load_returns_verified_cookie(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        c = store.load()
        assert c is not None
        assert c.verify()
        assert c.style == "terse, no preamble"

    def test_load_returns_none_when_missing(self, tmp_path):
        assert _store(tmp_path).load() is None

    def test_load_returns_none_on_tampered_file(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        path = tmp_path / "test.cookie.json"
        data = json.loads(path.read_text())
        data["style"] = "TAMPERED"
        path.write_text(json.dumps(data))
        assert store.load() is None

    def test_load_or_empty_returns_blank_when_missing(self, tmp_path):
        c = _store(tmp_path).load_or_empty()
        assert c.style == ""

    def test_file_contains_valid_json(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        data = json.loads((tmp_path / "test.cookie.json").read_text())
        assert "signature" in data
        assert len(data["signature"]) == 64


# ── CookieStore.update ────────────────────────────────────────────────────────

class TestCookieStoreUpdate:
    def test_update_creates_cookie_when_missing(self, tmp_path):
        store = _store(tmp_path)
        c = store.update(style="verbose")
        assert c.style == "verbose"
        assert c.verify()

    def test_update_preserves_unmentioned_fields(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        store.update(active_project="new project")
        c = store.load()
        assert c.active_project == "new project"
        assert c.style == "terse, no preamble"   # unchanged

    def test_update_merges_domain_expertise(self, tmp_path):
        store = _store(tmp_path)
        store.update(domain_expertise={"security": "expert"})
        store.update(domain_expertise={"legal": "beginner"})
        c = store.load()
        assert c.domain_expertise == {"security": "expert", "legal": "beginner"}

    def test_update_sets_updated_at(self, tmp_path):
        store = _store(tmp_path)
        c = store.update(style="terse")
        assert c.updated_at != ""

    def test_update_sets_created_at_once(self, tmp_path):
        store = _store(tmp_path)
        c1 = store.update(style="terse")
        import time; time.sleep(0.01)
        c2 = store.update(style="verbose")
        assert c1.created_at == c2.created_at   # created_at never changes

    def test_immutable_fields_cannot_be_overwritten(self, tmp_path):
        store = _store(tmp_path)
        store.update(style="terse")
        c_before = store.load()
        store.update(version=99, created_at="1970-01-01T00:00:00Z")
        c_after = store.load()
        assert c_after.version == c_before.version
        assert c_after.created_at == c_before.created_at


# ── CookieStore.forget ────────────────────────────────────────────────────────

class TestCookieStoreForget:
    def test_forget_clears_field(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        store.forget("active_project")
        c = store.load()
        assert c.active_project == ""

    def test_forget_preserves_other_fields(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        store.forget("active_project")
        c = store.load()
        assert c.style == "terse, no preamble"

    def test_forget_multiple_fields(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        store.forget("active_project", "active_goals", "style")
        c = store.load()
        assert c.active_project == ""
        assert c.active_goals == []
        assert c.style == ""

    def test_forget_immutable_field_is_noop(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        c_before = store.load()
        store.forget("version", "created_at")
        c_after = store.load()
        assert c_after.version == c_before.version
        assert c_after.created_at == c_before.created_at

    def test_forget_domain(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        store.forget_domain("security")
        c = store.load()
        assert "security" not in c.domain_expertise
        assert "legal" in c.domain_expertise

    def test_forget_all_removes_file(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        store.forget_all()
        assert not (tmp_path / "test.cookie.json").exists()
        assert store.load() is None


# ── goal helpers ──────────────────────────────────────────────────────────────

class TestGoals:
    def test_add_goal(self, tmp_path):
        store = _store(tmp_path)
        store.add_goal("improve Hit@10")
        store.add_goal("reduce latency")
        c = store.load()
        assert "improve Hit@10" in c.active_goals
        assert "reduce latency" in c.active_goals

    def test_add_goal_no_duplicates(self, tmp_path):
        store = _store(tmp_path)
        store.add_goal("improve Hit@10")
        store.add_goal("improve Hit@10")
        c = store.load()
        assert c.active_goals.count("improve Hit@10") == 1

    def test_remove_goal(self, tmp_path):
        store = _store(tmp_path)
        store.save(_full_cookie())
        store.remove_goal("improve Hit@10")
        c = store.load()
        assert "improve Hit@10" not in c.active_goals
        assert "reduce latency" in c.active_goals


# ── from_env ─────────────────────────────────────────────────────────────────

class TestFromEnv:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("AXIOM_USER_COOKIE", raising=False)
        assert from_env() is None

    def test_returns_none_when_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AXIOM_USER_COOKIE", str(tmp_path / "no.cookie.json"))
        assert from_env() is None

    def test_returns_cookie_when_valid_file(self, monkeypatch, tmp_path):
        path = tmp_path / "test.cookie.json"
        store = CookieStore(path)
        store.save(_full_cookie())
        monkeypatch.setenv("AXIOM_USER_COOKIE", str(path))
        c = from_env()
        assert c is not None
        assert c.style == "terse, no preamble"

    def test_returns_none_when_file_tampered(self, monkeypatch, tmp_path):
        path = tmp_path / "test.cookie.json"
        store = CookieStore(path)
        store.save(_full_cookie())
        data = json.loads(path.read_text())
        data["active_project"] = "HACKED"
        path.write_text(json.dumps(data))
        monkeypatch.setenv("AXIOM_USER_COOKIE", str(path))
        assert from_env() is None
