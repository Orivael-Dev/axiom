"""Multimodal fusion layer for AXIOM.

Bridges the EventToken's optional modality layers (text, audio, video,
physics, governance) into a single FusedIntent that LatentTrace /
MultiplexRunner can consume without modification.

Design contract
---------------
- Each present layer's features are extracted with modality-specific
  extractors and confidence-weighted before merging.
- Absent layers (None slots) contribute nothing — the Coordinator did
  not activate that agent, so it has no vote.
- fusion_confidence is capped at CONFIDENCE_CAP (0.85) matching
  axiom_latent.py's constitutional constraint (CANNOT_MUTATE).
- FusedIntent is HMAC-signed under "axiom-fusion-v1" — a fresh
  namespace so a fused payload cannot be replayed as a raw layer report.
- FusedIntent.to_latent_state() produces a LatentState drop-in so
  LatentTrace and MultiplexRunner require zero changes.

Signing namespace
-----------------
  axiom-fusion-v1  — outer FusedIntent HMAC-SHA256

Modality extractors (all pure functions)
-----------------------------------------
  _extract_text(report)       → ModalFeatures
  _extract_audio(report)      → ModalFeatures   (also used for tempo/vad/voice)
  _extract_video(report)      → ModalFeatures
  _extract_physics(report)    → ModalFeatures
  _extract_governance(report) → ModalFeatures

Fusion algorithm
----------------
  1. For each present layer, call its extractor → ModalFeatures.
  2. Modal weight = layer.confidence (clipped to [0, 1]).
  3. Intent signals: accumulate weights per signal across modalities,
     sort descending, deduplicate, keep top 6.
  4. Risk clusters: union across all modalities (any layer can raise a risk).
  5. fusion_confidence = mean of modal confidences, capped at 0.85.
  6. compressed_plan: looked up by primary intent (same table as LatentTrace).
  7. Sign the canonical form.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Optional

from axiom_signing import derive_key
from axiom_event_token.models import EventToken, LayerReport

# ── Constitutional constant (CANNOT_MUTATE — must match axiom_latent.py) ──────
CONFIDENCE_CAP = 0.85

FUSION_KEY_NS = b"axiom-fusion-v1"

# ── Audio intent + risk signal tables ─────────────────────────────────────────

_AUDIO_IMPACT_SIGNALS: dict[str, str] = {
    "sharp_transient": "physical_impact_detected",
    "soft_transient":  "soft_impact_detected",
    "sustained":       "sustained_sound_detected",
    "silence":         "audio_silence",
}

_AUDIO_MATERIAL_SIGNALS: dict[str, str] = {
    "glass-like":   "fragile_material_event",
    "metal-like":   "rigid_material_event",
    "wood-like":    "organic_material_event",
    "fabric-like":  "soft_material_event",
}

_AUDIO_RHYTHM_SIGNALS: dict[str, str] = {
    "periodic":      "recurring_pattern",
    "irregular":     "irregular_pattern",
    "single_impact": "single_event",
}

# ── Video intent + risk signal tables ─────────────────────────────────────────

_VIDEO_MOTION_INTENT: dict[str, str] = {
    "erratic":      "erratic_motion_detected",
    "accelerating": "accelerating_motion_detected",
    "downward":     "downward_motion_detected",
    "upward":       "upward_motion_detected",
    "lateral":      "lateral_motion_detected",
    "static":       "static_scene",
}

_VIDEO_MOTION_RISK: dict[str, str] = {
    "erratic":      "erratic_motion",
    "accelerating": "rapid_acceleration",
}

# ── Governance verdict → (intent_signal, risk_signals) ───────────────────────

_GOVERNANCE_VERDICT: dict[str, tuple[str, list[str]]] = {
    "HARM":      ("governance_blocked",     ["harm_detected"]),
    "DECEIVE":   ("governance_blocked",     ["deception_detected"]),
    "REFUSE":    ("governance_refused",     ["refusal_triggered"]),
    "CLARIFY":   ("clarification_needed",   []),
    "INFORM":    ("governance_clear",       []),
    "UNCERTAIN": ("governance_uncertain",   []),
}

# ── Text intent heuristics (fallback when payload lacks intent_vector) ────────

_TEXT_INTENT_MAP: dict[str, str] = {
    "ask_boolean":        r"\b(does|is|are|can|will|do|did|was|were|has|have)\b",
    "ask_procedural":     r"\b(how|explain|describe|walk me through|steps)\b",
    "ask_causal":         r"\b(why|what causes|reason for|leads to)\b",
    "ask_factual":        r"\b(what is|what are|define|tell me|when|where|who)\b",
    "ask_recommendation": r"\b(should|recommend|best|better|suggest|advice)\b",
    "ask_comparative":    r"\b(compare|difference|vs|versus|between|contrast)\b",
    "ask_predictive":     r"\b(predict|forecast|future|likely|probability)\b",
}

# ── Compressed plan table (mirrors axiom_latent._PLANS) ──────────────────────

_PLANS: dict[str, list[str]] = {
    "ask_boolean":             ["classify_question", "retrieve_evidence", "evaluate_truth_value", "assign_confidence", "state_verdict"],
    "ask_procedural":          ["parse_goal", "decompose_steps", "sequence_dependencies", "validate_feasibility", "output_procedure"],
    "ask_causal":              ["parse_causal_chain", "identify_mechanism", "evaluate_evidence_quality", "assess_confounders", "synthesize_explanation"],
    "ask_factual":             ["parse_entity_query", "retrieve_facts", "verify_consistency", "assess_recency", "state_facts"],
    "ask_recommendation":      ["parse_context", "enumerate_options", "evaluate_tradeoffs", "apply_constraints", "formulate_recommendation"],
    "ask_comparative":         ["identify_subjects", "define_dimensions", "score_each_dimension", "weight_by_relevance", "summarize_comparison"],
    "ask_predictive":          ["parse_prediction_target", "identify_base_rates", "assess_influencing_factors", "quantify_uncertainty", "state_prediction"],
    "physical_impact_detected":["detect_event_onset", "classify_material", "assess_severity", "correlate_visual", "synthesize_event_report"],
    "erratic_motion_detected": ["identify_trajectory", "assess_predictability", "flag_anomaly", "correlate_audio", "synthesize_motion_report"],
    "governance_blocked":      ["parse_governance_verdict", "identify_violation", "apply_block_policy", "log_refusal", "emit_block_response"],
    "ask_general":             ["parse_input", "retrieve_context", "reason_over_context", "validate_reasoning", "formulate_response"],
    "default":                 ["parse_input", "retrieve_context", "reason_over_context", "validate_reasoning", "formulate_response"],
}

_MAX_INTENT_SIGNALS = 6  # cap to prevent vector bloat


# ══════════════════════════════════════════════════════════════════════════════
# Data structures
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ModalFeatures:
    """Normalized features extracted from one modality's LayerReport."""
    modality:       str
    intent_signals: list[str]   # ordered by priority within this modality
    risk_signals:   list[str]
    confidence:     float       # the layer's reported confidence, in [0, 1]


@dataclass
class FusedIntent:
    """Unified multimodal intent produced by ModalFusion.fuse().

    Drop-in compatible with axiom_latent.LatentState via .to_latent_state().

    intent_vector   — top signals ranked by confidence-weighted vote count
    risk_clusters   — union of all risk signals raised by any modality
    modalities      — ordered list of modalities that contributed
    modal_weights   — per-modality confidence scores (dict[str, float])
    fusion_confidence — weighted average, capped at CONFIDENCE_CAP (0.85)
    compressed_plan — reasoning steps derived from primary intent
    signature       — HMAC-SHA256 under axiom-fusion-v1
    """
    intent_vector:     list[str]
    risk_clusters:     list[str]
    modalities:        list[str]
    modal_weights:     dict
    fusion_confidence: float
    compressed_plan:   list[str]
    signature:         str = ""

    # ── Bridge to LatentTrace ────────────────────────────────────────────

    def to_latent_state(self):
        """Return an axiom_latent.LatentState compatible with LatentTrace.

        Lazy import avoids import-time circular dependency since
        axiom_latent imports axiom_latent_v2 which does not import
        axiom_fusion.
        """
        from axiom_latent import LatentState  # local import — intentional
        return LatentState(
            intent_vector=self.intent_vector,
            risk_clusters=self.risk_clusters,
            compressed_plan=self.compressed_plan,
            confidence=self.fusion_confidence,
        )

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "intent_vector":     self.intent_vector,
            "risk_clusters":     self.risk_clusters,
            "modalities":        self.modalities,
            "modal_weights":     self.modal_weights,
            "fusion_confidence": self.fusion_confidence,
            "compressed_plan":   self.compressed_plan,
            "signature":         self.signature,
        }

    def to_json(self, *, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def verify(self) -> bool:
        """True iff the signature was produced by ModalFusion under FUSION_KEY_NS."""
        if not self.signature:
            return False
        expected = _sign(_canonical_fused(self), FUSION_KEY_NS)
        return hmac.compare_digest(self.signature, expected)


# ══════════════════════════════════════════════════════════════════════════════
# Per-modality extractors
# ══════════════════════════════════════════════════════════════════════════════


def _extract_text(report: LayerReport) -> ModalFeatures:
    p = report.payload
    intent = list(p.get("intent_vector", []))
    risk   = list(p.get("risk_clusters", []))

    if not intent:
        content = str(p.get("content", p.get("text", p.get("message", ""))))
        for label, pattern in _TEXT_INTENT_MAP.items():
            if re.search(pattern, content, re.I):
                intent.append(label)
        if not intent:
            intent = ["ask_general"]

    return ModalFeatures(
        modality="text",
        intent_signals=intent[:4],
        risk_signals=risk[:4],
        confidence=max(0.0, min(1.0, report.confidence)),
    )


def _extract_audio(report: LayerReport) -> ModalFeatures:
    """Extractor for audio, tempo, vad, and voice layers."""
    p = report.payload
    intents: list[str] = []
    risks:   list[str] = []

    impact   = str(p.get("impact_profile", ""))
    material = str(p.get("material_signature", ""))
    rhythm   = str(p.get("rhythm", ""))
    depth    = float(p.get("depth", 0.0))
    width    = float(p.get("width", 0.0))

    if sig := _AUDIO_IMPACT_SIGNALS.get(impact):
        intents.append(sig)
    if sig := _AUDIO_MATERIAL_SIGNALS.get(material):
        intents.append(sig)
    if sig := _AUDIO_RHYTHM_SIGNALS.get(rhythm):
        intents.append(sig)

    # Risk: strong low-frequency energy → high-energy event
    if depth > 0.6:
        risks.append("high_energy_event")
    # Risk: sharp broadband transient
    if impact == "sharp_transient" and width > 0.5:
        risks.append("sharp_impact_risk")

    # Voice activity detection signals
    if p.get("speech_detected") or p.get("voice_active"):
        intents.append("voice_present")
    if p.get("tempo_bpm") is not None:
        intents.append("rhythmic_content")

    return ModalFeatures(
        modality=report.agent if report.agent else "audio",
        intent_signals=intents,
        risk_signals=risks,
        confidence=max(0.0, min(1.0, report.confidence)),
    )


def _extract_video(report: LayerReport) -> ModalFeatures:
    p = report.payload
    intents: list[str] = []
    risks:   list[str] = []

    # Motion: may be nested list of per-track motions or scalar
    motions = p.get("motions", [])
    if isinstance(motions, list) and motions:
        seen: set[str] = set()
        for m in motions:
            if not isinstance(m, dict):
                continue
            mc = str(m.get("motion_class", ""))
            if mc and mc not in seen:
                seen.add(mc)
                if sig := _VIDEO_MOTION_INTENT.get(mc):
                    intents.append(sig)
                if risk := _VIDEO_MOTION_RISK.get(mc):
                    risks.append(risk)
    elif mc := str(p.get("motion_class", "")):
        if sig := _VIDEO_MOTION_INTENT.get(mc):
            intents.append(sig)
        if risk := _VIDEO_MOTION_RISK.get(mc):
            risks.append(risk)

    # Object presence
    obj_count = p.get("object_count", len(p.get("objects", [])))
    if isinstance(obj_count, (int, float)) and obj_count > 0:
        intents.append("objects_present")

    # Visual impact
    impact_score = float(p.get("impact_score", 0.0))
    if p.get("impact_detected") or impact_score > 0.5:
        intents.append("visual_impact_detected")
        risks.append("impact_event")

    # Temporal chain present
    if p.get("chain") or p.get("temporal_chain"):
        intents.append("temporal_sequence_detected")

    return ModalFeatures(
        modality="video",
        intent_signals=intents,
        risk_signals=risks,
        confidence=max(0.0, min(1.0, report.confidence)),
    )


def _extract_physics(report: LayerReport) -> ModalFeatures:
    p = report.payload
    intents: list[str] = []
    risks:   list[str] = []

    surface   = str(p.get("surface_class", p.get("surface", ""))).strip()
    depth_cls = str(p.get("depth_class",   p.get("depth",   ""))).strip()
    material  = str(p.get("material",      p.get("material_response", ""))).strip()

    if surface:
        intents.append("surface_" + surface.lower().replace(" ", "_").replace("-", "_"))
    if depth_cls:
        intents.append("depth_" + depth_cls.lower().replace(" ", "_").replace("-", "_"))
    if material:
        intents.append("material_" + material.lower().replace(" ", "_").replace("-", "_"))

    # Near/foreground objects signal proximity — potential interaction risk
    if depth_cls.lower() in ("foreground", "near", "close", "very_close"):
        risks.append("proximity_event")

    # Physics rule fired a specific material response
    if p.get("material_response") in ("brittle_break", "fracture", "shatter"):
        risks.append("brittle_fracture_event")

    return ModalFeatures(
        modality="physics",
        intent_signals=intents,
        risk_signals=risks,
        confidence=max(0.0, min(1.0, report.confidence)),
    )


def _extract_governance(report: LayerReport) -> ModalFeatures:
    p = report.payload
    # verdict may be stored as "verdict" or "intent_class"
    raw     = str(p.get("verdict", p.get("intent_class", p.get("intent", "UNCERTAIN")))).upper()
    verdict = raw if raw in _GOVERNANCE_VERDICT else "UNCERTAIN"
    intent_sig, risk_sigs = _GOVERNANCE_VERDICT[verdict]

    return ModalFeatures(
        modality="governance",
        intent_signals=[intent_sig],
        risk_signals=list(risk_sigs),
        confidence=max(0.0, min(1.0, report.confidence)),
    )


# Dispatch table — covers every slot on EventToken
_EXTRACTORS: dict[str, object] = {
    "text":       _extract_text,
    "audio":      _extract_audio,
    "tempo":      _extract_audio,
    "vad":        _extract_audio,
    "voice":      _extract_audio,
    "video":      _extract_video,
    "physics":    _extract_physics,
    "governance": _extract_governance,
}


# ══════════════════════════════════════════════════════════════════════════════
# ModalFusion — public entry point
# ══════════════════════════════════════════════════════════════════════════════


class ModalFusion:
    """Fuse all present modality layers in an EventToken into a FusedIntent.

    Usage::

        fusion  = ModalFusion()
        fused   = fusion.fuse(token)
        state   = fused.to_latent_state()   # → LatentState for LatentTrace
        assert fused.verify()
    """

    def fuse(self, token: EventToken) -> FusedIntent:
        """Extract, weight, and merge modality features.

        token.verify() is NOT called here — verification is the caller's
        responsibility. fuse() is read-only and safe to call on unverified
        tokens for inspection, but production paths should verify first.

        Returns a signed FusedIntent.
        """
        # Collect present layers in EventToken slot order
        slot_names = ("text", "audio", "tempo", "vad", "voice",
                      "video", "physics", "governance")
        present: list[tuple[str, LayerReport]] = [
            (slot, getattr(token, slot))
            for slot in slot_names
            if getattr(token, slot) is not None
        ]

        if not present:
            return self._signed_empty()

        # Extract features from each present layer; skip malformed payloads
        features: list[ModalFeatures] = []
        for slot, layer in present:
            extractor = _EXTRACTORS.get(slot)
            if extractor is None:
                continue
            try:
                features.append(extractor(layer))
            except Exception:
                pass  # malformed layer payload — skip, do not crash fusion

        if not features:
            return self._signed_empty()

        # ── Confidence-weighted intent signal accumulation ────────────────
        # Each modality votes for its intent signals with weight = confidence.
        intent_weights: dict[str, float] = {}
        for feat in features:
            w = feat.confidence
            for sig in feat.intent_signals:
                intent_weights[sig] = intent_weights.get(sig, 0.0) + w

        # ── Risk cluster union (any modality can flag a risk) ─────────────
        risk_set: set[str] = set()
        for feat in features:
            risk_set.update(feat.risk_signals)

        # ── Modal weights dict ────────────────────────────────────────────
        modal_weights: dict[str, float] = {
            feat.modality: round(feat.confidence, 4) for feat in features
        }

        # ── Fusion confidence: mean of modal confidences, capped ──────────
        raw_conf = sum(modal_weights.values()) / len(modal_weights)
        fusion_confidence = min(round(raw_conf, 4), CONFIDENCE_CAP)

        # ── Intent vector: top-N signals sorted by accumulated weight ─────
        intent_vector = [
            sig for sig, _ in sorted(
                intent_weights.items(), key=lambda kv: -kv[1]
            )
        ][:_MAX_INTENT_SIGNALS]

        risk_clusters = sorted(risk_set)
        modalities    = list(modal_weights.keys())

        # ── Compressed plan from primary intent ───────────────────────────
        primary        = intent_vector[0] if intent_vector else "default"
        compressed_plan = _PLANS.get(primary, _PLANS["default"])

        # ── Sign ──────────────────────────────────────────────────────────
        unsigned = FusedIntent(
            intent_vector=intent_vector,
            risk_clusters=risk_clusters,
            modalities=modalities,
            modal_weights=modal_weights,
            fusion_confidence=fusion_confidence,
            compressed_plan=compressed_plan,
            signature="",
        )
        sig = _sign(_canonical_fused(unsigned), FUSION_KEY_NS)
        unsigned.signature = sig
        return unsigned

    def _signed_empty(self) -> FusedIntent:
        """Return a valid signed FusedIntent for a token with no active layers."""
        f = FusedIntent(
            intent_vector=["ask_general"],
            risk_clusters=[],
            modalities=[],
            modal_weights={},
            fusion_confidence=0.5,
            compressed_plan=_PLANS["default"],
            signature="",
        )
        f.signature = _sign(_canonical_fused(f), FUSION_KEY_NS)
        return f


# ══════════════════════════════════════════════════════════════════════════════
# Signing helpers
# ══════════════════════════════════════════════════════════════════════════════


def _canonical_fused(fused: FusedIntent) -> bytes:
    d = fused.to_dict()
    d.pop("signature", None)
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(payload: bytes, namespace: bytes) -> str:
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()
