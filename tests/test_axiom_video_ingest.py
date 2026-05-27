"""Tests for axiom_video.ingest — the frame-ingestion adapter.

Three frame shapes exercised:
  1. Nested list of (R,G,B) tuples — pure-Python, no deps.
  2. PIL.Image.Image                — Pillow path.
  3. Mock numpy-like object         — duck-typed; proves the
                                       hasattr('shape') branch
                                       without importing numpy.

Plus:
  - sample_dominant_color median behavior + inset + clamping
  - FrameIngester end-to-end: real frames → SceneGraph with
    auto-sampled `extras['color']`
  - ScriptedObjectDetector replay contract
  - End-to-end pipeline: ingest → 6 AXIOM detectors → signed
    EventToken via VideoAgent
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith(("axiom_video", "axiom_event_token",
                            "axiom_signing")):
            sys.modules.pop(mod, None)
    yield


# ─── Helpers ────────────────────────────────────────────────────────────


def _solid_frame_nested(w: int, h: int,
                        rgb: tuple[int, int, int]
                       ) -> list[list[tuple[int, int, int]]]:
    """Build a w×h frame filled with a single RGB color, as nested lists."""
    return [[rgb for _ in range(w)] for _ in range(h)]


class MockNumpyArray:
    """Minimal numpy-like that satisfies _frame_shape's duck typing.

    Has `.shape` and supports `arr[y, x]` indexing returning a
    3-tuple. Enough to exercise the numpy branch without importing
    numpy.
    """
    def __init__(self, h: int, w: int, rgb):
        self.shape = (h, w, 3)
        self._rgb = rgb

    def __getitem__(self, key):
        # Only support arr[y, x] returning the RGB triple
        return self._rgb


# ─── _frame_shape — three input flavors ─────────────────────────────────


def test_frame_shape_nested_list(isolated):
    from axiom_video.ingest import _frame_shape
    f = _solid_frame_nested(4, 3, (100, 150, 200))
    h, w, get = _frame_shape(f)
    assert (h, w) == (3, 4)
    assert get(2, 1) == (100, 150, 200)


def test_frame_shape_pil_image(isolated):
    from PIL import Image
    from axiom_video.ingest import _frame_shape
    img = Image.new("RGB", (5, 4), (10, 20, 30))
    h, w, get = _frame_shape(img)
    assert (h, w) == (4, 5)
    assert get(3, 2) == (10, 20, 30)


def test_frame_shape_numpy_like(isolated):
    from axiom_video.ingest import _frame_shape
    arr = MockNumpyArray(h=6, w=8, rgb=(255, 0, 0))
    h, w, get = _frame_shape(arr)
    assert (h, w) == (6, 8)
    assert get(0, 0) == (255, 0, 0)


# ─── sample_dominant_color ──────────────────────────────────────────────


def test_sample_color_returns_median(isolated):
    """A uniform solid-color frame's median equals the fill color."""
    from axiom_video.ingest import sample_dominant_color
    frame = _solid_frame_nested(20, 20, (200, 50, 50))
    color = sample_dominant_color(frame, (0.1, 0.1, 0.9, 0.9))
    assert color == (200, 50, 50)


def test_sample_color_inset_avoids_border(isolated):
    """A frame with a red center on a blue border — with inset, the
    sampled color is red, not blue."""
    from axiom_video.ingest import sample_dominant_color
    # Build a 20×20 frame: outer ring blue, inner 10×10 red
    frame = [[(0, 0, 255) for _ in range(20)] for _ in range(20)]
    for y in range(5, 15):
        for x in range(5, 15):
            frame[y][x] = (255, 0, 0)
    # Inset 20% on a bbox covering the full frame samples the center
    color = sample_dominant_color(frame, (0.0, 0.0, 1.0, 1.0), inset=0.3)
    assert color == (255, 0, 0)


def test_sample_color_returns_none_on_empty_bbox(isolated):
    from axiom_video.ingest import sample_dominant_color
    frame = _solid_frame_nested(10, 10, (1, 2, 3))
    # Bbox with zero area
    assert sample_dominant_color(frame, (0.5, 0.5, 0.5, 0.5)) is None


def test_sample_color_clamps_out_of_frame_bbox(isolated):
    """Bbox extending past the frame edge still returns a color
    sampled from the in-frame portion."""
    from axiom_video.ingest import sample_dominant_color
    frame = _solid_frame_nested(10, 10, (50, 100, 150))
    color = sample_dominant_color(frame, (0.5, 0.5, 1.5, 1.5))
    assert color == (50, 100, 150)


def test_sample_color_pil_image_path(isolated):
    """PIL Image path produces the same result as nested-list path."""
    from PIL import Image
    from axiom_video.ingest import sample_dominant_color
    img = Image.new("RGB", (20, 20), (200, 50, 50))
    color = sample_dominant_color(img, (0.1, 0.1, 0.9, 0.9))
    assert color == (200, 50, 50)


# ─── ScriptedObjectDetector ─────────────────────────────────────────────


def test_scripted_detector_replays_each_frame(isolated):
    from axiom_video import DetectedObject, ScriptedObjectDetector
    scripted = [
        [DetectedObject(label="ball", bbox=(0.4, 0.4, 0.6, 0.6))],
        [DetectedObject(label="ball", bbox=(0.5, 0.5, 0.7, 0.7)),
         DetectedObject(label="cup",  bbox=(0.1, 0.1, 0.3, 0.3))],
    ]
    det = ScriptedObjectDetector(scripted)
    out0 = det.detect(None)
    out1 = det.detect(None)
    assert len(out0) == 1 and out0[0].label == "ball"
    assert len(out1) == 2


def test_scripted_detector_returns_empty_past_end(isolated):
    from axiom_video import ScriptedObjectDetector
    det = ScriptedObjectDetector([[]])
    det.detect(None)
    assert det.detect(None) == []
    assert det.detect(None) == []


def test_scripted_detector_reset(isolated):
    from axiom_video import DetectedObject, ScriptedObjectDetector
    scripted = [[DetectedObject(label="x", bbox=(0,0,1,1))]]
    det = ScriptedObjectDetector(scripted)
    det.detect(None)
    assert det.detect(None) == []
    det.reset()
    out = det.detect(None)
    assert len(out) == 1


# ─── FrameIngester end-to-end ───────────────────────────────────────────


def test_ingest_populates_color_from_pixels(isolated):
    """Frames are solid red; detector reports the full frame as one
    object; ingester samples and writes (255,0,0) into extras."""
    from axiom_video import DetectedObject, FrameIngester, ScriptedObjectDetector
    frames = [_solid_frame_nested(20, 20, (255, 0, 0)) for _ in range(3)]
    det = ScriptedObjectDetector([
        [DetectedObject(label="cup", bbox=(0.0, 0.0, 1.0, 1.0))]
        for _ in range(3)
    ])
    sg = FrameIngester(det).ingest(frames)
    assert len(sg) == 3
    for scene in sg.scenes:
        assert len(scene.objects) == 1
        obj = scene.objects[0]
        assert obj.label == "cup"
        assert obj.extras["color"] == (255, 0, 0)


def test_ingest_assigns_synthetic_ids_when_detector_omits_them(isolated):
    """DetectedObject without `id` gets a deterministic synthetic id."""
    from axiom_video import DetectedObject, FrameIngester, ScriptedObjectDetector
    frames = [_solid_frame_nested(10, 10, (0, 255, 0)) for _ in range(2)]
    det = ScriptedObjectDetector([
        [DetectedObject(label="a", bbox=(0, 0, 1, 1)),
         DetectedObject(label="b", bbox=(0, 0, 1, 1))]
        for _ in range(2)
    ])
    sg = FrameIngester(det).ingest(frames)
    # Each frame has two objects with distinct synthetic IDs
    for scene in sg.scenes:
        ids = {o.id for o in scene.objects}
        assert len(ids) == 2


def test_ingest_preserves_customer_ids(isolated):
    """When DetectedObject carries a non-empty id, it survives."""
    from axiom_video import DetectedObject, FrameIngester, ScriptedObjectDetector
    frames = [_solid_frame_nested(10, 10, (0, 0, 255)) for _ in range(2)]
    det = ScriptedObjectDetector([
        [DetectedObject(label="ball", id="ball-A", bbox=(0, 0, 1, 1))]
        for _ in range(2)
    ])
    sg = FrameIngester(det).ingest(frames)
    assert all(scene.objects[0].id == "ball-A" for scene in sg.scenes)


def test_ingest_respects_sample_color_false(isolated):
    """`sample_color=False` skips pixel sampling — extras stays empty."""
    from axiom_video import DetectedObject, FrameIngester, ScriptedObjectDetector
    frames = [_solid_frame_nested(10, 10, (1, 2, 3))]
    det = ScriptedObjectDetector([
        [DetectedObject(label="x", bbox=(0, 0, 1, 1))]
    ])
    sg = FrameIngester(det, sample_color=False).ingest(frames)
    assert "color" not in sg.scenes[0].objects[0].extras


def test_ingest_preserves_detector_supplied_color(isolated):
    """If the detector already supplies extras['color'], don't overwrite."""
    from axiom_video import DetectedObject, FrameIngester, ScriptedObjectDetector
    frames = [_solid_frame_nested(10, 10, (255, 0, 0))]
    det = ScriptedObjectDetector([
        [DetectedObject(label="x", bbox=(0, 0, 1, 1),
                        extras={"color": (50, 50, 50)})]
    ])
    sg = FrameIngester(det).ingest(frames)
    # Detector's hand-supplied (50, 50, 50) survives the sampling step
    assert sg.scenes[0].objects[0].extras["color"] == (50, 50, 50)


# Section "Full pipeline: real frames → 6 detectors → signed EventToken"
# trimmed — requires the event_token Coordinator's `video` agent
# registration, ships with the bonded-pair event_token PR.
