"""Ambient / physical-event audio agent.

Rule-based Phase A classifier. Input: a short mono PCM clip. Output:
a signed AudioReport with six fields that match the 3D-event-token
Audio layer schema.

Decision rules — coarse but explainable:

  impact_profile
    - "silence"          if peak envelope < SILENCE_FLOOR
    - "sharp_transient"  if attack < 30ms AND decay_slope < -40 dB/s
    - "soft_transient"   if attack < 80ms AND decay_slope < -10 dB/s
    - "sustained"        otherwise

  material_signature
    - "glass-like"   if centroid > 4 kHz AND hf_ratio > 0.55
    - "metal-like"   if centroid > 3 kHz AND has clear resonant peak (low spread)
    - "wood-like"    if 800 Hz < centroid < 2.5 kHz AND hf_ratio < 0.4
    - "fabric-like"  if centroid < 800 Hz AND hf_ratio < 0.2
    - "unknown"      else

  decay_pattern
    - "scattered_fragments"  if onset_count >= 2 in first 500ms after primary
    - "smooth_decay"         if decay_slope < -20 dB/s AND onset_count <= 1
    - "reverberant"          if decay_slope > -10 dB/s AND tail energy > 0.3 * peak
    - "unknown"              else

  depth   — low-frequency energy ratio (<500 Hz / total). In [0, 1].
  width   — normalized spectral spread (std-dev of centroid across frames). In [0, 1].
  rhythm
    - "single_impact"  if onset_count == 1
    - "periodic"       if onset_count >= 3 AND inter-onset gaps have low jitter
    - "irregular"      otherwise

Confidence: aggregate score combining peak energy + how cleanly the
spectral features fall into one of the four material categories.
"""
from __future__ import annotations

import math
from typing import Any

from .features import (
    DEFAULT_HOP_MS, decay_slope_db, detect_onsets, envelope,
    fft_magnitude, high_freq_ratio, load_wav, spectral_centroid_hz,
)
from .report import AudioReport

SILENCE_FLOOR = 0.005          # RMS amplitude below this = silence
SHARP_ATTACK_MS = 30
SOFT_ATTACK_MS = 80
SHARP_DECAY_DB_PER_S = -40.0
SOFT_DECAY_DB_PER_S = -10.0


# ─── Top-level convenience ──────────────────────────────────────────────


def classify_clip(wav_path: str) -> AudioReport:
    """Load a WAV from disk + return a signed AudioReport."""
    samples, sr = load_wav(wav_path)
    return AmbientAudioAgent().classify(samples, sr)


class AmbientAudioAgent:
    """Stateless ambient-audio classifier."""

    agent_name = "ambient-audio-v1"

    def classify(self, samples: list[float], sample_rate: int) -> AudioReport:
        """Analyze a mono PCM clip and emit a signed AudioReport."""
        if not samples:
            return _silence_report(reason="empty clip")

        env, hop = envelope(samples, sample_rate, DEFAULT_HOP_MS)
        if not env:
            return _silence_report(reason="empty envelope")

        peak_amp = max(env)
        peak_idx = env.index(peak_amp)
        if peak_amp < SILENCE_FLOOR:
            return _silence_report(reason=f"peak {peak_amp:.4f} below floor")

        # ─── Attack time: env crossing 10%→90% of peak before peak_idx ──
        attack_ms = _attack_time_ms(env, peak_idx, hop)

        # ─── Decay slope after peak (dB/s) ─────────────────────────────
        slope = decay_slope_db(env, peak_idx, DEFAULT_HOP_MS)

        # ─── Spectral analysis on a window centered on the peak ────────
        win_samples = int(0.1 * sample_rate)  # 100ms window
        start = max(0, peak_idx * hop - win_samples // 2)
        end = min(len(samples), start + win_samples)
        mag = fft_magnitude(samples[start:end])
        centroid = spectral_centroid_hz(mag, sample_rate)
        hf_ratio = high_freq_ratio(mag, sample_rate, cutoff_hz=2000.0)
        lf_ratio = 1.0 - high_freq_ratio(mag, sample_rate, cutoff_hz=500.0)

        # ─── Onsets across the whole clip ──────────────────────────────
        onsets = detect_onsets(env, rel_threshold=2.0)
        onset_count = len(onsets)

        # ─── Onsets within 500ms AFTER the primary peak ────────────────
        scatter_window_frames = int(500 / DEFAULT_HOP_MS)
        scatter_onsets = [
            o for o in onsets
            if peak_idx < o <= peak_idx + scatter_window_frames
        ]

        # ─── Field mapping ─────────────────────────────────────────────
        impact = _classify_impact(attack_ms, slope)
        material, material_conf = _classify_material(centroid, hf_ratio, mag)
        decay = _classify_decay(slope, env, peak_idx, len(scatter_onsets))
        rhythm = _classify_rhythm(onsets, hop, sample_rate)

        # Width: variability of centroid across frames (proxy for spread)
        width = _spectral_width(samples, sample_rate, hop)

        peak_amp_db = 20 * math.log10(max(peak_amp, 1e-6))
        confidence = _aggregate_confidence(
            peak_amp_db, material_conf, impact, decay,
        )

        payload = {
            "impact_profile":     impact,
            "material_signature": material,
            "decay_pattern":      decay,
            "depth":              round(lf_ratio, 3),
            "width":              round(width, 3),
            "rhythm":             rhythm,
            "debug": {
                "attack_ms":              round(attack_ms, 1),
                "decay_db_per_s":         round(slope, 1),
                "centroid_hz":            round(centroid, 1),
                "high_freq_ratio":        round(hf_ratio, 3),
                "low_freq_ratio":         round(lf_ratio, 3),
                "onset_count":            onset_count,
                "scatter_onset_count":    len(scatter_onsets),
                "peak_amp_db":            round(peak_amp_db, 1),
                "duration_s":             round(len(samples) / sample_rate, 3),
                "sample_rate":            sample_rate,
            },
        }
        return AudioReport.signed(payload=payload, confidence=confidence)


# ─── Internals ──────────────────────────────────────────────────────────


def _silence_report(*, reason: str) -> AudioReport:
    return AudioReport.signed(
        payload={
            "impact_profile":     "silence",
            "material_signature": "unknown",
            "decay_pattern":      "unknown",
            "depth":              0.0,
            "width":              0.0,
            "rhythm":             "single_impact",
            "debug": {"reason": reason},
        },
        confidence=0.95,  # we're confident the clip is silent
    )


def _attack_time_ms(env: list[float], peak_idx: int, hop: int) -> float:
    """Time from envelope crossing 10% of peak up to 90% of peak.

    Looks backward from peak_idx. Returns the duration in ms. A sharp
    impact crosses 10%→90% in a few ms; a soft pluck takes 50–150ms.
    """
    if peak_idx == 0:
        return 0.0
    peak_amp = env[peak_idx]
    thresh_low = 0.10 * peak_amp
    thresh_high = 0.90 * peak_amp
    idx_high = peak_idx
    idx_low = 0
    for i in range(peak_idx, -1, -1):
        if env[i] >= thresh_high:
            idx_high = i
        if env[i] <= thresh_low:
            idx_low = i
            break
    frames = max(idx_high - idx_low, 0)
    # frames * hop_samples / sample_rate * 1000 — but we don't have sr
    # here; use DEFAULT_HOP_MS instead.
    return frames * DEFAULT_HOP_MS


def _classify_impact(attack_ms: float, decay_db_per_s: float) -> str:
    if attack_ms < SHARP_ATTACK_MS and decay_db_per_s < SHARP_DECAY_DB_PER_S:
        return "sharp_transient"
    if attack_ms < SOFT_ATTACK_MS and decay_db_per_s < SOFT_DECAY_DB_PER_S:
        return "soft_transient"
    return "sustained"


def _classify_material(
    centroid_hz: float, hf_ratio: float, mag: list[float],
) -> tuple[str, float]:
    """Return (material_label, confidence_in_label_in_[0,1])."""
    if centroid_hz > 4000 and hf_ratio > 0.55:
        # Tight high-frequency content with broadband sparkle = glass-like
        return ("glass-like", 0.85)
    if centroid_hz > 3000 and hf_ratio > 0.4 and _has_resonant_peak(mag):
        return ("metal-like", 0.75)
    if 800 < centroid_hz < 2500 and hf_ratio < 0.4:
        return ("wood-like", 0.7)
    if centroid_hz < 800 and hf_ratio < 0.2:
        return ("fabric-like", 0.7)
    return ("unknown", 0.4)


def _has_resonant_peak(mag: list[float]) -> bool:
    """Detect a dominant narrow spectral peak (metallic ring).

    True if the loudest bin's energy is >= 25% of the total spectrum,
    indicating one frequency dominates rather than broadband noise.
    """
    if not mag:
        return False
    total = sum(mag)
    if total == 0:
        return False
    return max(mag) / total >= 0.25


def _classify_decay(
    decay_db_per_s: float, env: list[float], peak_idx: int,
    scatter_onsets: int,
) -> str:
    if scatter_onsets >= 2:
        return "scattered_fragments"
    if decay_db_per_s < -20 and scatter_onsets <= 1:
        return "smooth_decay"
    # Reverberant: slow decay AND tail still has energy
    tail_start = peak_idx + 30  # 300ms after peak at 10ms hops
    tail_end = min(len(env), tail_start + 50)
    if tail_start < len(env):
        tail = env[tail_start:tail_end]
        peak_amp = env[peak_idx] if env[peak_idx] > 0 else 1e-6
        avg_tail = sum(tail) / len(tail) if tail else 0.0
        if decay_db_per_s > -10 and avg_tail > 0.3 * peak_amp:
            return "reverberant"
    return "unknown"


def _classify_rhythm(
    onsets: list[int], hop: int, sample_rate: int,
) -> str:
    if len(onsets) <= 1:
        return "single_impact"
    if len(onsets) < 3:
        return "irregular"
    gaps = [onsets[i + 1] - onsets[i] for i in range(len(onsets) - 1)]
    if not gaps:
        return "single_impact"
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap == 0:
        return "irregular"
    variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
    cv = math.sqrt(variance) / mean_gap  # coefficient of variation
    return "periodic" if cv < 0.25 else "irregular"


def _spectral_width(samples: list[float], sample_rate: int, hop: int) -> float:
    """Std-dev of frame-wise spectral centroid, normalized into [0, 1]."""
    frame_size = max(hop * 8, 512)
    centroids: list[float] = []
    for i in range(0, len(samples) - frame_size, frame_size):
        mag = fft_magnitude(samples[i:i + frame_size])
        c = spectral_centroid_hz(mag, sample_rate)
        if c > 0:
            centroids.append(c)
    if len(centroids) < 2:
        return 0.0
    mean = sum(centroids) / len(centroids)
    var = sum((c - mean) ** 2 for c in centroids) / len(centroids)
    std = math.sqrt(var)
    # Normalize: assume Nyquist/2 is "very wide". Cap at 1.0.
    return min(std / (sample_rate / 4), 1.0)


def _aggregate_confidence(
    peak_amp_db: float, material_conf: float,
    impact: str, decay: str,
) -> float:
    """Composite confidence: louder + cleaner material match = higher."""
    # Loudness component: -40 dB → 0.3, -10 dB → 0.9
    loudness = max(0.0, min(1.0, (peak_amp_db + 40) / 30))
    loudness = 0.3 + 0.6 * loudness  # remap to [0.3, 0.9]
    # If impact is "silence" or material is "unknown", drop confidence
    penalty = 0.0
    if impact == "silence":
        penalty += 0.5  # rare here — handled in _silence_report
    if decay == "unknown":
        penalty += 0.1
    return round(max(0.05, min(0.99, 0.5 * loudness + 0.5 * material_conf - penalty)), 3)
