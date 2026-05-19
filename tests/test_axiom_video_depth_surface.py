"""Tests for axiom_video DepthClassifier + SurfaceClassifier.

The two ORVL-024 Phase C agents. Hermetic — synthetic scene graphs,
no real frames.

Covers:
  - Depth from extras["depth"]: near/mid/far classification,
    approach/recede events, occlusion detection, frame ordering
  - Depth fallback: when no extras, bbox-area becomes the depth proxy
  - Surface from extras["orientation"] (scalar + 3-tuple forms):
    upright/tilted/inverted/flat, tip events, stability score
  - Surface fallback: when no extras, aspect-ratio change becomes
    the tilt estimator
  - Two new HMAC namespaces are distinct from the other six
  - Tamper detection on both reports
  - End-to-end through the Coordinator: VideoAgent now has 8
    sub-reports + new summary fields
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


def _box(cx, cy, w=0.1, h=0.1):
    return (cx - w/2, cy - h/2, cx + w/2, cy + h/2)


# ─── DepthClassifier — extras-supplied path ─────────────────────────────


def test_depth_classifies_near_mid_far(isolated):
    from axiom_video import (
        DepthClassifier, Object, ObjectTracker, Scene, SceneGraph,
    )
    scenes = []
    for i in range(5):
        scenes.append(Scene(frame_index=i, objects=(
            Object(id="near",  label="a", bbox=_box(0.2, 0.5),
                   extras={"depth": 0.9}),
            Object(id="mid",   label="b", bbox=_box(0.5, 0.5),
                   extras={"depth": 0.5}),
            Object(id="far",   label="c", bbox=_box(0.8, 0.5),
                   extras={"depth": 0.1}),
        )))
    sg = SceneGraph.from_list(scenes)
    tr = ObjectTracker().track(sg)
    r = DepthClassifier().classify(sg, tr)
    assert r.verify() is True
    assert r.payload["source"] == "extras"
    classes = {t["id"]: t["depth_class"] for t in r.payload["tracks"]}
    assert classes == {"near": "near", "mid": "mid", "far": "far"}


def test_depth_emits_approach_event(isolated):
    """Track depth climbs from 0.2 to 0.9 → multiple approach events."""
    from axiom_video import (
        DepthClassifier, Object, ObjectTracker, Scene, SceneGraph,
    )
    scenes = []
    depths = [0.2, 0.4, 0.6, 0.8, 0.9]
    for i, d in enumerate(depths):
        scenes.append(Scene(frame_index=i, objects=(
            Object(id="t0", label="ball", bbox=_box(0.5, 0.5),
                   extras={"depth": d}),
        )))
    r = DepthClassifier().classify(
        SceneGraph.from_list(scenes),
        ObjectTracker().track(SceneGraph.from_list(scenes)),
    )
    events = r.payload["events"]
    assert all(e["event_type"] == "approach" for e in events)
    assert len(events) >= 3   # 4 step deltas, all 0.2 > threshold 0.1


def test_depth_emits_recede_event(isolated):
    from axiom_video import (
        DepthClassifier, Object, ObjectTracker, Scene, SceneGraph,
    )
    scenes = []
    depths = [0.9, 0.7, 0.5, 0.3, 0.1]
    for i, d in enumerate(depths):
        scenes.append(Scene(frame_index=i, objects=(
            Object(id="t0", label="ball", bbox=_box(0.5, 0.5),
                   extras={"depth": d}),
        )))
    sg = SceneGraph.from_list(scenes)
    r = DepthClassifier().classify(sg, ObjectTracker().track(sg))
    types = [e["event_type"] for e in r.payload["events"]]
    assert all(t == "recede" for t in types)


def test_depth_detects_occlusion(isolated):
    """Two tracks overlap AND have a meaningful depth gap → occlusion."""
    from axiom_video import (
        DepthClassifier, Object, ObjectTracker, Scene, SceneGraph,
    )
    scenes = []
    # Background object stays still and far; foreground object
    # moves laterally across, briefly overlapping the background.
    for i in range(8):
        fg_cx = 0.1 + i * 0.1   # 0.1 → 0.8
        scenes.append(Scene(frame_index=i, objects=(
            Object(id="bg", label="wall", bbox=_box(0.5, 0.5, w=0.4, h=0.4),
                   extras={"depth": 0.1}),
            Object(id="fg", label="person", bbox=_box(fg_cx, 0.5, w=0.2, h=0.2),
                   extras={"depth": 0.9}),
        )))
    sg = SceneGraph.from_list(scenes)
    r = DepthClassifier().classify(sg, ObjectTracker().track(sg))
    occlusions = [e for e in r.payload["events"]
                  if e["event_type"] == "occlusion"]
    assert len(occlusions) >= 1
    # The far one (bg) is the one being occluded, by the near one (fg)
    assert all(o["track_id"] == "bg" and o["occluded_by"] == "fg"
               for o in occlusions)


def test_depth_frame_ordering_front_to_back(isolated):
    from axiom_video import (
        DepthClassifier, Object, ObjectTracker, Scene, SceneGraph,
    )
    scenes = [Scene(frame_index=0, objects=(
        Object(id="a", label="x", bbox=_box(0.2, 0.5),
               extras={"depth": 0.3}),
        Object(id="b", label="x", bbox=_box(0.5, 0.5),
               extras={"depth": 0.9}),
        Object(id="c", label="x", bbox=_box(0.8, 0.5),
               extras={"depth": 0.6}),
    ))] * 2   # two identical frames so the tracker keeps them
    # NB: same-tuple frames work because Scene is frozen + dataclass eq
    sg = SceneGraph.from_list(scenes)
    r = DepthClassifier().classify(sg, ObjectTracker().track(sg))
    order = r.payload["frame_ordering"][0]["order"]
    assert order == ["b", "c", "a"]   # 0.9, 0.6, 0.3


# ─── DepthClassifier — bbox-area fallback ───────────────────────────────


def test_depth_falls_back_to_bbox_area_when_no_extras(isolated):
    """No extras['depth'] → use bbox area as depth proxy."""
    from axiom_video import (
        DepthClassifier, Object, ObjectTracker, Scene, SceneGraph,
    )
    scenes = []
    for i in range(3):
        scenes.append(Scene(frame_index=i, objects=(
            Object(id="big",  label="x", bbox=_box(0.3, 0.5, w=0.3, h=0.3)),
            Object(id="med",  label="x", bbox=_box(0.6, 0.5, w=0.15, h=0.15)),
            Object(id="tiny", label="x", bbox=_box(0.85, 0.5, w=0.05, h=0.05)),
        )))
    sg = SceneGraph.from_list(scenes)
    r = DepthClassifier().classify(sg, ObjectTracker().track(sg))
    assert r.payload["source"] == "bbox_area"
    assert r.confidence == 0.5     # honest moderate confidence
    classes = {t["id"]: t["depth_class"] for t in r.payload["tracks"]}
    # Bigger bbox → higher depth → "near"; tiny → "far"
    assert classes["big"]  == "near"
    assert classes["tiny"] == "far"


# ─── SurfaceClassifier — extras (scalar) ────────────────────────────────


@pytest.mark.parametrize("angle,expected", [
    (0,     "upright"),
    (15,    "upright"),
    (-15,   "upright"),
    (45,    "tilted"),
    (-45,   "tilted"),
    (75,    "flat"),
    (90,    "flat"),
    (130,   "inverted"),
    (180,   "inverted"),
])
def test_surface_class_from_scalar_angle(isolated, angle, expected):
    from axiom_video import (
        Object, ObjectTracker, Scene, SceneGraph, SurfaceClassifier,
    )
    scenes = [
        Scene(frame_index=i, objects=(
            Object(id="cup", label="cup", bbox=_box(0.5, 0.5),
                   extras={"orientation": angle}),
        ))
        for i in range(3)
    ]
    sg = SceneGraph.from_list(scenes)
    r = SurfaceClassifier().classify(sg, ObjectTracker().track(sg))
    assert r.verify() is True
    assert r.payload["tracks"][0]["orientation_class"] == expected


def test_surface_accepts_pitch_roll_yaw_tuple(isolated):
    """3-tuple form (pitch, roll, yaw) — only roll (index 1) counts.

    Use roll=45° (unambiguously tilted; outside both upright<20°
    and flat ≈75-90° windows) to assert that the roll axis is the
    one consumed by the classifier.
    """
    from axiom_video import (
        Object, ObjectTracker, Scene, SceneGraph, SurfaceClassifier,
    )
    scenes = [
        Scene(frame_index=i, objects=(
            Object(id="cup", label="cup", bbox=_box(0.5, 0.5),
                   extras={"orientation": (10.0, 45.0, 5.0)}),
        ))
        for i in range(3)
    ]
    sg = SceneGraph.from_list(scenes)
    r = SurfaceClassifier().classify(sg, ObjectTracker().track(sg))
    # roll=45° → tilted
    assert r.payload["tracks"][0]["orientation_class"] == "tilted"


def test_surface_emits_tip_event(isolated):
    """Cup goes from upright → tilted → inverted, two tip events fire."""
    from axiom_video import (
        Object, ObjectTracker, Scene, SceneGraph, SurfaceClassifier,
    )
    angles = [0, 0, 0, 45, 45, 45, 150, 150, 150]
    scenes = []
    for i, a in enumerate(angles):
        scenes.append(Scene(frame_index=i, objects=(
            Object(id="cup", label="cup", bbox=_box(0.5, 0.5),
                   extras={"orientation": a}),
        )))
    sg = SceneGraph.from_list(scenes)
    r = SurfaceClassifier().classify(sg, ObjectTracker().track(sg))
    types = [e["event_type"] for e in r.payload["events"]]
    assert "tip_to_tilted" in types
    assert "tip_to_inverted" in types


def test_surface_stability_score(isolated):
    """Track with no transitions has stability 1.0; flipping every
    frame has stability close to 0."""
    from axiom_video import (
        Object, ObjectTracker, Scene, SceneGraph, SurfaceClassifier,
    )
    stable_angles = [0] * 10
    flipping_angles = [0, 60, 0, 60, 0, 60, 0, 60, 0, 60]
    for angles, expected_high in [(stable_angles, True),
                                    (flipping_angles, False)]:
        scenes = [
            Scene(frame_index=i, objects=(
                Object(id="o", label="x", bbox=_box(0.5, 0.5),
                       extras={"orientation": a}),
            ))
            for i, a in enumerate(angles)
        ]
        sg = SceneGraph.from_list(scenes)
        r = SurfaceClassifier().classify(sg, ObjectTracker().track(sg))
        stab = r.payload["tracks"][0]["stability_score"]
        if expected_high:
            assert stab == 1.0
        else:
            assert stab < 0.3


def test_surface_scene_unstable_flag(isolated):
    """Two tilted tracks → scene_unstable=True."""
    from axiom_video import (
        Object, ObjectTracker, Scene, SceneGraph, SurfaceClassifier,
    )
    scenes = [
        Scene(frame_index=i, objects=(
            Object(id="a", label="x", bbox=_box(0.3, 0.5),
                   extras={"orientation": 45}),
            Object(id="b", label="x", bbox=_box(0.7, 0.5),
                   extras={"orientation": 50}),
        ))
        for i in range(3)
    ]
    sg = SceneGraph.from_list(scenes)
    r = SurfaceClassifier().classify(sg, ObjectTracker().track(sg))
    assert r.payload["scene_unstable"] is True


# ─── SurfaceClassifier — aspect-ratio fallback ──────────────────────────


def test_surface_aspect_ratio_fallback(isolated):
    """No extras['orientation'] → bbox aspect-ratio change estimates tilt."""
    from axiom_video import (
        Object, ObjectTracker, Scene, SceneGraph, SurfaceClassifier,
    )
    # Cup starts tall (upright), gradually becomes wide (tipped over)
    # height/width = aspect; baseline aspect = 2.0 (h=0.2, w=0.1)
    aspects = [(0.1, 0.2), (0.1, 0.2), (0.1, 0.2),
                (0.15, 0.15), (0.2, 0.1)]   # tall → square → wide
    scenes = []
    for i, (w, h) in enumerate(aspects):
        scenes.append(Scene(frame_index=i, objects=(
            Object(id="cup", label="cup",
                   bbox=(0.5 - w/2, 0.5 - h/2, 0.5 + w/2, 0.5 + h/2)),
        )))
    sg = SceneGraph.from_list(scenes)
    r = SurfaceClassifier().classify(sg, ObjectTracker().track(sg))
    assert r.payload["source"] == "aspect_ratio"
    assert r.confidence == 0.5
    # Should detect at least one tip event toward tilted/flat
    types = [e["event_type"] for e in r.payload["events"]]
    assert any(t in ("tip_to_tilted", "tip_to_flat") for t in types)


# ─── Namespace isolation + tamper detection ─────────────────────────────


def test_depth_and_surface_have_distinct_namespaces(isolated):
    from axiom_video.color_watcher import COLOR_KEY_NS
    from axiom_video.depth import DEPTH_KEY_NS
    from axiom_video.impact import IMPACT_KEY_NS
    from axiom_video.motion import MOTION_KEY_NS
    from axiom_video.object_tracker import OBJECTS_KEY_NS
    from axiom_video.surface import SURFACE_KEY_NS
    from axiom_video.temporal_chain import TEMPORAL_KEY_NS
    from axiom_video.time_keeper import TIMEKEEPER_KEY_NS
    all_ns = {
        OBJECTS_KEY_NS, MOTION_KEY_NS, IMPACT_KEY_NS, TEMPORAL_KEY_NS,
        TIMEKEEPER_KEY_NS, COLOR_KEY_NS, DEPTH_KEY_NS, SURFACE_KEY_NS,
    }
    assert len(all_ns) == 8       # all 8 distinct
    assert DEPTH_KEY_NS == b"axiom-video-depth-v1"
    assert SURFACE_KEY_NS == b"axiom-video-surface-v1"


def test_depth_report_tamper_fails_verify(isolated):
    from axiom_video import (
        DepthClassifier, Object, ObjectTracker, Scene, SceneGraph,
    )
    from axiom_video.depth import DepthReport
    scenes = [Scene(frame_index=i, objects=(
        Object(id="a", label="x", bbox=_box(0.5, 0.5),
               extras={"depth": 0.8}),
    )) for i in range(3)]
    sg = SceneGraph.from_list(scenes)
    r = DepthClassifier().classify(sg, ObjectTracker().track(sg))
    bad = DepthReport(
        payload={**r.payload, "source": "TAMPERED"},
        confidence=r.confidence, signature=r.signature,
    )
    assert bad.verify() is False


def test_surface_report_tamper_fails_verify(isolated):
    from axiom_video import (
        Object, ObjectTracker, Scene, SceneGraph, SurfaceClassifier,
    )
    from axiom_video.surface import SurfaceReport
    scenes = [Scene(frame_index=i, objects=(
        Object(id="a", label="x", bbox=_box(0.5, 0.5),
               extras={"orientation": 30}),
    )) for i in range(3)]
    sg = SceneGraph.from_list(scenes)
    r = SurfaceClassifier().classify(sg, ObjectTracker().track(sg))
    bad = SurfaceReport(
        payload={**r.payload, "scene_unstable": True},
        confidence=r.confidence, signature=r.signature,
    )
    assert bad.verify() is False


# ─── VideoAgent end-to-end: 8 sub-reports + new summary fields ──────────


def test_video_agent_now_includes_depth_and_surface(isolated):
    """VideoAgent real-mode payload grew from 6 sub-reports to 8.

    Cup approaches camera (depth steps of 0.12 — above the 0.10
    change threshold so each step fires an approach event), then
    tips over halfway through. Verifies that both new agents land
    in the payload + summary surfaces their headline fields.
    """
    from axiom_event_token import Coordinator
    from axiom_video import Object, Scene, SceneGraph
    scenes = []
    for i in range(8):
        cy = 0.2 + i * 0.05
        orientation = 0 if i < 4 else 45    # 45° = unambiguously tilted
        # depth jumps 0.12/frame so each step > change_threshold 0.10
        cup_depth = min(1.0, 0.10 + i * 0.12)
        scenes.append(Scene(frame_index=i, objects=(
            Object(id="cup", label="cup", bbox=_box(0.5, cy),
                   extras={"color": (220, 30, 30),
                           "depth": cup_depth,
                           "orientation": orientation}),
            Object(id="floor", label="floor",
                   bbox=(0.0, 0.85, 1.0, 1.0),
                   extras={"color": (30, 30, 220),
                           "depth": 0.05, "orientation": 0}),
        )))
    sg = SceneGraph.from_list(scenes)
    token = Coordinator().compose(
        video={"scene_graph": sg},
        activate=("video", "governance"),
    )
    assert token.verify() is True
    p = token.video.payload
    assert p["mode"] == "real"
    for key in (
        "object_track_report", "motion_report", "impact_report",
        "temporal_chain_report", "time_keeper_report", "color_report",
        "depth_report", "surface_report",
    ):
        assert key in p, f"{key} missing from VideoAgent payload"
    # New summary fields
    s = p["summary"]
    assert s["depth_source"] == "extras"
    assert s["n_depth_events"] >= 1     # cup is approaching
    assert s["n_tip_events"] >= 1       # cup tips over


def test_video_agent_legacy_stub_mode_still_works(isolated):
    """Sanity check we didn't break back-compat with the hand-coded
    video dict path."""
    from axiom_event_token import Coordinator
    coord = Coordinator()
    token = coord.compose(
        video={"object_motion": "downward",
               "impact_point": "floor",
               "fracture_pattern": "radial_scatter",
               "confidence": 0.85},
        activate=("video", "governance"),
    )
    assert token.verify() is True
    assert token.video.payload["mode"] == "stub"
