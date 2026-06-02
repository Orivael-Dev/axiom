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


# ── Named-block KV Cache DAG (v2) ────────────────────────────────────────────
#
# The DAG extends the flat KVCacheEntry model into a Git-like content-addressed
# graph where each logical context segment is a separate, independently reusable
# block.  Only downstream blocks need to recompute when a parent changes.
#
# Block topology (fixed order, DAG edges A→B→C→D→E):
#
#   A  system_prompt     — rarely changes; highest reuse across all sessions
#   B  dev_tool_rules    — changes per repository / tool-set loaded
#   C  user_profile      — changes per user-context or project loaded
#   D  rag_documents     — changes per retrieval batch
#   E  conversation_tail — changes every turn
#
# Deterministic key: kv_key = SHA-256( model_id | axm_fingerprint |
#   tokenizer_hash | rope_config | block_token_ids | position_offset |
#   dtype | quant_scheme )
#
# Verification chain: each block's signature covers its own fields + the
# parent's block_id, so the chain cannot be reordered or spliced silently.

BLOCK_TYPES: Tuple[str, ...] = ("A", "B", "C", "D", "E")
BLOCK_NAMES: dict = {
    "A": "system_prompt",
    "B": "dev_tool_rules",
    "C": "user_profile",
    "D": "rag_documents",
    "E": "conversation_tail",
}

KV_BLOCK_NS = b"axiom-kv-block-v1"


@dataclass(frozen=True)
class KVBlockKey:
    """Deterministic content-addressed lookup key for one KV cache block.

    All fields that affect the *numeric values* of the K/V tensors must
    appear here.  Two blocks are identical iff their KVBlockKey is identical.
    """
    model_id:        str   # HuggingFace model id or axm_fingerprint alias
    axm_fingerprint: str   # fingerprint() of the .axm archive ("" for non-AXM)
    tokenizer_hash:  str   # SHA-256 of tokenizer.json bytes
    rope_config:     str   # JSON {"base":..., "scaling":...} or "default"
    block_token_ids: str   # SHA-256 of the flat token-id list this block covers
    position_offset: int   # absolute position of the first token in this block
    dtype:           str   # "float16" | "bfloat16" | "float32"
    quant_scheme:    str   # "fp16" | "srd_7bpw" | "q4_k_m" | etc.
    kv_compression:  str   # "none" | "sq_paper" | "sq_validated" | "sq_edge"
                           # SpectralQuant preset applied to K/V tensors before
                           # signing.  Different values → different block_id so
                           # compressed and uncompressed caches can never mix.

    def hex(self) -> str:
        """Stable hex digest — use as cache lookup key / block_id."""
        canon = json.dumps(
            dataclasses.asdict(self), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(canon).hexdigest()

    @classmethod
    def from_token_ids(
        cls,
        token_ids: List[int],
        *,
        model_id: str,
        axm_fingerprint: str = "",
        tokenizer_hash: str = "",
        rope_config: str = "default",
        position_offset: int = 0,
        dtype: str = "float16",
        quant_scheme: str = "fp16",
        kv_compression: str = "none",
    ) -> "KVBlockKey":
        tid_hash = hashlib.sha256(
            json.dumps(token_ids, separators=(",", ":")).encode()
        ).hexdigest()
        return cls(
            model_id=model_id,
            axm_fingerprint=axm_fingerprint,
            tokenizer_hash=tokenizer_hash,
            rope_config=rope_config,
            block_token_ids=tid_hash,
            position_offset=position_offset,
            dtype=dtype,
            quant_scheme=quant_scheme,
            kv_compression=kv_compression,
        )


def _sign_block(
    block_id: str,
    parent_block_id: str,
    block_type: str,
    token_id: str,
    layer_slot: str,
    cache_hash: str,
    prompt_hash: str,
) -> str:
    namespace = KV_BLOCK_NS + block_type.encode()
    payload = (
        f"{block_id}|{parent_block_id}|{token_id}"
        f"|{layer_slot}|{cache_hash}|{prompt_hash}"
    ).encode()
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()


def _kv_fingerprint(block_id: str, parent_block_id: str, cache_hash: str) -> str:
    """Single hash linking block to EventToken.kv_sig — no tensor data embedded."""
    raw = f"{block_id}|{parent_block_id}|{cache_hash}".encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class KVCacheBlock:
    """A named, signed, content-addressed node in the KV cache DAG.

    Fields
    ------
    block_id        KVBlockKey.hex() — deterministic content-addressed id.
    block_type      "A" | "B" | "C" | "D" | "E" (see BLOCK_NAMES).
    parent_block_id block_id of the preceding block; "" for root (A).
    token_id        EventToken.id this block is bound to.
    layer_slot      One of LAYER_SLOTS ("text", "qrf", …).
    n_layers        Transformer layer count in the snapshot.
    seq_len         Number of cached tokens in this block.
    position_offset Absolute context-window offset of the first token.
    cache_hash      SHA-256 of all K/V tensor bytes (CPU fp16).
    kv_fingerprint  SHA-256(block_id|parent_block_id|cache_hash) — what
                    EventToken.kv_sig stores for this block.
    prompt_hash     SHA-256 of the raw text/token-ids this block covers.
    signature       HMAC-SHA256 over all identifying fields.
    created_at      Unix timestamp.
    """
    block_id:        str
    block_type:      str
    parent_block_id: str
    token_id:        str
    layer_slot:      str
    n_layers:        int
    seq_len:         int
    position_offset: int
    cache_hash:      str
    kv_fingerprint:  str
    prompt_hash:     str
    signature:       str
    created_at:      float

    # ── Construction ────────────────────────────────────────────────────

    @classmethod
    def from_past_key_values(
        cls,
        pkv: PastKeyValues,
        *,
        kv_key: KVBlockKey,
        block_type: str,
        parent_block_id: str = "",
        token_id: str,
        layer_slot: str,
        prompt_text: str = "",
    ) -> "KVCacheBlock":
        """Build and sign a KVCacheBlock from a HuggingFace past_key_values.

        Args:
            pkv:            past_key_values tuple from a model forward pass.
            kv_key:         Deterministic key covering all numeric-value inputs.
            block_type:     "A" through "E".
            parent_block_id: block_id of the predecessor (empty for block A).
            token_id:       EventToken.id this block belongs to.
            layer_slot:     LAYER_SLOTS value (e.g. "text").
            prompt_text:    Raw text for this block (used for prompt_hash only).
        """
        if block_type not in BLOCK_TYPES:
            raise ValueError(f"block_type {block_type!r} must be one of {BLOCK_TYPES}")
        if layer_slot not in LAYER_SLOTS:
            raise ValueError(f"layer_slot {layer_slot!r} not in LAYER_SLOTS")
        if not pkv:
            raise ValueError("past_key_values is empty")

        block_id    = kv_key.hex()
        n_layers    = len(pkv)
        seq_len     = pkv[0][0].shape[-2]
        cache_hash  = _hash_pkv(pkv)
        prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()
        kv_fp       = _kv_fingerprint(block_id, parent_block_id, cache_hash)
        sig = _sign_block(
            block_id, parent_block_id, block_type,
            token_id, layer_slot, cache_hash, prompt_hash,
        )
        return cls(
            block_id=block_id,
            block_type=block_type,
            parent_block_id=parent_block_id,
            token_id=token_id,
            layer_slot=layer_slot,
            n_layers=n_layers,
            seq_len=seq_len,
            position_offset=kv_key.position_offset,
            cache_hash=cache_hash,
            kv_fingerprint=kv_fp,
            prompt_hash=prompt_hash,
            signature=sig,
            created_at=time.time(),
        )

    # ── Verification ─────────────────────────────────────────────────────

    def verify(self) -> bool:
        """True iff the signature is valid under the current AXIOM_MASTER_KEY."""
        try:
            expected = _sign_block(
                self.block_id, self.parent_block_id, self.block_type,
                self.token_id, self.layer_slot, self.cache_hash, self.prompt_hash,
            )
            return hmac.compare_digest(self.signature, expected)
        except Exception:
            return False

    def verify_tensors(self, pkv: PastKeyValues) -> bool:
        return hmac.compare_digest(self.cache_hash, _hash_pkv(pkv))

    def verify_fingerprint(self) -> bool:
        expected = _kv_fingerprint(self.block_id, self.parent_block_id, self.cache_hash)
        return hmac.compare_digest(self.kv_fingerprint, expected)

    # ── Save / Load ──────────────────────────────────────────────────────

    def save(self, path: Path, pkv: PastKeyValues) -> None:
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
    ) -> Tuple["KVCacheBlock", _PkvList]:
        blob  = torch.load(Path(path), map_location="cpu", weights_only=False)
        meta  = blob["meta"]
        raw   = blob["pkv"]
        block = cls(**meta)
        if verify and not block.verify():
            raise ValueError(
                f"KV block signature verification failed for {path}."
            )
        if verify and not block.verify_tensors(tuple((k, v) for k, v in raw)):  # type: ignore
            raise ValueError(
                f"KV block tensor hash mismatch for {path}."
            )
        return block, raw

    # ── Restoration ─────────────────────────────────────────────────────

    @staticmethod
    def to_past_key_values(
        raw: "_PkvList",
        *,
        device: str,
        dtype: torch.dtype,
    ) -> "PastKeyValues":
        """Restore device-placed tensors from the raw list returned by load()."""
        return _list_to_pkv(raw, device=device, dtype=dtype)

    # ── Serialisation ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KVCacheBlock":
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


class KVCacheDAG:
    """Content-addressed DAG of KVCacheBlocks (one per context segment A–E).

    The DAG mirrors the structure of a Git commit chain: each block is
    identified by a deterministic hash of its inputs, and each block
    references its parent.  Callers can check whether any prefix of the
    chain is still valid (block_ids match), and skip re-computing those
    blocks.

    Usage
    -----
    dag = KVCacheDAG()
    block_a = KVCacheBlock.from_past_key_values(pkv_a, kv_key=key_a,
                  block_type="A", token_id=tid, layer_slot="text")
    dag.add(block_a, pkv_a)

    # Later: check whether block A is still valid
    if dag.block_still_valid("A", new_key_a):
        pkv_a, _ = dag.get_tensors("A")   # reuse — no prefill
    else:
        ... recompute ...
    """

    def __init__(self) -> None:
        self._blocks: dict[str, KVCacheBlock] = {}   # block_type → block
        self._tensors: dict[str, _PkvList]    = {}   # block_type → raw pkv list

    def add(self, block: KVCacheBlock, pkv: PastKeyValues) -> None:
        self._blocks[block.block_type]  = block
        self._tensors[block.block_type] = _pkv_to_list(pkv)

    def get(self, block_type: str) -> Optional[KVCacheBlock]:
        return self._blocks.get(block_type)

    def get_tensors(
        self, block_type: str, *, device: str = "cpu", dtype: torch.dtype = torch.float16
    ) -> Tuple[PastKeyValues, KVCacheBlock]:
        block = self._blocks[block_type]
        raw   = self._tensors[block_type]
        return _list_to_pkv(raw, device=device, dtype=dtype), block

    def block_still_valid(self, block_type: str, kv_key: KVBlockKey) -> bool:
        """True iff `block_type` is cached and its block_id matches `kv_key`."""
        block = self._blocks.get(block_type)
        return block is not None and block.block_id == kv_key.hex()

    def reusable_prefix(self, kv_keys: dict) -> List[str]:
        """Return block types that can be reused given updated keys.

        Args:
            kv_keys: mapping of block_type → KVBlockKey for the new request.

        Returns the longest leading sequence of BLOCK_TYPES whose cached
        block_id still matches.  The first mismatch and all downstream
        blocks must be recomputed.
        """
        reusable = []
        for bt in BLOCK_TYPES:
            if bt not in kv_keys:
                break
            if not self.block_still_valid(bt, kv_keys[bt]):
                break
            reusable.append(bt)
        return reusable

    def verify_all(self) -> bool:
        return all(b.verify() for b in self._blocks.values())

    def dag_sig(self) -> str:
        """SHA-256 of all block_ids in canonical topological order (A→E).

        Store this in EventToken.kv_sig to bind the full DAG to the token.
        """
        ordered = [
            self._blocks[bt].block_id
            for bt in BLOCK_TYPES
            if bt in self._blocks
        ]
        return hashlib.sha256(json.dumps(ordered, separators=(",", ":")).encode()).hexdigest()

    def save_all(self, directory: Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for bt, block in self._blocks.items():
            path = directory / f"block_{bt}.kvcache.pt"
            raw_pkv: PastKeyValues = tuple(  # type: ignore[assignment]
                (t[0], t[1]) for t in self._tensors[bt]
            )
            block.save(path, raw_pkv)

    @classmethod
    def load_all(cls, directory: Path, *, verify: bool = True) -> "KVCacheDAG":
        dag = cls()
        for bt in BLOCK_TYPES:
            path = Path(directory) / f"block_{bt}.kvcache.pt"
            if path.exists():
                block, raw = KVCacheBlock.load(path, verify=verify)
                pkv: PastKeyValues = tuple((k, v) for k, v in raw)  # type: ignore[misc]
                dag.add(block, pkv)
        return dag

    def to_dict(self) -> dict:
        return {bt: b.to_dict() for bt, b in self._blocks.items()}

    def bind_to_token(self, token: "EventToken") -> "EventToken":  # type: ignore[name-defined]
        """Return a new EventToken with kv_sig set to this DAG's dag_sig()."""
        from axiom_event_token.models import (
            EventToken, TOKEN_KEY_NS, _canonical_token, _sign,
        )
        import dataclasses as _dc
        unsigned = _dc.replace(token, kv_sig=self.dag_sig(), signature="")
        sig = _sign(_canonical_token(unsigned), TOKEN_KEY_NS)
        return _dc.replace(unsigned, signature=sig)
