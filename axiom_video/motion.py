"""MotionClassifier — labels each track with a motion class +
emits a signed MotionReport.

Operates on the `Track` objects from ObjectTrackReport. For each
track, computes per-frame velocity vectors from consecutive
bounding-box centers, then classifies the track's overall motion
into one of:

  static        velocity below stillness threshold for ≥80% of frames
  downward      net y-displacement positive + dominant motion is downward
  upward        net y-displacement negative + dominant motion is upward
  lateral       net x-displacement > 2× net y-displacement
  accelerating  variance of velocity magnitude high (changing speed)
  erratic       direction changes more than direction_change_threshold

Stillness and direction thresholds are normalized-coord units —
0.005 = half a percent of frame width per frame. Conservative but
robust to detector jitter.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Sequence

from axiom_signing import derive_key

from .object_tracker import ObjectTrackReport, Track, TrackPoint

MOTION_KEY_NS = b"axiom-video-motion-v1"

MOTION_CLASSES = (
    "static", "downward", "upward", "lateral",
    "accelerating", "erratic",
)


@dataclass(frozen=True)
class TrackMotion:
    """Per-track motion summary."""
    id:                   str
    label:                str
    motion_class:         str
    net_displacement:     tuple[float, float]   # (dx, dy) over the full track
    mean_velocity_mag:    float
    velocity_variance:    float
    direction_changes:    int                    # how many sign-flips in vy


@dataclass(frozen=True)
class MotionReport:
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
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "MotionReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned))
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(_canonical(self)))

    @property
    def motions(self) -> list[TrackMotion]:
        return [
            TrackMotion(
                id=m["id"], label=m["label"],
                motion_class=m["motion_class"],
                net_displacement=tuple(m["net_displacement"]),
                mean_velocity_mag=m["mean_velocity_mag"],
                velocity_variance=m["velocity_variance"],
                direction_changes=m["direction_changes"],
            )
            for m in self.payload.get("motions", [])
        ]


class MotionClassifier:
    """Classifies each track's motion into a discrete class.

    Thresholds are tuned for normalized [0,1] coords on a 30 fps
    clip:

      stillness_threshold       mean velocity below this = "static"
      direction_change_threshold sign-flips above this = "erratic"
      lateral_ratio              dx/dy > this AND |dx| > stillness = "lateral"
      accel_variance_threshold   velocity-magnitude variance above this =
                                  "accelerating" (overrides direction)
    """

    def __init__(
        self,
        *,
        stillness_threshold:        float = 0.003,
        direction_change_threshold: int   = 3,
        lateral_ratio:              float = 2.0,
        accel_variance_threshold:   float = 0.0005,
    ) -> None:
        self.stillness_threshold = stillness_threshold
        self.direction_change_threshold = direction_change_threshold
        self.lateral_ratio = lateral_ratio
        self.accel_variance_threshold = accel_variance_threshold

    def classify(self, track_report: ObjectTrackReport) -> MotionReport:
        motions: list[TrackMotion] = []
        for track in track_report.tracks:
            motions.append(self._classify_track(track))

        # Confidence = fraction of tracks that landed in a non-static
        # class with non-zero displacement — proxy for "we saw movement
        # we can talk about". Static-only scenes return 0.5 (we ARE
        # confident nothing moved).
        moving = [m for m in motions if m.motion_class != "static"]
        if not motions:
            conf = 0.0
        elif not moving:
            conf = 0.5
        else:
            conf = min(1.0, 0.5 + 0.5 * (len(moving) / len(motions)))

        payload = {
            "motions": [
                {
                    "id": m.id, "label": m.label,
                    "motion_class": m.motion_class,
                    "net_displacement": list(m.net_displacement),
                    "mean_velocity_mag": round(m.mean_velocity_mag, 6),
                    "velocity_variance": round(m.velocity_variance, 8),
                    "direction_changes": m.direction_changes,
                }
                for m in motions
            ],
            "n_motions": len(motions),
            "dominant_class": _dominant_class(motions),
        }
        return MotionReport.signed(payload=payload, confidence=round(conf, 4))

    def _classify_track(self, track: Track) -> TrackMotion:
        if len(track.points) < 2:
            return TrackMotion(
                id=track.id, label=track.label,
                motion_class="static",
                net_displacement=(0.0, 0.0),
                mean_velocity_mag=0.0, velocity_variance=0.0,
                direction_changes=0,
            )

        velocities = _per_frame_velocities(track.points)
        v_mags = [(vx**2 + vy**2) ** 0.5 for vx, vy in velocities]
        mean_v = sum(v_mags) / len(v_mags)
        var_v = (
            sum((v - mean_v) ** 2 for v in v_mags) / len(v_mags)
            if v_mags else 0.0
        )

        # Net displacement (start to end of track)
        first = track.points[0].bbox
        last = track.points[-1].bbox
        first_cx = (first[0] + first[2]) / 2
        first_cy = (first[1] + first[3]) / 2
        last_cx = (last[0] + last[2]) / 2
        last_cy = (last[1] + last[3]) / 2
        net_dx = last_cx - first_cx
        net_dy = last_cy - first_cy

        # Direction changes in vy (the falling/rising signal)
        dir_changes = _count_sign_flips([vy for _, vy in velocities])

        # Classification priority — static beats accelerating beats erratic
        # beats lateral beats vertical
        if mean_v < self.stillness_threshold:
            motion_class = "static"
        elif var_v > self.accel_variance_threshold:
            motion_class = "accelerating"
        elif dir_changes >= self.direction_change_threshold:
            motion_class = "erratic"
        elif abs(net_dx) > self.lateral_ratio * abs(net_dy):
            motion_class = "lateral"
        elif net_dy > 0:
            motion_class = "downward"
        elif net_dy < 0:
            motion_class = "upward"
        else:
            motion_class = "static"

        return TrackMotion(
            id=track.id, label=track.label,
            motion_class=motion_class,
            net_displacement=(net_dx, net_dy),
            mean_velocity_mag=mean_v,
            velocity_variance=var_v,
            direction_changes=dir_changes,
        )


# ─── helpers ────────────────────────────────────────────────────────────


def _per_frame_velocities(points: Sequence[TrackPoint]) -> list[tuple[float, float]]:
    """Velocity between consecutive points, in normalized-coords-per-frame.

    The gap is `(frame_i+1 - frame_i)` so missing-frame gaps don't
    inflate apparent velocity.
    """
    out = []
    for prev, cur in zip(points, points[1:]):
        gap = max(1, cur.frame_index - prev.frame_index)
        pcx = (prev.bbox[0] + prev.bbox[2]) / 2
        pcy = (prev.bbox[1] + prev.bbox[3]) / 2
        ccx = (cur.bbox[0]  + cur.bbox[2])  / 2
        ccy = (cur.bbox[1]  + cur.bbox[3])  / 2
        out.append(((ccx - pcx) / gap, (ccy - pcy) / gap))
    return out


def _count_sign_flips(seq: Sequence[float]) -> int:
    """Number of sign changes in a sequence, ignoring zeros."""
    flips = 0
    prev_sign = 0
    for v in seq:
        s = 1 if v > 0 else -1 if v < 0 else 0
        if s != 0 and prev_sign != 0 and s != prev_sign:
            flips += 1
        if s != 0:
            prev_sign = s
    return flips


def _dominant_class(motions: list[TrackMotion]) -> str:
    if not motions:
        return "none"
    counts: dict[str, int] = {}
    for m in motions:
        counts[m.motion_class] = counts.get(m.motion_class, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _canonical(r: MotionReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(derive_key(MOTION_KEY_NS), payload,
                    hashlib.sha256).hexdigest()
