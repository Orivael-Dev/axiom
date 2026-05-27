"""MedicalCoordinatorToken — binds multiple per-layer EventTokens into
one signed federated event per PDF section 4.

The PDF's Coordinator Token holds:
    event_id, summary, active_layers, primary_layer,
    layer_links: {source: tok_..., text: tok_..., ...},
    cross_layer_consistency, contradictions[], fusion_signature.

This module does NOT touch the existing EventToken dataclass. Each
medical "layer" (source/text/data/bio/physics/governance) is its own
signed EventToken; this object holds the layer_name → event_token_id
edges and an independent HMAC under namespace `axiom-medical-coord-v1`.

Verification path:
    coord.verify()                 — fusion signature only
    coord.verify(event_lookup={...})— also re-verifies every linked
                                      EventToken's signature chain
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterable, Optional

from axiom_signing import derive_key


COORD_KEY_NS = b"axiom-medical-coord-v1"


# Cross-token link types (PDF section 4). Stored as a frozenset so
# extending the vocabulary is a deliberate const change.
LINK_TYPES: frozenset[str] = frozenset({
    "supports", "contradicts", "depends_on",
    "same_entity", "requires_human_review",
})


# Medical layer names recognised by the coordinator. The 6 from PDF
# section 3 plus "challenger" / "contradiction" / "human_review"
# meta-layers that the activation profiles in
# `axiom_medical_agent.LAYER_ACTIVATION_PROFILES` may reference.
MEDICAL_LAYERS: frozenset[str] = frozenset({
    "source", "text", "claim", "data", "bio", "physics", "governance",
    "challenger", "contradiction", "human_review", "safety",
    "proposer",
})


class MedicalCoordError(ValueError):
    """Validation / signing / verification failure."""


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _coord_key() -> bytes:
    return derive_key(COORD_KEY_NS)


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


@dataclass(frozen=True)
class MedicalCoordinatorToken:
    """Federated medical-event binding — one signed object that points
    at the per-layer EventTokens making up a single research finding.

    Example:
        coord = MedicalCoordinatorToken.bind(
            event_tokens=[src_tok, claim_tok, data_tok, ...],
            layer_assignments={
                "source": src_tok.id,
                "text":   claim_tok.id,
                "data":   data_tok.id,
                ...
            },
            summary="GLP-1 drug → reduced inflammatory signaling",
            primary_layer="text",
        )
        assert coord.verify()
    """
    event_id:                str
    created_at:              str
    summary:                 str
    active_layers:           tuple[str, ...]
    primary_layer:           str
    layer_links:             dict[str, str]    # layer_name → event_token_id
    cross_layer_consistency: float
    contradictions:          tuple[str, ...]
    requires_human_review:   bool
    fusion_signature:        str = ""

    # ── factory ─────────────────────────────────────────────────────

    @classmethod
    def bind(
        cls,
        *,
        event_tokens: Iterable,
        layer_assignments: dict[str, str],
        summary: str,
        primary_layer: str = "text",
        contradictions: Iterable[str] = (),
        requires_human_review: bool = False,
        event_id: Optional[str] = None,
        cross_layer_consistency: Optional[float] = None,
    ) -> "MedicalCoordinatorToken":
        """Build + sign a coordinator token from a list of EventTokens.

        `layer_assignments` maps each medical layer name to the
        EventToken ID that supplies it. The IDs must all appear in
        `event_tokens`. `cross_layer_consistency` defaults to the
        mean of per-EventToken layer confidences (deterministic
        placeholder per the plan — embedding-similarity is future
        work).
        """
        tokens_list = list(event_tokens)
        token_by_id = {t.id: t for t in tokens_list}

        unknown_layers = set(layer_assignments) - MEDICAL_LAYERS
        if unknown_layers:
            raise MedicalCoordError(
                f"layer_assignments references unknown layer names: "
                f"{sorted(unknown_layers)}. Known: "
                f"{sorted(MEDICAL_LAYERS)}"
            )

        missing = [tid for tid in layer_assignments.values()
                   if tid not in token_by_id]
        if missing:
            raise MedicalCoordError(
                f"layer_assignments references event_token_ids not in "
                f"event_tokens: {missing}"
            )

        if primary_layer not in layer_assignments:
            raise MedicalCoordError(
                f"primary_layer {primary_layer!r} must be one of the "
                f"assigned layers: {sorted(layer_assignments)}"
            )

        if cross_layer_consistency is None:
            cross_layer_consistency = _mean_layer_confidence(
                tokens_list, layer_assignments,
            )

        unsigned = cls(
            event_id=event_id or f"medcoord_{uuid.uuid4().hex[:12]}",
            created_at=_now_iso(),
            summary=str(summary),
            active_layers=tuple(sorted(layer_assignments)),
            primary_layer=primary_layer,
            layer_links=dict(layer_assignments),
            cross_layer_consistency=float(cross_layer_consistency),
            contradictions=tuple(contradictions),
            requires_human_review=bool(requires_human_review),
            fusion_signature="",
        )
        sig = hmac.new(
            _coord_key(),
            _canonical(unsigned._payload_for_sig()),
            hashlib.sha256,
        ).hexdigest()
        # `replace` preserves tuple-ness on active_layers and
        # contradictions; round-tripping through to_dict would coerce
        # them to lists and break frozen-dataclass equality.
        return replace(unsigned, fusion_signature=sig)

    # ── verification ────────────────────────────────────────────────

    def verify(
        self,
        event_token_lookup: Optional[dict] = None,
    ) -> bool:
        """Check `fusion_signature` matches the canonical form.

        If `event_token_lookup` is provided (dict[event_id → EventToken])
        also re-verify every linked EventToken's signature chain.
        """
        if not self.fusion_signature:
            return False
        expected = hmac.new(
            _coord_key(),
            _canonical(self._payload_for_sig()),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(self.fusion_signature, expected):
            return False
        if event_token_lookup is not None:
            for layer_name, tid in self.layer_links.items():
                tok = event_token_lookup.get(tid)
                if tok is None:
                    return False
                if not tok.verify():
                    return False
        return True

    # ── serialization ───────────────────────────────────────────────

    def _payload_for_sig(self) -> dict:
        """Canonical body that the fusion_signature covers.

        Excludes the signature itself. `layer_links` is sorted via
        the JSON canonicaliser, so dict iteration order can't change
        the signature.
        """
        return {
            "event_id":                self.event_id,
            "created_at":              self.created_at,
            "summary":                 self.summary,
            "active_layers":           list(self.active_layers),
            "primary_layer":           self.primary_layer,
            "layer_links":             dict(self.layer_links),
            "cross_layer_consistency": round(float(
                self.cross_layer_consistency), 6),
            "contradictions":          list(self.contradictions),
            "requires_human_review":   bool(self.requires_human_review),
        }

    def to_dict(self) -> dict:
        d = self._payload_for_sig()
        d["fusion_signature"] = self.fusion_signature
        return d

    def to_json(self, *, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent,
                          ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "MedicalCoordinatorToken":
        return cls(
            event_id=d["event_id"],
            created_at=d.get("created_at", ""),
            summary=d.get("summary", ""),
            active_layers=tuple(d.get("active_layers", ())),
            primary_layer=d.get("primary_layer", "text"),
            layer_links=dict(d.get("layer_links", {})),
            cross_layer_consistency=float(
                d.get("cross_layer_consistency", 0.0)),
            contradictions=tuple(d.get("contradictions", ())),
            requires_human_review=bool(
                d.get("requires_human_review", False)),
            fusion_signature=d.get("fusion_signature", ""),
        )

    @classmethod
    def from_json(cls, s: str) -> "MedicalCoordinatorToken":
        return cls.from_dict(json.loads(s))


# ── helpers ──────────────────────────────────────────────────────────


def _mean_layer_confidence(
    tokens: list, layer_assignments: dict[str, str],
) -> float:
    """Mean of per-EventToken layer confidences for the bound layers.

    Each EventToken has up to 9 LayerReport slots; for the medical
    coordinator we sample the report on its primary slot (the slot
    the medical delegate's output landed in — usually `text`). If no
    confidence is available, the layer contributes 0.0.
    """
    confidences: list[float] = []
    by_id = {t.id: t for t in tokens}
    for _, tid in layer_assignments.items():
        tok = by_id.get(tid)
        if tok is None:
            continue
        layer = tok.text or tok.governance or tok.physics or tok.qrf
        if layer is not None:
            confidences.append(float(getattr(layer, "confidence", 0.0)))
    if not confidences:
        return 0.0
    return sum(confidences) / len(confidences)
