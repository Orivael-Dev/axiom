"""Layer agents for the 3D event token prototype.

Five agent types — only Text uses real production code; the rest are
stubs returning believable shapes so the container abstraction can be
validated end-to-end before audio + video engines exist.

Each agent's contract:
  run(inputs: dict) -> LayerReport
  agent_name: str

When Audio / Video / Physics engines ship, drop in real implementations
behind these classes without touching the Coordinator or container.
"""
from __future__ import annotations

import abc
from typing import Any

from axiom_signing import derive_key

from .models import LayerReport


class Agent(abc.ABC):
    """Specialist agent for one modality layer."""
    agent_name: str = "abstract"

    @abc.abstractmethod
    def run(self, inputs: dict[str, Any]) -> LayerReport:
        """Inspect `inputs`, emit a signed LayerReport."""
        raise NotImplementedError


# ─── Text Agent (real — wraps IntentClassifier) ─────────────────────────


class TextAgent(Agent):
    """Real text agent: runs the production IntentClassifier on inputs['text'].

    Output payload mirrors the IntentTypingResult fields so downstream
    consumers can switch on intent_class identically.
    """
    agent_name = "text"

    def __init__(self) -> None:
        from axiom_intent_classifier import IntentClassifier
        self._clf = IntentClassifier(derive_key(b"axiom-firewall-v1"))

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        text = inputs.get("text", "")
        if not isinstance(text, str):
            raise TypeError("TextAgent requires inputs['text'] to be a string")
        result = self._clf.classify(text)
        payload = {
            "phrase":        text,
            "intent_class":  result.intent_class,
            "confidence":    result.confidence,
            "signals":       list(result.signals),
            "classifier_sig": result.signature,
        }
        return LayerReport.signed(
            agent=self.agent_name,
            payload=payload,
            confidence=result.confidence,
        )


# ─── Audio Agent (stub) ─────────────────────────────────────────────────


class AudioAgent(Agent):
    """Stub Audio agent — returns the audio fields from a caller-provided
    dict at inputs['audio'] OR a fixed plausible shape if absent.

    Real implementation: axiom_audio (per saved plan). This stub validates
    that the container holds together end-to-end with a believable shape.
    """
    agent_name = "audio"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("audio", {})
        payload = {
            "impact_profile":    provided.get("impact_profile", "neutral"),
            "material_signature": provided.get("material_signature", "unknown"),
            "decay_pattern":     provided.get("decay_pattern", "flat"),
            "depth":             provided.get("depth", 0.0),
            "width":             provided.get("width", 0.0),
            "rhythm":            provided.get("rhythm", "steady"),
        }
        confidence = float(provided.get("confidence", 0.5))
        return LayerReport.signed(
            agent=self.agent_name, payload=payload, confidence=confidence,
        )


# ─── Video Agent (stub) ─────────────────────────────────────────────────


class VideoAgent(Agent):
    """Stub Video agent — emits an object-tracks + temporal-chain shape
    matching the video-topology concept doc.

    Real implementation: axiom_video (concept doc shipped).
    """
    agent_name = "video"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("video", {})
        payload = {
            "objects":          provided.get("objects", []),
            "object_motion":    provided.get("object_motion", "static"),
            "impact_point":     provided.get("impact_point", None),
            "fracture_pattern": provided.get("fracture_pattern", None),
            "temporal_chain":   provided.get("temporal_chain", []),
        }
        confidence = float(provided.get("confidence", 0.5))
        return LayerReport.signed(
            agent=self.agent_name, payload=payload, confidence=confidence,
        )


# ─── Physics Agent (stub with a tiny lookup table) ─────────────────────


_PHYSICS_RULES: dict[tuple[str, str, str], dict] = {
    # (material, surface, motion) -> expected result
    ("brittle_glass",   "hard_surface", "downward"): {
        "cause": "gravity", "collision": "hard_surface",
        "material_response": "brittle_break", "plausible": True,
    },
    ("ceramic_cup",     "hard_surface", "downward"): {
        "cause": "gravity", "collision": "hard_surface",
        "material_response": "brittle_break", "plausible": True,
    },
    ("rubber_ball",     "hard_surface", "downward"): {
        "cause": "gravity", "collision": "hard_surface",
        "material_response": "elastic_bounce", "plausible": True,
    },
    ("plastic_toy",     "soft_surface", "downward"): {
        "cause": "gravity", "collision": "soft_surface",
        "material_response": "minimal_deformation", "plausible": True,
    },
}


class PhysicsAgent(Agent):
    """Stub Physics agent — small lookup table per the concept note.

    Real implementation (Phase 3+) would call PyBullet / MuJoCo. The
    stub's contract: take (material, surface, motion) → return cause +
    collision + plausibility verdict.
    """
    agent_name = "physics"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("physics", {})
        material = provided.get("material", "unknown")
        surface  = provided.get("surface", "unknown")
        motion   = provided.get("motion", "unknown")

        rule = _PHYSICS_RULES.get((material, surface, motion))
        if rule is not None:
            payload = dict(rule)
            payload["lookup"] = f"{material}+{surface}+{motion}"
            confidence = 0.9
        else:
            payload = {
                "cause": "unknown", "collision": "unknown",
                "material_response": "unknown", "plausible": False,
                "lookup": f"{material}+{surface}+{motion}",
                "note": "no rule matched",
            }
            confidence = 0.3

        return LayerReport.signed(
            agent=self.agent_name, payload=payload, confidence=confidence,
        )


# ─── Governance Agent (real — records audit trail) ─────────────────────


class GovernanceAgent(Agent):
    """Real Governance agent — populates evidence_trace + per-layer
    confidence from the OTHER layers' reports.

    Must run LAST so it can read the others' outputs. The Coordinator
    enforces this ordering.
    """
    agent_name = "governance"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        sibling_reports: list[LayerReport] = inputs.get("_sibling_reports", [])
        evidence_trace = [r.agent for r in sibling_reports]
        per_layer_confidence = {r.agent: r.confidence for r in sibling_reports}
        avg = (sum(per_layer_confidence.values()) / len(per_layer_confidence)
               if per_layer_confidence else 0.0)
        payload = {
            "evidence_trace":         evidence_trace,
            "layer_activation":       "task_specific",
            "audit_mode":             "enabled",
            "per_layer_confidence":   per_layer_confidence,
            "aggregate_confidence":   round(avg, 3),
        }
        return LayerReport.signed(
            agent=self.agent_name, payload=payload, confidence=avg,
        )


# ─── Registry ───────────────────────────────────────────────────────────


AGENT_REGISTRY: dict[str, type[Agent]] = {
    "text":       TextAgent,
    "audio":      AudioAgent,
    "video":      VideoAgent,
    "physics":    PhysicsAgent,
    "governance": GovernanceAgent,
}
