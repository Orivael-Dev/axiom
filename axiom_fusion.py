"""
AXIOM Fusion — multimodal intent fusion over an EventToken (axiom-fusion-v1).
============================================================================
``ModalFusion.fuse(token: EventToken) -> FusedIntent``

Each present layer slot (text, audio, tempo, vad, voice, video, physics,
governance) is run through a modality extractor that returns ``ModalFeatures``
(intent signals + risk signals + confidence). Modalities vote for their intent
signals weighted by confidence; the top-6 accumulated signals form the
``intent_vector``. ``risk_clusters`` is the union across every modality (any
layer can raise a risk; a governance HARM/DECEIVE verdict propagates directly).
``fusion_confidence`` is the mean of modal confidences, capped at 0.85
(CANNOT_MUTATE). The result is HMAC-signed under ``axiom-fusion-v1`` and
self-verifies via ``verify()``.

Behaviours:
  - Absent layers contribute nothing (a None slot means the Coordinator did
    not fire that agent).
  - Physical-event modalities (audio + video) dominate text when they each
    fire multiple strong signals — by design, not a bug.
  - An empty token yields a signed fallback intent of ["ask_general"].

BUG-003 UTF-8 · BUG-007 .hexdigest() · BUG-008 .encode("utf-8") before HMAC
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import sys
import types as _types
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from axiom_signing import derive_key

FUSION_KEY_NS: bytes = b"axiom-fusion-v1"

# ── CANNOT_MUTATE constitutional constants ─────────────────────────────────
CONF_CAP: float = 0.85          # fusion_confidence ceiling
TOP_SIGNALS: int = 6            # intent_vector width
PHYSICAL_BOOST: float = 1.5     # audio/video dominance multiplier
STRONG_CONF: float = 0.6        # "strong" modal-confidence threshold

# Layer slots the fusion considers, in deterministic tie-break order. qrf is a
# forecast layer, not a present-moment modality, so it is intentionally excluded.
MODALITIES: tuple = (
    "text", "audio", "tempo", "vad", "voice", "video", "physics", "governance",
)
PHYSICAL: frozenset = frozenset({"audio", "video"})
RISK_INTENTS: frozenset = frozenset({"HARM", "DECEIVE"})
FALLBACK_SIGNAL: str = "ask_general"

# Default intent signal a modality emits when its payload names none.
_DEFAULT_SIGNAL = {
    "text": "text_intent", "audio": "audio_event", "tempo": "tempo_shift",
    "vad": "voice_activity", "voice": "speaker_identity", "video": "visual_event",
    "physics": "physical_state", "governance": "governance_review",
}

_FROZEN = frozenset({
    "CONF_CAP", "TOP_SIGNALS", "PHYSICAL_BOOST", "STRONG_CONF", "MODALITIES",
    "PHYSICAL", "RISK_INTENTS", "FALLBACK_SIGNAL", "FUSION_KEY_NS",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})


# ── data shapes ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModalFeatures:
    """One modality's contribution: intent signals + risk signals + confidence."""
    modality: str
    intent_signals: tuple = field(default_factory=tuple)
    risk_signals: tuple = field(default_factory=tuple)
    confidence: float = 0.0


@dataclass(frozen=True)
class FusedIntent:
    """Signed multimodal intent verdict (axiom-fusion-v1)."""
    intent_vector: tuple = field(default_factory=tuple)
    risk_clusters: tuple = field(default_factory=tuple)
    fusion_confidence: float = 0.0
    modalities: tuple = field(default_factory=tuple)
    timestamp: str = ""
    signature: str = ""

    def to_dict(self) -> dict:
        return {
            "intent_vector": list(self.intent_vector),
            "risk_clusters": list(self.risk_clusters),
            "fusion_confidence": self.fusion_confidence,
            "modalities": list(self.modalities),
            "timestamp": self.timestamp,
            "signature": self.signature,
        }

    def verify(self) -> bool:
        """True iff the signature was produced under axiom-fusion-v1 over the
        current field values — detects any tampering after signing."""
        if not self.signature:
            return False
        return hmac_lib.compare_digest(self.signature, _sign(_canonical(self)))


# ── signing ─────────────────────────────────────────────────────────────────

def _canonical(fi: FusedIntent) -> bytes:
    return json.dumps({
        "intent_vector": list(fi.intent_vector),
        "risk_clusters": list(fi.risk_clusters),
        "fusion_confidence": fi.fusion_confidence,
        "modalities": list(fi.modalities),
        "timestamp": fi.timestamp,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")  # BUG-008


def _sign(payload: bytes) -> str:
    return hmac_lib.new(derive_key(FUSION_KEY_NS), payload, hashlib.sha256).hexdigest()  # BUG-007


# ── modality extraction ─────────────────────────────────────────────────────

def _coerce_signals(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    try:
        return tuple(str(s) for s in value if str(s))
    except TypeError:
        return ()


def _extract(modality: str, layer: Any) -> ModalFeatures:
    """Read intent/risk signals + confidence from a layer's agent-specific
    payload, with a modality-appropriate default signal."""
    payload = getattr(layer, "payload", None) or {}

    signals = _coerce_signals(payload.get("intent_signals") or payload.get("signals"))
    if not signals:
        signals = (_DEFAULT_SIGNAL.get(modality, modality),)

    risk = set(_coerce_signals(payload.get("risk_signals") or payload.get("risk")))
    verdict = str(payload.get("intent_class") or payload.get("verdict") or "").upper()
    if verdict in RISK_INTENTS:           # governance/text HARM/DECEIVE → risk cluster
        risk.add(verdict)

    conf = getattr(layer, "confidence", None)
    if conf is None:
        conf = payload.get("aggregate_confidence", 0.5)
    conf = max(0.0, min(1.0, float(conf)))

    return ModalFeatures(modality=modality, intent_signals=signals,
                         risk_signals=tuple(sorted(risk)), confidence=conf)


# ── the fusion engine ───────────────────────────────────────────────────────

class ModalFusion:
    """Fuse an EventToken's present layers into a signed FusedIntent."""

    def __init__(self, hmac_key: Optional[bytes] = None):
        # Signing always uses the fixed axiom-fusion-v1 namespace; the optional
        # key is accepted for API symmetry with other AXIOM engines.
        self._key = hmac_key or derive_key(FUSION_KEY_NS)

    def fuse(self, token: Any) -> FusedIntent:
        feats: list[ModalFeatures] = []
        for m in MODALITIES:
            layer = getattr(token, m, None) if token is not None else None
            if layer is None:
                continue                       # absent layer contributes nothing
            feats.append(_extract(m, layer))

        if not feats:                          # empty token → signed fallback
            return self._finalize(FusedIntent(intent_vector=(FALLBACK_SIGNAL,)))

        # Weighted vote. Physical-event modalities (audio + video) dominate text
        # when they each fire multiple strong signals.
        weights: dict[str, float] = {}
        for f in feats:
            strong_physical = (
                f.modality in PHYSICAL
                and len(f.intent_signals) >= 2
                and f.confidence >= STRONG_CONF
            )
            w = f.confidence * (PHYSICAL_BOOST if strong_physical else 1.0)
            for s in f.intent_signals:
                weights[s] = weights.get(s, 0.0) + w

        ranked = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
        intent_vector = tuple(s for s, _ in ranked[:TOP_SIGNALS])

        risk_clusters = tuple(sorted({r for f in feats for r in f.risk_signals}))

        mean_conf = sum(f.confidence for f in feats) / len(feats)
        fusion_confidence = round(min(CONF_CAP, mean_conf), 6)

        return self._finalize(FusedIntent(
            intent_vector=intent_vector,
            risk_clusters=risk_clusters,
            fusion_confidence=fusion_confidence,
            modalities=tuple(f.modality for f in feats),
        ))

    @staticmethod
    def _finalize(fi: FusedIntent) -> FusedIntent:
        stamped = replace(fi, timestamp=datetime.now(timezone.utc).isoformat())
        return replace(stamped, signature=_sign(_canonical(stamped)))


def fuse(token: Any) -> FusedIntent:
    """Module-level convenience: fuse a token with a default ModalFusion."""
    return ModalFusion().fuse(token)
