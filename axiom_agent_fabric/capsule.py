"""MiniSRDAgent — dormant signed capability capsule.

Two data structures:

  MiniSRDAgent   — full capsule stored in cold memory.  Fields cover
                   identity, wake conditions, tool permissions, memory
                   pointer, compression state, and governance limits.
                   Signed under CAPSULE_KEY_NS so tampering with any
                   field (e.g. tool_permissions) is detectable.

  VRAMAgentToken — lightweight hot-memory strip produced by
                   MiniSRDAgent.to_vram_token().  Only the fields the
                   AgentRouter needs for scoring and wake decisions are
                   kept; heavy fields (skills, governance_limits, etc.)
                   are omitted so the hot footprint is small.

Signing pattern mirrors axiom_event_token/models.py: canonical JSON
(sort_keys, no whitespace, exclude signature field) → HMAC-SHA256.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from axiom_signing import derive_key

CAPSULE_KEY_NS   = b"axiom-mini-srd-agent-v1"
VRAM_TOKEN_NS    = b"axiom-vram-agent-token-v1"

_CAPSULE_KEY: Optional[bytes] = None
_VRAM_KEY:    Optional[bytes] = None


def _capsule_key() -> bytes:
    global _CAPSULE_KEY
    if _CAPSULE_KEY is None:
        _CAPSULE_KEY = derive_key(CAPSULE_KEY_NS)
    return _CAPSULE_KEY


def _vram_key() -> bytes:
    global _VRAM_KEY
    if _VRAM_KEY is None:
        _VRAM_KEY = derive_key(VRAM_TOKEN_NS)
    return _VRAM_KEY


def _canonical(d: dict) -> bytes:
    """Canonical signing form: JSON with sorted keys, no whitespace, no signature."""
    payload = {k: v for k, v in d.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _hmac_sign(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def _role_embedding(role: str) -> list[float]:
    """Deterministic 8-float hash of role string (no neural model needed).

    Splits the SHA-256 digest into 8 big-endian uint32 values and
    normalises each to [0, 1] by dividing by 2^32-1.  Used for cheap
    cosine scoring in AgentRouter without loading an embedding model.
    """
    digest = hashlib.sha256(role.encode("utf-8")).digest()
    ints = struct.unpack(">8I", digest[:32])
    return [v / 0xFFFF_FFFF for v in ints]


# ─── MiniSRDAgent ────────────────────────────────────────────────────────────


@dataclass
class MiniSRDAgent:
    """Full signed capability capsule stored in cold memory.

    Fields
    ------
    agent_id          Stable unique identifier (e.g. "medical_researcher").
    role              One-line identity description used for cosine scoring.
    wake_conditions   Keywords that trigger scoring (matched against MET text).
    skills            Capabilities this agent offers.
    tool_permissions  Allowed tool IDs (e.g. ["pubmed", "web", "pdf_reader"]).
    memory_pointer    URI to dormant knowledge ("srd://bundle" or axm_path).
    compression_state Lifecycle: "dormant"|"idle"|"active"|"archived".
    governance_limits Constitutional constraints the agent must honour.
    axm_fingerprint   8-char fingerprint from the backing .axm file.
    bpw               Bits-per-weight after SRD pack (default 4.5).
    params_m          Parameter count in millions (≤500 constraint).
    signature         HMAC-SHA256 over all other fields.
    """
    agent_id:          str
    role:              str
    wake_conditions:   list[str]
    skills:            list[str]
    tool_permissions:  list[str]
    memory_pointer:    str
    compression_state: str
    governance_limits: list[str]
    axm_fingerprint:   str   = ""
    bpw:               float = 4.5
    params_m:          int   = 135
    signature:         str   = ""

    # ── Signing ──────────────────────────────────────────────────────

    def _as_dict(self) -> dict:
        return {
            "agent_id":          self.agent_id,
            "role":              self.role,
            "wake_conditions":   self.wake_conditions,
            "skills":            self.skills,
            "tool_permissions":  self.tool_permissions,
            "memory_pointer":    self.memory_pointer,
            "compression_state": self.compression_state,
            "governance_limits": self.governance_limits,
            "axm_fingerprint":   self.axm_fingerprint,
            "bpw":               self.bpw,
            "params_m":          self.params_m,
            "signature":         self.signature,
        }

    def sign(self) -> "MiniSRDAgent":
        """Return a copy of self with the signature field populated."""
        d    = self._as_dict()
        sig  = _hmac_sign(_canonical(d), _capsule_key())
        copy = MiniSRDAgent(**{k: v for k, v in d.items() if k != "signature"},
                             signature=sig)
        return copy

    def verify(self) -> bool:
        """True iff the capsule's signature matches its current fields."""
        if not self.signature:
            return False
        d        = self._as_dict()
        expected = _hmac_sign(_canonical(d), _capsule_key())
        return hmac.compare_digest(self.signature, expected)

    # ── Hot-strip ────────────────────────────────────────────────────

    def to_vram_token(self) -> "VRAMAgentToken":
        """Strip heavy fields, sign the hot token, and return it."""
        token = VRAMAgentToken(
            agent_id          = self.agent_id,
            role_embedding    = _role_embedding(self.role),
            memory_pointer    = self.memory_pointer,
            tool_map          = list(self.tool_permissions),
            wake_conditions   = list(self.wake_conditions),
            compression_state = self.compression_state,
            activation_cost   = max(1, self.params_m // 100),
        )
        return token.sign()

    # ── Lifecycle ────────────────────────────────────────────────────

    def activate(self) -> "MiniSRDAgent":
        """Return copy with compression_state = 'active'."""
        d = self._as_dict()
        d["compression_state"] = "active"
        d.pop("signature")
        return MiniSRDAgent(**d).sign()

    def sleep(self) -> "MiniSRDAgent":
        """Return copy with compression_state = 'dormant'."""
        d = self._as_dict()
        d["compression_state"] = "dormant"
        d.pop("signature")
        return MiniSRDAgent(**d).sign()

    def __repr__(self) -> str:
        return (f"MiniSRDAgent(id={self.agent_id!r}, "
                f"state={self.compression_state!r}, "
                f"bpw={self.bpw}, params_m={self.params_m})")


# ─── VRAMAgentToken ──────────────────────────────────────────────────────────


@dataclass
class VRAMAgentToken:
    """Lightweight hot-memory token — only what the AgentRouter needs.

    Produced by MiniSRDAgent.to_vram_token().  role_embedding is a
    deterministic 8-float hash of the role string; priority_score and
    activation_cost are set by the router after scoring.
    """
    agent_id:          str
    role_embedding:    list[float]
    memory_pointer:    str
    tool_map:          list[str]
    wake_conditions:   list[str]
    compression_state: str
    priority_score:    float = 0.0
    activation_cost:   int   = 1
    signature:         str   = ""

    def _as_dict(self) -> dict:
        return {
            "agent_id":          self.agent_id,
            "role_embedding":    self.role_embedding,
            "memory_pointer":    self.memory_pointer,
            "tool_map":          self.tool_map,
            "wake_conditions":   self.wake_conditions,
            "compression_state": self.compression_state,
            "priority_score":    self.priority_score,
            "activation_cost":   self.activation_cost,
            "signature":         self.signature,
        }

    def sign(self) -> "VRAMAgentToken":
        d   = self._as_dict()
        sig = _hmac_sign(_canonical(d), _vram_key())
        return VRAMAgentToken(
            agent_id          = self.agent_id,
            role_embedding    = self.role_embedding,
            memory_pointer    = self.memory_pointer,
            tool_map          = self.tool_map,
            wake_conditions   = self.wake_conditions,
            compression_state = self.compression_state,
            priority_score    = self.priority_score,
            activation_cost   = self.activation_cost,
            signature         = sig,
        )

    def verify(self) -> bool:
        if not self.signature:
            return False
        d        = self._as_dict()
        expected = _hmac_sign(_canonical(d), _vram_key())
        return hmac.compare_digest(self.signature, expected)
