"""Signed KV cache entries bound to EventToken layer slots.

The whitepaper (`axiom_event_token_kv_cache.pdf`) describes shifting the
Transformer KV cache from an opaque heap buffer into a cryptographically
bound state ledger. This module implements that binding:

    KVCacheEntry   — frozen dataclass carrying the HMAC signature and
                     metadata for a single `past_key_values` snapshot.
                     The raw tensors are stored in a sidecar .pt file;
                     the dataclass travels in JSON alongside the EventToken.

    KVCacheStore   — thin dict wrapper for multi-slot scenarios (e.g.
                     separate `text` and `qrf` caches for one token).

Signing follows the same three-tier pattern as `models.py`:
    namespace = KV_CACHE_NS + layer_slot.encode()   (per-slot isolation)
    payload   = "{token_id}|{layer_slot}|{cache_hash}".encode()
    signature = HMAC-SHA256(derive_key(namespace), payload)

Tamper vectors that are caught:
  • Any float changed in any K or V tensor → cache_hash changes → sig fails
  • layer_slot mutated in the saved meta  → HMAC payload changes → fails
  • token_id mutated in the saved meta   → HMAC payload changes → fails
  • Different AXIOM_MASTER_KEY           → derived key differs  → fails
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import torch

from axiom_signing import derive_key

KV_CACHE_NS = b"axiom-kv-cache-v1"

LAYER_SLOTS = frozenset({
    "text", "audio", "video", "physics",
    "tempo", "voice", "qrf", "vad", "governance",
})

# Type alias for the nested-tuple format HuggingFace uses.
# past_key_values: Tuple[Tuple[Tensor, Tensor], ...] — one pair per layer.
PastKeyValues = Tuple[Tuple[torch.Tensor, torch.Tensor], ...]

# Serialised form: List[List[Tensor, Tensor]] — JSON-incompatible tensors
# travel as a torch.save'd list alongside the JSON meta.
_PkvList = List[List[torch.Tensor]]


def _hash_pkv(pkv: PastKeyValues) -> str:
    """SHA-256 of all K and V tensor bytes (CPU float16 canonical form)."""
    h = hashlib.sha256()
    for k, v in pkv:
        h.update(k.detach().cpu().half().numpy().tobytes())
        h.update(v.detach().cpu().half().numpy().tobytes())
    return h.hexdigest()


def _sign_entry(token_id: str, layer_slot: str, cache_hash: str) -> str:
    namespace = KV_CACHE_NS + layer_slot.encode()
    payload   = f"{token_id}|{layer_slot}|{cache_hash}".encode()
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()


def _pkv_to_list(pkv: PastKeyValues) -> _PkvList:
    return [[k.detach().cpu().half(), v.detach().cpu().half()] for k, v in pkv]


def _list_to_pkv(raw: _PkvList, device: str, dtype: torch.dtype) -> PastKeyValues:
    return tuple(
        (k.to(device=device, dtype=dtype), v.to(device=device, dtype=dtype))
        for k, v in raw
    )


@dataclass(frozen=True)
class KVCacheEntry:
    """Signed metadata for a single `past_key_values` snapshot.

    The raw tensors are NOT stored in the dataclass — they live in a
    sidecar .pt file written by `save()`. The dataclass carries only
    the metadata needed to verify the signature before trusting the tensors.

    Fields
    ------
    token_id    EventToken.id that owns this cache entry.
    layer_slot  One of the nine LAYER_SLOTS from the whitepaper.
    n_layers    Number of transformer layers in the snapshot.
    seq_len     Number of cached prompt tokens.
    cache_hash  SHA-256 of all K/V tensor bytes (CPU fp16 canonical form).
    signature   HMAC-SHA256 over (token_id, layer_slot, cache_hash).
    created_at  Unix timestamp (float).
    """
    token_id:   str
    layer_slot: str
    n_layers:   int
    seq_len:    int
    cache_hash: str
    signature:  str
    created_at: float

    # ── Construction ────────────────────────────────────────────────────

    @classmethod
    def from_past_key_values(
        cls,
        pkv: PastKeyValues,
        *,
        token_id: str,
        layer_slot: str,
    ) -> "KVCacheEntry":
        """Build and sign a KVCacheEntry from a HuggingFace past_key_values tuple.

        Args:
            pkv:        The `past_key_values` tuple from a model forward pass.
            token_id:   The EventToken.id this cache will be bound to.
            layer_slot: Which whitepaper layer this maps to (e.g. "text").
        """
        if layer_slot not in LAYER_SLOTS:
            raise ValueError(f"layer_slot {layer_slot!r} not in LAYER_SLOTS")
        if not pkv:
            raise ValueError("past_key_values is empty")

        n_layers = len(pkv)
        seq_len  = pkv[0][0].shape[-2]   # (batch, heads, seq_len, head_dim)
        cache_hash = _hash_pkv(pkv)
        signature  = _sign_entry(token_id, layer_slot, cache_hash)
        return cls(
            token_id=token_id,
            layer_slot=layer_slot,
            n_layers=n_layers,
            seq_len=seq_len,
            cache_hash=cache_hash,
            signature=signature,
            created_at=time.time(),
        )

    # ── Verification ─────────────────────────────────────────────────────

    def verify(self) -> bool:
        """True iff the signature is valid under the current AXIOM_MASTER_KEY."""
        try:
            expected = _sign_entry(self.token_id, self.layer_slot, self.cache_hash)
            return hmac.compare_digest(self.signature, expected)
        except Exception:
            return False

    def verify_tensors(self, pkv: PastKeyValues) -> bool:
        """True iff the tensors hash to the stored cache_hash.

        Call this after loading tensors to confirm the sidecar .pt
        file hasn't been tampered with independently of the metadata.
        """
        return hmac.compare_digest(self.cache_hash, _hash_pkv(pkv))

    # ── Save / Load ──────────────────────────────────────────────────────

    def save(self, path: Path, pkv: PastKeyValues) -> None:
        """Write signed KV cache to `path`.

        Stores meta (JSON-serialisable) and raw tensors in a single
        torch.save'd dict so the file is self-contained.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {"meta": dataclasses.asdict(self), "pkv": _pkv_to_list(pkv)},
            path,
        )

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        verify: bool = True,
    ) -> Tuple["KVCacheEntry", _PkvList]:
        """Load a signed KV cache from `path`.

        Returns (entry, raw_pkv_list). Call `entry.to_past_key_values(raw)`
        to get device-placed tensors ready for model.generate().

        Raises
        ------
        ValueError  if `verify=True` and the signature check fails.
        """
        blob = torch.load(Path(path), map_location="cpu", weights_only=False)
        meta = blob["meta"]
        raw  = blob["pkv"]
        entry = cls(**meta)
        if verify and not entry.verify():
            raise ValueError(
                f"KV cache signature verification failed for {path}. "
                "The file may have been tampered or the AXIOM_MASTER_KEY differs."
            )
        if verify and not entry.verify_tensors(
            tuple((k, v) for k, v in raw)  # type: ignore[arg-type]
        ):
            raise ValueError(
                f"KV cache tensor hash mismatch for {path}. "
                "The .pt sidecar tensors were modified after signing."
            )
        return entry, raw

    # ── Restoration ─────────────────────────────────────────────────────

    @staticmethod
    def to_past_key_values(
        raw: _PkvList,
        *,
        device: str,
        dtype: torch.dtype,
    ) -> PastKeyValues:
        """Restore device-placed tensors from the raw list returned by load()."""
        return _list_to_pkv(raw, device=device, dtype=dtype)

    # ── Serialisation (metadata only) ───────────────────────────────────

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KVCacheEntry":
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


# ── Multi-slot store ─────────────────────────────────────────────────────────


class KVCacheStore:
    """Container for multiple KVCacheEntries (one per active layer slot).

    Typical use: a text-only session creates one entry under "text";
    a multimodal session might also cache a "qrf" fallback.

    The store itself is not signed — each individual entry carries its
    own HMAC.  The EventToken's `kv_sig` field (from models.py) stores
    the SHA-256 of the store's JSON representation for outer-token binding.
    """

    def __init__(self) -> None:
        self._entries: dict[str, KVCacheEntry] = {}

    def add(self, entry: KVCacheEntry) -> None:
        self._entries[entry.layer_slot] = entry

    def get(self, layer_slot: str) -> Optional[KVCacheEntry]:
        return self._entries.get(layer_slot)

    def verify_all(self) -> bool:
        return all(e.verify() for e in self._entries.values())

    def store_sig(self) -> str:
        """SHA-256 of the sorted JSON of all entry metadata.

        This is what EventToken.kv_sig stores — a single hash that
        covers all active slot entries without embedding tensor data
        into the token JSON.
        """
        canon = json.dumps(
            {slot: e.to_dict() for slot, e in sorted(self._entries.items())},
            sort_keys=True, separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canon).hexdigest()

    def bind_to_token(self, token: "EventToken") -> "EventToken":  # type: ignore[name-defined]
        """Return a new EventToken with kv_sig filled from this store.

        The outer token signature covers kv_sig, so tampering with any
        KV cache entry also breaks the EventToken's outer signature.
        """
        from axiom_event_token.models import (
            EventToken, TOKEN_KEY_NS, _canonical_token, _sign,
        )
        import dataclasses as _dc
        unsigned = _dc.replace(token, kv_sig=self.store_sig(), signature="")
        sig = _sign(_canonical_token(unsigned), TOKEN_KEY_NS)
        return _dc.replace(unsigned, signature=sig)

    def to_dict(self) -> dict:
        return {slot: e.to_dict() for slot, e in self._entries.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "KVCacheStore":
        store = cls()
        for entry_dict in d.values():
            store.add(KVCacheEntry.from_dict(entry_dict))
        return store
