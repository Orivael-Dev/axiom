"""Signed ResearchReport — final output of the research engine.

HMAC-SHA256 over canonical JSON (sort_keys + compact separators +
ensure_ascii), signature excluded from canonical form, key derived
from namespace `axiom-research-v1`. Same pattern as AudioReport,
TempoReport, VADReport, VoiceReport — verify standalone, never
cross-namespace.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from axiom_signing import derive_key

RESEARCH_KEY_NS = b"axiom-research-v1"


@dataclass(frozen=True)
class ResearchReport:
    """Final research output, HMAC-signed.

    payload fields:
      query                   the original user question
      answer_markdown         the synthesized written report with citations
      branches                weighted reasoning branches from QRF (top-K of N)
      probability_band        QRF's confidence band — HIGH/MODERATE/LOW/UNCERTAIN
      top_branch              the highest-weight branch label
      citations               retrieved documents that backed the synthesis
      domain                  the QRF domain used (medical/financial/general/etc.)
      n_branches              how many branches QRF generated total
      n_killed                how many branches the monotonic gate killed
      synth_model             which LLM generated the answer (for audit)
      created_at              ISO timestamp
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
    def from_dict(cls, d: dict) -> "ResearchReport":
        return cls(
            payload=d["payload"],
            confidence=float(d.get("confidence", 1.0)),
            signature=d.get("signature", ""),
        )

    @classmethod
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "ResearchReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned), RESEARCH_KEY_NS)
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        expected = _sign(_canonical(self), RESEARCH_KEY_NS)
        return hmac.compare_digest(self.signature, expected)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def _canonical(r: ResearchReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(payload: bytes, namespace: bytes) -> str:
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()
