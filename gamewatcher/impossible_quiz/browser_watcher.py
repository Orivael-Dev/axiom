"""
AXIOM GameWatcher — The Impossible Quiz (Browser)
==================================================
Watches any browser-based version of The Impossible Quiz by capturing the
screen region and feeding it through the three-layer constitutional governance
system: QuizWatcher → QuizPlayer → QuizEvaluator.

SmolVLM reads each screenshot to extract the question and answers.
The player reasons about the best answer (with QRF for low-confidence turns).
Every decision is HMAC-signed and logged to ~/.axiom/quiz_manifests.jsonl.

How it works:
  1. Set the capture region once (drag to select, or --region x y w h)
  2. Frames are grabbed at ~1fps via mss (the quiz is turn-based, no need for speed)
  3. SmolVLM extracts question + answers from each screenshot
  4. QuizPlayer picks the best answer (QRF triggers when confidence < 0.6)
  5. pyautogui clicks the chosen button
  6. Signed manifest appended to ~/.axiom/quiz_manifests.jsonl

Setup:
  pip install -e ".[games]"
  pip install transformers torch accelerate

  Open The Impossible Quiz in a browser — search "impossible quiz ruffle" for a
  browser-playable version via the Ruffle Flash emulator.

  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

  # First run: dry-run only (downloads SmolVLM ~500MB, no clicks)
  python browser_watcher.py --dry-run

  # Live run: select game window region when prompted, then let it play
  python browser_watcher.py --delay 3.0

Usage:
  python browser_watcher.py                          # click-to-select region
  python browser_watcher.py --region x y w h         # set capture region directly
  python browser_watcher.py --dry-run                # capture + reason, no clicks
  python browser_watcher.py --headless               # print manifests only
  python browser_watcher.py --delay 3.0              # seconds between turns (default 3)
  python browser_watcher.py --model smolvlm-256m     # smaller/faster model
  python browser_watcher.py --device cuda            # GPU if available
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import mss
    import mss.tools
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyautogui
    pyautogui.FAILSAFE = True   # move mouse to top-left corner to abort
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

from layers import QuizWatcher, QuizPlayer, QuizEvaluator
from smolvlm_reader import SmolVLMReader

REGION_CONFIG = Path(__file__).parent / "browser_region.json"

# ── Region helpers ────────────────────────────────────────────────────────────

def _load_region() -> dict | None:
    if REGION_CONFIG.exists():
        try:
            return json.loads(REGION_CONFIG.read_text())
        except Exception:
            pass
    return None


def _save_region(data: dict) -> None:
    REGION_CONFIG.write_text(json.dumps(data, indent=2))


def _select_region_interactive() -> dict:
    """Ask the user to enter the capture region manually."""
    print("\nNo region configured. Enter the game window region.")
    print("Open your browser dev tools → hover over the game canvas to find pixel coords.")
    print("Format: x y width height  (e.g. 100 200 640 480)")
    while True:
        raw = input("Region (x y w h): ").strip()
        parts = raw.split()
        if len(parts) == 4:
            try:
                x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                return {"left": x, "top": y, "width": w, "height": h}
            except ValueError:
                pass
        print("  → Please enter 4 integers: x y width height")


def _compute_button_coords(region: dict) -> list[list[int]]:
    """
    Compute the 4 answer-button centres from the capture region.
    The Impossible Quiz places buttons in a 2×2 grid in the bottom 50% of the canvas.
    Returns [[x,y],[x,y],[x,y],[x,y]] in screen coordinates (absolute pixels).
    """
    left, top, w, h = region["left"], region["top"], region["width"], region["height"]
    # Bottom half, divided into 2 cols × 2 rows
    col_w = w // 2
    row_h = h // 4     # bottom 50% = 2 rows of h/4 each
    btn_top_y = top + h // 2   # bottom half starts at midpoint
    coords = []
    for row in range(2):
        for col in range(2):
            cx = left + col * col_w + col_w // 2
            cy = btn_top_y + row * row_h + row_h // 2
            coords.append([cx, cy])
    return coords   # [A, B, C, D]


# ── Capture ───────────────────────────────────────────────────────────────────

def _capture_frame(sct, region: dict):
    """Capture one frame. Returns PIL.Image or None."""
    if not MSS_AVAILABLE or not PIL_AVAILABLE:
        return None
    monitor = {
        "left":   region["left"],
        "top":    region["top"],
        "width":  region["width"],
        "height": region["height"],
    }
    raw = sct.grab(monitor)
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    if not MSS_AVAILABLE:
        print("ERROR: mss not installed — run: pip install mss", file=sys.stderr)
        sys.exit(1)
    if not PIL_AVAILABLE:
        print("ERROR: Pillow not installed — run: pip install Pillow", file=sys.stderr)
        sys.exit(1)

    # ── Region setup ──────────────────────────────────────────────────────────
    saved = _load_region()

    if args.region:
        x, y, w, h = args.region
        region = {"left": x, "top": y, "width": w, "height": h}
    elif saved and "left" in saved:
        region = {k: saved[k] for k in ("left", "top", "width", "height")}
        print(f"Using saved region: {region}")
    else:
        region = _select_region_interactive()

    button_coords = (
        saved.get("button_coords")
        if saved and "button_coords" in saved
        else _compute_button_coords(region)
    )

    _save_region({**region, "button_coords": button_coords})
    print(f"Region: {region}")
    print(f"Button coords (A/B/C/D): {button_coords}")

    # ── Model setup ───────────────────────────────────────────────────────────
    print(f"\nLoading SmolVLM ({args.model}) on {args.device} …")
    reader    = SmolVLMReader(model_id=args.model, device=args.device)
    watcher   = QuizWatcher()
    player    = QuizPlayer()
    evaluator = QuizEvaluator()
    print("Ready.\n")

    if args.dry_run:
        print("DRY-RUN mode — decisions will be printed but no clicks will be sent.\n")
    if args.headless:
        print("HEADLESS mode — printing manifests to stdout.\n")

    # ── Capture loop ──────────────────────────────────────────────────────────
    turn = 0
    with mss.mss() as sct:
        while True:
            frame = _capture_frame(sct, region)
            if frame is None:
                time.sleep(1.0)
                continue

            # Layer 1: observe
            state = watcher.observe(frame, reader)
            if state is None:
                time.sleep(0.5)
                continue

            turn += 1
            print(f"\n{'═'*60}")
            print(f"  Turn {turn} | Q{state.question_num} | Lives: {state.lives}")
            print(f"  Q: {state.question}")
            for i, ans in enumerate(state.answers):
                label = "ABCD"[i]
                print(f"  {label}) {ans}")

            # Layer 2: decide (+ QRF if confidence is low)
            decision = player.decide(state, reader)
            print(f"\n  → Choice: {decision.choice}  confidence={decision.confidence:.2f}"
                  f"  qrf={'yes' if decision.qrf_used else 'no'}")
            print(f"  Reasoning: {decision.reasoning[:120]}")
            if decision.qrf_used and decision.qrf_votes:
                print(f"  QRF votes: {decision.qrf_votes}")

            # Layer 3: evaluate + sign
            manifest = evaluator.evaluate(state, decision)
            if args.headless:
                print(json.dumps(manifest, indent=2))

            # Click answer
            if not args.dry_run and not args.headless:
                idx = decision.click_index
                if 0 <= idx < len(button_coords):
                    cx, cy = button_coords[idx]
                    if PYAUTOGUI_AVAILABLE:
                        pyautogui.click(cx, cy)
                        print(f"  Clicked ({cx},{cy}) [button {decision.choice}]")
                    else:
                        print(f"  Would click ({cx},{cy}) — pyautogui not installed")
                else:
                    print(f"  Click index {idx} out of range — skipping")

            time.sleep(args.delay)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AXIOM GameWatcher — The Impossible Quiz",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
        help="Capture region in screen pixels",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Capture and reason but do not click",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Print signed manifests to stdout, no visual output",
    )
    parser.add_argument(
        "--delay", type=float, default=3.0,
        help="Seconds to wait between turns (default: 3.0)",
    )
    parser.add_argument(
        "--model", default="smolvlm",
        choices=["smolvlm", "smolvlm-256m", "smolvlm-500m"],
        help="SmolVLM variant to use",
    )
    parser.add_argument(
        "--device", default="cpu",
        help="torch device (cpu / cuda / mps)",
    )
    args = parser.parse_args()

    try:
        run(args)
    except KeyboardInterrupt:
        print("\n\nStopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
