# tests/test_vector_state_store.py
# encoding: utf-8
# Tests for VectorStateStore — 3 BLOCKED + 3 PASSED
# Written BEFORE implementation per AXIOM test-first discipline.

import json
import os
import tempfile
import pytest

from axiom_vector_state_store import (
    VectorStateStore,
    VectorStateNotFoundError,
    VectorStateDuplicateError,
    VectorStateTamperError,
)

KEY    = b"axiom-state-store-test-key"
PROMPT = "Does vitamin D improve sleep quality?"
PHASH  = "8fb62a821e380138"   # deterministic for the test — not computed from prompt
VEC_A  = [0.991167, 0.495584]


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKED — must raise
# ══════════════════════════════════════════════════════════════════════════════

def test_blocked_restore_unknown_run_id():
    """BLOCKED: restore a run_id that was never stored → VectorStateNotFoundError."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        path = tmp.name
    try:
        store = VectorStateStore(KEY, store_path=path)
        with pytest.raises(VectorStateNotFoundError, match="ghost-run-999"):
            store.restore(PHASH, "ghost-run-999")
    finally:
        os.unlink(path)


def test_blocked_duplicate_run_id():
    """BLOCKED: store same prompt_hash + run_id twice → VectorStateDuplicateError on second call."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        path = tmp.name
    try:
        store = VectorStateStore(KEY, store_path=path)
        store.store(PHASH, "run-001", VEC_A, "LTV2-A", confidence=0.77)
        with pytest.raises(VectorStateDuplicateError, match="run-001"):
            store.store(PHASH, "run-001", VEC_A, "LTV2-A", confidence=0.77)
    finally:
        os.unlink(path)


def test_blocked_restore_tampered_entry():
    """BLOCKED: restore an entry whose vector was tampered after storage → VectorStateTamperError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                     delete=False, encoding="utf-8") as tmp:
        path = tmp.name

    try:
        store = VectorStateStore(KEY, store_path=path)
        store.store(PHASH, "run-tamper", VEC_A, "LTV2-T", confidence=0.77)

        # Tamper the stored entry — mutate the vector in the file
        with open(path, "r", encoding="utf-8") as f:
            entry = json.loads(f.read().strip())

        entry["intent_vector"] = [9.99, 9.99]   # mutation

        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        with pytest.raises(VectorStateTamperError, match="run-tamper"):
            store.restore(PHASH, "run-tamper")
    finally:
        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# PASSED — must succeed with correct values
# ══════════════════════════════════════════════════════════════════════════════

def test_passed_store_and_restore_round_trip():
    """PASSED: store a vector then restore it — returned vector is identical."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        path = tmp.name
    try:
        store = VectorStateStore(KEY, store_path=path)
        store.store(PHASH, "run-A", VEC_A, "LTV2-A", confidence=0.77)

        restored = store.restore(PHASH, "run-A")

        assert restored == pytest.approx(VEC_A, abs=1e-9), \
            f"Restored vector {restored} != original {VEC_A}"
    finally:
        os.unlink(path)


def test_passed_list_runs_sorted_by_timestamp():
    """PASSED: list_runs returns all entries for a prompt in insertion order, other prompts excluded."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        path = tmp.name
    try:
        store = VectorStateStore(KEY, store_path=path)

        # Three runs for PHASH
        store.store(PHASH, "run-1", [0.1],    "LTV2-1", confidence=0.70)
        store.store(PHASH, "run-2", [0.5],    "LTV2-2", confidence=0.75)
        store.store(PHASH, "run-3", [0.9],    "LTV2-3", confidence=0.80)
        # One run for a different prompt — must not appear
        store.store("other_hash", "run-X", [0.3], "LTV2-X", confidence=0.60)

        runs = store.list_runs(PHASH)

        assert len(runs) == 3, f"Expected 3 runs for PHASH, got {len(runs)}"
        assert [r["run_id"] for r in runs] == ["run-1", "run-2", "run-3"]
        # Each entry must have the required keys
        for r in runs:
            assert "run_id"       in r
            assert "manifest_id"  in r
            assert "intent_vector" in r
            assert "timestamp"    in r
    finally:
        os.unlink(path)


def test_passed_entry_signed_with_64_char_hmac():
    """PASSED: stored entry has 64-char hex signature covering all payload fields."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        path = tmp.name
    try:
        store = VectorStateStore(KEY, store_path=path)
        store.store(PHASH, "run-sig", VEC_A, "LTV2-S", confidence=0.77)

        with open(path, "r", encoding="utf-8") as f:
            entry = json.loads(f.read().strip())

        sig = entry.get("signature", "")
        assert isinstance(sig, str) and len(sig) == 64, \
            f"Expected 64-char hex signature, got {sig!r}"

        # Required fields all present
        for field in ("prompt_hash", "run_id", "intent_vector",
                      "manifest_id", "confidence", "timestamp"):
            assert field in entry, f"Missing field: {field}"
    finally:
        os.unlink(path)
