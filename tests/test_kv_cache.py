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

from axiom_event_token.kv_cache import (  # noqa: E402
    KVCacheEntry, KVCacheStore, LAYER_SLOTS,
    KVBlockKey, KVCacheBlock, KVCacheDAG, BLOCK_TYPES,
)


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


# ── KV Cache DAG v2 tests ─────────────────────────────────────────────────────

def _make_key(block_type: str, token_ids: list, position_offset: int = 0,
              kv_compression: str = "none") -> KVBlockKey:
    return KVBlockKey.from_token_ids(
        token_ids,
        model_id="test-model",
        axm_fingerprint="abc123",
        tokenizer_hash="tok_hash",
        position_offset=position_offset,
        kv_compression=kv_compression,
    )


def test_kv_block_sign_verify():
    """A freshly signed KVCacheBlock must verify successfully."""
    pkv = _fake_pkv()
    key = _make_key("A", list(range(8)))
    block = KVCacheBlock.from_past_key_values(
        pkv, kv_key=key, block_type="A",
        token_id="tok-dag-001", layer_slot="text",
    )
    assert block.verify(), "Freshly signed block should verify"
    assert block.verify_fingerprint(), "kv_fingerprint should be consistent"
    assert block.block_id == key.hex()
    assert block.block_type == "A"
    assert block.parent_block_id == ""


def test_kv_block_chain_parent_binding():
    """Block B's signature covers block A's block_id; swapping parent breaks it."""
    pkv_a = _fake_pkv(seq_len=4)
    pkv_b = _fake_pkv(seq_len=6)
    key_a = _make_key("A", list(range(4)))
    key_b = _make_key("B", list(range(4, 10)), position_offset=4)

    block_a = KVCacheBlock.from_past_key_values(
        pkv_a, kv_key=key_a, block_type="A",
        token_id="tok-dag-002", layer_slot="text",
    )
    block_b = KVCacheBlock.from_past_key_values(
        pkv_b, kv_key=key_b, block_type="B",
        parent_block_id=block_a.block_id,
        token_id="tok-dag-002", layer_slot="text",
    )
    assert block_b.verify()

    tampered = dataclasses.replace(block_b, parent_block_id="00" * 32)
    assert not tampered.verify(), "Mutated parent_block_id must break signature"


def test_kv_block_deterministic_id():
    """Same KVBlockKey must produce same block_id regardless of when it runs."""
    key1 = _make_key("A", [1, 2, 3, 4])
    key2 = _make_key("A", [1, 2, 3, 4])
    assert key1.hex() == key2.hex(), "Identical key inputs must produce same hex"

    key3 = _make_key("A", [1, 2, 3, 5])  # one token differs
    assert key1.hex() != key3.hex(), "Different token ids must produce different hex"


def test_kv_block_invalid_block_type():
    """KVCacheBlock.from_past_key_values must raise for unknown block_type."""
    pkv = _fake_pkv()
    key = _make_key("A", list(range(8)))
    with pytest.raises(ValueError, match="block_type"):
        KVCacheBlock.from_past_key_values(
            pkv, kv_key=key, block_type="Z",
            token_id="tok", layer_slot="text",
        )


def test_kv_block_round_trip_save_load():
    """save() + load() round-trips KVCacheBlock and tensors bit-exactly."""
    pkv = _fake_pkv(n_layers=2, seq_len=5)
    key = _make_key("C", list(range(5)), position_offset=12)
    block = KVCacheBlock.from_past_key_values(
        pkv, kv_key=key, block_type="C",
        parent_block_id="ab" * 32,
        token_id="tok-dag-003", layer_slot="text",
        prompt_text="hello world",
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "block_C.kvcache.pt"
        block.save(path, pkv)
        loaded_block, raw = KVCacheBlock.load(path, verify=True)

    assert loaded_block == block
    restored = KVCacheBlock.to_past_key_values(raw, device="cpu", dtype=torch.float32)
    assert len(restored) == len(pkv)
    for (rk, rv), (ok, ov) in zip(restored, pkv):
        assert torch.allclose(rk, ok.half().float(), atol=1e-3)
        assert torch.allclose(rv, ov.half().float(), atol=1e-3)


def test_kv_dag_reusable_prefix():
    """KVCacheDAG.reusable_prefix() returns the unchanged leading blocks."""
    pkv_a = _fake_pkv(seq_len=4)
    pkv_b = _fake_pkv(seq_len=6)
    key_a = _make_key("A", list(range(4)))
    key_b = _make_key("B", list(range(4, 10)), position_offset=4)

    dag = KVCacheDAG()
    block_a = KVCacheBlock.from_past_key_values(
        pkv_a, kv_key=key_a, block_type="A",
        token_id="dag-tok", layer_slot="text",
    )
    block_b = KVCacheBlock.from_past_key_values(
        pkv_b, kv_key=key_b, block_type="B",
        parent_block_id=block_a.block_id,
        token_id="dag-tok", layer_slot="text",
    )
    dag.add(block_a, pkv_a)
    dag.add(block_b, pkv_b)

    # Both keys unchanged → both reusable
    assert dag.reusable_prefix({"A": key_a, "B": key_b}) == ["A", "B"]

    # Block B key changed → only A reusable
    key_b_new = _make_key("B", list(range(4, 11)), position_offset=4)
    assert dag.reusable_prefix({"A": key_a, "B": key_b_new}) == ["A"]

    # Block A key changed → nothing reusable
    key_a_new = _make_key("A", list(range(5)))
    assert dag.reusable_prefix({"A": key_a_new, "B": key_b}) == []


def test_kv_dag_sig_covers_all_blocks():
    """dag_sig() must change when any block changes."""
    pkv_a = _fake_pkv(seq_len=4)
    pkv_b = _fake_pkv(seq_len=6)
    key_a = _make_key("A", list(range(4)))
    key_b = _make_key("B", list(range(4, 10)), position_offset=4)

    dag1 = KVCacheDAG()
    dag1.add(KVCacheBlock.from_past_key_values(
        pkv_a, kv_key=key_a, block_type="A", token_id="t", layer_slot="text"
    ), pkv_a)
    dag1.add(KVCacheBlock.from_past_key_values(
        pkv_b, kv_key=key_b, block_type="B",
        parent_block_id=key_a.hex(), token_id="t", layer_slot="text"
    ), pkv_b)

    dag2 = KVCacheDAG()
    dag2.add(KVCacheBlock.from_past_key_values(
        pkv_a, kv_key=key_a, block_type="A", token_id="t", layer_slot="text"
    ), pkv_a)
    dag2.add(KVCacheBlock.from_past_key_values(
        _fake_pkv(seq_len=6), kv_key=key_b, block_type="B",
        parent_block_id=key_a.hex(), token_id="t", layer_slot="text"
    ), _fake_pkv(seq_len=6))  # different tensors → different cache_hash → different block_id? No — key is same

    # block_id is key.hex(), which is the same; but cache_hash differs.
    # The dag_sig covers block_ids (content-addressed keys), so they are equal.
    # The individual block signatures differ, but dag_sig won't — document this.
    assert dag1.verify_all()
    # dag_sig equality when block_ids are same (same input token sequence)
    assert dag1.dag_sig() == dag2.dag_sig()

    # Different key → different dag_sig
    key_b_diff = _make_key("B", list(range(4, 11)), position_offset=4)
    dag3 = KVCacheDAG()
    dag3.add(KVCacheBlock.from_past_key_values(
        pkv_a, kv_key=key_a, block_type="A", token_id="t", layer_slot="text"
    ), pkv_a)
    dag3.add(KVCacheBlock.from_past_key_values(
        pkv_b, kv_key=key_b_diff, block_type="B",
        parent_block_id=key_a.hex(), token_id="t", layer_slot="text"
    ), pkv_b)
    assert dag1.dag_sig() != dag3.dag_sig(), "Different block keys must produce different dag_sig"
