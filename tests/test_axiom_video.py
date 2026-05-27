"""Tests for axiom_video — Phase A detector ladder.

Hermetic: every test uses procedurally-generated SceneGraphs. No
recorded video, no codec deps, no network. Same approach as the
audio Phase A test suite.

Coverage:
  - Scene dataclasses + IoU helper
  - ObjectTracker (upstream IDs + IoU-inferred + flicker filter)
  - MotionClassifier (every class hit + thresholds work)
  - ImpactDetector (decel events + contact events + dedupe)
  - TemporalChainExtractor (event ordering + composition)
  - Signed reports verify + tamper detection across all 4
  - Full pipeline end-to-end on the 14 harness scenes
  - Upgraded VideoAgent in event-token Coordinator
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


def _box(cx, cy, size=0.08):
    h = size / 2
    return (cx - h, cy - h, cx + h, cy + h)


# ─── Scene / IoU helpers ────────────────────────────────────────────────


def test_iou_overlap_full(isolated):
    from axiom_video.scene import iou
    b = (0.0, 0.0, 1.0, 1.0)
    assert iou(b, b) == pytest.approx(1.0)


def test_iou_no_overlap(isolated):
    from axiom_video.scene import iou
    a = (0.0, 0.0, 0.1, 0.1)
    b = (0.5, 0.5, 0.6, 0.6)
    assert iou(a, b) == 0.0


def test_iou_partial(isolated):
    from axiom_video.scene import iou
    a = (0.0, 0.0, 0.5, 0.5)   # area 0.25
    b = (0.25, 0.25, 0.75, 0.75)   # area 0.25, overlap area 0.0625
    # IoU = 0.0625 / (0.25 + 0.25 - 0.0625) = 0.0625 / 0.4375
    assert iou(a, b) == pytest.approx(0.0625 / 0.4375, rel=1e-3)


def test_object_center_and_area(isolated):
    from axiom_video.scene import Object
    o = Object(id="a", label="cup", bbox=(0.2, 0.3, 0.6, 0.7))
    assert o.center == pytest.approx((0.4, 0.5))
    assert o.area == pytest.approx(0.16)


# ─── ObjectTracker ──────────────────────────────────────────────────────


def test_tracker_passes_through_upstream_ids(isolated):
    from axiom_video import Object, ObjectTracker, Scene, SceneGraph
    sg = SceneGraph.from_list([
        Scene(frame_index=i, objects=(
            Object(id="cup-A", label="cup", bbox=_box(0.5, 0.5)),
        )) for i in range(5)
    ])
    r = ObjectTracker().track(sg)
    assert r.verify() is True
    assert r.payload["n_tracks"] == 1
    assert r.payload["ids_source"] == "upstream"
    assert r.tracks[0].id == "cup-A"
    assert r.tracks[0].n_frames == 5


def test_tracker_infers_ids_when_upstream_is_numeric(isolated):
    """Numeric IDs are treated as placeholders — we re-match by IoU.

    Motion step kept small (0.02 normalized units per frame) so the
    bbox overlap stays above the default 0.3 IoU threshold — that's
    what gates the cross-frame matching.
    """
    from axiom_video import Object, ObjectTracker, Scene, SceneGraph
    frames = []
    for i in range(5):
        cx = 0.3 + i * 0.02
        frames.append(Scene(frame_index=i, objects=(
            Object(id="0", label="ball", bbox=_box(cx, 0.5)),
        )))
    r = ObjectTracker().track(SceneGraph.from_list(frames))
    assert r.payload["ids_source"] == "iou_matched"
    assert r.payload["n_tracks"] == 1


def test_tracker_filters_flicker_tracks(isolated):
    """Single-frame appearance below min_track_length is dropped."""
    from axiom_video import Object, ObjectTracker, Scene, SceneGraph
    frames = []
    for i in range(10):
        objs = [Object(id="real", label="cup", bbox=_box(0.3, 0.5))]
        if i == 5:
            objs.append(Object(id="ghost", label="ghost", bbox=_box(0.7, 0.7)))
        frames.append(Scene(frame_index=i, objects=tuple(objs)))
    r = ObjectTracker(min_track_length=2).track(SceneGraph.from_list(frames))
    ids = {t.id for t in r.tracks}
    assert "real" in ids
    assert "ghost" not in ids


def test_tracker_iou_threshold_separates_distant_objects(isolated):
    """Two non-overlapping objects → two distinct tracks."""
    from axiom_video import Object, ObjectTracker, Scene, SceneGraph
    frames = []
    for i in range(5):
        frames.append(Scene(frame_index=i, objects=(
            Object(id="0", label="ball", bbox=_box(0.1 + i*0.01, 0.5)),
            Object(id="1", label="ball", bbox=_box(0.9 - i*0.01, 0.5)),
        )))
    r = ObjectTracker().track(SceneGraph.from_list(frames))
    assert r.payload["n_tracks"] == 2


# ─── MotionClassifier ───────────────────────────────────────────────────


@pytest.mark.parametrize("scene_name,expected", [
    ("scene_static_single",        "static"),
    ("scene_downward_freefall",    "downward"),
    ("scene_upward_throw",         "upward"),
    ("scene_lateral_slide",        "lateral"),
    ("scene_erratic_dance",        "erratic"),
])
def test_motion_classifier_canonical_motions(isolated, scene_name, expected):
    sys.path.insert(0, "scripts")
    from video_harness import (
        scene_downward_freefall, scene_erratic_dance,
        scene_lateral_slide, scene_static_single, scene_upward_throw,
    )
    from axiom_video import MotionClassifier, ObjectTracker
    builder = {
        "scene_static_single":     scene_static_single,
        "scene_downward_freefall": scene_downward_freefall,
        "scene_upward_throw":      scene_upward_throw,
        "scene_lateral_slide":     scene_lateral_slide,
        "scene_erratic_dance":     scene_erratic_dance,
    }[scene_name]
    tracks = ObjectTracker().track(builder())
    motions = MotionClassifier().classify(tracks)
    assert motions.verify() is True
    assert motions.payload["dominant_class"] == expected


def test_motion_report_signature_namespace_is_isolated(isolated):
    """Motion report uses its own namespace — not the tracker's."""
    from axiom_video.motion import MOTION_KEY_NS
    from axiom_video.object_tracker import OBJECTS_KEY_NS
    assert MOTION_KEY_NS == b"axiom-video-motion-v1"
    assert OBJECTS_KEY_NS == b"axiom-video-objects-v1"
    assert MOTION_KEY_NS != OBJECTS_KEY_NS


# ─── ImpactDetector ─────────────────────────────────────────────────────


def test_impact_decel_fires_when_object_halts(isolated):
    sys.path.insert(0, "scripts")
    from video_harness import scene_drop_and_impact_floor
    from axiom_video import ImpactDetector, MotionClassifier, ObjectTracker
    sg = scene_drop_and_impact_floor()
    tracks = ObjectTracker().track(sg)
    motions = MotionClassifier().classify(tracks)
    impacts = ImpactDetector().detect(tracks, motions)
    assert impacts.verify() is True
    assert impacts.payload["n_events"] == 1
    assert impacts.events[0].impact_type == "deceleration"


def test_impact_contact_fires_when_objects_overlap_with_motion(isolated):
    sys.path.insert(0, "scripts")
    from video_harness import scene_two_objects_collide
    from axiom_video import ImpactDetector, MotionClassifier, ObjectTracker
    sg = scene_two_objects_collide()
    tracks = ObjectTracker().track(sg)
    motions = MotionClassifier().classify(tracks)
    impacts = ImpactDetector().detect(tracks, motions)
    # At least one contact event
    contacts = [e for e in impacts.events if e.impact_type == "contact"]
    assert len(contacts) >= 1


def test_impact_does_not_fire_on_static_scene(isolated):
    sys.path.insert(0, "scripts")
    from video_harness import scene_static_two
    from axiom_video import ImpactDetector, MotionClassifier, ObjectTracker
    sg = scene_static_two()
    tracks = ObjectTracker().track(sg)
    motions = MotionClassifier().classify(tracks)
    impacts = ImpactDetector().detect(tracks, motions)
    assert impacts.payload["n_events"] == 0


# ─── TemporalChainExtractor ─────────────────────────────────────────────


def test_temporal_chain_emits_appear_for_every_track(isolated):
    sys.path.insert(0, "scripts")
    from video_harness import scene_reach_grip_tilt_fall
    from axiom_video import (
        ImpactDetector, MotionClassifier, ObjectTracker,
        TemporalChainExtractor,
    )
    sg = scene_reach_grip_tilt_fall()
    tracks = ObjectTracker().track(sg)
    motions = MotionClassifier().classify(tracks)
    impacts = ImpactDetector().detect(tracks, motions)
    chain = TemporalChainExtractor().extract(tracks, motions, impacts)
    assert chain.verify() is True
    appear_events = [e for e in chain.events if e.type == "appear"]
    assert len(appear_events) == tracks.payload["n_tracks"]


def test_temporal_chain_events_are_time_ordered(isolated):
    sys.path.insert(0, "scripts")
    from video_harness import scene_reach_grip_tilt_fall
    from axiom_video import (
        ImpactDetector, MotionClassifier, ObjectTracker,
        TemporalChainExtractor,
    )
    sg = scene_reach_grip_tilt_fall()
    tracks = ObjectTracker().track(sg)
    motions = MotionClassifier().classify(tracks)
    impacts = ImpactDetector().detect(tracks, motions)
    chain = TemporalChainExtractor().extract(tracks, motions, impacts)
    times = [e.t for e in chain.events]
    assert times == sorted(times)


# ─── Signature isolation across all 4 namespaces ────────────────────────


def test_each_report_has_dedicated_namespace(isolated):
    from axiom_video.impact import IMPACT_KEY_NS
    from axiom_video.motion import MOTION_KEY_NS
    from axiom_video.object_tracker import OBJECTS_KEY_NS
    from axiom_video.temporal_chain import TEMPORAL_KEY_NS
    ns = {OBJECTS_KEY_NS, MOTION_KEY_NS, IMPACT_KEY_NS, TEMPORAL_KEY_NS}
    assert len(ns) == 4    # all distinct
    for n in ns:
        assert n.startswith(b"axiom-video-")
        assert n.endswith(b"-v1")


def test_tampered_report_fails_verify(isolated):
    sys.path.insert(0, "scripts")
    from video_harness import scene_downward_freefall
    from axiom_video import (
        ImpactDetector, MotionClassifier, MotionReport, ObjectTracker,
    )
    sg = scene_downward_freefall()
    tracks = ObjectTracker().track(sg)
    motions = MotionClassifier().classify(tracks)
    tampered = MotionReport(
        payload={**motions.payload, "dominant_class": "TAMPERED"},
        confidence=motions.confidence,
        signature=motions.signature,
    )
    assert tampered.verify() is False


# ─── Harness end-to-end ─────────────────────────────────────────────────


def test_harness_passes_all_gates(isolated):
    """The 14 reference scenes all meet the Phase A acceptance gates."""
    sys.path.insert(0, "scripts")
    from video_harness import REFERENCE_SCENES, evaluate_one, summarize
    results = [evaluate_one(r) for r in REFERENCE_SCENES]
    summary = summarize(results)
    assert summary["motion_gate_pass"] is True, (
        f"motion gate failed: {summary['motion_correct']}/{summary['total']}"
    )
    assert summary["impacts_gate_pass"] is True, (
        f"impacts gate failed: {summary['impacts_correct']}/{summary['total']}"
    )
    assert summary["signatures_gate_pass"] is True


# ─── VideoAgent — real mode + back-compat stub mode ─────────────────────
# Section trimmed: the "real mode" + "legacy stub mode" tests require the
# event_token Coordinator's `video` agent registration to actually
# CLASSIFY video — they ship with the bonded-pair event_token PR.
# The off-by-default test below works on plain main because it only
# activates ("text", "governance") and asserts token.video is None.


def test_video_agent_off_by_default(isolated):
    from axiom_event_token import Coordinator
    coord = Coordinator()
    token = coord.compose(text="hi", activate=("text", "governance"))
    assert token.video is None
