"""Unit tests for axiom_event_token.kv_cache.

Tests focus on the signing / verification layer only — no real model or
transformers dependency. Fake past_key_values are small random tensors.
"""
from __future__ import annotations

import dataclasses
import os
import tempfile
from pathlib import Path

import pytest
import torch

os.environ.setdefault("AXIOM_MASTER_KEY", "a" * 64)  # stable test key

from axiom_event_token.kv_cache import KVCacheEntry, KVCacheStore, LAYER_SLOTS  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fake_pkv(n_layers: int = 3, seq_len: int = 8, heads: int = 4, dim: int = 16):
    """Return a fake past_key_values tuple (n_layers pairs of (k, v) tensors)."""
    return tuple(
        (torch.randn(1, heads, seq_len, dim),
         torch.randn(1, heads, seq_len, dim))
        for _ in range(n_layers)
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_kv_entry_sign_verify():
    """A freshly signed entry must verify successfully."""
    pkv = _fake_pkv()
    entry = KVCacheEntry.from_past_key_values(pkv, token_id="tok-001", layer_slot="text")

    assert entry.token_id == "tok-001"
    assert entry.layer_slot == "text"
    assert entry.n_layers == 3
    assert entry.seq_len == 8
    assert entry.signature != ""
    assert entry.verify(), "Freshly signed entry should verify"


def test_kv_entry_tamper_detected():
    """Mutating cache_hash directly breaks the signature check."""
    pkv = _fake_pkv()
    entry = KVCacheEntry.from_past_key_values(pkv, token_id="tok-002", layer_slot="qrf")
    tampered = dataclasses.replace(entry, cache_hash="00" * 32)
    assert not tampered.verify(), "Mutated cache_hash must not verify"


def test_kv_entry_wrong_key_rejected():
    """Verifying under a different AXIOM_MASTER_KEY must fail."""
    pkv = _fake_pkv()
    entry = KVCacheEntry.from_past_key_values(pkv, token_id="tok-003", layer_slot="text")
    assert entry.verify()

    old_key = os.environ["AXIOM_MASTER_KEY"]
    try:
        os.environ["AXIOM_MASTER_KEY"] = "b" * 64
        # derive_key is module-level cached — reload the module to pick up new key
        import importlib
        import axiom_signing
        import axiom_event_token.kv_cache as kvc
        importlib.reload(axiom_signing)
        importlib.reload(kvc)
        # Rebuild the entry under the new (wrong) key — should not match old sig
        bad_entry = kvc.KVCacheEntry.from_past_key_values(
            pkv, token_id="tok-003", layer_slot="text"
        )
        assert bad_entry.signature != entry.signature, (
            "Different key should produce different signature"
        )
    finally:
        os.environ["AXIOM_MASTER_KEY"] = old_key
        importlib.reload(axiom_signing)
        importlib.reload(kvc)


def test_kv_entry_token_id_mismatch_rejected():
    """Mutating token_id in the stored metadata must break verification."""
    pkv = _fake_pkv()
    entry = KVCacheEntry.from_past_key_values(pkv, token_id="tok-004", layer_slot="text")
    tampered = dataclasses.replace(entry, token_id="tok-EVIL")
    assert not tampered.verify(), "Mutated token_id must not verify"


def test_kv_entry_slot_mismatch_rejected():
    """Mutating layer_slot in the stored metadata must break verification."""
    pkv = _fake_pkv()
    entry = KVCacheEntry.from_past_key_values(pkv, token_id="tok-005", layer_slot="text")
    tampered = dataclasses.replace(entry, layer_slot="governance")
    assert not tampered.verify(), "Mutated layer_slot must not verify"


def test_kv_entry_round_trip_save_load():
    """save() + load() round-trips the entry and tensors bit-exactly."""
    pkv = _fake_pkv(n_layers=2, seq_len=6)
    entry = KVCacheEntry.from_past_key_values(pkv, token_id="tok-006", layer_slot="text")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.kvcache.pt"
        entry.save(path, pkv)

        loaded_entry, raw = KVCacheEntry.load(path, verify=True)

    assert loaded_entry == entry

    restored_pkv = KVCacheEntry.to_past_key_values(raw, device="cpu", dtype=torch.float32)
    assert len(restored_pkv) == len(pkv)
    for (rk, rv), (ok, ov) in zip(restored_pkv, pkv):
        # Stored as fp16, reloaded as fp32 — cast original to fp16 for comparison
        assert torch.allclose(rk, ok.half().float(), atol=1e-3)
        assert torch.allclose(rv, ov.half().float(), atol=1e-3)


def test_kv_store_sig_covers_all_entries():
    """KVCacheStore.store_sig() must change when any entry changes."""
    pkv_a = _fake_pkv(seq_len=4)
    pkv_b = _fake_pkv(seq_len=4)

    store1 = KVCacheStore()
    store1.add(KVCacheEntry.from_past_key_values(pkv_a, token_id="t", layer_slot="text"))
    store1.add(KVCacheEntry.from_past_key_values(pkv_b, token_id="t", layer_slot="qrf"))

    store2 = KVCacheStore()
    store2.add(KVCacheEntry.from_past_key_values(pkv_a, token_id="t", layer_slot="text"))
    store2.add(KVCacheEntry.from_past_key_values(pkv_b, token_id="DIFFERENT", layer_slot="qrf"))

    assert store1.store_sig() != store2.store_sig(), (
        "Different entries must produce different store sigs"
    )
    assert store1.verify_all()


def test_invalid_layer_slot_rejected():
    """from_past_key_values must raise ValueError for unknown slot names."""
    pkv = _fake_pkv()
    with pytest.raises(ValueError, match="LAYER_SLOTS"):
        KVCacheEntry.from_past_key_values(pkv, token_id="tok", layer_slot="INVALID")
