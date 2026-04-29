"""
AXIOM Jeopardy GameWatcher — Browser Screen Capture
====================================================
Watches any browser-based Jeopardy game via screen capture.
Reads clues using Claude Vision API, answers in terminal,
signs every decision as an HMAC-SHA256 manifest.

3-Layer constitutional pipeline:
  Layer 1 — JeopardyWatcher  : detects new clue on screen (color heuristic, no API)
  Layer 2 — JeopardyPlayer   : reads + answers clue via Claude Vision API
  Layer 3 — JeopardyEvaluator: evaluates answer quality, signs manifest

Works on Jetson Nano / Python 3.8 / ARM64.

Usage:
  # Step 1 — select region (open Jeopardy in browser first):
  python3 jeopardy/browser_watcher.py --select

  # Step 2 — watch game:
  python3 jeopardy/browser_watcher.py

  # Optional flags:
  python3 jeopardy/browser_watcher.py --model claude-haiku-4-5-20251001
  python3 jeopardy/browser_watcher.py --fps 2 --debug

Requirements:
  pip install mss Pillow numpy anthropic
  export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Optional

# ── Paths / constants ──────────────────────────────────────────────────────────
_HERE         = os.path.dirname(os.path.abspath(__file__))
REGION_FILE   = os.path.join(_HERE, "jeopardy_region.json")
MANIFEST_FILE = os.path.join(_HERE, "jeopardy_manifests.json")

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_FPS   = 2

# Box-draw chars as variables — Python 3.8 f-string safe
BOX_DOUBLE   = "\u2550" * 50  # ══════...
BOX_SINGLE   = "\u2500" * 50  # ──────...
BOX_TOP      = "\u2554" + "\u2550" * 48 + "\u2557"
BOX_BTM      = "\u255a" + "\u2550" * 48 + "\u255d"
BOX_MID      = "\u255f" + "\u2500" * 48 + "\u2562"


# ── Region helpers ─────────────────────────────────────────────────────────────

def _load_region():
    # type: () -> Optional[Dict]
    if not os.path.exists(REGION_FILE):
        return None
    try:
        with open(REGION_FILE) as f:
            return json.load(f)
    except (ValueError, IOError):
        return None


def _save_region(region):
    # type: (dict) -> None
    with open(REGION_FILE, "w") as f:
        json.dump(region, f)
    print("  Region saved to %s" % REGION_FILE)


def _select_region():
    # type: () -> dict
    """Interactively select screen region. Saves to REGION_FILE."""
    print()
    print("  AXIOM Jeopardy GameWatcher — Region Selection")
    print("  " + BOX_DOUBLE)
    print("  1. Open your Jeopardy game in the browser.")
    print("  2. Position and resize the browser window as desired.")
    print("  3. Note the pixel coordinates (left, top, width, height).")
    print()
    print("  Tip: On Linux/X11 run 'xdotool getmouselocation' to find coords.")
    print("       On Windows right-click Desktop > Display Settings for resolution.")
    print()

    try:
        raw = input("  Enter region (left top width height): ").strip()
        parts = raw.split()
        if len(parts) != 4:
            print("  [error] Need exactly 4 values.")
            sys.exit(1)
        left, top, width, height = [int(p) for p in parts]
    except (ValueError, EOFError) as exc:
        print("  [error] Invalid input: %s" % exc)
        sys.exit(1)

    region = {"left": left, "top": top, "width": width, "height": height}
    _save_region(region)
    print()
    print("  Region set: left=%d top=%d width=%d height=%d" % (left, top, width, height))
    print("  Run without --select to start watching.")
    return region


# ── Manifest persistence ───────────────────────────────────────────────────────

def _load_manifests():
    # type: () -> list
    if not os.path.exists(MANIFEST_FILE):
        return []
    try:
        with open(MANIFEST_FILE) as f:
            return json.load(f)
    except (ValueError, IOError):
        return []


def _save_manifests(manifests):
    # type: (list) -> None
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifests, f, indent=2)


# ── Terminal output ────────────────────────────────────────────────────────────

def _print_clue_block(clue_n, manifest, player_decision, frame, debug=False):
    # type: (int, dict, dict, int, bool) -> None
    verdict    = manifest.get("classification", "?")
    confidence = manifest.get("confidence", 0.0)
    rival      = manifest.get("rival_move", "")
    mid_id     = manifest.get("manifest_id", "?")
    sig        = manifest.get("signature", "")[:40]
    category   = manifest.get("category", "?")
    amount     = manifest.get("amount", "?")
    clue_text  = manifest.get("clue_text", "?")
    answer     = manifest.get("answer", "?")
    reasoning  = player_decision.get("reasoning", "?")
    latency_ms = player_decision.get("latency_ms", 0)

    print()
    print(BOX_TOP)
    print("  CLUE #%d   [frame %d]  latency %dms" % (clue_n, frame, latency_ms))
    print("  Category : %s" % category)
    print("  Amount   : %s" % amount)
    print("  Clue     : %s" % clue_text)
    print(BOX_MID)
    print("  Answer   : %s" % answer)
    print("  Reasoning: %s" % reasoning)
    print("  Confidence: %.1f%%" % (confidence * 100))
    print("  Verdict  : %s" % verdict)
    if rival:
        print("  Rival    : %s" % rival)
    print("  Manifest : %s  sig=%s" % (mid_id, sig))
    print(BOX_BTM)

    if debug:
        print()
        print("  [debug] raw_response: %s" % player_decision.get("raw_response", "")[:300])
        print()


# ── Main watch loop ────────────────────────────────────────────────────────────

def watch_loop(region, model, fps, output_path, debug):
    # type: (dict, str, float, str, bool) -> None
    try:
        import mss
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        print("[error] Missing dependency: %s" % exc)
        print("  pip install mss Pillow numpy")
        sys.exit(1)

    # Import layers from same directory
    sys.path.insert(0, _HERE)
    try:
        from layers import JeopardyWatcher, JeopardyPlayer, JeopardyEvaluator
    except ImportError as exc:
        print("[error] Could not import layers: %s" % exc)
        sys.exit(1)

    watcher   = JeopardyWatcher()
    player    = JeopardyPlayer()
    evaluator = JeopardyEvaluator()

    manifests = _load_manifests()
    interval  = 1.0 / fps
    frame     = 0
    clue_n    = len(manifests)

    print()
    print("  AXIOM Jeopardy GameWatcher v1.0")
    print("  " + BOX_DOUBLE)
    print("  Region  : left=%d top=%d width=%d height=%d" % (
        region["left"], region["top"], region["width"], region["height"]
    ))
    print("  Model   : %s" % model)
    print("  FPS     : %.1f" % fps)
    print("  Manifests: %s" % output_path)
    print("  " + BOX_DOUBLE)
    print("  Watching... (Ctrl+C to stop)")
    print()

    state = {
        "frame":        0,
        "clue_active":  False,
        "clue_hash":    "",
    }

    try:
        with mss.mss() as sct:
            while True:
                t0 = time.time()
                frame += 1
                state["frame"] = frame

                # Capture frame
                raw_img = sct.grab(region)
                img = Image.frombytes("RGB", raw_img.size, raw_img.bgra, "raw", "BGRX")
                arr = __import__("numpy").array(img)

                # Layer 1 — Watcher
                observation = watcher.observe(arr, state)

                if debug and frame % 30 == 0:
                    stats = observation.get("stats", {})
                    print("  [frame %4d]  verdict=%-6s  kept=%d  skipped=%d" % (
                        frame,
                        observation.get("verdict", "?"),
                        stats.get("kept", 0),
                        stats.get("skipped", 0),
                    ))

                if observation["verdict"] == "SKIP":
                    elapsed = time.time() - t0
                    wait    = interval - elapsed
                    if wait > 0:
                        time.sleep(wait)
                    continue

                # Layer 2 — Player (Claude Vision API)
                print("  [frame %d] New clue detected — calling Claude Vision..." % frame)
                decision = player.decide(arr, observation, model)

                if not decision.get("answer"):
                    print("  [frame %d] No answer extracted — skipping." % frame)
                    elapsed = time.time() - t0
                    wait    = interval - elapsed
                    if wait > 0:
                        time.sleep(wait)
                    continue

                # Layer 3 — Evaluator (signed manifest)
                manifest = evaluator.evaluate(decision, observation)

                clue_n += 1
                manifests.append(manifest)
                _save_manifests(manifests)

                _print_clue_block(clue_n, manifest, decision, frame, debug)

                elapsed = time.time() - t0
                wait    = interval - elapsed
                if wait > 0:
                    time.sleep(wait)

    except KeyboardInterrupt:
        print()
        print("  Stopped. %d clues recorded → %s" % (clue_n, output_path))


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    # type: () -> None
    parser = argparse.ArgumentParser(
        description="AXIOM Jeopardy GameWatcher — constitutional 3-layer browser watcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 jeopardy/browser_watcher.py --select\n"
            "  python3 jeopardy/browser_watcher.py\n"
            "  python3 jeopardy/browser_watcher.py --model claude-haiku-4-5-20251001 --debug\n"
        ),
    )
    parser.add_argument(
        "--select",
        action="store_true",
        help="Interactively select the screen region to watch",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Claude model ID (default: %s)" % DEFAULT_MODEL,
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help="Capture rate (default: %d fps)" % DEFAULT_FPS,
    )
    parser.add_argument(
        "--output",
        default=MANIFEST_FILE,
        help="Output manifest JSON path (default: jeopardy_manifests.json)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print raw API responses and per-frame detection stats",
    )
    args = parser.parse_args()

    # Check API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[error] ANTHROPIC_API_KEY not set.")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    # Check anthropic package
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("[error] anthropic package not installed.")
        print("  pip install anthropic")
        sys.exit(1)

    # Region selection
    if args.select:
        _select_region()
        return

    region = _load_region()
    if region is None:
        print()
        print("  No region configured. Run with --select first:")
        print("  python3 jeopardy/browser_watcher.py --select")
        print()
        sys.exit(1)

    watch_loop(region, args.model, args.fps, args.output, args.debug)


if __name__ == "__main__":
    main()
