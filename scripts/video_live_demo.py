#!/usr/bin/env python3
"""End-to-end video pipeline demo — proves the live-demo path.

What this demo does:

  1. Procedurally renders 30 PIL frames of a red cup falling onto
     a blue floor.
  2. Feeds them through `FrameIngester` with a `ScriptedObjectDetector`
     that knows where the cup + floor are in each frame.
     (A real customer plugs in YOLO/Detectron/OpenCV here — same
     interface.)
  3. Runs the resulting `SceneGraph` through `VideoAgent` via the
     event-token `Coordinator`.
  4. Prints the signed `EventToken` summary + the temporal chain
     event sequence.

The whole thing runs in pure Python + PIL — no numpy, no cv2, no
GPU. Same hardware target as a Raspberry Pi 5 or Orin Nano running
headless.

Usage:

    export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
    python3 scripts/video_live_demo.py

    # Save the rendered frames to disk for visual inspection:
    python3 scripts/video_live_demo.py --dump-frames /tmp/demo-frames
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402

from axiom_event_token import Coordinator  # noqa: E402
from axiom_video import (  # noqa: E402
    DetectedObject, FrameIngester, ScriptedObjectDetector,
)


def build_demo_clip(n_frames: int = 30, w: int = 120, h: int = 120):
    """Render `n_frames` frames of a red cup falling onto a blue floor.

    Returns (frames, detector_script):
      frames           list of PIL.Image
      detector_script  list of per-frame DetectedObject lists,
                       suitable for ScriptedObjectDetector
    """
    frames = []
    script = []

    # Cup falls from y=10 to y=85; the floor is the blue strip at y=85+
    floor_y_start_norm = 85 / h

    for i in range(n_frames):
        img = Image.new("RGB", (w, h), (245, 245, 245))   # off-white bg

        # Floor — fixed blue strip across the bottom
        for y in range(85, h):
            for x in range(w):
                img.putpixel((x, y), (40, 60, 220))

        # Cup — red rectangle that falls until it hits the floor
        cup_h = 18
        cup_y = min(10 + i * 3, 85 - cup_h)    # stop at floor
        cup_x1, cup_x2 = 50, 70
        cup_y2 = cup_y + cup_h
        for y in range(cup_y, cup_y2):
            for x in range(cup_x1, cup_x2):
                img.putpixel((x, y), (215, 35, 35))

        frames.append(img)
        script.append([
            DetectedObject(label="cup", id="cup",
                           bbox=(cup_x1 / w, cup_y / h,
                                  cup_x2 / w, cup_y2 / h),
                           confidence=0.95),
            DetectedObject(label="floor", id="floor",
                           bbox=(0.0, floor_y_start_norm, 1.0, 1.0),
                           confidence=0.99),
        ])

    return frames, script


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--n-frames", type=int, default=30,
                   help="How many frames to render. Default: 30.")
    p.add_argument("--dump-frames", type=Path, default=None,
                   help="If set, write each frame as a .png to this dir.")
    p.add_argument("--json", action="store_true",
                   help="Emit the full EventToken as JSON instead of text.")
    args = p.parse_args(argv[1:])

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("AXIOM_MASTER_KEY must be set.", file=sys.stderr)
        return 1

    print(f"  Rendering {args.n_frames} demo frames...", file=sys.stderr)
    frames, script = build_demo_clip(n_frames=args.n_frames)

    if args.dump_frames is not None:
        args.dump_frames.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(frames):
            frame.save(args.dump_frames / f"frame_{i:03d}.png")
        print(f"  Frames written to {args.dump_frames}/", file=sys.stderr)

    print(f"  Ingesting frames through FrameIngester...", file=sys.stderr)
    sg = FrameIngester(ScriptedObjectDetector(script)).ingest(
        frames, fps=30.0,
    )

    print(f"  Running through VideoAgent (Coordinator) "
          f"with all 6 sub-detectors...", file=sys.stderr)
    token = Coordinator().compose(
        video={"scene_graph": sg},
        activate=("video", "governance"),
    )

    if args.json:
        print(json.dumps(token.to_dict(), indent=2))
        return 0

    # Human-readable output
    p_video = token.video.payload
    print()
    print("══ EventToken (video) ══════════════════════════════════════")
    print(f"  token verified:    {token.verify()}")
    print(f"  video confidence:  {token.video.confidence}")
    print()
    s = p_video["summary"]
    print(f"  tracks:            {s['n_tracks']}")
    print(f"  dominant motion:   {s['dominant_motion']}")
    print(f"  impacts:           {s['n_impacts']}")
    print(f"  chain events:      {s['n_chain_events']}")
    print(f"  rhythm class:      {s['rhythm_class']}")
    print(f"  scene color:       {s['scene_color']}")
    print(f"  color events:      {s['n_color_events']}")

    print()
    print("── Per-track motion ────────────────────────────────")
    for m in p_video["motion_report"]["payload"]["motions"]:
        net = m["net_displacement"]
        print(f"  {m['id']:<10} {m['label']:<10} {m['motion_class']:<12} "
              f"dx={net[0]:+.3f} dy={net[1]:+.3f}")

    print()
    print("── Per-track color ─────────────────────────────────")
    for c in p_video["color_report"]["payload"]["tracks"]:
        rgb = c["rgb_mean"]
        print(f"  {c['id']:<10} {c['label']:<10} {c['dominant_color']:<12} "
              f"({c['saturation_class']}/{c['brightness_class']})  "
              f"RGB=({int(rgb[0])}, {int(rgb[1])}, {int(rgb[2])})")

    print()
    print("── Impact events ───────────────────────────────────")
    impacts = p_video["impact_report"]["payload"]["events"]
    if not impacts:
        print("  (none)")
    for e in impacts:
        ids = ", ".join(e["track_ids"])
        print(f"  frame {e['frame_index']:3d}  "
              f"{e['impact_type']:<14} subjects=[{ids}]  "
              f"mag={e['magnitude']:.2f}")

    print()
    print("── Temporal chain ──────────────────────────────────")
    for e in p_video["temporal_chain_report"]["payload"]["events"]:
        subs = ", ".join(e["subjects"])
        mot = f" ({e['motion']})" if e.get("motion") else ""
        print(f"  t={e['t']:>5.2f}s  {e['type']:<14} [{subs}]{mot}")

    print()
    print("── HMAC signatures (6 namespaces) ──────────────────")
    for key, ns in [
        ("object_track_report",   "axiom-video-objects-v1"),
        ("motion_report",         "axiom-video-motion-v1"),
        ("impact_report",         "axiom-video-impact-v1"),
        ("temporal_chain_report", "axiom-video-temporal-v1"),
        ("time_keeper_report",    "axiom-video-timekeeper-v1"),
        ("color_report",          "axiom-video-color-v1"),
    ]:
        sig = p_video[key]["signature"]
        print(f"  {ns:<32}  {sig[:16]}...{sig[-16:]}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
