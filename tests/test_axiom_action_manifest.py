"""Tests for axiom_action_manifest — Layer 4 Governance Guard."""
from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from axiom_action_manifest import (
    DEFAULT_BLOCKED_PATHS,
    ActionManifest,
    ManifestStore,
    ManifestValidator,
    ManifestVerdict,
    _MANIFEST_VERSION,
    _derive_key,
)


@pytest.fixture()
def validator():
    return ManifestValidator()


@pytest.fixture()
def basic_manifest(validator):
    return validator.create(
        scope="Fix auth bug",
        allowed_paths=("src/auth/**", "tests/test_auth*"),
        allowed_tools=("Read", "Edit", "Bash", "Grep"),
        allowed_commands=("python3 -m pytest*", "git diff*", "git status*"),
        session_id="test-session-001",
    )


@pytest.fixture()
def store(tmp_path):
    return ManifestStore(path=tmp_path / "manifests.jsonl")


# ── Immutability ──────────────────────────────────────────────────────────────

def test_manifest_is_frozen(basic_manifest):
    with pytest.raises(FrozenInstanceError):
        basic_manifest.scope = "tampered"  # type: ignore[misc]


# ── Factory ───────────────────────────────────────────────────────────────────

def test_create_fields(validator):
    m = validator.create(
        scope="My task",
        allowed_paths=("src/**",),
        allowed_tools=("Edit",),
        allowed_commands=("git*",),
        session_id="abc",
    )
    assert m.session_id == "abc"
    assert m.scope == "My task"
    assert m.allowed_paths == ("src/**",)
    assert m.allowed_tools == ("Edit",)
    assert m.allowed_commands == ("git*",)
    assert m.version == _MANIFEST_VERSION
    assert m.hmac_signature == ""


# ── Signing ───────────────────────────────────────────────────────────────────

def test_sign_verify_roundtrip(validator, basic_manifest):
    key = _derive_key()
    signed = validator.sign(basic_manifest, key)
    assert signed.hmac_signature != ""
    assert validator.verify(signed, key)


def test_verify_fails_on_tamper(validator, basic_manifest):
    key = _derive_key()
    signed = validator.sign(basic_manifest, key)
    from dataclasses import asdict
    tampered = ActionManifest(**{**asdict(signed), "scope": "tampered scope"})
    assert not validator.verify(tampered, key)


def test_verify_fails_empty_signature(validator, basic_manifest):
    assert not validator.verify(basic_manifest, _derive_key())


# ── check_action — ALLOW ──────────────────────────────────────────────────────

def test_allow_in_scope_path(validator, basic_manifest):
    verdict, reason = validator.check_action(
        basic_manifest, "Edit", path="src/auth/login.py"
    )
    assert verdict == ManifestVerdict.ALLOW


def test_allow_wildcard_tools(validator):
    m = ManifestValidator().create(scope="x", allowed_tools=("*",))
    verdict, _ = validator.check_action(m, "Write", path="anywhere.py")
    assert verdict == ManifestVerdict.ALLOW


def test_allow_wildcard_commands(validator):
    m = ManifestValidator().create(scope="x", allowed_commands=("*",))
    verdict, _ = validator.check_action(m, "Bash", command="rm -rf /tmp/test")
    assert verdict == ManifestVerdict.ALLOW


# ── check_action — BLOCK ──────────────────────────────────────────────────────

def test_block_explicitly_blocked_path(validator, basic_manifest):
    verdict, reason = validator.check_action(
        basic_manifest, "Edit", path="/home/user/axiom/.env"
    )
    assert verdict == ManifestVerdict.BLOCK
    assert "blocked" in reason


def test_block_disallowed_tool(validator, basic_manifest):
    verdict, reason = validator.check_action(basic_manifest, "Write", path="src/auth/x.py")
    assert verdict == ManifestVerdict.BLOCK
    assert "Write" in reason


def test_block_path_outside_scope(validator, basic_manifest):
    verdict, reason = validator.check_action(
        basic_manifest, "Edit", path="src/billing/invoice.py"
    )
    assert verdict == ManifestVerdict.BLOCK
    assert "outside declared scope" in reason


# ── check_action — REVIEW ────────────────────────────────────────────────────

def test_review_command_outside_scope(validator, basic_manifest):
    verdict, reason = validator.check_action(
        basic_manifest, "Bash", command="curl https://example.com"
    )
    assert verdict == ManifestVerdict.REVIEW
    assert "scope" in reason


# ── Expiry ────────────────────────────────────────────────────────────────────

def test_not_expired_when_empty(validator, basic_manifest):
    assert not validator.is_expired(basic_manifest)


def test_expired_past_timestamp(validator, basic_manifest):
    from dataclasses import asdict
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    expired = ActionManifest(**{**asdict(basic_manifest), "expires_at": past})
    assert validator.is_expired(expired)
    verdict, reason = validator.check_action(expired, "Edit")
    assert verdict == ManifestVerdict.BLOCK
    assert "expired" in reason


# ── Store ─────────────────────────────────────────────────────────────────────

def test_store_save_load_roundtrip(store, basic_manifest):
    store.save(basic_manifest)
    loaded = store.load("test-session-001")
    assert loaded is not None
    assert loaded.scope == basic_manifest.scope
    assert loaded.allowed_paths == basic_manifest.allowed_paths


def test_store_forget(store, basic_manifest):
    store.save(basic_manifest)
    store.forget("test-session-001")
    assert store.load("test-session-001") is None


def test_store_purge_expired(store, validator):
    from dataclasses import asdict
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    m = validator.create(scope="x", session_id="expired-sess", expires_at=past)
    store.save(m)
    purged = store.purge_expired()
    assert purged == 1
    assert store.load("expired-sess") is None
