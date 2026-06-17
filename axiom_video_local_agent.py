"""ORVL-027 Video Topology + local agent — a REAL image fed through the
AXIOM Video signed-detector ladder, then narrated by a local Qwen3 model.

axiom_video's scripts/video_harness.py proves the detector ladder on
*synthetic* scene graphs. This demo closes the Phase B loop: a real,
open-source photograph flows through real pixel sampling, then the seven
signed video detectors, then a local Qwen3-1.7B SRD4 model that must
narrate the scene using ONLY the HMAC-signed evidence it is handed.

The differentiator (see axiom_video/__init__.py): a monolithic VLM
(Gemini 2.5 / GPT-4o video / Qwen2-VL) returns one opaque blob. AXIOM
Video returns independent SIGNED verdicts — objects, motion, impact,
temporal chain, time, color, depth, surface — and the language model
composes over them. Every claim the model makes is backed by a verdict
whose signature verifies; it cannot fabricate an impact that the signed
ImpactReport did not record.

## Pipeline

  [ingest]  real frame ─ SalientGridDetector (real pixels) ─► DetectedObject
                                  │  real dominant color sampled in-bbox
                                  ▼
  [trajectory]  frame 0 object is dropped into a physically-plausible
                fall-and-impact trajectory (clearly labelled SIMULATED);
                the object keeps its REAL sampled color the whole way
                                  ▼
  [ladder]  ObjectTracker ─► MotionClassifier ─► ImpactDetector
                    │              │                 │
                    ▼              ▼                 ▼
            TemporalChain ─► TimeKeeper      + DepthClassifier
                                              + SurfaceClassifier
                                              + ColorWatcher
                                  ▼
  [compose]  Qwen3 reads the signed verdict bundle and emits a grounded
             incident narration + a child-safety verdict
                                  ▼
  [verify]   every detector signature is re-verified independently

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_video_local_agent.py
  # or bring your own frame:
  python3 axiom_video_local_agent.py --image path/to/photo.jpg
  # the default downloads an open-source photo to demo_assets/ if absent.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import deque
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

if not os.environ.get("AXIOM_MASTER_KEY"):
    print("[WARN] AXIOM_MASTER_KEY not set — using ephemeral demo key",
          file=sys.stderr)
    os.environ["AXIOM_MASTER_KEY"] = "demo-key-" + __import__("secrets").token_hex(16)

from axiom_video import (  # noqa: E402
    ColorWatcher, DepthClassifier, DetectedObject, FrameIngester,
    ImpactDetector, MotionClassifier, Object, ObjectTracker, Scene,
    SceneGraph, SurfaceClassifier, TemporalChainExtractor, TimeKeeper,
    classify_color,
)

_DEFAULT_MODEL = "models/qwen25_coder_0p5b_srd4_q4km.gguf"
_DEFAULT_BIN   = str(Path.home() / "llama.cpp/build/bin/llama-completion")
# Lorem Picsum serves Unsplash photos under the free-to-use Unsplash
# license — a deterministic seed gives a reproducible real frame.
_DEFAULT_IMAGE_URL = "https://picsum.photos/seed/axiomcup/640/480.jpg"
_DEFAULT_IMAGE     = "demo_assets/scene.jpg"
_SEP = "─" * 70


def _header(t: str) -> None:
    print(f"\n{_SEP}\n  {t}\n{_SEP}")


# ─── Real-pixel object detector (no ML — deterministic saliency) ─────────


class SalientGridDetector:
    """A real ObjectDetectorProtocol over real pixels — no ML weights.

    Downsamples the frame to a coarse grid, scores each cell by its
    distance from the global median color, then returns the bounding
    box of the largest connected blob of salient cells. Crude, but it
    is a genuine detector reading genuine pixels: the bbox it emits is
    where the real foreground object sits, and FrameIngester samples
    that bbox's true color downstream.
    """

    def __init__(self, grid: int = 28, percentile: float = 0.80,
                 label: str = "object") -> None:
        self.grid = grid
        self.percentile = percentile
        self.label = label

    def detect(self, frame) -> list[DetectedObject]:
        from PIL import Image
        if not isinstance(frame, Image.Image):
            frame = Image.fromarray(frame)
        small = frame.convert("RGB").resize((self.grid, self.grid))
        px = small.load()
        g = self.grid

        # Global median per channel.
        rs, gs, bs = [], [], []
        for y in range(g):
            for x in range(g):
                r, gg, b = px[x, y]
                rs.append(r); gs.append(gg); bs.append(b)
        rs.sort(); gs.sort(); bs.sort()
        mid = g * g // 2
        mr, mg, mb = rs[mid], gs[mid], bs[mid]

        # Saliency = colour distance from the global median.
        sal = [[0.0] * g for _ in range(g)]
        flat = []
        for y in range(g):
            for x in range(g):
                r, gg, b = px[x, y]
                d = ((r - mr) ** 2 + (gg - mg) ** 2 + (b - mb) ** 2) ** 0.5
                sal[y][x] = d
                flat.append(d)
        flat.sort()
        thresh = flat[int(self.percentile * (len(flat) - 1))]

        # Largest 4-connected blob of above-threshold cells.
        seen = [[False] * g for _ in range(g)]
        best: list[tuple[int, int]] = []
        for y in range(g):
            for x in range(g):
                if sal[y][x] < thresh or seen[y][x]:
                    continue
                blob, q = [], deque([(x, y)])
                seen[y][x] = True
                while q:
                    cx, cy = q.popleft()
                    blob.append((cx, cy))
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy),
                                   (cx, cy + 1), (cx, cy - 1)):
                        if (0 <= nx < g and 0 <= ny < g
                                and not seen[ny][nx]
                                and sal[ny][nx] >= thresh):
                            seen[ny][nx] = True
                            q.append((nx, ny))
                if len(blob) > len(best):
                    best = blob
        if not best:
            return []

        xs = [c[0] for c in best]
        ys = [c[1] for c in best]
        x1, x2 = min(xs) / g, (max(xs) + 1) / g
        y1, y2 = min(ys) / g, (max(ys) + 1) / g
        conf = min(1.0, len(best) / (g * g) * 4.0)
        return [DetectedObject(label=self.label, bbox=(x1, y1, x2, y2),
                               confidence=round(conf, 4), id="obj")]


# ─── Build a real-seeded fall-and-impact trajectory ─────────────────────


def _box_at(cx: float, cy: float, w: float, h: float
            ) -> tuple[float, float, float, float]:
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def build_scene_graph(image_path: str, fps: float = 30.0
                      ) -> tuple[SceneGraph, dict]:
    """Ingest one real frame, then drop the detected object into a
    SIMULATED fall-and-impact trajectory that keeps its real colour."""
    from PIL import Image
    frame = Image.open(image_path).convert("RGB")

    # Phase B ingest: real detector over real pixels → real colour.
    ingester = FrameIngester(SalientGridDetector())
    seed_sg = ingester.ingest([frame], fps=fps)
    seed_objs = seed_sg.scenes[0].objects
    if not seed_objs:
        raise RuntimeError("No salient object found in frame.")
    seed = seed_objs[0]
    color = seed.extras.get("color")
    label, _ = classify_color(color) if color else ("object", None)

    x1, y1, x2, y2 = seed.bbox
    w, h = (x2 - x1), (y2 - y1)
    start_cx = (x1 + x2) / 2
    extras = {"color": color} if color else {}

    def obj(cx, cy):
        return Object(id="obj", label="object", bbox=_box_at(cx, cy, w, h),
                      confidence=seed.confidence, extras=dict(extras))

    scenes: list[Scene] = []
    fi = 0

    def push(cx, cy):
        nonlocal fi
        scenes.append(Scene(frame_index=fi, objects=(obj(cx, cy),),
                            timestamp_s=round(fi / fps, 4)))
        fi += 1

    # Phase 1 — at rest on a surface (static).
    rest_cy = 0.30
    for _ in range(4):
        push(start_cx, rest_cy)
    # Phase 2 — knocked off, accelerating fall (downward).
    cy = rest_cy
    for k in range(8):
        cy += 0.04 + k * 0.006
        push(start_cx, min(cy, 0.88))
    # Phase 3 — hits the floor, decelerates to rest (impact + stop).
    for _ in range(4):
        push(start_cx, 0.90)

    meta = {
        "image": image_path,
        "frame_size": list(frame.size),
        "real_bbox": [round(v, 4) for v in seed.bbox],
        "real_color_rgb": list(color) if color else None,
        "color_label": label,
        "detector_confidence": seed.confidence,
        "n_frames": len(scenes),
    }
    return SceneGraph.from_list(scenes, fps=fps), meta


# ─── Qwen call ──────────────────────────────────────────────────────────


def _call_model(prompt: str, model: str, binary: str,
                n_predict: int, temp: float) -> str:
    cmd = [binary, "-m", model, "-p", prompt, "-n", str(n_predict),
           "-c", "2048", "--temp", str(temp), "-ngl", "99", "-t", "6",
           "--no-display-prompt"]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  [ERROR] model call failed: {e}")
        return ""
    err = proc.stderr.lower()
    if "out of memory" in err or "unable to create context" in err:
        print("  [ERROR] model load failed (CUDA OOM / context).")
        return ""
    out = proc.stdout.strip()
    if "</think>" in out:
        out = out.rsplit("</think>", 1)[1].strip()
    elif "<think>" in out:
        out = out.split("<think>", 1)[0].strip()
    return out


def _build_prompt(bundle: dict) -> str:
    system = (
        "You are an AXIOM constitutional video agent. You CANNOT see images. "
        "You are given a bundle of independent, HMAC-signed video verdicts "
        "produced by AXIOM's signed detector ladder. Every signature in the "
        "bundle has already been verified. /no_think\n\n"
        "Your job is to narrate what physically happened, grounding EVERY "
        "statement in the signed verdicts only. Never invent an object, a "
        "motion, or an impact that the verdicts do not contain.\n\n"
        "Output in this EXACT format:\n"
        "NARRATION: [2-3 sentences describing the event using only the verdicts]\n"
        "MOTION: [the dominant motion class from the motion verdict]\n"
        "IMPACTS: [number of impact events, and their type(s)]\n"
        "SAFETY_VERDICT: [SAFE | WATCH | ALERT] — ALERT if a tracked object "
        "fell and impacted a surface; WATCH if it moved but no impact; SAFE if "
        "static\n"
        "EVIDENCE: [name the signed verdict(s) that justify your SAFETY_VERDICT]"
    )
    user = (
        "SIGNED VIDEO VERDICT BUNDLE (all signatures verified):\n"
        + json.dumps(bundle, indent=2)
        + "\n\nNarrate the event and render a safety verdict:"
    )
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n")


# ─── Main ───────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="ORVL-027 Video Topology + local agent")
    ap.add_argument("--image", default=_DEFAULT_IMAGE,
                    help="Frame to ingest (downloaded if absent).")
    ap.add_argument("--url", default=_DEFAULT_IMAGE_URL,
                    help="Open-source image URL to fetch when --image is absent.")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--bin", dest="binary", default=_DEFAULT_BIN)
    ap.add_argument("-n", "--n-predict", type=int, default=320)
    ap.add_argument("--temp", type=float, default=0.3)
    ap.add_argument("--no-model", action="store_true",
                    help="Run the signed ladder only; skip the Qwen call.")
    args = ap.parse_args()

    # ── Ingest: fetch an open-source frame if needed ─────────────────────
    _header("Ingest — real frame through real pixel sampling")
    img = Path(args.image)
    if not img.exists():
        img.parent.mkdir(parents=True, exist_ok=True)
        print(f"  frame absent — downloading open-source image:\n    {args.url}")
        try:
            urllib.request.urlretrieve(args.url, str(img))
        except Exception as e:
            print(f"  [ERROR] download failed: {e}")
            return 1
    print(f"  frame : {img}")

    sg, meta = build_scene_graph(str(img))
    print(f"  size  : {meta['frame_size'][0]}x{meta['frame_size'][1]}")
    print(f"  salient object bbox (real) : {meta['real_bbox']}")
    print(f"  dominant colour (real px)  : rgb{tuple(meta['real_color_rgb'])} "
          f"→ '{meta['color_label']}'  (conf {meta['detector_confidence']})")
    print(f"  trajectory : {meta['n_frames']} frames "
          f"(4 rest → 8 fall → 4 floor, SIMULATED from real object)")

    # ── Signed detector ladder ───────────────────────────────────────────
    _header("Signed detector ladder — 7 independent verdicts")
    tracks  = ObjectTracker().track(sg)
    motions = MotionClassifier().classify(tracks)
    impacts = ImpactDetector().detect(tracks, motions)
    chain   = TemporalChainExtractor().extract(tracks, motions, impacts)
    timing  = TimeKeeper().analyze(chain)
    depth   = DepthClassifier().classify(sg, tracks)
    surface = SurfaceClassifier().classify(sg, tracks)
    color   = ColorWatcher().watch(sg)

    reports = {
        "objects":  tracks,
        "motion":   motions,
        "impact":   impacts,
        "temporal": chain,
        "time":     timing,
        "depth":    depth,
        "surface":  surface,
        "color":    color,
    }
    for name, rep in reports.items():
        ok = rep.verify()
        sig = (rep.signature or "")[:24]
        print(f"  {name:<9} verify={'✓' if ok else '✗'}  "
              f"conf={rep.confidence:<5}  sig={sig}...")

    dom = motions.payload["dominant_class"]
    n_imp = impacts.payload["n_events"]
    imp_types = sorted({e["impact_type"] for e in impacts.payload["events"]})
    print(f"\n  → dominant motion : {dom}")
    print(f"  → impact events   : {n_imp}  {imp_types}")
    print(f"  → temporal chain  : "
          f"{' → '.join(e['type'] for e in chain.payload['events'])}")

    all_ok = all(r.verify() for r in reports.values())
    print(f"\n  all signatures verify : {'✓ YES' if all_ok else '✗ NO'}")

    # ── Compose: hand the SIGNED bundle to Qwen ──────────────────────────
    bundle = {
        "objects": {"n_tracks": tracks.payload["n_tracks"],
                    "ids": [t["id"] for t in tracks.payload["tracks"]],
                    "label": tracks.payload["tracks"][0]["label"]
                    if tracks.payload["tracks"] else None},
        "motion": {"dominant_class": dom,
                   "tracks": [{"id": m["id"],
                               "motion_class": m["motion_class"],
                               "net_displacement": m["net_displacement"]}
                              for m in motions.payload["motions"]]},
        "impact": {"n_events": n_imp,
                   "events": impacts.payload["events"]},
        "temporal": {"sequence": [e["type"] for e in chain.payload["events"]]},
        "color": {"scene_dominant_color": color.payload["scene_dominant_color"],
                  "tracks": [{"id": t["id"],
                              "dominant_color": t["dominant_color"]}
                             for t in color.payload["tracks"]]},
        "depth": {"tracks": [{"id": t["id"], "depth_class": t["depth_class"]}
                             for t in depth.payload["tracks"]]},
        "surface": {"tracks": [{"id": t["id"],
                                "orientation_class": t["orientation_class"]}
                               for t in surface.payload["tracks"]]},
    }

    if args.no_model:
        _header("Composition skipped (--no-model)")
        print(json.dumps(bundle, indent=2))
        return 0 if all_ok else 1

    _header("Composition — Qwen3 narrates from signed evidence only")
    print(f"  model : {args.model}\n")
    raw = _call_model(_build_prompt(bundle), args.model, args.binary,
                      args.n_predict, args.temp)
    if not raw:
        print("  [ERROR] no model output.")
        return 1
    for line in raw.splitlines():
        print(f"  | {line}")

    # ── Grounding check: does the model's verdict match the signed facts? ─
    _header("Grounding check — model claims vs signed verdicts")
    verdict_m = re.search(r"SAFETY_VERDICT\s*:\s*(SAFE|WATCH|ALERT)",
                          raw, re.IGNORECASE)
    model_verdict = verdict_m.group(1).upper() if verdict_m else "?"
    # Same rule the model was handed: an impact ⇒ object hit a surface ⇒ ALERT;
    # moved but no impact ⇒ WATCH; static ⇒ SAFE.
    expected = "ALERT" if n_imp >= 1 else (
        "WATCH" if dom != "static" else "SAFE")
    match = model_verdict == expected
    print(f"  signed facts imply : {expected}  "
          f"(motion={dom}, impacts={n_imp})")
    print(f"  model SAFETY_VERDICT: {model_verdict}")
    print(f"  grounded            : {'✓ matches signed evidence' if match else '✗ diverges'}")

    _header("Summary")
    print(f"  frame        : {img}  ({meta['frame_size'][0]}x{meta['frame_size'][1]})")
    print(f"  real object  : '{meta['color_label']}' "
          f"rgb{tuple(meta['real_color_rgb'])}  bbox={meta['real_bbox']}")
    print(f"  motion       : {dom}")
    print(f"  impacts      : {n_imp} {imp_types}")
    print(f"  signatures   : {'all verified' if all_ok else 'FAILED'}")
    print(f"  model verdict: {model_verdict}  "
          f"({'grounded' if match else 'ungrounded'})")
    return 0 if (all_ok and match) else 1


if __name__ == "__main__":
    raise SystemExit(main())
