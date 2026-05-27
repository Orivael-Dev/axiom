"""Voice Activity Detection / silence gate.

Single purpose: take a mono PCM clip and report which time regions
contain "active" audio vs. silence. Used three ways:

  1. As a Coordinator agent ("vad") for callers who want a pure
     timeline of speech-vs-silence regions — e.g. streaming chunking,
     long-clip preprocessing.
  2. As a preprocessing step inside the Voice agent — VoiceAgent
     calls voice_activity_regions() to isolate voiced frames before
     pitch + prosody analysis.
  3. As a "dead-air cutter" — gate out silent regions before
     downstream classification runs, lowering FP rate.

Algorithm (stdlib only):
  - Frame the audio into 20ms windows (RMS envelope at 20ms hops).
  - Compute per-frame RMS energy + zero-crossing rate.
  - A frame is "active" iff:
       RMS > max(absolute_floor, relative_floor * max(env))
       AND zcr is in [ZCR_MIN, ZCR_MAX] (rejects DC offsets + ultra-bright noise).
  - Merge contiguous active frames; drop runs shorter than MIN_ACTIVE_MS;
    join active runs separated by gaps < MAX_GAP_MS.

Confidence = energy contrast between active and silent regions —
high contrast (clean speech in quiet room) ⇒ high confidence;
low contrast (whisper in noise) ⇒ low confidence.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Sequence

from axiom_signing import derive_key

from .features import (
    DEFAULT_HOP_MS, envelope, load_wav,
)

VAD_KEY_NS = b"axiom-vad-v1"

# Detection parameters — tuned for 16 kHz mono speech in moderate noise.
SILENCE_ABS_FLOOR = 0.003          # below this RMS = definitely silent
SILENCE_REL_FLOOR = 0.12           # frames below 12% of max are silent
ZCR_MIN = 0.005                    # rejects DC-only frames
ZCR_MAX = 0.35                     # rejects very bright noise / hiss
MIN_ACTIVE_MS = 80                 # active runs shorter than this are blips
MAX_GAP_MS = 200                   # bridge silent gaps shorter than this
FRAME_HOP_MS = 20                  # 20ms hops for VAD framing


# ─── Signed report ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class VADReport:
    """One VAD analysis result, HMAC-signed.

    payload fields:
      regions             list[[start_s, end_s], ...]  detected active windows
      activity_ratio      float in [0, 1] — total active / total duration
      total_active_s      float
      total_silent_s      float
      region_count        int
      method              str — algorithm tag for forward-compat
      debug               dict — frame counts + thresholds used
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
    def from_dict(cls, d: dict) -> "VADReport":
        return cls(
            payload=d["payload"],
            confidence=float(d.get("confidence", 1.0)),
            signature=d.get("signature", ""),
        )

    @classmethod
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "VADReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned), VAD_KEY_NS)
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        expected = _sign(_canonical(self), VAD_KEY_NS)
        return hmac.compare_digest(self.signature, expected)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def _canonical(r: VADReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(payload: bytes, namespace: bytes) -> str:
    return hmac.new(derive_key(namespace), payload, hashlib.sha256).hexdigest()


# ─── Public entry points ────────────────────────────────────────────────


def classify_vad_clip(wav_path: str) -> VADReport:
    """Load a WAV from disk + return a signed VADReport."""
    samples, sr = load_wav(wav_path)
    return VoiceActivityDetector().detect(samples, sr)


def voice_activity_regions(
    samples: Sequence[float], sample_rate: int,
) -> list[tuple[float, float]]:
    """Convenience: just the (start_s, end_s) active regions.

    Used internally by the Voice agent — VoiceAgent calls this to
    isolate voiced frames before pitch + prosody analysis.
    """
    report = VoiceActivityDetector().detect(samples, sample_rate)
    return [(r[0], r[1]) for r in report.payload["regions"]]


class VoiceActivityDetector:
    """Stateless energy + ZCR based VAD."""

    agent_name = "vad-v1"

    def detect(self, samples: Sequence[float], sample_rate: int) -> VADReport:
        if not samples:
            return _empty_report(reason="empty clip", duration_s=0.0)

        duration_s = len(samples) / sample_rate
        env, hop = envelope(samples, sample_rate, FRAME_HOP_MS)
        if not env:
            return _empty_report(reason="empty envelope", duration_s=duration_s)

        max_amp = max(env)
        if max_amp < SILENCE_ABS_FLOOR:
            # Entire clip is below the absolute silence floor
            return _empty_report(
                reason="clip entirely below silence floor",
                duration_s=duration_s,
                max_amp=max_amp,
            )

        threshold = max(SILENCE_ABS_FLOOR, SILENCE_REL_FLOOR * max_amp)
        zcrs = _per_frame_zcr(samples, hop)

        # Per-frame active mask combining energy + ZCR
        n_frames = min(len(env), len(zcrs))
        active = [False] * n_frames
        for i in range(n_frames):
            if env[i] > threshold and ZCR_MIN <= zcrs[i] <= ZCR_MAX:
                active[i] = True

        # Merge contiguous active runs; bridge small gaps; drop blips.
        regions = _runs_to_regions(active, hop, sample_rate)

        total_active_s = sum(end - start for start, end in regions)
        total_silent_s = max(0.0, duration_s - total_active_s)
        activity_ratio = total_active_s / duration_s if duration_s > 0 else 0.0

        # Confidence: contrast between active-frame mean energy and
        # silent-frame mean energy. High contrast = clean detection.
        active_mean = _mean([env[i] for i in range(n_frames) if active[i]])
        silent_mean = _mean([env[i] for i in range(n_frames) if not active[i]])
        if active_mean > 0:
            contrast = (active_mean - silent_mean) / active_mean
            confidence = max(0.05, min(0.99, contrast))
        else:
            # No active frames — confident in the silent verdict
            confidence = 0.9

        payload = {
            "regions":         [[round(s, 3), round(e, 3)] for s, e in regions],
            "activity_ratio":  round(activity_ratio, 3),
            "total_active_s":  round(total_active_s, 3),
            "total_silent_s":  round(total_silent_s, 3),
            "region_count":    len(regions),
            "method":          "energy_zcr_v1",
            "debug": {
                "frame_count":      n_frames,
                "hop_ms":           FRAME_HOP_MS,
                "energy_threshold": round(threshold, 5),
                "max_amp":          round(max_amp, 5),
                "active_mean_amp":  round(active_mean, 5),
                "silent_mean_amp":  round(silent_mean, 5),
                "duration_s":       round(duration_s, 3),
                "sample_rate":      sample_rate,
            },
        }
        return VADReport.signed(payload=payload, confidence=round(confidence, 3))


# ─── Internals ──────────────────────────────────────────────────────────


def _per_frame_zcr(samples: Sequence[float], hop: int) -> list[float]:
    """Zero-crossing rate per frame (one rate per `hop` samples).

    ZCR is the fraction of adjacent-sample pairs that change sign.
    Speech sits in ~0.02–0.20; pure tones near 0; bright noise > 0.4.
    """
    out: list[float] = []
    for i in range(0, len(samples), hop):
        chunk = samples[i:i + hop]
        if len(chunk) < 2:
            break
        crossings = 0
        for j in range(1, len(chunk)):
            if (chunk[j] >= 0) != (chunk[j - 1] >= 0):
                crossings += 1
        out.append(crossings / (len(chunk) - 1))
    return out


def _runs_to_regions(
    active: list[bool], hop: int, sample_rate: int,
) -> list[tuple[float, float]]:
    """Convert a frame-level active/silent mask into (start_s, end_s) regions.

    Pipeline: contiguous active frames → raw regions → bridge small
    gaps → drop short blips. Returns the final list of (start, end)
    in seconds.
    """
    if not active:
        return []
    frame_s = hop / sample_rate
    # 1. Raw contiguous runs of True
    raw: list[tuple[int, int]] = []
    i = 0
    while i < len(active):
        if active[i]:
            j = i
            while j < len(active) and active[j]:
                j += 1
            raw.append((i, j))  # exclusive end
            i = j
        else:
            i += 1
    if not raw:
        return []
    # 2. Bridge gaps shorter than MAX_GAP_MS
    max_gap_frames = max(1, int(MAX_GAP_MS / (frame_s * 1000)))
    merged: list[list[int]] = [list(raw[0])]
    for start, end in raw[1:]:
        if start - merged[-1][1] <= max_gap_frames:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    # 3. Drop runs shorter than MIN_ACTIVE_MS
    min_active_frames = max(1, int(MIN_ACTIVE_MS / (frame_s * 1000)))
    final: list[tuple[float, float]] = []
    for start, end in merged:
        if end - start >= min_active_frames:
            final.append((start * frame_s, end * frame_s))
    return final


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _empty_report(
    *, reason: str, duration_s: float = 0.0, max_amp: float = 0.0,
) -> VADReport:
    payload = {
        "regions":        [],
        "activity_ratio": 0.0,
        "total_active_s": 0.0,
        "total_silent_s": round(duration_s, 3),
        "region_count":   0,
        "method":         "energy_zcr_v1",
        "debug": {
            "reason":     reason,
            "duration_s": round(duration_s, 3),
            "max_amp":    round(max_amp, 5),
        },
    }
    return VADReport.signed(payload=payload, confidence=0.9)
