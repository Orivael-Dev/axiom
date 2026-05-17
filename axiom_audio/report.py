"""Signed AudioReport.

Mirrors `axiom_event_token.models.LayerReport`: HMAC-SHA256 over the
canonical JSON form (sort_keys + compact separators + ensure_ascii),
signature excluded from the canonical form, key derived from the
namespace `axiom-audio-v1`.

A fresh namespace (NOT `axiom-event-token-layer-v1`) so an audio
report can be verified standalone — without pulling in the event-token
package — and so cross-namespace replay can't move a forged audio
payload into a different signing context.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from axiom_signing import derive_key

AUDIO_KEY_NS = b"axiom-audio-v1"


@dataclass(frozen=True)
class AudioReport:
    """One ambient-audio analysis result.

    `payload` carries the six fields the AudioAgent stub already documents
    (impact_profile, material_signature, decay_pattern, depth, width,
    rhythm) so the existing 3D-event-token Audio layer can adopt this
    drop-in. Extra free-form fields (e.g. `onset_count`, `peak_amp_db`)
    travel under `payload["debug"]` for trace + telemetry.
    """
    payload: dict
    confidence: float = 1.0
    signature: str = ""

    def to_dict(self) -> dict:
        return {
            "payload": self.payload,
            "confidence": self.confidence,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AudioReport":
        return cls(
            payload=d["payload"],
            confidence=float(d.get("confidence", 1.0)),
            signature=d.get("signature", ""),
        )

    @classmethod
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "AudioReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned), AUDIO_KEY_NS)
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        expected = _sign(_canonical(self), AUDIO_KEY_NS)
        return hmac.compare_digest(self.signature, expected)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def _canonical(r: AudioReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(payload: bytes, namespace: bytes) -> str:
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()
