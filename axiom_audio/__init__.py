"""Axiom Audio — physical-event / ambient audio analysis (Phase A).

Phase A scope: the ambient / physical-event agent. Takes a short
mono WAV clip and emits a signed AudioReport with six fields that
match the 3D-event-token Audio layer:

    impact_profile     — sharp_transient | soft_transient | sustained | silence
    material_signature — glass-like | metal-like | wood-like | fabric-like | unknown
    decay_pattern      — scattered_fragments | smooth_decay | reverberant | unknown
    depth              — float in [0, 1], proxy for low-frequency energy
    width              — float in [0, 1], spectral spread proxy
    rhythm             — single_impact | periodic | irregular

stdlib-only — `wave`, `math`, `array`, `struct`. No numpy / scipy /
librosa dependency. A short FFT in pure Python is plenty for a
~3-second clip; we never call it on a hot path.

Voice + music agents land in Phase B / C, reusing AudioReport.
"""
from __future__ import annotations

from .ambient import AmbientAudioAgent, classify_clip
from .features import (
    decay_slope_db, detect_onsets, envelope, fft_magnitude,
    high_freq_ratio, load_wav, spectral_centroid_hz,
)
from .report import AUDIO_KEY_NS, AudioReport
from .tempo import (
    TEMPO_KEY_NS, TempoEstimator, TempoReport, classify_tempo_clip,
)

__all__ = [
    "AmbientAudioAgent",
    "AudioReport",
    "AUDIO_KEY_NS",
    "TEMPO_KEY_NS",
    "TempoEstimator",
    "TempoReport",
    "classify_clip",
    "classify_tempo_clip",
    "decay_slope_db",
    "detect_onsets",
    "envelope",
    "fft_magnitude",
    "high_freq_ratio",
    "load_wav",
    "spectral_centroid_hz",
]
