"""ObjectTracker — assigns stable cross-frame identities + emits
a signed ObjectTrackReport.

Two modes:

  IDS_PROVIDED   the upstream detector already carries stable IDs.
                 We pass them through, count tracks, sign the report.

  IDS_INFERRED   the upstream detector emits per-frame detections
                 with no identity. We greedy-match across frames via
                 IoU (threshold 0.3 by default) + label equality.

The report carries one `Track` per object identity, with the full
list of (frame_index, bbox) tuples that identity appeared in.
Downstream detectors (motion, impact, temporal-chain) consume tracks
directly — they don't re-scan the scene graph.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Optional

from axiom_signing import derive_key

from .scene import Object, SceneGraph, iou

OBJECTS_KEY_NS = b"axiom-video-objects-v1"


@dataclass(frozen=True)
class TrackPoint:
    """One (frame_index, bbox) observation for a track."""
    frame_index: int
    bbox:        tuple[float, float, float, float]
    confidence:  float


@dataclass(frozen=True)
class Track:
    """One object identity across the full clip.

    `label` is the upstream class label (or "unknown" if the inferred
    matcher hit a gap). `points` is sorted ascending by frame_index.
    """
    id:     str
    label:  str
    points: tuple[TrackPoint, ...]

    @property
    def first_frame(self) -> int:
        return self.points[0].frame_index if self.points else -1

    @property
    def last_frame(self) -> int:
        return self.points[-1].frame_index if self.points else -1

    @property
    def n_frames(self) -> int:
        return len(self.points)


@dataclass(frozen=True)
class ObjectTrackReport:
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
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "ObjectTrackReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned))
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(_canonical(self)))

    @property
    def tracks(self) -> list[Track]:
        out = []
        for t in self.payload.get("tracks", []):
            out.append(Track(
                id=t["id"],
                label=t["label"],
                points=tuple(
                    TrackPoint(
                        frame_index=p["frame_index"],
                        bbox=tuple(p["bbox"]),
                        confidence=p["confidence"],
                    )
                    for p in t["points"]
                ),
            ))
        return out


class ObjectTracker:
    """Detects + signs the set of object identities in a scene graph.

    `iou_threshold` controls cross-frame matching when the upstream
    detector doesn't carry IDs. Default 0.3 is forgiving enough for
    moderate motion, strict enough to reject random false matches.

    `min_track_length` filters out flicker tracks (object appears in
    1-2 frames then vanishes — usually a false detection).
    """

    def __init__(
        self,
        *,
        iou_threshold: float = 0.3,
        min_track_length: int = 2,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.min_track_length = min_track_length

    def track(self, sg: SceneGraph) -> ObjectTrackReport:
        # Decide whether upstream provided IDs by checking the first
        # frame's first object. Track-by-IoU only when IDs are blank
        # / numeric placeholders — if every object has a non-empty
        # string `id`, trust it.
        ids_provided = self._upstream_provided_ids(sg)
        if ids_provided:
            tracks = self._pass_through_ids(sg)
        else:
            tracks = self._infer_ids_by_iou(sg)

        # Filter flicker
        tracks = [t for t in tracks if t.n_frames >= self.min_track_length]

        # Average upstream confidence across all observations
        all_points = [p for t in tracks for p in t.points]
        avg_conf = (
            sum(p.confidence for p in all_points) / len(all_points)
            if all_points else 0.0
        )

        payload = {
            "tracks": [
                {
                    "id": t.id, "label": t.label,
                    "points": [
                        {"frame_index": p.frame_index,
                         "bbox": list(p.bbox),
                         "confidence": round(p.confidence, 4)}
                        for p in t.points
                    ],
                }
                for t in tracks
            ],
            "n_tracks": len(tracks),
            "n_frames": len(sg),
            "fps":      sg.fps,
            "ids_source": "upstream" if ids_provided else "iou_matched",
        }
        return ObjectTrackReport.signed(
            payload=payload, confidence=round(avg_conf, 4),
        )

    # ─── Internals ──────────────────────────────────────────────────

    def _upstream_provided_ids(self, sg: SceneGraph) -> bool:
        """True iff at least one object has a non-empty, non-numeric ID
        in the first non-empty frame."""
        for scene in sg.scenes:
            if not scene.objects:
                continue
            obj = scene.objects[0]
            if obj.id and not obj.id.isdigit():
                return True
            return False
        return False

    def _pass_through_ids(self, sg: SceneGraph) -> list[Track]:
        grouped: dict[str, list[TrackPoint]] = {}
        labels:  dict[str, str] = {}
        for scene in sg.scenes:
            for obj in scene.objects:
                grouped.setdefault(obj.id, []).append(TrackPoint(
                    frame_index=scene.frame_index,
                    bbox=obj.bbox,
                    confidence=obj.confidence,
                ))
                labels.setdefault(obj.id, obj.label)
        tracks = []
        for tid, pts in grouped.items():
            pts.sort(key=lambda p: p.frame_index)
            tracks.append(Track(id=tid, label=labels[tid], points=tuple(pts)))
        return tracks

    def _infer_ids_by_iou(self, sg: SceneGraph) -> list[Track]:
        """Greedy frame-by-frame IoU matching with label equality."""
        next_id = 0
        active: list[tuple[str, str, tuple[float,float,float,float], list[TrackPoint]]] = []
        # active = [(id, label, last_bbox, points), ...]
        for scene in sg.scenes:
            used: set[int] = set()
            new_active = []
            # Try to extend each existing active track
            for tid, label, last_bbox, points in active:
                best_j = None
                best_iou = self.iou_threshold
                for j, obj in enumerate(scene.objects):
                    if j in used or obj.label != label:
                        continue
                    s = iou(last_bbox, obj.bbox)
                    if s > best_iou:
                        best_iou = s
                        best_j = j
                if best_j is not None:
                    used.add(best_j)
                    obj = scene.objects[best_j]
                    points.append(TrackPoint(
                        frame_index=scene.frame_index,
                        bbox=obj.bbox,
                        confidence=obj.confidence,
                    ))
                    new_active.append((tid, label, obj.bbox, points))
                else:
                    # Track ended; preserve it as a completed entry
                    new_active.append((tid, label, last_bbox, points))
            # Start new tracks for unmatched objects
            for j, obj in enumerate(scene.objects):
                if j in used:
                    continue
                tid = f"t{next_id}"
                next_id += 1
                new_active.append((tid, obj.label, obj.bbox, [TrackPoint(
                    frame_index=scene.frame_index, bbox=obj.bbox,
                    confidence=obj.confidence,
                )]))
            active = new_active
        return [
            Track(id=tid, label=label, points=tuple(points))
            for tid, label, _, points in active
        ]


# ─── Signing helpers ────────────────────────────────────────────────────


def _canonical(r: ObjectTrackReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(derive_key(OBJECTS_KEY_NS), payload,
                    hashlib.sha256).hexdigest()
