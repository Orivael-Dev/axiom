"""
Automotive audio event adapter for KIA US models.

Maps AmbientAudioAgent + VADAgent output to in-car event categories:
ENGINE | BRAKE | CABIN | VOICE | SILENCE | UNKNOWN

No new ML — pure feature-threshold routing using the existing ambient
classifier (axiom_audio/ambient.py) and VAD (axiom_audio/vad.py).
Thresholds tuned for in-cabin microphone conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from axiom_audio.ambient import AudioReport, classify_clip
from axiom_audio.vad import VADReport, classify_vad_clip


@dataclass
class AutomotiveAudioEvent:
    """Classified automotive in-car audio event."""
    event_type: str    # ENGINE | BRAKE | CABIN | VOICE | SILENCE | UNKNOWN
    severity: str      # NORMAL | WARNING | CRITICAL
    confidence: float
    ambient: AudioReport | None = None
    vad: VADReport | None = None

    @property
    def is_safety_critical(self) -> bool:
        return self.severity == "CRITICAL"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "severity": self.severity,
            "confidence": self.confidence,
            "is_safety_critical": self.is_safety_critical,
            "ambient_payload": self.ambient.payload if self.ambient else None,
            "vad_active": bool(
                self.vad and self.vad.payload.get("activity_ratio", 0) > 0
            ),
        }


# ── Threshold constants ─────────────────────────────────────────────────────

# Brake squeal: metal-like, periodic, high-frequency (width = spectral spread)
_BRAKE_HF_WARN     = 0.65   # width > this → brake WARNING
_BRAKE_HF_CRITICAL = 0.80   # width > this → brake CRITICAL (metal grind)

# Engine knock: sharp transient + metal signature
_ENGINE_SHARP_PROFILES = frozenset({"sharp_transient"})
_METAL_MATERIALS       = frozenset({"metal-like", "glass-like"})

# Cabin chimes: sustained/soft low-frequency events
_CABIN_PROFILES  = frozenset({"sustained", "soft_transient"})
_CABIN_MATERIALS = frozenset({"fabric-like", "unknown", "wood-like"})

# VAD: activity_ratio threshold to call a clip "voice active"
_VAD_ACTIVE_RATIO = 0.25


# ── Public API ──────────────────────────────────────────────────────────────

def classify_automotive_audio(wav_path: str) -> AutomotiveAudioEvent:
    """
    Classify an in-cabin audio clip into an automotive event category.

    Runs AmbientAudioAgent and VADAgent in sequence, applies KIA US
    in-cabin thresholds, and returns an AutomotiveAudioEvent.

    Safety-critical events (BRAKE CRITICAL, ENGINE WARNING) should be
    surfaced to the user immediately regardless of VAD state.
    """
    ambient_report = classify_clip(wav_path)
    vad_report     = classify_vad_clip(wav_path)

    return _classify(ambient_report, vad_report)


def classify_automotive_samples(
    samples: list[float],
    sample_rate: int,
) -> AutomotiveAudioEvent:
    """Same as classify_automotive_audio but accepts pre-loaded PCM data."""
    from axiom_audio.ambient import AmbientAudioAgent
    from axiom_audio.vad import VoiceActivityDetector

    ambient_report = AmbientAudioAgent().classify(samples, sample_rate)
    vad_report     = VoiceActivityDetector().detect(samples, sample_rate)
    return _classify(ambient_report, vad_report)


# ── Internal classifier ─────────────────────────────────────────────────────

def _classify(ambient: AudioReport, vad: VADReport) -> AutomotiveAudioEvent:
    payload  = ambient.payload
    profile  = payload.get("impact_profile", "unknown")
    material = payload.get("material_signature", "unknown")
    rhythm   = payload.get("rhythm", "unknown")
    width    = float(payload.get("width", 0.0))   # spectral spread / HF proxy
    conf     = float(ambient.confidence)

    # ── 1. Silence ───────────────────────────────────────────────────────────
    if profile == "silence":
        return AutomotiveAudioEvent("SILENCE", "NORMAL", 1.0, ambient, vad)

    # ── 2. Voice (VAD takes priority over ambient classification) ────────────
    vad_ratio = float(vad.payload.get("activity_ratio", 0.0))
    if vad_ratio >= _VAD_ACTIVE_RATIO:
        return AutomotiveAudioEvent("VOICE", "NORMAL", vad.confidence, ambient, vad)

    # ── 3. Brake squeal / grind: periodic + metal-like + high HF ────────────
    if material in _METAL_MATERIALS and rhythm == "periodic":
        if width > _BRAKE_HF_CRITICAL:
            return AutomotiveAudioEvent("BRAKE", "CRITICAL", conf, ambient, vad)
        if width > _BRAKE_HF_WARN:
            return AutomotiveAudioEvent("BRAKE", "WARNING", conf, ambient, vad)

    # ── 4. Engine knock / misfire / belt: sharp transient + metal ────────────
    if profile in _ENGINE_SHARP_PROFILES and material in _METAL_MATERIALS:
        decay = payload.get("decay_pattern", "unknown")
        sev   = "CRITICAL" if decay == "scattered_fragments" else "WARNING"
        return AutomotiveAudioEvent("ENGINE", sev, conf, ambient, vad)

    # ── 5. Cabin alert chime / HVAC: sustained or soft low-freq ─────────────
    if profile in _CABIN_PROFILES and material in _CABIN_MATERIALS:
        return AutomotiveAudioEvent("CABIN", "NORMAL", conf, ambient, vad)

    # ── 6. Unknown — return raw ambient for upstream disambiguation ──────────
    return AutomotiveAudioEvent("UNKNOWN", "NORMAL", conf, ambient, vad)
