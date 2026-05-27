"""TimeKeeper — rhythm + timing analysis over a TemporalChainReport.

Conceptually parallel to `axiom_audio.tempo` (which finds BPM in
waveform envelopes), but applied to event streams: instead of
"are these audio peaks periodic?" it asks "are these video events
periodic?"

Useful for:

  - kid-AI: "does the toy's interaction cadence look natural or
    is the model spamming?" — rhythm score on contact events
  - dashcam: "are deceleration events clustered or evenly spread?"
    — burst detection
  - smart-home camera: "the doorbell-press events have a 4-second
    refractory period — anything tighter than 1 second is spoof"
  - sports: "regular foot-strike rhythm at 180/min = healthy gait"

The algorithm is intentionally pure-Python + tiny:

  1. Sort events by time.
  2. Compute inter-event intervals across the full stream + per
     event type.
  3. Rhythm score = 1 - (std / mean). Perfectly periodic → score 1.
  4. Silence: any interval > silence_threshold_s.
  5. Burst: a sliding window of `burst_window_s` containing ≥
     `burst_min_events` events.

Stateless across calls; same input → same signed output.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from axiom_signing import derive_key

from .temporal_chain import TemporalChainReport

TIMEKEEPER_KEY_NS = b"axiom-video-timekeeper-v1"


@dataclass(frozen=True)
class IntervalStats:
    n:    int
    mean: float
    std:  float
    min:  float
    max:  float


@dataclass(frozen=True)
class Silence:
    start_s:    float
    end_s:      float
    duration_s: float


@dataclass(frozen=True)
class Burst:
    start_s:  float
    end_s:    float
    n_events: int
    types:    tuple[str, ...]


@dataclass(frozen=True)
class TimeKeeperReport:
    payload:    dict
    confidence: float = 1.0
    signature:  str = ""

    def to_dict(self) -> dict:
        return {
            "payload": self.payload,
            "confidence": self.confidence,
            "signature": self.signature,
        }

    @classmethod
    def signed(cls, *, payload: dict, confidence: float = 1.0
              ) -> "TimeKeeperReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned))
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(_canonical(self)))


class TimeKeeper:
    """Analyzes the rhythm + timing of events in a TemporalChainReport.

    Thresholds default to values that work for normal-speed
    interactions (≥1 s gaps as silence, 3+ events in 0.5 s as a
    burst). Tune via constructor for faster / slower domains.

    `event_type_filter` — set of event types to consider for the
    rhythm score. By default we use all types; pass e.g.
    `{"contact"}` to score only contact rhythm.
    """

    def __init__(
        self,
        *,
        silence_threshold_s: float = 1.0,
        burst_window_s:      float = 0.5,
        burst_min_events:    int   = 3,
        event_type_filter:   set[str] | None = None,
    ) -> None:
        self.silence_threshold_s = silence_threshold_s
        self.burst_window_s = burst_window_s
        self.burst_min_events = burst_min_events
        self.event_type_filter = event_type_filter

    def analyze(self, chain: TemporalChainReport) -> TimeKeeperReport:
        events = chain.events
        if self.event_type_filter:
            events = [e for e in events
                      if e.type in self.event_type_filter]

        # Sort by time (the chain should already be sorted; we're
        # defensive in case)
        events = sorted(events, key=lambda e: e.t)

        times = [e.t for e in events]
        intervals = _intervals(times)
        overall_stats = _stats(intervals)

        # Per-type intervals
        by_type: dict[str, IntervalStats] = {}
        type_groups: dict[str, list[float]] = {}
        for e in events:
            type_groups.setdefault(e.type, []).append(e.t)
        for t, ts in type_groups.items():
            by_type[t] = _stats(_intervals(ts))

        rhythm_score = _rhythm_score(overall_stats)
        silences = _detect_silences(times, self.silence_threshold_s)
        bursts = _detect_bursts(
            events,
            window_s=self.burst_window_s,
            min_events=self.burst_min_events,
        )

        # Confidence: function of how many events we had.
        #   0 events  → 0.0
        #   1 event   → 0.25 (we ran but only saw one)
        #   2+ events → 0.5 + 0.5 * fraction-of-rhythm-clarity
        if not events:
            conf = 0.0
        elif len(events) == 1:
            conf = 0.25
        else:
            conf = min(1.0, 0.5 + 0.5 * rhythm_score)

        payload = {
            "duration_s":     round(_chain_duration(chain), 4),
            "n_events":       len(events),
            "rhythm_score":   round(rhythm_score, 4),
            "intervals": {
                "all_events": _stats_to_dict(overall_stats),
                "by_type":    {t: _stats_to_dict(s)
                                for t, s in by_type.items()},
            },
            "silences": [
                {"start_s": round(s.start_s, 4),
                 "end_s":   round(s.end_s, 4),
                 "duration_s": round(s.duration_s, 4)}
                for s in silences
            ],
            "bursts": [
                {"start_s": round(b.start_s, 4),
                 "end_s":   round(b.end_s, 4),
                 "n_events": b.n_events,
                 "types": list(b.types)}
                for b in bursts
            ],
            "rhythm_class": _rhythm_class(rhythm_score, len(events)),
        }
        return TimeKeeperReport.signed(
            payload=payload, confidence=round(conf, 4),
        )


# ─── stats helpers ──────────────────────────────────────────────────────


def _intervals(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:])]


def _stats(values: list[float]) -> IntervalStats:
    n = len(values)
    if n == 0:
        return IntervalStats(n=0, mean=0.0, std=0.0, min=0.0, max=0.0)
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / n
    std = var ** 0.5
    return IntervalStats(n=n, mean=m, std=std,
                         min=min(values), max=max(values))


def _stats_to_dict(s: IntervalStats) -> dict:
    return {
        "n":    s.n,
        "mean": round(s.mean, 4),
        "std":  round(s.std, 4),
        "min":  round(s.min, 4),
        "max":  round(s.max, 4),
    }


def _rhythm_score(s: IntervalStats) -> float:
    """1 - coefficient-of-variation, clamped to [0, 1].

    Perfectly regular → std=0 → score=1.
    Highly irregular → std≈mean → score≈0.
    Single event (n=0 intervals) → 0 (no rhythm to score).
    """
    if s.n < 1 or s.mean <= 0:
        return 0.0
    cv = s.std / s.mean
    return max(0.0, min(1.0, 1.0 - cv))


def _rhythm_class(score: float, n_events: int) -> str:
    """Discretize the rhythm score for downstream readability."""
    if n_events < 2:
        return "insufficient"
    if score >= 0.85:
        return "regular"
    if score >= 0.6:
        return "semi_regular"
    if score >= 0.3:
        return "irregular"
    return "chaotic"


def _detect_silences(times: list[float], threshold_s: float) -> list[Silence]:
    out = []
    for a, b in zip(times, times[1:]):
        gap = b - a
        if gap > threshold_s:
            out.append(Silence(start_s=a, end_s=b, duration_s=gap))
    return out


def _detect_bursts(
    events,
    *,
    window_s: float,
    min_events: int,
) -> list[Burst]:
    out: list[Burst] = []
    n = len(events)
    i = 0
    while i < n:
        j = i
        while j < n and events[j].t - events[i].t <= window_s:
            j += 1
        if (j - i) >= min_events:
            window = events[i:j]
            out.append(Burst(
                start_s=window[0].t,
                end_s=window[-1].t,
                n_events=len(window),
                types=tuple(sorted({e.type for e in window})),
            ))
            i = j   # skip past this burst to avoid overlapping reports
        else:
            i += 1
    return out


def _chain_duration(chain: TemporalChainReport) -> float:
    events = chain.events
    if not events:
        return 0.0
    return max(e.t for e in events) - min(e.t for e in events)


# ─── signing ────────────────────────────────────────────────────────────


def _canonical(r: TimeKeeperReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(derive_key(TIMEKEEPER_KEY_NS), payload,
                    hashlib.sha256).hexdigest()
