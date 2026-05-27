"""Data container + signing for 3D / multimodal event tokens.

Three signing namespaces (matches the four-layer trust pattern from
PR #9/#10 used elsewhere in the codebase):

  axiom-event-token-layer-v1  — each LayerReport independently signed
  axiom-event-token-coord-v1  — Coordinator decision (which agents
                                 fired, in what order) signed
  axiom-event-token-v1        — outer EventToken signed (transport
                                 integrity for the whole bundle)

Canonical signing form matches the Skill Pack pattern
(`axiom_firewall.skill_pack._canonical_payload`):
  json.dumps(payload, sort_keys=True, separators=(",", ":"),
             ensure_ascii=True).encode("utf-8")
with the signature field excluded.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from axiom_signing import derive_key

LAYER_KEY_NS  = b"axiom-event-token-layer-v1"
COORD_KEY_NS  = b"axiom-event-token-coord-v1"
TOKEN_KEY_NS  = b"axiom-event-token-v1"

EVENT_TOKEN_FORMAT_VERSION = "1.0"


# ─── Per-layer report ────────────────────────────────────────────────────


@dataclass(frozen=True)
class LayerReport:
    """One specialist agent's contribution to the event token.

    `agent` is the agent's stable identifier (e.g. "text", "audio",
    "video", "physics", "governance"). `payload` is the agent-specific
    body (free-form dict). `confidence` is the agent's self-rated
    confidence in [0.0, 1.0]. `signature` is the per-layer HMAC.
    """
    agent: str
    payload: dict
    confidence: float = 1.0
    signature: str = ""

    def to_dict(self) -> dict:
        return {
            "agent":      self.agent,
            "payload":    self.payload,
            "confidence": self.confidence,
            "signature":  self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LayerReport":
        return cls(
            agent=d["agent"],
            payload=d["payload"],
            confidence=float(d.get("confidence", 1.0)),
            signature=d.get("signature", ""),
        )

    @classmethod
    def signed(cls, *, agent: str, payload: dict,
               confidence: float = 1.0) -> "LayerReport":
        """Build + sign a LayerReport in one step."""
        unsigned = cls(agent=agent, payload=payload, confidence=confidence)
        sig = _sign(_canonical_layer(unsigned), LAYER_KEY_NS)
        return cls(agent=agent, payload=payload,
                   confidence=confidence, signature=sig)

    def verify(self) -> bool:
        """True iff this layer's signature was produced under LAYER_KEY_NS."""
        if not self.signature:
            return False
        expected = _sign(_canonical_layer(self), LAYER_KEY_NS)
        return hmac.compare_digest(self.signature, expected)


# ─── The 3D event token itself ──────────────────────────────────────────


@dataclass(frozen=True)
class EventToken:
    """Compact multimodal event with selectively-activated layer reports.

    Mirrors the AXIOM_EVENT_TOKEN block in the concept note:
      - id              stable identifier (caller-provided or auto)
      - format_version  "1.0", 2-year backward-compat per project standard
      - text/audio/tempo/video/physics/governance — six LayerReports OR
        None if the Coordinator did NOT activate that agent
      - parent_signature outer signature of the predecessor token in a
                        conversation chain (empty = chain root or
                        single-token use). Covered by the outer
                        signature when present.
      - coordinator_sig signature over the activation manifest +
                        layer signatures (composition integrity)
      - signature       outer HMAC over the canonical bundle
    """
    id: str
    format_version: str = EVENT_TOKEN_FORMAT_VERSION
    created_at: str = ""
    activated_agents: tuple[str, ...] = field(default_factory=tuple)

    text:        Optional[LayerReport] = None
    audio:       Optional[LayerReport] = None
    tempo:       Optional[LayerReport] = None
    vad:         Optional[LayerReport] = None
    voice:       Optional[LayerReport] = None
    qrf:         Optional[LayerReport] = None
    video:       Optional[LayerReport] = None
    physics:     Optional[LayerReport] = None
    governance:  Optional[LayerReport] = None

    parent_signature: str = ""
    coordinator_sig: str = ""
    signature: str = ""

    # ─── Serialization ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = {
            "id":              self.id,
            "format_version":  self.format_version,
            "created_at":      self.created_at,
            "activated_agents": list(self.activated_agents),
            "text":       self.text.to_dict()       if self.text       else None,
            "audio":      self.audio.to_dict()      if self.audio      else None,
            "tempo":      self.tempo.to_dict()      if self.tempo      else None,
            "vad":        self.vad.to_dict()        if self.vad        else None,
            "voice":      self.voice.to_dict()      if self.voice      else None,
            "qrf":        self.qrf.to_dict()        if self.qrf        else None,
            "video":      self.video.to_dict()      if self.video      else None,
            "physics":    self.physics.to_dict()    if self.physics    else None,
            "governance": self.governance.to_dict() if self.governance else None,
            "coordinator_sig": self.coordinator_sig,
            "signature":       self.signature,
        }
        # parent_signature is omitted when empty so tokens not part of
        # a chain serialize byte-identical to the pre-chaining format
        # — pre-existing signed tokens on disk still verify.
        if self.parent_signature:
            d["parent_signature"] = self.parent_signature
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "EventToken":
        return cls(
            id=d["id"],
            format_version=d.get("format_version", EVENT_TOKEN_FORMAT_VERSION),
            created_at=d.get("created_at", ""),
            activated_agents=tuple(d.get("activated_agents", ())),
            text=       LayerReport.from_dict(d["text"])       if d.get("text")       else None,
            audio=      LayerReport.from_dict(d["audio"])      if d.get("audio")      else None,
            tempo=      LayerReport.from_dict(d["tempo"])      if d.get("tempo")      else None,
            vad=        LayerReport.from_dict(d["vad"])        if d.get("vad")        else None,
            voice=      LayerReport.from_dict(d["voice"])      if d.get("voice")      else None,
            qrf=        LayerReport.from_dict(d["qrf"])        if d.get("qrf")        else None,
            video=      LayerReport.from_dict(d["video"])      if d.get("video")      else None,
            physics=    LayerReport.from_dict(d["physics"])    if d.get("physics")    else None,
            governance= LayerReport.from_dict(d["governance"]) if d.get("governance") else None,
            parent_signature=d.get("parent_signature", ""),
            coordinator_sig=d.get("coordinator_sig", ""),
            signature=d.get("signature", ""),
        )

    def to_json(self, *, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    # ─── Verification ───────────────────────────────────────────────

    def verify(self) -> bool:
        """Full verification: every layer signature + coordinator sig +
        outer token signature must all check out.
        """
        for layer in (self.text, self.audio, self.tempo, self.vad,
                      self.voice, self.qrf, self.video, self.physics,
                      self.governance):
            if layer is not None and not layer.verify():
                return False
        if self.coordinator_sig:
            expected_coord = _sign(_canonical_coordinator(self), COORD_KEY_NS)
            if not hmac.compare_digest(self.coordinator_sig, expected_coord):
                return False
        if not self.signature:
            return False
        expected_outer = _sign(_canonical_token(self), TOKEN_KEY_NS)
        return hmac.compare_digest(self.signature, expected_outer)


# ─── Canonical-form helpers + signing ───────────────────────────────────


def _canonical_layer(layer: LayerReport) -> bytes:
    """Layer canonical form excludes the signature field."""
    d = layer.to_dict()
    d.pop("signature", None)
    return _canonical(d)


def _canonical_coordinator(token: EventToken) -> bytes:
    """Coordinator canonical form: activation manifest + per-layer sigs.

    This is what the Coordinator signs — composition integrity. If any
    layer's report is silently swapped, the coordinator sig fails.
    """
    return _canonical({
        "id":               token.id,
        "format_version":   token.format_version,
        "activated_agents": list(token.activated_agents),
        "layer_signatures": {
            "text":        token.text.signature       if token.text       else None,
            "audio":       token.audio.signature      if token.audio      else None,
            "tempo":       token.tempo.signature      if token.tempo      else None,
            "vad":         token.vad.signature        if token.vad        else None,
            "voice":       token.voice.signature      if token.voice      else None,
            "qrf":         token.qrf.signature        if token.qrf        else None,
            "video":       token.video.signature      if token.video      else None,
            "physics":     token.physics.signature    if token.physics    else None,
            "governance":  token.governance.signature if token.governance else None,
        },
    })


def _canonical_token(token: EventToken) -> bytes:
    """Outer token canonical form excludes the outer signature field
    but INCLUDES the per-layer signatures + coordinator signature, so
    tampering anywhere inside breaks the outer signature.
    """
    d = token.to_dict()
    d.pop("signature", None)
    return _canonical(d)


def _canonical(d: dict) -> bytes:
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(payload: bytes, namespace: bytes) -> str:
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
