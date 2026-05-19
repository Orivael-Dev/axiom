"""Tests for axiom_video TimeKeeper + ColorWatcher.

Two new Phase A agents:

  TimeKeeper      rhythm + silence + burst detection over the
                   TemporalChainReport's event stream
  ColorWatcher    dominant color per track + shift events,
                   driven by Object.extras["color"]

Same hermetic synthetic-fixture pattern as test_axiom_video.py.
No network, no recorded video, no real frames.
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


def _make_chain(times, *, types=None, fps=30.0):
    """Build a minimal TemporalChainReport with hand-fed events.

    `times` is a list of seconds-since-start floats. `types` is the
    matching list of event type strings (defaults to 'contact').
    """
    from axiom_video import TemporalChainExtractor
    from axiom_video.temporal_chain import TemporalChainReport
    types = types or ["contact"] * len(times)
    payload = {
        "events": [
            {"t": t, "type": ty, "subjects": [f"t{i}"]}
            for i, (t, ty) in enumerate(zip(times, types))
        ],
        "n_events": len(times),
        "n_subjects": len(times),
        "fps": fps,
    }
    return TemporalChainReport.signed(payload=payload, confidence=1.0)


# ─── TimeKeeper ─────────────────────────────────────────────────────────


def test_time_keeper_perfect_regular_rhythm(isolated):
    """5 events at exactly 1s intervals → rhythm_score 1.0, class 'regular'."""
    from axiom_video import TimeKeeper
    chain = _make_chain([0.0, 1.0, 2.0, 3.0, 4.0])
    r = TimeKeeper().analyze(chain)
    assert r.verify() is True
    assert r.payload["rhythm_score"] == pytest.approx(1.0)
    assert r.payload["rhythm_class"] == "regular"
    assert r.payload["n_events"] == 5


def test_time_keeper_irregular_rhythm(isolated):
    """Irregular gaps → low rhythm score → 'irregular' or 'chaotic'."""
    from axiom_video import TimeKeeper
    chain = _make_chain([0.0, 0.1, 1.5, 1.6, 4.0])
    r = TimeKeeper().analyze(chain)
    assert r.payload["rhythm_score"] < 0.6
    assert r.payload["rhythm_class"] in ("irregular", "chaotic")


def test_time_keeper_detects_silence_gap(isolated):
    """A 3-second gap with silence_threshold=1.0 → 1 silence reported."""
    from axiom_video import TimeKeeper
    chain = _make_chain([0.0, 0.5, 1.0, 4.0, 4.5])
    r = TimeKeeper(silence_threshold_s=1.0).analyze(chain)
    assert len(r.payload["silences"]) == 1
    assert r.payload["silences"][0]["duration_s"] == pytest.approx(3.0)


def test_time_keeper_detects_burst(isolated):
    """4 events in 0.3 seconds → 1 burst (window=0.5, min=3)."""
    from axiom_video import TimeKeeper
    chain = _make_chain([0.0, 0.1, 0.2, 0.3, 5.0])
    r = TimeKeeper(burst_window_s=0.5, burst_min_events=3).analyze(chain)
    assert len(r.payload["bursts"]) == 1
    assert r.payload["bursts"][0]["n_events"] == 4


def test_time_keeper_handles_empty_chain(isolated):
    """No events → confidence 0, no silences/bursts, no crash."""
    from axiom_video import TimeKeeper
    chain = _make_chain([])
    r = TimeKeeper().analyze(chain)
    assert r.verify() is True
    assert r.confidence == 0.0
    assert r.payload["n_events"] == 0
    assert r.payload["rhythm_class"] == "insufficient"


def test_time_keeper_event_type_filter(isolated):
    """Filter limits which events participate in rhythm scoring."""
    from axiom_video import TimeKeeper
    chain = _make_chain(
        times=[0.0, 0.05, 1.0, 1.05, 2.0, 2.05],
        types=["contact", "appear", "contact", "appear", "contact", "appear"],
    )
    # Only contacts → 0, 1, 2 → perfectly regular at 1s
    r = TimeKeeper(event_type_filter={"contact"}).analyze(chain)
    assert r.payload["rhythm_score"] == pytest.approx(1.0)
    assert r.payload["n_events"] == 3


def test_time_keeper_signature_isolated_namespace(isolated):
    from axiom_video.time_keeper import TIMEKEEPER_KEY_NS
    from axiom_video.object_tracker import OBJECTS_KEY_NS
    assert TIMEKEEPER_KEY_NS == b"axiom-video-timekeeper-v1"
    assert TIMEKEEPER_KEY_NS != OBJECTS_KEY_NS


def test_time_keeper_tamper_breaks_signature(isolated):
    from axiom_video import TimeKeeper
    from axiom_video.time_keeper import TimeKeeperReport
    chain = _make_chain([0.0, 1.0, 2.0])
    r = TimeKeeper().analyze(chain)
    tampered = TimeKeeperReport(
        payload={**r.payload, "rhythm_class": "TAMPERED"},
        confidence=r.confidence, signature=r.signature,
    )
    assert tampered.verify() is False


# ─── ColorWatcher — classify_color helper ───────────────────────────────


@pytest.mark.parametrize("rgb,expected_label", [
    ((255, 0,   0),   "red"),
    ((0,   255, 0),   "green"),
    ((0,   0,   255), "blue"),
    ((255, 128, 0),   "orange"),
    ((0,   255, 255), "cyan"),
    ((255, 0,   255), "magenta"),
    ((128, 128, 128), "gray"),
    ((0,   0,   0),   "black"),
    ((255, 255, 255), "white"),
    ((50,  0,   0),   "dark_red"),
])
def test_classify_color_canonical_swatches(isolated, rgb, expected_label):
    from axiom_video import classify_color
    label, _ = classify_color(rgb)
    assert label == expected_label, f"{rgb} expected {expected_label}, got {label}"


# ─── ColorWatcher — over a SceneGraph ───────────────────────────────────


def _make_color_sg(per_frame_colors):
    """Build a SceneGraph with one object 'a' whose extras['color']
    cycles through per_frame_colors."""
    from axiom_video import Object, Scene, SceneGraph
    scenes = []
    for i, color in enumerate(per_frame_colors):
        scenes.append(Scene(
            frame_index=i,
            objects=(Object(
                id="a", label="cup",
                bbox=(0.4, 0.4, 0.6, 0.6),
                extras={"color": color},
            ),),
        ))
    return SceneGraph.from_list(scenes)


def test_color_watcher_stable_red_cup(isolated):
    from axiom_video import ColorWatcher
    sg = _make_color_sg([(255, 0, 0)] * 10)
    r = ColorWatcher().watch(sg)
    assert r.verify() is True
    assert r.payload["n_tracks"] == 1
    track = r.payload["tracks"][0]
    assert track["dominant_color"] == "red"
    assert track["stable"] is True
    assert track["saturation_class"] == "vivid"
    assert r.payload["scene_dominant_color"] == "red"
    assert r.payload["n_color_events"] == 0


def test_color_watcher_traffic_light_shift(isolated):
    """Green → yellow → red sequence emits 2 color shift events."""
    from axiom_video import ColorWatcher
    sg = _make_color_sg([
        (0, 255, 0), (0, 255, 0), (0, 255, 0),    # green
        (255, 255, 0), (255, 255, 0),               # yellow (-> orange in our naming)
        (255, 0, 0), (255, 0, 0), (255, 0, 0),      # red
    ])
    r = ColorWatcher().watch(sg)
    assert r.payload["n_color_events"] == 2
    types_seen = {(e["from"], e["to"]) for e in r.payload["color_events"]}
    assert any("green" in t[0] for t in types_seen)
    assert any("red" in t[1] for t in types_seen)


def test_color_watcher_pale_red_classification(isolated):
    """Light pink → 'pale_red' classification (high V, low-mid S)."""
    from axiom_video import classify_color
    label, _ = classify_color((255, 200, 200))
    assert label == "pale_red"


def test_color_watcher_skips_uncolored_objects(isolated):
    """Objects without extras['color'] don't crash + show in n_uncolored."""
    from axiom_video import ColorWatcher, Object, Scene, SceneGraph
    scenes = []
    for i in range(5):
        scenes.append(Scene(
            frame_index=i,
            objects=(Object(id="a", label="ghost",
                            bbox=(0.4, 0.4, 0.6, 0.6)),),
        ))
    r = ColorWatcher().watch(SceneGraph.from_list(scenes))
    assert r.verify() is True
    assert r.payload["n_tracks"] == 0
    assert r.payload["n_uncolored"] == 5
    assert r.confidence == 0.0


def test_color_watcher_multi_object_scene(isolated):
    """Two objects with distinct colors → both appear in tracks."""
    from axiom_video import ColorWatcher, Object, Scene, SceneGraph
    scenes = []
    for i in range(5):
        scenes.append(Scene(
            frame_index=i,
            objects=(
                Object(id="cup",  label="cup",  bbox=(0.1, 0.4, 0.3, 0.6),
                       extras={"color": (255, 0, 0)}),
                Object(id="ball", label="ball", bbox=(0.7, 0.4, 0.9, 0.6),
                       extras={"color": (0, 0, 255)}),
            ),
        ))
    r = ColorWatcher().watch(SceneGraph.from_list(scenes))
    assert r.payload["n_tracks"] == 2
    doms = {t["id"]: t["dominant_color"] for t in r.payload["tracks"]}
    assert doms["cup"] == "red"
    assert doms["ball"] == "blue"


def test_color_watcher_signature_isolated_namespace(isolated):
    from axiom_video.color_watcher import COLOR_KEY_NS
    from axiom_video.time_keeper import TIMEKEEPER_KEY_NS
    assert COLOR_KEY_NS == b"axiom-video-color-v1"
    assert COLOR_KEY_NS != TIMEKEEPER_KEY_NS


def test_color_watcher_tamper_breaks_signature(isolated):
    from axiom_video import ColorWatcher
    from axiom_video.color_watcher import ColorReport
    sg = _make_color_sg([(255, 0, 0)] * 5)
    r = ColorWatcher().watch(sg)
    tampered = ColorReport(
        payload={**r.payload, "scene_dominant_color": "TAMPERED"},
        confidence=r.confidence, signature=r.signature,
    )
    assert tampered.verify() is False


# ─── VideoAgent end-to-end through the Coordinator ──────────────────────


def test_video_agent_real_mode_includes_time_and_color_reports(isolated):
    """The upgraded VideoAgent now produces 6 sub-reports + a summary
    that surfaces rhythm_class + scene_color."""
    from axiom_event_token import Coordinator
    from axiom_video import Object, Scene, SceneGraph

    # Hand-built mini-scene: two colored objects, with motion
    scenes = []
    for i in range(10):
        cx = 0.2 + i * 0.02     # slow lateral motion
        scenes.append(Scene(
            frame_index=i,
            objects=(
                Object(id="cup", label="cup",
                       bbox=(cx, 0.4, cx + 0.1, 0.5),
                       extras={"color": (255, 0, 0)}),
                Object(id="ball", label="ball",
                       bbox=(0.7, 0.7, 0.8, 0.8),
                       extras={"color": (0, 0, 255)}),
            ),
        ))
    sg = SceneGraph.from_list(scenes)

    token = Coordinator().compose(
        video={"scene_graph": sg},
        activate=("video", "governance"),
    )
    assert token.verify() is True
    p = token.video.payload
    assert p["mode"] == "real"
    # Six sub-reports nested
    for key in (
        "object_track_report", "motion_report", "impact_report",
        "temporal_chain_report", "time_keeper_report", "color_report",
    ):
        assert key in p, f"{key} missing from VideoAgent payload"
    # Summary surfaces the new fields
    assert "rhythm_class" in p["summary"]
    assert "scene_color"  in p["summary"]
    # Both colored tracks accounted for
    assert p["summary"]["scene_color"] in ("red", "blue")
