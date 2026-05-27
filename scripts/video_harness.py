#!/usr/bin/env python3
"""Axiom Video — synthetic scene harness.

Generates procedural SceneGraphs in-process so the video Phase A
detector ladder can be tested + demoed without recorded video or
codec deps. Same approach as axiom_audio's synthetic clip
generator.

Each helper returns a `SceneGraph` of `Scene` of `Object`. The
detector chain is:

    SceneGraph
      → ObjectTracker     → ObjectTrackReport
      → MotionClassifier  → MotionReport
      → ImpactDetector    → ImpactReport
      → TemporalChainExtractor → TemporalChainReport

14 reference scenes, designed to exercise every motion class +
every impact type:

  1.  static_single                — one static object (baseline)
  2.  static_two                   — two static objects (no contact)
  3.  downward_freefall            — single object falling vertically
  4.  upward_throw                 — single object rising
  5.  lateral_slide                — object translating horizontally
  6.  diagonal_slide               — clear lateral wins over vertical
  7.  ball_bounce                  — accelerating then sudden direction change
  8.  drop_and_impact_floor        — object falls, decelerates at floor
  9.  cup_tilt_pour                — object slowly rotates / tilts (lateral-ish)
  10. two_objects_collide          — both moving, then bbox overlap
  11. handover                     — object_A moves, contact with object_B, then both
  12. flicker_track                — short-lived track that should be filtered
  13. erratic_dance                — many direction changes
  14. reach_grip_tilt_fall         — full kid-toy use-case sequence

Acceptance gates (Phase A):
  - motion classification: ≥ 12/14 correct (>= 85%)
  - impact detection: every scene that should have an event does;
    no scene without an expected event triggers a false positive
  - end-to-end signed report verifies on every scene

Usage:
    python3 scripts/video_harness.py             # runs full suite
    python3 scripts/video_harness.py --scene 8   # one scene
    python3 scripts/video_harness.py --json      # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from axiom_video import (  # noqa: E402
    ImpactDetector, MotionClassifier, Object, ObjectTracker, Scene, SceneGraph,
    TemporalChainExtractor,
)


# ─── Scene helpers ──────────────────────────────────────────────────────


def _box(cx: float, cy: float, size: float = 0.08
        ) -> tuple[float, float, float, float]:
    h = size / 2
    return (cx - h, cy - h, cx + h, cy + h)


def _build(scene_objects: list[list[Object]], fps: float = 30.0) -> SceneGraph:
    """Wrap a list-of-objects-per-frame into a SceneGraph."""
    return SceneGraph.from_list(
        [Scene(frame_index=i, objects=tuple(objs),
               timestamp_s=round(i / fps, 4))
         for i, objs in enumerate(scene_objects)],
        fps=fps,
    )


# ─── 14 reference scenes ────────────────────────────────────────────────


def scene_static_single() -> SceneGraph:
    frames = [[Object(id="a", label="cup", bbox=_box(0.5, 0.5))]
              for _ in range(15)]
    return _build(frames)


def scene_static_two() -> SceneGraph:
    frames = [[Object(id="a", label="cup", bbox=_box(0.3, 0.5)),
               Object(id="b", label="cup", bbox=_box(0.7, 0.5))]
              for _ in range(15)]
    return _build(frames)


def scene_downward_freefall() -> SceneGraph:
    frames = []
    for i in range(20):
        cy = 0.1 + i * 0.04                       # gentle constant fall
        frames.append([Object(id="a", label="ball", bbox=_box(0.5, cy))])
    return _build(frames)


def scene_upward_throw() -> SceneGraph:
    frames = []
    for i in range(20):
        cy = 0.9 - i * 0.04
        frames.append([Object(id="a", label="ball", bbox=_box(0.5, cy))])
    return _build(frames)


def scene_lateral_slide() -> SceneGraph:
    frames = []
    for i in range(20):
        cx = 0.1 + i * 0.04
        frames.append([Object(id="a", label="book", bbox=_box(cx, 0.5))])
    return _build(frames)


def scene_diagonal_slide() -> SceneGraph:
    frames = []
    for i in range(20):
        cx = 0.1 + i * 0.04
        cy = 0.4 + i * 0.005                       # dx >> dy → lateral
        frames.append([Object(id="a", label="card", bbox=_box(cx, cy))])
    return _build(frames)


def scene_ball_bounce() -> SceneGraph:
    """Accelerating fall, then reverses (bounces).

    Direction-changes >= 3 + variance → erratic OR accelerating
    depending on which threshold trips first. We expect 'accelerating'
    because the velocity-magnitude variance is large.
    """
    frames = []
    speeds = [0.01, 0.015, 0.02, 0.03, 0.04, 0.05,    # accelerating down
              -0.04, -0.03, -0.02, -0.015, -0.01]      # bounce up
    y = 0.2
    for i, v in enumerate(speeds):
        y += v
        frames.append([Object(id="a", label="ball", bbox=_box(0.5, y))])
    return _build(frames)


def scene_drop_and_impact_floor() -> SceneGraph:
    """Object falls steadily, then halts (deceleration event)."""
    frames = []
    for i in range(15):
        cy = 0.1 + i * 0.05
        frames.append([Object(id="a", label="cup", bbox=_box(0.5, cy))])
    # Object hits floor: 5 more frames at the same y
    for _ in range(5):
        frames.append([Object(id="a", label="cup", bbox=_box(0.5, 0.85))])
    return _build(frames)


def scene_cup_tilt_pour() -> SceneGraph:
    """Subtle lateral drift over many frames (tipping)."""
    frames = []
    for i in range(20):
        cx = 0.5 + i * 0.01
        frames.append([Object(id="a", label="cup", bbox=_box(cx, 0.5))])
    return _build(frames)


def scene_two_objects_collide() -> SceneGraph:
    """Two objects move toward each other, overlap in middle."""
    frames = []
    for i in range(15):
        ax = 0.1 + i * 0.04                        # left ball moves right
        bx = 0.9 - i * 0.04                        # right ball moves left
        frames.append([
            Object(id="a", label="ball", bbox=_box(ax, 0.5)),
            Object(id="b", label="ball", bbox=_box(bx, 0.5)),
        ])
    return _build(frames)


def scene_handover() -> SceneGraph:
    """Object_A moves to touch B, then they share frames."""
    frames = []
    for i in range(15):
        if i < 10:
            ax = 0.2 + i * 0.04
        else:
            ax = 0.6                              # parked at B
        frames.append([
            Object(id="a", label="hand", bbox=_box(ax, 0.5)),
            Object(id="b", label="cup",  bbox=_box(0.6, 0.5)),
        ])
    return _build(frames)


def scene_flicker_track() -> SceneGraph:
    """Single object appears for one frame, should be filtered out."""
    frames = []
    for i in range(10):
        objs = [Object(id="a", label="cup", bbox=_box(0.3, 0.5))]
        if i == 5:
            objs.append(Object(id="b", label="ghost", bbox=_box(0.7, 0.7)))
        frames.append(objs)
    return _build(frames)


def scene_erratic_dance() -> SceneGraph:
    """Single track with many direction changes in y."""
    frames = []
    ys = [0.4, 0.5, 0.4, 0.5, 0.4, 0.5, 0.4, 0.5, 0.4, 0.5]
    for i, cy in enumerate(ys):
        frames.append([Object(id="a", label="bird", bbox=_box(0.5, cy))])
    return _build(frames)


def scene_reach_grip_tilt_fall() -> SceneGraph:
    """The flagship use case from the concept doc:
       hand moves to cup (contact), then cup falls (downward → impact)."""
    frames = []
    # Phase 1: hand approaches cup
    for i in range(8):
        hx = 0.2 + i * 0.04
        frames.append([
            Object(id="hand", label="hand", bbox=_box(hx, 0.5)),
            Object(id="cup",  label="cup",  bbox=_box(0.6, 0.5)),
        ])
    # Phase 2: hand and cup are in contact
    for _ in range(3):
        frames.append([
            Object(id="hand", label="hand", bbox=_box(0.55, 0.5)),
            Object(id="cup",  label="cup",  bbox=_box(0.6, 0.5)),
        ])
    # Phase 3: cup falls
    for i in range(8):
        cy = 0.5 + i * 0.05
        frames.append([
            Object(id="hand", label="hand", bbox=_box(0.55, 0.5)),
            Object(id="cup",  label="cup",  bbox=_box(0.62, cy)),
        ])
    # Phase 4: cup hits floor, decelerates
    for _ in range(4):
        frames.append([
            Object(id="hand", label="hand", bbox=_box(0.55, 0.5)),
            Object(id="cup",  label="cup",  bbox=_box(0.62, 0.92)),
        ])
    return _build(frames)


# ─── Reference catalogue ────────────────────────────────────────────────


@dataclass(frozen=True)
class ReferenceScene:
    name:              str
    builder:           callable
    expected_motion:   str               # dominant class
    expected_impacts:  int               # how many impact events SHOULD fire
    notes:             str


REFERENCE_SCENES: tuple[ReferenceScene, ...] = (
    ReferenceScene("static_single",         scene_static_single,        "static",       0,
                   "Baseline — no motion."),
    ReferenceScene("static_two",            scene_static_two,           "static",       0,
                   "Two parked objects, no contact."),
    ReferenceScene("downward_freefall",     scene_downward_freefall,    "downward",     0,
                   "Constant downward velocity."),
    ReferenceScene("upward_throw",          scene_upward_throw,         "upward",       0,
                   "Constant upward velocity."),
    ReferenceScene("lateral_slide",         scene_lateral_slide,        "lateral",      0,
                   "Pure horizontal translation."),
    ReferenceScene("diagonal_slide",        scene_diagonal_slide,       "lateral",      0,
                   "Mostly horizontal — dx >> dy."),
    ReferenceScene("ball_bounce",           scene_ball_bounce,          "downward",     2,
                   "Throw-and-fall trajectory: net dy positive + decel events at apex."),
    ReferenceScene("drop_and_impact_floor", scene_drop_and_impact_floor, "downward",    1,
                   "Falling cup → deceleration event."),
    ReferenceScene("cup_tilt_pour",         scene_cup_tilt_pour,        "lateral",      0,
                   "Subtle lateral drift."),
    ReferenceScene("two_objects_collide",   scene_two_objects_collide,  "lateral",      1,
                   "Two balls meet in the middle."),
    ReferenceScene("handover",              scene_handover,             "lateral",      2,
                   "Moving hand contacts cup + decelerates."),
    ReferenceScene("flicker_track",         scene_flicker_track,        "static",       0,
                   "Flicker filtered out by min_track_length."),
    ReferenceScene("erratic_dance",         scene_erratic_dance,        "erratic",      0,
                   "Many y-direction changes."),
    ReferenceScene("reach_grip_tilt_fall",  scene_reach_grip_tilt_fall, "lateral",      3,
                   "Hand-lateral motion dominates count; chain has contact + 2 decels."),
)


# ─── Evaluation ─────────────────────────────────────────────────────────


def evaluate_one(ref: ReferenceScene) -> dict:
    """Run the full detector ladder on one reference scene."""
    sg = ref.builder()
    tracks = ObjectTracker().track(sg)
    motions = MotionClassifier().classify(tracks)
    impacts = ImpactDetector().detect(tracks, motions)
    chain = TemporalChainExtractor().extract(tracks, motions, impacts)

    actual_motion = motions.payload["dominant_class"]
    actual_impacts = len(impacts.events)

    return {
        "name":             ref.name,
        "expected_motion":  ref.expected_motion,
        "actual_motion":    actual_motion,
        "motion_ok":        actual_motion == ref.expected_motion,
        "expected_impacts": ref.expected_impacts,
        "actual_impacts":   actual_impacts,
        "impacts_ok":       actual_impacts == ref.expected_impacts,
        "n_tracks":         tracks.payload["n_tracks"],
        "n_chain_events":   chain.payload["n_events"],
        "all_signatures_verify": (
            tracks.verify() and motions.verify()
            and impacts.verify() and chain.verify()
        ),
        "notes": ref.notes,
    }


def summarize(results: list[dict]) -> dict:
    n = len(results)
    motion_ok = sum(1 for r in results if r["motion_ok"])
    impacts_ok = sum(1 for r in results if r["impacts_ok"])
    sigs_ok = sum(1 for r in results if r["all_signatures_verify"])
    return {
        "total":           n,
        "motion_correct":  motion_ok,
        "motion_accuracy": round(motion_ok / n, 3) if n else 0,
        "impacts_correct": impacts_ok,
        "impacts_accuracy": round(impacts_ok / n, 3) if n else 0,
        "signatures_verified": sigs_ok,
        "motion_gate_pass":    motion_ok >= 12,    # ≥ 85%
        "impacts_gate_pass":   impacts_ok == n,    # 100% — no FP allowed
        "signatures_gate_pass": sigs_ok == n,      # 100%
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--scene", type=int, default=None,
                   help="Run only this scene index (0-based).")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of text.")
    args = p.parse_args(argv[1:])

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("AXIOM_MASTER_KEY must be set.", file=sys.stderr)
        return 1

    scenes = (
        [REFERENCE_SCENES[args.scene]]
        if args.scene is not None else
        list(REFERENCE_SCENES)
    )
    results = [evaluate_one(r) for r in scenes]
    summary = summarize(results) if args.scene is None else None

    if args.json:
        print(json.dumps({"results": results, "summary": summary},
                         indent=2))
        return 0 if (summary is None or
                     (summary["motion_gate_pass"] and
                      summary["impacts_gate_pass"] and
                      summary["signatures_gate_pass"])) else 2

    # Pretty output
    print(f"{'scene':<30}  {'motion':<14} {'imp':>4} {'sigs':<6}")
    print("-" * 60)
    for r in results:
        m_mark = "✓" if r["motion_ok"] else "✗"
        i_mark = "✓" if r["impacts_ok"] else "✗"
        s_mark = "✓" if r["all_signatures_verify"] else "✗"
        print(f"{r['name']:<30}  {m_mark} {r['actual_motion']:<12} "
              f"{i_mark}{r['actual_impacts']:>3} {s_mark}")
    if summary:
        print()
        print(f"  motion accuracy:   {summary['motion_accuracy']:.1%}  "
              f"({summary['motion_correct']}/{summary['total']})  "
              f"gate {'PASS' if summary['motion_gate_pass'] else 'FAIL'}")
        print(f"  impacts accuracy:  {summary['impacts_accuracy']:.1%}  "
              f"({summary['impacts_correct']}/{summary['total']})  "
              f"gate {'PASS' if summary['impacts_gate_pass'] else 'FAIL'}")
        print(f"  signatures:        {summary['signatures_verified']}/{summary['total']}  "
              f"gate {'PASS' if summary['signatures_gate_pass'] else 'FAIL'}")
        return 0 if (summary['motion_gate_pass'] and
                     summary['impacts_gate_pass'] and
                     summary['signatures_gate_pass']) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
