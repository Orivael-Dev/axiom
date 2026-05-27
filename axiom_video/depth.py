"""DepthClassifier — front-to-back ordering + approach/recede + occlusion.

Per the user's framing: depth is a number (or three numbers) per
object. Once you have that number, classification is partitioning
the depth axis — same shape as ColorWatcher partitioning the HSV
cylinder.

## Input contract

Detector-agnostic, same as the rest of axiom_video.

Each `Object.extras["depth"]` is a float in [0.0, 1.0]:

  0.0  — infinity (background / horizon)
  1.0  — closest to camera

Customers with absolute depth (RGBD camera, LIDAR, stereo) divide
by their max-range to normalize. Customers without depth metadata
(monocular RGB camera) get a **bbox-area fallback**: relative
depth within a single clip, using bbox area as the proxy (bigger
= closer). The fallback labels every object's depth as relative
to the scene's bbox-area distribution, which is honest about the
ambiguity ("we don't have true depth — here's a rank ordering").

## What it produces

  1. Per-track depth class: near / mid / far
  2. Per-track approach / recede events (Δdepth > threshold)
  3. Per-frame front-to-back ordering of tracks
  4. Occlusion events: two tracks' bboxes overlap AND the front
     one is significantly closer in depth

## Use cases

  - Kid-toy: "child reaches toward toy" — toy depth fixed, hand
    depth increasing (approaching)
  - Dashcam: collision warning — vehicle depth increasing rapidly
  - Smart-home: visitor-at-door vs visitor-distant
  - Sports: player line-of-defense ordering

## Why partitioning, not raw values

LLM-style "tell me what's in the frame" would dump raw depth maps
as tokens. AXIOM picks a 3-bucket partition (near / mid / far) +
signed events because:

  - 3 buckets are auditor-readable; raw depth maps are not
  - Events compose with the temporal chain
  - Determinism survives float drift across runs
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Optional

from axiom_signing import derive_key

from .object_tracker import ObjectTrackReport
from .scene import Object, Scene, SceneGraph, iou

DEPTH_KEY_NS = b"axiom-video-depth-v1"

NEAR_THRESHOLD = 0.66
FAR_THRESHOLD  = 0.33


@dataclass(frozen=True)
class TrackDepth:
    id:                str
    label:             str
    depth_mean:        float
    depth_class:       str            # near | mid | far
    depth_range:       float          # max-min across observations
    stable:            bool
    n_observations:    int
    source:            str            # "extras" | "bbox_area"


@dataclass(frozen=True)
class DepthEvent:
    frame_index:  int
    track_id:     str
    event_type:   str        # "approach" | "recede" | "occlusion"
    magnitude:    float
    occluded_by:  Optional[str] = None    # for occlusion events


@dataclass(frozen=True)
class DepthReport:
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
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "DepthReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned))
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(_canonical(self)))


class DepthClassifier:
    """Front-to-back ordering + approach/recede/occlusion event detection.

    `change_threshold` — Δdepth between consecutive frames that
    triggers an approach/recede event. Default 0.10 (10% of the
    normalized depth axis per frame).

    `occlusion_iou` — minimum bbox IoU between two tracks for them
    to count as visually overlapping (precondition for occlusion).

    `occlusion_depth_gap` — minimum depth difference for the
    overlap to count as occlusion vs side-by-side.
    """

    def __init__(
        self,
        *,
        change_threshold:    float = 0.10,
        occlusion_iou:       float = 0.10,
        occlusion_depth_gap: float = 0.15,
    ) -> None:
        self.change_threshold = change_threshold
        self.occlusion_iou = occlusion_iou
        self.occlusion_depth_gap = occlusion_depth_gap

    def classify(
        self,
        sg: SceneGraph,
        track_report: ObjectTrackReport,
    ) -> DepthReport:
        # Per-track depth observations
        tracks = track_report.tracks
        track_ids = {t.id for t in tracks}
        label_lookup = {t.id: t.label for t in tracks}

        # Decide source: if ANY object in ANY scene has extras["depth"],
        # use extras; otherwise fall back to bbox-area.
        source = "extras" if _has_extras_depth(sg) else "bbox_area"

        # Build (frame_index -> {track_id: depth}) so we can also
        # produce per-frame front-to-back ordering.
        depth_by_frame: dict[int, dict[str, float]] = {}
        depth_by_track: dict[str, list[tuple[int, float]]] = {}
        bbox_by_frame:  dict[int, dict[str, tuple[float, float, float, float]]] = {}

        if source == "bbox_area":
            # Normalize across the WHOLE clip — find min/max bbox area
            # across all observations to map area → depth in [0, 1].
            all_areas = []
            for scene in sg.scenes:
                for obj in scene.objects:
                    all_areas.append(_obj_area(obj))
            min_area = min(all_areas) if all_areas else 0.0
            max_area = max(all_areas) if all_areas else 1.0
            span = max(1e-9, max_area - min_area)

        for scene in sg.scenes:
            for obj in scene.objects:
                if obj.id not in track_ids:
                    continue
                if source == "extras":
                    d = obj.extras.get("depth")
                    if d is None:
                        continue
                    d = float(max(0.0, min(1.0, d)))
                else:
                    area = _obj_area(obj)
                    d = (area - min_area) / span   # 0 = smallest = far
                depth_by_frame.setdefault(scene.frame_index, {})[obj.id] = d
                depth_by_track.setdefault(obj.id, []).append(
                    (scene.frame_index, d),
                )
                bbox_by_frame.setdefault(scene.frame_index, {})[obj.id] = obj.bbox

        # Per-track aggregates + events
        track_depths: list[TrackDepth] = []
        events:       list[DepthEvent] = []

        for tid, obs in depth_by_track.items():
            obs.sort(key=lambda p: p[0])
            depths = [d for _, d in obs]
            mean_d = sum(depths) / len(depths)
            depth_range = max(depths) - min(depths)
            depth_class = (
                "near" if mean_d >= NEAR_THRESHOLD else
                "far"  if mean_d <  FAR_THRESHOLD  else
                "mid"
            )
            stable = depth_range < self.change_threshold
            track_depths.append(TrackDepth(
                id=tid,
                label=label_lookup.get(tid, "unknown"),
                depth_mean=mean_d,
                depth_class=depth_class,
                depth_range=depth_range,
                stable=stable,
                n_observations=len(obs),
                source=source,
            ))

            # Approach / recede events
            for (prev_f, prev_d), (cur_f, cur_d) in zip(obs, obs[1:]):
                delta = cur_d - prev_d
                if abs(delta) < self.change_threshold:
                    continue
                events.append(DepthEvent(
                    frame_index=cur_f,
                    track_id=tid,
                    event_type=("approach" if delta > 0 else "recede"),
                    magnitude=round(abs(delta), 4),
                ))

        # Occlusion events: per frame, find pairs that overlap AND
        # have a meaningful depth gap. Emit a single event per pair
        # per frame (no continuous re-firing).
        seen_occlusion_pairs: set[tuple[str, str, int]] = set()
        for frame_idx in sorted(bbox_by_frame):
            ids = sorted(bbox_by_frame[frame_idx].keys())
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    if iou(bbox_by_frame[frame_idx][a],
                            bbox_by_frame[frame_idx][b]) < self.occlusion_iou:
                        continue
                    da = depth_by_frame.get(frame_idx, {}).get(a)
                    db = depth_by_frame.get(frame_idx, {}).get(b)
                    if da is None or db is None:
                        continue
                    gap = abs(da - db)
                    if gap < self.occlusion_depth_gap:
                        continue
                    front, back = (a, b) if da > db else (b, a)
                    key = (back, front, frame_idx)
                    if key in seen_occlusion_pairs:
                        continue
                    seen_occlusion_pairs.add(key)
                    events.append(DepthEvent(
                        frame_index=frame_idx,
                        track_id=back,
                        event_type="occlusion",
                        magnitude=round(gap, 4),
                        occluded_by=front,
                    ))

        events.sort(key=lambda e: (e.frame_index, e.track_id))

        # Front-to-back ordering per frame (front = highest depth)
        per_frame_order = []
        for frame_idx in sorted(depth_by_frame):
            depths = depth_by_frame[frame_idx]
            order = sorted(depths.items(), key=lambda kv: -kv[1])
            per_frame_order.append({
                "frame_index": frame_idx,
                "order": [tid for tid, _ in order],
            })

        # Confidence: high if extras-supplied + multiple tracks; moderate
        # if bbox-area fallback; low if depth data was missing.
        n_tracks = len(track_depths)
        if n_tracks == 0:
            conf = 0.0
        elif source == "extras":
            conf = min(1.0, 0.7 + 0.05 * n_tracks)
        else:
            conf = 0.5    # honest about being a fallback estimate

        payload = {
            "source": source,
            "n_tracks": n_tracks,
            "n_events": len(events),
            "tracks": [
                {
                    "id": td.id, "label": td.label,
                    "depth_mean": round(td.depth_mean, 4),
                    "depth_class": td.depth_class,
                    "depth_range": round(td.depth_range, 4),
                    "stable": td.stable,
                    "n_observations": td.n_observations,
                    "source": td.source,
                }
                for td in track_depths
            ],
            "events": [
                {
                    "frame_index": e.frame_index,
                    "track_id": e.track_id,
                    "event_type": e.event_type,
                    "magnitude": e.magnitude,
                    **({"occluded_by": e.occluded_by}
                        if e.occluded_by else {}),
                }
                for e in events
            ],
            "frame_ordering": per_frame_order,
        }
        return DepthReport.signed(payload=payload, confidence=round(conf, 4))


# ─── helpers ────────────────────────────────────────────────────────────


def _has_extras_depth(sg: SceneGraph) -> bool:
    for scene in sg.scenes:
        for obj in scene.objects:
            if obj.extras and "depth" in obj.extras:
                return True
    return False


def _obj_area(obj: Object) -> float:
    x1, y1, x2, y2 = obj.bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _canonical(r: DepthReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(derive_key(DEPTH_KEY_NS), payload,
                    hashlib.sha256).hexdigest()
