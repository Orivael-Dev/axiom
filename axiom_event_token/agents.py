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
    """Audio agent — runs the real ambient classifier when a `wav_path`
    is provided, otherwise echoes a caller-supplied dict (back-compat
    for the original stub behaviour).

    Real implementation: `axiom_audio.AmbientAudioAgent`. Phase A ships
    ambient/physical-event analysis; voice + music agents land in later
    phases behind the same Audio layer slot.
    """
    agent_name = "audio"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("audio", {})
        wav_path = provided.get("wav_path") if isinstance(provided, dict) else None
        if wav_path:
            from axiom_audio import classify_clip
            audio_report = classify_clip(wav_path)
            return LayerReport.signed(
                agent=self.agent_name,
                payload=audio_report.payload,
                confidence=audio_report.confidence,
            )
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
    """Real Video agent — runs the axiom_video detector ladder
    (ObjectTracker → MotionClassifier → ImpactDetector →
    TemporalChainExtractor) against an input scene graph and emits
    a signed LayerReport whose payload carries all four sub-reports
    plus a summary.

    Input shape:
      inputs["video"] = {
          "scene_graph": SceneGraph instance (or list of Scene dicts),
          ...
      }

    Back-compat: if a caller passes hand-coded fields (objects,
    object_motion, impact_point, fracture_pattern, temporal_chain)
    instead of a scene_graph, we fall back to the legacy stub shape
    so existing tests + the older event-token contract keep working.
    """
    agent_name = "video"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("video", {}) or {}
        scene_graph = provided.get("scene_graph")
        if scene_graph is None:
            # Legacy / stub mode — preserve the prior contract.
            payload = {
                "objects":          provided.get("objects", []),
                "object_motion":    provided.get("object_motion", "static"),
                "impact_point":     provided.get("impact_point", None),
                "fracture_pattern": provided.get("fracture_pattern", None),
                "temporal_chain":   provided.get("temporal_chain", []),
                "mode":             "stub",
            }
            return LayerReport.signed(
                agent=self.agent_name, payload=payload,
                confidence=float(provided.get("confidence", 0.5)),
            )

        # Real-detector mode
        try:
            from axiom_video import (
                ColorWatcher, DepthClassifier, ImpactDetector,
                MotionClassifier, ObjectTracker, SurfaceClassifier,
                TemporalChainExtractor, TimeKeeper,
            )
        except ImportError:
            return LayerReport.signed(
                agent=self.agent_name,
                payload={"mode": "unavailable",
                         "reason": "axiom_video not importable"},
                confidence=0.0,
            )

        try:
            tracks = ObjectTracker().track(scene_graph)
            motions = MotionClassifier().classify(tracks)
            impacts = ImpactDetector().detect(tracks, motions)
            chain = TemporalChainExtractor().extract(tracks, motions, impacts)
            timing = TimeKeeper().analyze(chain)
            colors = ColorWatcher().watch(scene_graph)
            depths = DepthClassifier().classify(scene_graph, tracks)
            surfaces = SurfaceClassifier().classify(scene_graph, tracks)
        except Exception as e:
            return LayerReport.signed(
                agent=self.agent_name,
                payload={"mode": "error",
                         "reason": f"{type(e).__name__}: {e}"},
                confidence=0.0,
            )

        # Surface a compact summary alongside the eight sub-reports
        # so downstream consumers can read the headline without
        # walking the full payload.
        payload = {
            "mode": "real",
            "summary": {
                "n_tracks":         tracks.payload["n_tracks"],
                "dominant_motion":  motions.payload.get("dominant_class",
                                                          "none"),
                "n_impacts":        impacts.payload["n_events"],
                "n_chain_events":   chain.payload["n_events"],
                "rhythm_class":     timing.payload.get("rhythm_class",
                                                        "insufficient"),
                "scene_color":      colors.payload.get("scene_dominant_color",
                                                        "none"),
                "n_color_events":   colors.payload.get("n_color_events", 0),
                "depth_source":     depths.payload.get("source", "none"),
                "n_depth_events":   depths.payload.get("n_events", 0),
                "scene_unstable":   surfaces.payload.get("scene_unstable",
                                                          False),
                "n_tip_events":     surfaces.payload.get("n_events", 0),
            },
            "object_track_report":   tracks.to_dict(),
            "motion_report":         motions.to_dict(),
            "impact_report":         impacts.to_dict(),
            "temporal_chain_report": chain.to_dict(),
            "time_keeper_report":    timing.to_dict(),
            "color_report":          colors.to_dict(),
            "depth_report":          depths.to_dict(),
            "surface_report":        surfaces.to_dict(),
        }
        # Roll up confidence — mean of the eight sub-reports
        sub_confs = [tracks.confidence, motions.confidence,
                     impacts.confidence, chain.confidence,
                     timing.confidence, colors.confidence,
                     depths.confidence, surfaces.confidence]
        conf = sum(sub_confs) / len(sub_confs)
        return LayerReport.signed(
            agent=self.agent_name, payload=payload,
            confidence=round(conf, 4),
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


# ─── Tempo Agent (real — autocorrelation BPM estimator) ────────────────


class TempoAgent(Agent):
    """Real tempo/BPM agent.

    Activates when the caller wants rhythm analysis as a first-class
    citizen alongside other layers (vs. the coarse rhythm field the
    ambient agent already emits). Input shape mirrors AudioAgent:
    `inputs["audio"]["wav_path"]` or raw `inputs["audio"]["samples"]`
    + `inputs["audio"]["sample_rate"]`.

    Why a separate agent: tempo crosses all three audio families
    (ambient / voice / music) AND it has objective ground truth, so
    it serves as the numeric-truth anchor for the audio testing
    library.
    """
    agent_name = "tempo"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("audio", {})
        if not isinstance(provided, dict):
            provided = {}
        from axiom_audio import TempoEstimator, classify_tempo_clip
        wav_path = provided.get("wav_path")
        if wav_path:
            tempo_report = classify_tempo_clip(wav_path)
        else:
            samples = provided.get("samples", [])
            sr = int(provided.get("sample_rate", 16_000))
            tempo_report = TempoEstimator().estimate(samples, sr)
        return LayerReport.signed(
            agent=self.agent_name,
            payload=tempo_report.payload,
            confidence=tempo_report.confidence,
        )


# ─── VAD Agent (gate / dead-air cutter) ────────────────────────────────


class VADAgent(Agent):
    """Voice Activity Detection — the silence gate.

    Activates when the caller wants a timeline of active vs. silent
    regions in an audio clip. Used to chunk long recordings, gate
    out dead air before downstream classification, or surface
    "this 5-second clip is 80% silence" as an audit-visible fact.

    Real implementation: axiom_audio.VoiceActivityDetector.
    """
    agent_name = "vad"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("audio", {})
        if not isinstance(provided, dict):
            provided = {}
        from axiom_audio import VoiceActivityDetector, classify_vad_clip
        wav_path = provided.get("wav_path")
        if wav_path:
            vad_report = classify_vad_clip(wav_path)
        else:
            samples = provided.get("samples", [])
            sr = int(provided.get("sample_rate", 16_000))
            vad_report = VoiceActivityDetector().detect(samples, sr)
        return LayerReport.signed(
            agent=self.agent_name,
            payload=vad_report.payload,
            confidence=vad_report.confidence,
        )


# ─── Voice Agent (real — pitch + prosody, ASR is a future layer) ───────


class VoiceAgent(Agent):
    """Voice characterization — pitch + prosody + speaker register.

    Runs VAD internally so silence is already gated before pitch
    analysis. Phase B scope: prosody only, NOT speech-to-text.

    Real implementation: axiom_audio.VoiceAgent.
    """
    agent_name = "voice"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("audio", {})
        if not isinstance(provided, dict):
            provided = {}
        from axiom_audio import VoiceAgent as _VoiceClassifier, classify_voice_clip
        wav_path = provided.get("wav_path")
        if wav_path:
            voice_report = classify_voice_clip(wav_path)
        else:
            samples = provided.get("samples", [])
            sr = int(provided.get("sample_rate", 16_000))
            voice_report = _VoiceClassifier().classify(samples, sr)
        return LayerReport.signed(
            agent=self.agent_name,
            payload=voice_report.payload,
            confidence=voice_report.confidence,
        )


# ─── QRF Agent (research / probability-weighted reasoning) ─────────────


class QRFAgent(Agent):
    """Quantum Reasoning Forecast agent.

    Activates when the caller wants a multi-branch probability forecast
    as a peer layer alongside text/audio/video/physics. Wraps
    axiom_qrf.QRFEngine; supports the same five domains
    (medical / financial / supply_chain / hr / security) plus
    "general" added by axiom_research.

    Input:  inputs["qrf"] = {"query": str, "domain": str}
    Output: payload with top_branch, probability_band, branches[],
            n_killed — same shape as axiom_qrf.QRFResult.

    Falls back to a deterministic empty-result LayerReport if QRF's
    LatentEngine can't reach an LLM endpoint — the COORDINATOR shape
    keeps working even when reasoning is unavailable.
    """
    agent_name = "qrf"

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        provided = inputs.get("qrf", {})
        if not isinstance(provided, dict):
            provided = {}
        query = provided.get("query", "")
        domain = provided.get("domain", "financial")
        if not query:
            return LayerReport.signed(
                agent=self.agent_name,
                payload={
                    "top_branch": "(no-query)",
                    "probability_band": "UNCERTAIN",
                    "branches": [],
                    "n_killed": 0,
                    "domain": domain,
                },
                confidence=0.0,
            )
        from axiom_qrf import DOMAIN_BRANCH_COUNTS as QRF_DOMAINS, QRFEngine
        from axiom_signing import derive_key
        qrf_domain = domain if domain in QRF_DOMAINS else "financial"
        try:
            engine = QRFEngine(qrf_domain, derive_key(b"axiom-research-qrf-v1"))
            result = engine.forecast(query)
        except Exception:
            return LayerReport.signed(
                agent=self.agent_name,
                payload={
                    "top_branch": "(qrf-error)",
                    "probability_band": "UNCERTAIN",
                    "branches": [],
                    "n_killed": 0,
                    "domain": domain,
                },
                confidence=0.0,
            )
        band_to_conf = {"HIGH": 0.9, "MODERATE": 0.7, "LOW": 0.5, "UNCERTAIN": 0.3}
        return LayerReport.signed(
            agent=self.agent_name,
            payload={
                "top_branch":       result.top_branch,
                "probability_band": result.probability_band,
                "branches": [
                    {
                        "branch_label": b.get("branch_label")
                                          or b.get("label", "(unlabeled)"),
                        "probability_weight": b.get("probability_weight", 0.0),
                    }
                    for b in result.branches[:6]
                ],
                "n_killed": len(result.killed),
                "domain":   domain,
            },
            confidence=band_to_conf.get(result.probability_band, 0.5),
        )


# ─── Registry ───────────────────────────────────────────────────────────


AGENT_REGISTRY: dict[str, type[Agent]] = {
    "text":       TextAgent,
    "audio":      AudioAgent,
    "tempo":      TempoAgent,
    "vad":        VADAgent,
    "voice":      VoiceAgent,
    "qrf":        QRFAgent,
    "video":      VideoAgent,
    "physics":    PhysicsAgent,
    "governance": GovernanceAgent,
}
