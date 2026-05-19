"""SurfaceClassifier — orientation state + tip events + stability.

Pairs with DepthClassifier and feeds the eventual PhysicsAgent.
Where DepthClassifier asks "where in z is this object?", the
Surface agent asks "how is this object oriented in space, and
how stable is that orientation?"

## Input contract

Each `Object.extras["orientation"]` is one of:

  - A single float — tilt angle in degrees, where 0° = canonical
    upright and ±180° = inverted. Sign indicates direction
    (positive = left-lean, negative = right-lean — coordinate
    convention is the customer's).
  - A tuple (pitch, roll, yaw) in degrees — only roll is used in
    Phase A; pitch/yaw inform the Causality / Physics agents
    downstream.

When `extras["orientation"]` is missing, the agent **falls back
to aspect-ratio analysis** of the bounding box: tracks whose
upright aspect ratio (height / width) is known from early frames
provide a baseline, and frames whose aspect ratio diverges by
more than `aspect_change_threshold` from the baseline emit
tip events. Honest fallback — works for "cup tips over" without
needing the customer to wire a pose estimator.

## What it produces

  1. Per-track orientation class: upright / tilted / inverted /
     flat (override when prone)
  2. Per-track stability score: how consistent the orientation
     is across the track's lifetime (lower variance = more stable)
  3. Tip events: orientation crossing the upright→tilted or
     tilted→inverted threshold
  4. Scene-level "instability" alert when more than one track is
     classified as `tilted` or `inverted`

## Use cases

  - Kid-toy: "cup tilt → pour" — the concept doc's flagship
    sequence. Surface watches for the tilt event; Causality
    chains tilt + downward-motion + impact = "spill"
  - Dashcam: vehicle rollover detection (sustained orientation
    > 90°)
  - Smart-home: glass dropping (rapid tilt + downward motion)
  - Healthcare: patient posture monitoring (upright → leaning →
    flat = fall)
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Optional

from axiom_signing import derive_key

from .object_tracker import ObjectTrackReport
from .scene import Object, Scene, SceneGraph

SURFACE_KEY_NS = b"axiom-video-surface-v1"

# Orientation class thresholds (degrees from upright)
TILT_THRESHOLD     = 20.0      # |angle| > 20° = tilted
INVERTED_THRESHOLD = 120.0     # |angle| > 120° = inverted
FLAT_THRESHOLD     = 75.0      # 75-105° = flat-on-side


@dataclass(frozen=True)
class TrackSurface:
    id:                str
    label:             str
    orientation_mean:  float
    orientation_class: str             # upright | tilted | inverted | flat
    stability_score:   float           # 0..1 — higher = more stable
    n_observations:    int
    source:            str             # "extras" | "aspect_ratio"


@dataclass(frozen=True)
class TipEvent:
    frame_index:   int
    track_id:      str
    event_type:    str        # "tip_to_tilted" | "tip_to_inverted" | "tip_to_flat" | "right_self"
    from_class:    str
    to_class:      str
    angle:         float


@dataclass(frozen=True)
class SurfaceReport:
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
              ) -> "SurfaceReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned))
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(_canonical(self)))


class SurfaceClassifier:
    """Orientation classification + tip-event detection.

    `aspect_change_threshold` — fraction by which the aspect
    ratio must change from the baseline to trigger fallback-mode
    tip detection. Default 0.40 (a normally-tall cup becoming
    40%+ wider relative to its first-frame baseline).
    """

    def __init__(
        self,
        *,
        aspect_change_threshold: float = 0.40,
    ) -> None:
        self.aspect_change_threshold = aspect_change_threshold

    def classify(
        self,
        sg: SceneGraph,
        track_report: ObjectTrackReport,
    ) -> SurfaceReport:
        tracks = track_report.tracks
        track_ids = {t.id for t in tracks}
        label_lookup = {t.id: t.label for t in tracks}

        source = "extras" if _has_extras_orientation(sg) else "aspect_ratio"

        # Per-track observations: list of (frame_index, angle_degrees)
        obs_by_track: dict[str, list[tuple[int, float]]] = {}

        if source == "extras":
            for scene in sg.scenes:
                for obj in scene.objects:
                    if obj.id not in track_ids:
                        continue
                    angle = _extract_orientation(obj)
                    if angle is None:
                        continue
                    obs_by_track.setdefault(obj.id, []).append(
                        (scene.frame_index, angle),
                    )
        else:
            # Aspect-ratio fallback: track's first observation defines
            # the baseline; subsequent observations get an estimated
            # tilt angle from the ratio change.
            baseline_aspect: dict[str, float] = {}
            scenes_sorted = sorted(sg.scenes, key=lambda s: s.frame_index)
            for scene in scenes_sorted:
                for obj in scene.objects:
                    if obj.id not in track_ids:
                        continue
                    aspect = _aspect_ratio(obj)
                    if aspect is None:
                        continue
                    if obj.id not in baseline_aspect:
                        baseline_aspect[obj.id] = aspect
                        obs_by_track.setdefault(obj.id, []).append(
                            (scene.frame_index, 0.0),
                        )
                        continue
                    baseline = baseline_aspect[obj.id]
                    delta = (aspect - baseline) / baseline
                    # Map ratio delta -> estimated tilt angle:
                    # -1.0 (object now wider than tall vs baseline) ≈ 90°
                    angle = max(-180.0, min(180.0, -delta * 90.0))
                    obs_by_track.setdefault(obj.id, []).append(
                        (scene.frame_index, angle),
                    )

        track_surfaces: list[TrackSurface] = []
        events:         list[TipEvent] = []

        for tid, obs in obs_by_track.items():
            obs.sort(key=lambda p: p[0])
            angles = [a for _, a in obs]
            mean_a = sum(angles) / len(angles)
            classes_seen = [_orientation_class(a) for a in angles]
            dominant = _mode(classes_seen) or _orientation_class(mean_a)

            # Stability score: 1 - normalized variance of class transitions
            n = len(classes_seen)
            transitions = sum(
                1 for a, b in zip(classes_seen, classes_seen[1:]) if a != b
            )
            stability = 1.0 if n <= 1 else max(0.0, 1.0 - transitions / (n - 1))

            track_surfaces.append(TrackSurface(
                id=tid,
                label=label_lookup.get(tid, "unknown"),
                orientation_mean=mean_a,
                orientation_class=dominant,
                stability_score=stability,
                n_observations=len(obs),
                source=source,
            ))

            # Tip events: each class transition emits one event
            prev_class = classes_seen[0]
            for (frame_idx, angle), cur_class in zip(
                obs[1:], classes_seen[1:]
            ):
                if cur_class == prev_class:
                    continue
                ev_type = _event_type(prev_class, cur_class)
                events.append(TipEvent(
                    frame_index=frame_idx,
                    track_id=tid,
                    event_type=ev_type,
                    from_class=prev_class,
                    to_class=cur_class,
                    angle=round(angle, 2),
                ))
                prev_class = cur_class

        events.sort(key=lambda e: (e.frame_index, e.track_id))

        # Scene-level instability flag: multiple tracks tilted or inverted
        unstable_tracks = sum(
            1 for ts in track_surfaces
            if ts.orientation_class in ("tilted", "inverted", "flat")
        )
        scene_unstable = unstable_tracks >= 2

        # Confidence: high if extras-supplied; honest moderate if
        # we fell back to aspect-ratio.
        n_tracks = len(track_surfaces)
        if n_tracks == 0:
            conf = 0.0
        elif source == "extras":
            conf = min(1.0, 0.7 + 0.05 * n_tracks)
        else:
            conf = 0.5

        payload = {
            "source": source,
            "n_tracks": n_tracks,
            "n_events": len(events),
            "scene_unstable": scene_unstable,
            "tracks": [
                {
                    "id": ts.id, "label": ts.label,
                    "orientation_mean":  round(ts.orientation_mean, 2),
                    "orientation_class": ts.orientation_class,
                    "stability_score":   round(ts.stability_score, 4),
                    "n_observations":    ts.n_observations,
                    "source":            ts.source,
                }
                for ts in track_surfaces
            ],
            "events": [
                {
                    "frame_index": e.frame_index,
                    "track_id": e.track_id,
                    "event_type": e.event_type,
                    "from": e.from_class,
                    "to": e.to_class,
                    "angle": e.angle,
                }
                for e in events
            ],
        }
        return SurfaceReport.signed(payload=payload, confidence=round(conf, 4))


# ─── helpers ────────────────────────────────────────────────────────────


def _has_extras_orientation(sg: SceneGraph) -> bool:
    for scene in sg.scenes:
        for obj in scene.objects:
            if obj.extras and "orientation" in obj.extras:
                return True
    return False


def _extract_orientation(obj: Object) -> Optional[float]:
    """Pull the roll angle from obj.extras['orientation']. Handles
    both scalar (single tilt angle) and 3-tuple (pitch, roll, yaw)
    forms."""
    o = obj.extras.get("orientation") if obj.extras else None
    if o is None:
        return None
    if isinstance(o, (int, float)):
        return float(o)
    if isinstance(o, (tuple, list)) and len(o) >= 2:
        # (pitch, roll, yaw) — roll is the in-plane tilt
        return float(o[1])
    return None


def _aspect_ratio(obj: Object) -> Optional[float]:
    """height / width of the bbox; None if undefined."""
    x1, y1, x2, y2 = obj.bbox
    w = x2 - x1
    h = y2 - y1
    if w <= 0 or h <= 0:
        return None
    return h / w


def _orientation_class(angle: float) -> str:
    """Map an angle in degrees to one of upright / tilted / flat / inverted."""
    a = abs(angle)
    if a < TILT_THRESHOLD:
        return "upright"
    if a > INVERTED_THRESHOLD:
        return "inverted"
    if FLAT_THRESHOLD - 15.0 <= a <= FLAT_THRESHOLD + 15.0:
        return "flat"
    return "tilted"


def _mode(values: list) -> Optional[str]:
    if not values:
        return None
    counts: dict = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _event_type(prev: str, cur: str) -> str:
    """Name the transition for readability."""
    if cur == "upright" and prev != "upright":
        return "right_self"
    if cur == "tilted":
        return "tip_to_tilted"
    if cur == "inverted":
        return "tip_to_inverted"
    if cur == "flat":
        return "tip_to_flat"
    return f"{prev}_to_{cur}"


def _canonical(r: SurfaceReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(derive_key(SURFACE_KEY_NS), payload,
                    hashlib.sha256).hexdigest()
