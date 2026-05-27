"""Scene graph data types — the input every detector consumes.

A `SceneGraph` is a sequence of `Scene`s, one per video frame. Each
`Scene` carries a list of detected `Object`s with their bounding
boxes. This is what an upstream object detector (YOLO / Detectron /
OpenCV) produces; AXIOM detectors operate on this output, not raw
pixels.

Why this shape:

  - Detector-agnostic — customers bring their own tracker.
  - Synthetic-test-friendly — procedurally generate scenes without
    needing recorded video or codec deps.
  - Composable — same structure for a single frame, a clip, or a
    streaming window.

All coordinates are normalized to [0, 1] so detector logic doesn't
depend on resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence


@dataclass(frozen=True)
class Object:
    """One detected object in one frame.

    Coordinates are normalized to [0, 1]: (0, 0) is top-left,
    (1, 1) is bottom-right. `id` is the cross-frame identity
    assigned by the upstream tracker; if you don't have a tracker,
    AXIOM's ObjectTracker assigns IDs using IoU-based matching.

    `label` is the upstream class label (e.g. "cup", "child",
    "hand"). AXIOM doesn't classify — it consumes the label.

    `confidence` is the upstream detector's own confidence. AXIOM
    propagates it without re-scoring.
    """
    id:         str
    label:      str
    bbox:       tuple[float, float, float, float]   # (x1, y1, x2, y2) normalized
    confidence: float = 1.0
    extras:     dict = field(default_factory=dict)

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


@dataclass(frozen=True)
class Scene:
    """One frame in a video — frame index + list of objects.

    `frame_index` is the integer position in the source clip; clips
    are 0-indexed. `timestamp_s` (optional) is the seconds offset
    from clip start — used by detectors that care about wall-clock
    velocity, not just frame-to-frame deltas.
    """
    frame_index: int
    objects:     tuple[Object, ...]
    timestamp_s: float = 0.0


@dataclass(frozen=True)
class SceneGraph:
    """A sequence of Scenes representing a single video clip.

    Construct directly from a list of Scenes or use the helper
    classmethods on subclasses (see scripts/video_harness.py for
    synthetic constructors).
    """
    scenes: tuple[Scene, ...]
    fps:    float = 30.0

    def __iter__(self) -> Iterator[Scene]:
        return iter(self.scenes)

    def __len__(self) -> int:
        return len(self.scenes)

    @property
    def duration_s(self) -> float:
        if not self.scenes:
            return 0.0
        return len(self.scenes) / self.fps

    @classmethod
    def from_list(cls, scenes: Sequence[Scene], fps: float = 30.0) -> "SceneGraph":
        return cls(scenes=tuple(scenes), fps=fps)


# ─── IoU helper (used by detectors) ─────────────────────────────────────


def iou(a: tuple[float, float, float, float],
        b: tuple[float, float, float, float]) -> float:
    """Intersection-over-Union of two bounding boxes.

    Returns 0.0 if the boxes don't overlap. Used by ObjectTracker
    for cross-frame identity matching when the upstream detector
    doesn't carry stable IDs.
    """
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    a_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    b_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = a_area + b_area - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union
