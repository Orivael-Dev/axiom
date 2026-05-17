"""Tempo / BPM estimator — Phase A's second agent.

Single-purpose: take a mono PCM clip and return an estimated BPM.

Why a separate agent (vs. baking tempo into ambient.py):

  - Tempo crosses the three audio families (ambient / voice / music).
    A drum loop, a snare-roll, a heartbeat, a metronome, and a
    walking-pace speech rhythm all want the same building block.
  - Tempo has objective ground truth — a 120 BPM metronome IS 120
    BPM — so testing is rigorous in a way material classification
    can't be.
  - Fits the selective-activation patent claim cleanly. Coordinator
    activates the tempo agent only when the caller's task wants it.

Algorithm:
  1. Build an RMS envelope of the input (10ms hops by default).
  2. Compute the autocorrelation of the envelope across lags
     corresponding to BPMs in [40, 240].
  3. Pick the lag with the strongest autocorrelation peak — that's
     the dominant period. Convert period → BPM.
  4. Stability = how dominant that peak is vs. the rest of the
     autocorrelation curve. High = clean periodic signal; low =
     uneven rhythm or background noise.

Confidence combines onset count + stability so a 3-second silent
clip can't claim a strong tempo verdict.

Stdlib-only — math, no numpy. The autocorrelation inner loop is
O(N · L) where L is the lag range (~125 frames for our BPM window),
which runs in tens of ms on a 3-second clip.
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

TEMPO_KEY_NS = b"axiom-tempo-v1"

MIN_BPM = 40
MAX_BPM = 240
SILENCE_FLOOR = 0.005    # same as ambient agent — RMS below this = silent


# ─── Signed report ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class TempoReport:
    """One tempo-estimation result, HMAC-signed.

    payload fields:
      bpm                  float — estimated BPM (0.0 if no tempo detected)
      tempo_stability      float in [0, 1] — peak prominence
      dominant_period_ms   float — period of the dominant rhythm
      onset_count          int — onsets the detector saw
      method               str — algorithm tag for forward-compat
      debug                dict — candidate BPMs + their scores
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
    def from_dict(cls, d: dict) -> "TempoReport":
        return cls(
            payload=d["payload"],
            confidence=float(d.get("confidence", 1.0)),
            signature=d.get("signature", ""),
        )

    @classmethod
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "TempoReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned), TEMPO_KEY_NS)
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        expected = _sign(_canonical(self), TEMPO_KEY_NS)
        return hmac.compare_digest(self.signature, expected)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def _canonical(r: TempoReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(payload: bytes, namespace: bytes) -> str:
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()


# ─── Public entry points ────────────────────────────────────────────────


def classify_tempo_clip(wav_path: str) -> TempoReport:
    """Load a WAV from disk + return a signed TempoReport."""
    samples, sr = load_wav(wav_path)
    return TempoEstimator().estimate(samples, sr)


class TempoEstimator:
    """Stateless autocorrelation-based BPM estimator."""

    agent_name = "tempo-v1"

    def estimate(self, samples: Sequence[float], sample_rate: int) -> TempoReport:
        env, hop = envelope(samples, sample_rate, DEFAULT_HOP_MS)
        if not env:
            return _no_tempo_report(reason="empty envelope")

        # Below the silence floor → no tempo (low confidence)
        if max(env) < SILENCE_FLOOR:
            return _no_tempo_report(reason="below silence floor")

        # Onsets feed both the autocorrelation seed AND the confidence
        onsets = detect_onsets(env, rel_threshold=2.0)
        if len(onsets) < 2:
            return _no_tempo_report(
                reason=f"only {len(onsets)} onset(s) — need ≥ 2",
                onset_count=len(onsets),
            )

        # Frame rate of the envelope (frames / second)
        frame_rate = 1000.0 / DEFAULT_HOP_MS  # = 100 frames/sec at 10 ms hops

        # Lag range mapping to [MIN_BPM, MAX_BPM] beats per minute
        min_lag = max(1, int(frame_rate * 60 / MAX_BPM))   # ~25 frames at 240 BPM
        max_lag = min(len(env) - 1, int(frame_rate * 60 / MIN_BPM))  # ~150 frames at 40 BPM
        if max_lag <= min_lag:
            return _no_tempo_report(
                reason="clip too short for tempo analysis",
                onset_count=len(onsets),
            )

        # Centered envelope (mean-subtracted) for autocorrelation
        mean_env = sum(env) / len(env)
        centered = [x - mean_env for x in env]
        n = len(centered)

        # Energy of the signal (lag-0 autocorrelation) — used for normalizing
        ac0 = sum(x * x for x in centered)
        if ac0 <= 0:
            return _no_tempo_report(
                reason="zero-energy envelope", onset_count=len(onsets),
            )

        # Compute normalized autocorrelation across the BPM lag window
        scores: list[tuple[float, float]] = []  # (bpm, normalized_score)
        for lag in range(min_lag, max_lag + 1):
            s = 0.0
            for i in range(n - lag):
                s += centered[i] * centered[i + lag]
            norm = s / ac0  # 1.0 at lag 0, [-1, 1] elsewhere
            bpm = 60.0 * frame_rate / lag
            scores.append((bpm, norm))

        # Best peak
        best_bpm, best_score = max(scores, key=lambda t: t[1])

        # Stability = the normalized autocorrelation peak height itself,
        # clipped to [0, 1]. The normalization (division by lag-0 energy)
        # already maps a perfectly periodic signal to ~1.0 and pure
        # noise toward ~0.0. A peak-vs-mean ratio saturates too easily
        # (any single dominant lag scores near 1.0 even if it's faint
        # in absolute terms), which obscures the difference between a
        # crisp metronome (peak ~0.8+) and random clicks (peak ~0.1).
        stability = max(0.0, min(1.0, best_score))

        dominant_period_ms = 60_000.0 / best_bpm if best_bpm > 0 else 0.0

        # Confidence = stability * onset-saturation. With < 4 onsets we
        # damp confidence linearly — autocorrelation can produce a
        # plausible-looking BPM from a single accidental periodicity.
        onset_factor = min(1.0, len(onsets) / 6.0)
        confidence = round(stability * onset_factor, 3)

        # Top-3 candidate BPMs for the debug block (humans + auditors
        # can sanity-check whether 60 BPM and 120 BPM both scored high,
        # which is the classic "octave error" failure mode)
        top3 = sorted(scores, key=lambda t: -t[1])[:3]

        payload = {
            "bpm":                round(best_bpm, 2),
            "tempo_stability":    round(stability, 3),
            "dominant_period_ms": round(dominant_period_ms, 1),
            "onset_count":        len(onsets),
            "method":             "autocorr_v1",
            "debug": {
                "candidate_bpms":   [(round(b, 2), round(s, 3)) for b, s in top3],
                "lag_range_frames": [min_lag, max_lag],
                "frame_rate_hz":    frame_rate,
                "duration_s":       round(len(samples) / sample_rate, 3),
                "sample_rate":      sample_rate,
            },
        }
        return TempoReport.signed(payload=payload, confidence=confidence)


def _no_tempo_report(*, reason: str, onset_count: int = 0) -> TempoReport:
    payload = {
        "bpm":                0.0,
        "tempo_stability":    0.0,
        "dominant_period_ms": 0.0,
        "onset_count":        onset_count,
        "method":             "autocorr_v1",
        "debug":              {"reason": reason},
    }
    return TempoReport.signed(payload=payload, confidence=0.0)
