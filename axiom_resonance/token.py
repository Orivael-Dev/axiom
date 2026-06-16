"""ResonanceSignal + ResonantEventToken — HMAC-signed resonance data structures.

Two data structures:

  ResonanceSignal    — per-event frequency/amplitude/phase/decay vector.
                       Signed under RESONANCE_KEY_NS.
  ResonantEventToken — EventToken enriched with a resonance signal.
                       Signed under RET_KEY_NS (references parent sigs
                       by hex, not by nesting full dicts).

Phase constants (radians):
  PHASE_STABLE    = 0.0     — normal operation, benign trajectory
  PHASE_UNCERTAIN = π / 2   — low-confidence or ambiguous signal
  PHASE_OPPOSING  = π       — adversarial / HARM / DECEIVE trajectory

Signing pattern mirrors axiom_agent_fabric/capsule.py exactly:
  module-level lazy key via derive_key(NAMESPACE), canonical JSON
  (sort_keys, no whitespace, exclude "signature" field), HMAC-SHA256.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import struct
from dataclasses import dataclass
from typing import Optional

from axiom_signing import derive_key
from axiom_event_token.models import EventToken

RESONANCE_KEY_NS = b"axiom-resonance-signal-v1"
RET_KEY_NS       = b"axiom-resonant-event-token-v1"

_RESONANCE_KEY: Optional[bytes] = None
_RET_KEY:       Optional[bytes] = None


def _resonance_key() -> bytes:
    global _RESONANCE_KEY
    if _RESONANCE_KEY is None:
        _RESONANCE_KEY = derive_key(RESONANCE_KEY_NS)
    return _RESONANCE_KEY


def _ret_key() -> bytes:
    global _RET_KEY
    if _RET_KEY is None:
        _RET_KEY = derive_key(RET_KEY_NS)
    return _RET_KEY


def _canonical(d: dict) -> bytes:
    payload = {k: v for k, v in d.items() if k != "signature"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _hmac_sign(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


# ── Phase constants ────────────────────────────────────────────────────────────

PHASE_STABLE     = 0.0
PHASE_UNCERTAIN  = math.pi / 2   # ≈ 1.5708
PHASE_OPPOSING   = math.pi       # ≈ 3.1416

# ── Domain frequency helper ────────────────────────────────────────────────────


def domain_to_frequency(domain: str) -> float:
    """Deterministic float in [0, 1] from SHA-256 of domain string.

    Uses the first 4 bytes of SHA-256(domain) as a big-endian uint32
    divided by 0xFFFF_FFFF.  Identical approach to _role_embedding() in
    axiom_agent_fabric/capsule.py — no neural model needed.
    """
    digest = hashlib.sha256(domain.encode("utf-8")).digest()
    val = struct.unpack(">I", digest[:4])[0]
    return val / 0xFFFF_FFFF


# ── ResonanceSignal ────────────────────────────────────────────────────────────


@dataclass
class ResonanceSignal:
    """Per-event resonance vector derived from existing Axiom token outputs.

    Fields
    ------
    token_id    Stable ID linking this signal back to its source.
    domain      Human-readable domain label ("medical", "security", ...).
    frequency   float [0,1] — domain_to_frequency(domain).
    amplitude   float [0,1] — confidence * risk_amplification, capped 1.0.
    phase       float       — 0.0 (stable) | π/2 (uncertain) | π (opposing).
    decay       float [0,1] — exp(-age_seconds / 3600); 1.0 at creation.
    confidence  float       — direct from layer payload.
    risk_flags  list[str]   — risk cluster labels from governance layer.
    timestamp   str         — ISO-8601 UTC creation time.
    signature   str         — HMAC-SHA256 under RESONANCE_KEY_NS.
    """
    token_id:    str
    domain:      str
    frequency:   float
    amplitude:   float
    phase:       float
    decay:       float
    confidence:  float
    risk_flags:  list
    timestamp:   str
    signature:   str = ""

    def _as_dict(self) -> dict:
        return {
            "token_id":   self.token_id,
            "domain":     self.domain,
            "frequency":  self.frequency,
            "amplitude":  self.amplitude,
            "phase":      self.phase,
            "decay":      self.decay,
            "confidence": self.confidence,
            "risk_flags": self.risk_flags,
            "timestamp":  self.timestamp,
            "signature":  self.signature,
        }

    def sign(self) -> "ResonanceSignal":
        d   = self._as_dict()
        sig = _hmac_sign(_canonical(d), _resonance_key())
        return ResonanceSignal(
            token_id   = self.token_id,
            domain     = self.domain,
            frequency  = self.frequency,
            amplitude  = self.amplitude,
            phase      = self.phase,
            decay      = self.decay,
            confidence = self.confidence,
            risk_flags = list(self.risk_flags),
            timestamp  = self.timestamp,
            signature  = sig,
        )

    def verify(self) -> bool:
        if not self.signature:
            return False
        d        = self._as_dict()
        expected = _hmac_sign(_canonical(d), _resonance_key())
        return hmac.compare_digest(self.signature, expected)


# ── ResonantEventToken ─────────────────────────────────────────────────────────


@dataclass
class ResonantEventToken:
    """EventToken enriched with a resonance signal.

    The canonical signing payload references the EventToken and
    ResonanceSignal by their HMAC signatures only (not by nesting full
    dicts), following the pattern from AgentResult._signable().

    Fields
    ------
    event_token      Original signed EventToken from FabricCoordinator.
    resonance        ResonanceSignal encoded for this event.
    drift_direction  Direction derived from confidence trend:
                     "stable"|"toward_boundary"|"away_from_boundary".
    drift_score      Magnitude of confidence delta (absolute value).
    alert_level      Propagated from detector: "NONE"|"LOW"|"MEDIUM"|"HIGH"|"CRITICAL".
    parent_freq      frequency of the parent token's resonance (chain coherence).
    signature        HMAC-SHA256 under RET_KEY_NS.
    """
    event_token:     EventToken
    resonance:       ResonanceSignal
    drift_direction: str   = "stable"
    drift_score:     float = 0.0
    alert_level:     str   = "NONE"
    parent_freq:     float = 0.0
    signature:       str   = ""

    def _as_dict(self) -> dict:
        return {
            "event_token_id":  self.event_token.id,
            "event_token_sig": self.event_token.signature,
            "resonance_sig":   self.resonance.signature,
            "drift_direction": self.drift_direction,
            "drift_score":     round(self.drift_score, 6),
            "alert_level":     self.alert_level,
            "parent_freq":     round(self.parent_freq, 6),
            "signature":       self.signature,
        }

    def sign(self) -> "ResonantEventToken":
        d   = self._as_dict()
        sig = _hmac_sign(_canonical(d), _ret_key())
        return ResonantEventToken(
            event_token     = self.event_token,
            resonance       = self.resonance,
            drift_direction = self.drift_direction,
            drift_score     = self.drift_score,
            alert_level     = self.alert_level,
            parent_freq     = self.parent_freq,
            signature       = sig,
        )

    def verify(self) -> bool:
        if not self.signature:
            return False
        d        = self._as_dict()
        expected = _hmac_sign(_canonical(d), _ret_key())
        return hmac.compare_digest(self.signature, expected)
