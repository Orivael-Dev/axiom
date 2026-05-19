"""Frame ingestion — bridges real images / camera frames to SceneGraph.

The customer brings:

  1. A sequence of frames — anything indexable as a 2-D grid of RGB
     tuples. We duck-type three common cases:
       - PIL.Image.Image                       (Pillow)
       - numpy.ndarray of shape (H, W, 3)      (NumPy)
       - nested list/tuple of (R, G, B) tuples (pure Python)
     No hard dep on any of those — the ingester sniffs the shape.

  2. An object detector — anything implementing `detect(frame) ->
     list[DetectedObject]`. The customer adapts their YOLO /
     Detectron / OpenCV / proprietary detector to this Protocol.

The ingester:

  1. Calls the detector on each frame to get bounding boxes.
  2. Samples pixels inside each bbox (median RGB, with 20% inset
     to avoid edge bleed) to populate `Object.extras["color"]`.
  3. Wraps everything in `Scene` → `SceneGraph` for the rest of
     `axiom_video` to consume.

This is the Phase B piece that unlocks **live demos**: real
frames flow through real upstream detection, then through the
6 signed AXIOM detectors, ending in an EventToken any phone /
laptop / regulator can verify.

## Stub detector for tests + offline demos

`ScriptedObjectDetector` replays pre-canned detection lists keyed
by frame index. Lets tests + demos work without a real vision
model — same approach as the audio harness's synthetic clip
generator.

## Real adapters (sketches, not bundled)

YOLOv8 adapter:

    class YoloAdapter:
        def __init__(self, model): self.model = model
        def detect(self, frame):
            r = self.model(frame)[0]
            out = []
            h, w = r.orig_shape
            for box in r.boxes:
                x1, y1, x2, y2 = (b/w if i%2==0 else b/h
                                  for i, b in enumerate(box.xyxy[0].tolist()))
                out.append(DetectedObject(
                    label=r.names[int(box.cls)],
                    bbox=(x1, y1, x2, y2),
                    confidence=float(box.conf),
                ))
            return out

OpenCV multi-tracker adapter follows the same pattern — map
`(success, boxes)` to `DetectedObject`s with normalized coords.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence

from .scene import Object, Scene, SceneGraph


# ─── Customer-facing types ──────────────────────────────────────────────


@dataclass(frozen=True)
class DetectedObject:
    """One detection from the upstream model.

    `bbox` is normalized [0, 1] — divide pixel coords by frame
    width/height before constructing. AXIOM detectors all operate
    on normalized coords so they're resolution-independent.

    `id` is optional. Leave blank ("") if the upstream model
    doesn't track identity; `ObjectTracker` will infer IDs by IoU
    matching. Set a stable string (e.g. "cup-A") if you have
    cross-frame identity.
    """
    label:      str
    bbox:       tuple[float, float, float, float]
    confidence: float = 1.0
    id:         str = ""
    extras:     dict = field(default_factory=dict)


class ObjectDetectorProtocol(Protocol):
    """Any detector with a `detect(frame) -> list[DetectedObject]`."""

    def detect(self, frame: Any) -> list[DetectedObject]:
        ...


# ─── Frame-shape adapter (pure Python, no hard deps) ────────────────────


def _frame_shape(frame: Any) -> tuple[int, int, Callable[[int, int],
                                                          tuple[int, int, int]]]:
    """Return (height, width, get_pixel(x, y) -> (r, g, b)).

    Duck-types three common frame representations. Adds new ones
    by extending this function — every other piece of the ingester
    only sees the normalized (h, w, getter) tuple.
    """
    # PIL.Image.Image
    if hasattr(frame, "getpixel") and hasattr(frame, "size"):
        w, h = frame.size

        def get(x: int, y: int) -> tuple[int, int, int]:
            p = frame.getpixel((x, y))
            if isinstance(p, int):
                return (p, p, p)
            return (int(p[0]), int(p[1]), int(p[2]))

        return h, w, get

    # numpy.ndarray (or anything with .shape + 2-D indexing)
    if hasattr(frame, "shape"):
        h, w = frame.shape[:2]

        def get(x: int, y: int) -> tuple[int, int, int]:
            p = frame[y, x]
            return (int(p[0]), int(p[1]), int(p[2]))

        return h, w, get

    # Nested list/tuple of pixel triples
    h = len(frame)
    w = len(frame[0]) if h > 0 else 0

    def get(x: int, y: int) -> tuple[int, int, int]:
        p = frame[y][x]
        return (int(p[0]), int(p[1]), int(p[2]))

    return h, w, get


# ─── Color sampling ─────────────────────────────────────────────────────


def sample_dominant_color(
    frame: Any,
    bbox_normalized: tuple[float, float, float, float],
    *,
    inset: float = 0.2,
    sample_grid: int = 8,
) -> Optional[tuple[int, int, int]]:
    """Median RGB inside a bbox, with `inset` border shrink.

    `bbox_normalized` is (x1, y1, x2, y2) in [0, 1] — same shape as
    `Object.bbox` everywhere else in axiom_video.

    `inset` is the fraction of bbox width/height to shrink in from
    each side to avoid background bleed at the edges (default 0.2
    = 20%, so a 50%-of-bbox center patch is sampled).

    `sample_grid` controls density: we sample at most `grid × grid`
    points across the inset region. Default 8 = 64 samples — fast
    + deterministic + insensitive to small motion within frame.

    Returns `None` if the bbox has zero area inside the frame.
    """
    h, w, get = _frame_shape(frame)
    if h == 0 or w == 0:
        return None

    x1, y1, x2, y2 = bbox_normalized
    # Apply inset
    bw = x2 - x1
    bh = y2 - y1
    if bw <= 0 or bh <= 0:
        return None
    x1 += bw * inset
    x2 -= bw * inset
    y1 += bh * inset
    y2 -= bh * inset

    # Pixel-space coords, clamped to frame
    px1 = max(0, min(w - 1, int(round(x1 * w))))
    py1 = max(0, min(h - 1, int(round(y1 * h))))
    px2 = max(0, min(w - 1, int(round(x2 * w))))
    py2 = max(0, min(h - 1, int(round(y2 * h))))
    if px2 <= px1 or py2 <= py1:
        return None

    # Grid sampling — `sample_grid` rows × `sample_grid` cols
    rs, gs, bs = [], [], []
    n = sample_grid
    for i in range(n):
        for j in range(n):
            x = px1 + int((px2 - px1) * (i + 0.5) / n)
            y = py1 + int((py2 - py1) * (j + 0.5) / n)
            r, g, b = get(x, y)
            rs.append(r); gs.append(g); bs.append(b)

    if not rs:
        return None
    return (
        int(statistics.median(rs)),
        int(statistics.median(gs)),
        int(statistics.median(bs)),
    )


# ─── FrameIngester ──────────────────────────────────────────────────────


class FrameIngester:
    """Drives an upstream detector over a frame sequence, builds a SceneGraph.

    Set `sample_color=False` to skip color sampling — useful when
    the customer's detector already populates `extras["color"]`,
    or when color isn't needed for the downstream activation.
    """

    def __init__(
        self,
        detector: ObjectDetectorProtocol,
        *,
        sample_color: bool = True,
        color_inset:  float = 0.2,
        color_grid:   int   = 8,
    ) -> None:
        self.detector = detector
        self.sample_color = sample_color
        self.color_inset = color_inset
        self.color_grid = color_grid

    def ingest(
        self,
        frames: Iterable[Any],
        *,
        fps: float = 30.0,
        start_frame: int = 0,
    ) -> SceneGraph:
        scenes: list[Scene] = []
        for i, frame in enumerate(frames):
            detected = self.detector.detect(frame)
            objects = []
            for d in detected:
                extras = dict(d.extras or {})
                if self.sample_color and "color" not in extras:
                    color = sample_dominant_color(
                        frame, d.bbox,
                        inset=self.color_inset,
                        sample_grid=self.color_grid,
                    )
                    if color is not None:
                        extras["color"] = color
                objects.append(Object(
                    id=d.id or f"d{i}_{len(objects)}",
                    label=d.label,
                    bbox=d.bbox,
                    confidence=d.confidence,
                    extras=extras,
                ))
            scenes.append(Scene(
                frame_index=start_frame + i,
                objects=tuple(objects),
                timestamp_s=round((start_frame + i) / fps, 4),
            ))
        return SceneGraph.from_list(scenes, fps=fps)


# ─── ScriptedObjectDetector — for tests + offline demos ─────────────────


class ScriptedObjectDetector:
    """Detector that replays canned detections.

    Construct with a list-of-lists: `scripted[i]` is the list of
    `DetectedObject` for frame `i`. Returns empty list when
    `frame_idx` is out of range. Useful for tests that want
    deterministic detector behavior without spinning up YOLO, and
    for offline demos that pre-compute detections.

    The detector tracks its own call count so you can pass it to
    `FrameIngester` and it indexes through `scripted` automatically.
    """

    def __init__(self, scripted: Sequence[Sequence[DetectedObject]]) -> None:
        self.scripted = [list(s) for s in scripted]
        self._call_count = 0

    def detect(self, frame: Any) -> list[DetectedObject]:
        idx = self._call_count
        self._call_count += 1
        if idx < len(self.scripted):
            return list(self.scripted[idx])
        return []

    def reset(self) -> None:
        self._call_count = 0
