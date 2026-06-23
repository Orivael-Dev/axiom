"""Tests for axiom_delta_memory — Trifecta Pillar 2."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

from axiom_delta_memory import (
    DELTA_MAP_VERSION,
    DeltaMemoryMap,
    DeltaMemoryStore,
    DeltaState,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_state(**kw) -> DeltaState:
    return DeltaState(session_id="test-session", **kw)


def _key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-delta-memory-v1")


# ── CANNOT_MUTATE ─────────────────────────────────────────────────────────────

def test_cannot_mutate_version() -> None:
    import axiom_delta_memory as adm
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        adm.DELTA_MAP_VERSION = "9.9"  # type: ignore[misc]


# ── DeltaState immutability ───────────────────────────────────────────────────

def test_delta_state_is_frozen() -> None:
    s = _fresh_state()
    with pytest.raises((FrozenInstanceError, TypeError)):
        s.turn_count = 99  # type: ignore[misc]


# ── apply_delta ───────────────────────────────────────────────────────────────

def test_apply_delta_changes_only_dirty_field() -> None:
    dm = DeltaMemoryMap()
    s0 = _fresh_state(current_objective="Review NDA", active_constraints=("cite sources",))
    s1 = dm.apply_delta(s0, current_objective="Revise NDA clause 9")

    assert s1.current_objective    == "Revise NDA clause 9"
    assert s1.active_constraints   == ("cite sources",)    # unchanged
    assert s1.unresolved_questions == ()                   # unchanged
    assert s1.turn_count           == 0                    # unchanged
    assert s1 is not s0                                    # new object


def test_apply_delta_unknown_field_raises() -> None:
    dm = DeltaMemoryMap()
    s  = _fresh_state()
    with pytest.raises(ValueError, match="Unknown"):
        dm.apply_delta(s, nonexistent_field="oops")


# ── to_context_string ─────────────────────────────────────────────────────────

def test_to_context_string_empty_state_returns_empty() -> None:
    dm  = DeltaMemoryMap()
    s   = _fresh_state()
    ctx = dm.to_context_string(s)
    assert ctx == ""


def test_to_context_string_populated_is_valid_json_under_500_chars() -> None:
    dm = DeltaMemoryMap()
    s  = _fresh_state(
        current_objective="Optimise vector DB indexing",
        active_constraints=("4GB VRAM max", "C++ core"),
        unresolved_questions=("Benchmarking suite required?",),
    )
    ctx = dm.to_context_string(s)
    assert ctx != ""
    parsed = json.loads(ctx)   # must be valid JSON
    assert "current_objective" in parsed
    assert len(ctx) < 500


# ── extract_delta ─────────────────────────────────────────────────────────────

def test_extract_delta_question_query_adds_to_questions() -> None:
    dm    = DeltaMemoryMap()
    s     = _fresh_state()
    dirty = dm.extract_delta("The answer is 42.", "Is clause 9 valid?", s)
    assert "unresolved_questions" in dirty
    assert "Is clause 9 valid?" in dirty["unresolved_questions"]


def test_extract_delta_always_increments_turn_count() -> None:
    dm    = DeltaMemoryMap()
    s     = _fresh_state(turn_count=3)
    dirty = dm.extract_delta("Some output.", "Some query", s)
    assert dirty["turn_count"] == 4


def test_extract_delta_resolved_output_promotes_question() -> None:
    dm    = DeltaMemoryMap()
    s     = _fresh_state(unresolved_questions=("Is this safe?",))
    dirty = dm.extract_delta("Resolved: yes, it is safe.", "Check safety", s)
    assert "completed_milestones" in dirty
    assert "Is this safe?" in dirty["completed_milestones"]
    assert "Is this safe?" not in dirty.get("unresolved_questions", ())


def test_extract_delta_non_question_query_no_questions_added() -> None:
    dm    = DeltaMemoryMap()
    s     = _fresh_state()
    dirty = dm.extract_delta("Output text.", "Review the document", s)
    assert "unresolved_questions" not in dirty


# ── sign / verify ─────────────────────────────────────────────────────────────

def test_sign_verify_roundtrip() -> None:
    dm  = DeltaMemoryMap()
    key = _key()
    s   = _fresh_state(current_objective="Build vector DB")
    s_signed = dm.sign(s, key)
    assert s_signed.hmac_signature != ""
    assert dm.verify(s_signed, key) is True


def test_verify_fails_on_tampered_state() -> None:
    dm  = DeltaMemoryMap()
    key = _key()
    s   = dm.sign(_fresh_state(current_objective="Build vector DB"), key)
    tampered = DeltaState(
        session_id           = s.session_id,
        current_objective    = "TAMPERED",   # ← changed
        active_constraints   = s.active_constraints,
        completed_milestones = s.completed_milestones,
        unresolved_questions = s.unresolved_questions,
        turn_count           = s.turn_count,
        domain               = s.domain,
        last_updated         = s.last_updated,
        hmac_signature       = s.hmac_signature,
    )
    assert dm.verify(tampered, key) is False


# ── DeltaMemoryStore ──────────────────────────────────────────────────────────

def test_store_save_load_roundtrip() -> None:
    dm = DeltaMemoryMap()
    s  = _fresh_state(current_objective="Test objective", turn_count=2)

    with tempfile.TemporaryDirectory() as tmp:
        store = DeltaMemoryStore(path=Path(tmp) / "delta.jsonl")
        store.save("test-session", s)
        loaded = store.load("test-session")

    assert loaded is not None
    assert loaded.current_objective == "Test objective"
    assert loaded.turn_count        == 2


def test_store_forget_removes_session() -> None:
    s = _fresh_state(turn_count=1)
    with tempfile.TemporaryDirectory() as tmp:
        store = DeltaMemoryStore(path=Path(tmp) / "delta.jsonl")
        store.save("test-session", s)
        store.forget("test-session")
        assert store.load("test-session") is None


def test_store_purge_older_than_zero_removes_all() -> None:
    s = _fresh_state(last_updated="2020-01-01T00:00:00.000Z", turn_count=1)
    with tempfile.TemporaryDirectory() as tmp:
        store = DeltaMemoryStore(path=Path(tmp) / "delta.jsonl")
        store.save("test-session", s)
        purged = store.purge_older_than(days=0)
    assert purged >= 1


def test_store_load_returns_none_for_unknown_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = DeltaMemoryStore(path=Path(tmp) / "delta.jsonl")
        assert store.load("no-such-session") is None
