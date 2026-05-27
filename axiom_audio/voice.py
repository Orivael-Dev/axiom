"""Voice agent — Phase B.

Detect + characterize speech in a mono PCM clip. Phase B scope is
deliberately narrow: NO speech-to-text (ASR is a separate layer
that will come later), NO speaker identification. Just the
prosodic + acoustic profile of the voice signal:

  is_speech            bool        — did we detect voiced content at all?
  pitch_hz_mean        float       — average F0 across voiced frames
  pitch_hz_stability   float in [0, 1] — how steady the F0 is
  voicing_ratio        float in [0, 1] — fraction of clip that is voiced
  speaker_register     low | mid | high — pitch band of the speaker
  prosody              monotone | melodic | excited — F0 variance class
  syllable_rate_hz     float       — onset density inside voiced regions
  intensity_db         float       — peak loudness (dB FS)
  total_voiced_s       float
  total_silent_s       float
  confidence           float

The agent runs the VAD preprocessor internally — silence is cut
before pitch analysis so a 5-second clip with 200ms of speech in
the middle gives the same verdict as a 200ms speech-only clip.

Pitch (F0) estimation: time-domain autocorrelation in each voiced
frame, lag range mapping to 50–400 Hz (adult vocal range). Same
algorithm family as the tempo agent, different lag window.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass
from typing import Sequence

from axiom_signing import derive_key

from .features import (
    DEFAULT_HOP_MS, detect_onsets, envelope, load_wav,
)
from .vad import voice_activity_regions

VOICE_KEY_NS = b"axiom-voice-v1"

# Adult speech F0 range. Children + falsetto land above; very low
# bass voices below 80 Hz; we clip to [50, 400] for stability.
F0_MIN_HZ = 50.0
F0_MAX_HZ = 400.0

# Speaker-register bands (Hz).
REGISTER_LOW_MAX = 130.0
REGISTER_MID_MAX = 220.0
# Higher than that → "high" (children, female-typical range, falsetto).

# Prosody classification thresholds — coefficient of variation of F0
# across voiced frames.
PROSODY_MONOTONE_MAX_CV = 0.06    # CV < 6% → monotone
PROSODY_MELODIC_MAX_CV = 0.20     # 6%–20% → melodic; >20% → excited


# ─── Signed report ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class VoiceReport:
    """Voice analysis result, HMAC-signed under axiom-voice-v1."""
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
    def from_dict(cls, d: dict) -> "VoiceReport":
        return cls(
            payload=d["payload"],
            confidence=float(d.get("confidence", 1.0)),
            signature=d.get("signature", ""),
        )

    @classmethod
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "VoiceReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned), VOICE_KEY_NS)
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        expected = _sign(_canonical(self), VOICE_KEY_NS)
        return hmac.compare_digest(self.signature, expected)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def _canonical(r: VoiceReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(payload: bytes, namespace: bytes) -> str:
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()


# ─── Public entry points ────────────────────────────────────────────────


def classify_voice_clip(wav_path: str) -> VoiceReport:
    """Load a WAV from disk + return a signed VoiceReport."""
    samples, sr = load_wav(wav_path)
    return VoiceAgent().classify(samples, sr)


class VoiceAgent:
    """Stateless voice-characteristic analyzer.

    Pipeline:
      1. VAD → list of voiced regions (silence gated out)
      2. F0 autocorrelation per ~30ms frame inside voiced regions
      3. Aggregate pitch stats → register + prosody labels
      4. Syllable rate via onset density inside voiced regions
    """

    agent_name = "voice-v1"

    def classify(self, samples: Sequence[float], sample_rate: int) -> VoiceReport:
        if not samples:
            return _no_voice_report(reason="empty clip", duration_s=0.0)

        duration_s = len(samples) / sample_rate

        # 1. VAD gate
        regions = voice_activity_regions(samples, sample_rate)
        total_voiced_s = sum(end - start for start, end in regions)
        total_silent_s = max(0.0, duration_s - total_voiced_s)
        voicing_ratio = total_voiced_s / duration_s if duration_s > 0 else 0.0

        if not regions:
            return _no_voice_report(
                reason="no active regions found by VAD",
                duration_s=duration_s,
                total_silent_s=total_silent_s,
            )

        # 2. F0 estimation per voiced frame
        f0s: list[float] = []
        for start_s, end_s in regions:
            f0s.extend(_estimate_f0_in_region(
                samples, sample_rate, start_s, end_s,
            ))

        if not f0s:
            return _no_voice_report(
                reason="VAD found active regions but no F0 was estimable",
                duration_s=duration_s,
                total_silent_s=total_silent_s,
                voicing_ratio=voicing_ratio,
            )

        pitch_mean = sum(f0s) / len(f0s)
        if len(f0s) >= 2:
            var = sum((f - pitch_mean) ** 2 for f in f0s) / len(f0s)
            pitch_std = math.sqrt(var)
        else:
            pitch_std = 0.0
        cv = pitch_std / pitch_mean if pitch_mean > 0 else 0.0
        # Map CV into a [0, 1] stability score (lower CV → higher stability)
        pitch_stability = max(0.0, min(1.0, 1.0 - cv / PROSODY_MELODIC_MAX_CV))

        # 3. Speaker register + prosody labels
        register = _speaker_register(pitch_mean)
        prosody = _prosody_label(cv)

        # 4. Syllable rate: onsets / total voiced duration
        syllable_rate_hz = _syllable_rate(samples, sample_rate, regions)

        # 5. Loudness
        peak = max(abs(x) for x in samples) if samples else 0.0
        intensity_db = round(20 * math.log10(max(peak, 1e-6)), 1)

        # Confidence: voicing ratio × pitch stability × number of F0 samples
        n_factor = min(1.0, len(f0s) / 10.0)  # need ≥10 voiced frames for full conf
        confidence = round(
            max(0.05, min(0.99, voicing_ratio * pitch_stability * n_factor)), 3,
        )

        payload = {
            "is_speech":          True,
            "pitch_hz_mean":      round(pitch_mean, 1),
            "pitch_hz_stability": round(pitch_stability, 3),
            "voicing_ratio":      round(voicing_ratio, 3),
            "speaker_register":   register,
            "prosody":            prosody,
            "syllable_rate_hz":   round(syllable_rate_hz, 2),
            "intensity_db":       intensity_db,
            "total_voiced_s":     round(total_voiced_s, 3),
            "total_silent_s":     round(total_silent_s, 3),
            "method":             "autocorr_f0_v1",
            "debug": {
                "voiced_regions":   [[round(s, 3), round(e, 3)] for s, e in regions],
                "f0_sample_count":  len(f0s),
                "pitch_cv":         round(cv, 4),
                "pitch_std_hz":     round(pitch_std, 2),
                "duration_s":       round(duration_s, 3),
                "sample_rate":      sample_rate,
            },
        }
        return VoiceReport.signed(payload=payload, confidence=confidence)


# ─── Internals ──────────────────────────────────────────────────────────


def _estimate_f0_in_region(
    samples: Sequence[float], sample_rate: int,
    start_s: float, end_s: float,
) -> list[float]:
    """One F0 estimate per ~30ms frame in [start_s, end_s).

    Time-domain autocorrelation. For each frame, find the lag in
    [sr/F0_MAX, sr/F0_MIN] with the strongest correlation; that lag
    → F0 = sr / lag. Frames with a weak peak are dropped (they're
    likely unvoiced fricatives).
    """
    start = int(start_s * sample_rate)
    end = min(len(samples), int(end_s * sample_rate))
    frame_size = int(0.030 * sample_rate)   # 30ms frames
    hop = int(0.015 * sample_rate)          # 50% overlap
    if frame_size < 64:
        return []

    min_lag = max(1, int(sample_rate / F0_MAX_HZ))
    max_lag = min(frame_size - 1, int(sample_rate / F0_MIN_HZ))
    if max_lag <= min_lag:
        return []

    f0s: list[float] = []
    pos = start
    while pos + frame_size <= end:
        frame = samples[pos:pos + frame_size]
        # Mean-subtract for autocorrelation stability
        mean_f = sum(frame) / len(frame)
        centered = [x - mean_f for x in frame]
        # Energy at lag 0
        ac0 = sum(x * x for x in centered)
        if ac0 <= 0:
            pos += hop
            continue
        best_lag = 0
        best_score = 0.0
        for lag in range(min_lag, max_lag + 1):
            s = 0.0
            for i in range(len(centered) - lag):
                s += centered[i] * centered[i + lag]
            norm = s / ac0
            if norm > best_score:
                best_score = norm
                best_lag = lag
        # Voiced threshold — autocorrelation peak ≥ 0.3 is the classic
        # cutoff for "this frame is voiced"
        if best_lag > 0 and best_score >= 0.3:
            f0s.append(sample_rate / best_lag)
        pos += hop
    return f0s


def _speaker_register(pitch_hz_mean: float) -> str:
    if pitch_hz_mean <= REGISTER_LOW_MAX:
        return "low"
    if pitch_hz_mean <= REGISTER_MID_MAX:
        return "mid"
    return "high"


def _prosody_label(cv: float) -> str:
    if cv <= PROSODY_MONOTONE_MAX_CV:
        return "monotone"
    if cv <= PROSODY_MELODIC_MAX_CV:
        return "melodic"
    return "excited"


def _syllable_rate(
    samples: Sequence[float], sample_rate: int,
    regions: list[tuple[float, float]],
) -> float:
    """Onsets per second inside the voiced regions only.

    Returns 0.0 if total voiced duration is 0 or there are no onsets.
    """
    total_voiced_s = sum(end - start for start, end in regions)
    if total_voiced_s <= 0:
        return 0.0
    onsets = 0
    for start_s, end_s in regions:
        start = int(start_s * sample_rate)
        end = min(len(samples), int(end_s * sample_rate))
        if end - start < int(0.05 * sample_rate):
            continue
        env, hop = envelope(samples[start:end], sample_rate, DEFAULT_HOP_MS)
        onsets += len(detect_onsets(env, rel_threshold=2.0))
    return onsets / total_voiced_s


def _no_voice_report(
    *, reason: str, duration_s: float, total_silent_s: float | None = None,
    voicing_ratio: float = 0.0,
) -> VoiceReport:
    if total_silent_s is None:
        total_silent_s = duration_s
    payload = {
        "is_speech":          False,
        "pitch_hz_mean":      0.0,
        "pitch_hz_stability": 0.0,
        "voicing_ratio":      round(voicing_ratio, 3),
        "speaker_register":   "none",
        "prosody":            "none",
        "syllable_rate_hz":   0.0,
        "intensity_db":       -120.0,
        "total_voiced_s":     0.0,
        "total_silent_s":     round(total_silent_s, 3),
        "method":             "autocorr_f0_v1",
        "debug":              {"reason": reason, "duration_s": round(duration_s, 3)},
    }
    return VoiceReport.signed(payload=payload, confidence=0.05)
