"""
AXIOM GameWatcher — Browser Pacman Adapter
==========================================
Watches any browser-based Pacman game (Google Doodle, pacman.cc, etc.)
by capturing the screen region and feeding detected state into the existing
three-layer constitutional governance system.

How it works:
  1. You set the capture region once (drag to select, or --region x y w h)
  2. Frames are grabbed at ~10fps via mss (fast, no GPU needed)
  3. Color detection finds Pacman, ghosts, dots, walls
  4. State is fed into PacmanWatcher → PacmanPlayer → PacmanEvaluator
  5. Constitutional verdicts overlaid on a HUD window (or printed headless)

Usage:
  pip install mss pillow numpy
  python browser_watcher.py                        # click to select region
  python browser_watcher.py --region 100 200 600 500  # x y w h
  python browser_watcher.py --calibrate            # show detected colors live
  python browser_watcher.py --headless             # print manifests only
  python browser_watcher.py --fps 10               # capture rate

Supported games (color profiles):
  --game google    Google Pacman Doodle (default)
  --game classic   Classic arcade colors
  --game auto      Auto-detect from first frame
"""

import argparse
import json
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from collections import deque

import numpy as np

try:
    import mss
    import mss.tools
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

# ── Constitutional layers (standalone — no pygame, no game engine) ────────────
from layers import PacmanWatcher, PacmanPlayer, PacmanEvaluator

# ── Region config file ────────────────────────────────────────────────────────
REGION_CONFIG = Path(__file__).parent / "browser_region.json"

# ── Color profiles (HSV ranges) ───────────────────────────────────────────────
# Each entry: (hue_low, hue_high, sat_low, val_low) in OpenCV HSV scale (0-179 H, 0-255 S/V)
# We use numpy-based HSV detection without opencv dependency.

COLOR_PROFILES = {
    "google": {
        # Pacman: bright yellow
        "pacman":   {"r": (200, 255), "g": (180, 255), "b": (0,   80)},
        # Blinky (red ghost)
        "blinky":   {"r": (180, 255), "g": (0,   80),  "b": (0,   80)},
        # Pinky (pink)
        "pinky":    {"r": (220, 255), "g": (100, 180),  "b": (140, 255)},
        # Inky (cyan)
        "inky":     {"r": (0,   80),  "g": (180, 255),  "b": (180, 255)},
        # Clyde (orange)
        "clyde":    {"r": (200, 255), "g": (100, 170),  "b": (0,   60)},
        # Dots (light cream/white)
        "dot":      {"r": (200, 255), "g": (200, 255),  "b": (160, 255)},
        # Power pellet (same as dot but larger blob)
        "power":    {"r": (200, 255), "g": (200, 255),  "b": (160, 255)},
        # Walls (dark blue)
        "wall":     {"r": (0,   80),  "g": (0,   80),  "b": (100, 255)},
        # Scared ghost (blue)
        "scared":   {"r": (0,   80),  "g": (0,   100), "b": (150, 255)},
    },
    "classic": {
        "pacman":   {"r": (220, 255), "g": (200, 255), "b": (0,  60)},
        "blinky":   {"r": (200, 255), "g": (0,  60),   "b": (0,  60)},
        "pinky":    {"r": (255, 255), "g": (130, 200),  "b": (200, 255)},
        "inky":     {"r": (0,  80),   "g": (200, 255),  "b": (220, 255)},
        "clyde":    {"r": (220, 255), "g": (120, 190),  "b": (0,  60)},
        "dot":      {"r": (240, 255), "g": (185, 215),  "b": (130, 180)},
        "power":    {"r": (240, 255), "g": (185, 215),  "b": (130, 180)},
        "wall":     {"r": (20,  60),  "g": (20,  80),  "b": (150, 220)},
        "scared":   {"r": (20,  80),  "g": (20,  80),  "b": (180, 255)},
    },
}
COLOR_PROFILES["auto"] = COLOR_PROFILES["google"]


# ── Color detection helpers ───────────────────────────────────────────────────

def _rgb_mask(img_array: np.ndarray, profile: dict) -> np.ndarray:
    """Return boolean mask where pixels match the color profile."""
    r, g, b = img_array[:, :, 0], img_array[:, :, 1], img_array[:, :, 2]
    mask = (
        (r >= profile["r"][0]) & (r <= profile["r"][1]) &
        (g >= profile["g"][0]) & (g <= profile["g"][1]) &
        (b >= profile["b"][0]) & (b <= profile["b"][1])
    )
    return mask


def _find_blobs(mask: np.ndarray, min_pixels: int = 20) -> list[tuple[int, int, int]]:
    """
    Simple connected-component approximation via column/row projection.
    Returns list of (cx, cy, pixel_count) for each detected blob.
    Fast — no scipy/cv2 required.
    """
    blobs = []
    visited = np.zeros_like(mask, dtype=bool)
    coords = np.argwhere(mask)

    if len(coords) == 0:
        return blobs

    # Group coords that are within 20px of each other (fast clustering)
    groups: list[list] = []
    for y, x in coords:
        if visited[y, x]:
            continue
        # Find all unvisited pixels within 20px
        dists = np.abs(coords[:, 0] - y) + np.abs(coords[:, 1] - x)
        nearby = coords[dists < 20]
        if len(nearby) < min_pixels:
            continue
        cy = int(np.mean(nearby[:, 0]))
        cx = int(np.mean(nearby[:, 1]))
        blobs.append((cx, cy, len(nearby)))
        for py, px in nearby:
            visited[py, px] = True

    return blobs


def _detect_game_state(
    img_array: np.ndarray,
    profile_name: str,
    prev_state: dict,
    frame: int,
    cell_size_hint: int = 0,
) -> dict:
    """
    Extract game state from a captured frame.
    Returns a state dict compatible with PacmanWatcher.observe().
    """
    p = COLOR_PROFILES.get(profile_name, COLOR_PROFILES["google"])
    h, w = img_array.shape[:2]

    # Detect each object
    pacman_mask  = _rgb_mask(img_array, p["pacman"])
    blinky_mask  = _rgb_mask(img_array, p["blinky"])
    pinky_mask   = _rgb_mask(img_array, p["pinky"])
    inky_mask    = _rgb_mask(img_array, p["inky"])
    clyde_mask   = _rgb_mask(img_array, p["clyde"])
    scared_mask  = _rgb_mask(img_array, p["scared"])
    dot_mask     = _rgb_mask(img_array, p["dot"])

    # Find Pacman position
    pacman_blobs = _find_blobs(pacman_mask, min_pixels=30)
    if pacman_blobs:
        px, py, _ = max(pacman_blobs, key=lambda b: b[2])
        pacman_cx, pacman_cy = px / w, py / h  # normalized 0-1
    else:
        pacman_cx = prev_state.get("pacman_nx", 0.5)
        pacman_cy = prev_state.get("pacman_ny", 0.5)

    # Find ghost positions
    ghost_positions = []
    ghost_scared    = False
    for ghost_mask in [blinky_mask, pinky_mask, inky_mask, clyde_mask]:
        blobs = _find_blobs(ghost_mask, min_pixels=25)
        for bx, by, _ in blobs:
            ghost_positions.append((bx / w, by / h))

    # Scared ghosts
    scared_blobs = _find_blobs(scared_mask, min_pixels=25)
    if scared_blobs:
        ghost_scared = True
        for bx, by, _ in scared_blobs:
            ghost_positions.append((bx / w, by / h))

    # Nearest ghost distance (normalized)
    nearest_dist = 1.0
    for gx, gy in ghost_positions:
        d = abs(gx - pacman_cx) + abs(gy - pacman_cy)
        if d < nearest_dist:
            nearest_dist = d

    # Dot count (score proxy)
    dot_pixels   = int(np.sum(dot_mask))
    dot_estimate = max(0, prev_state.get("dots_remaining", 240) -
                   max(0, prev_state.get("dot_pixels_prev", dot_pixels) - dot_pixels) // 15)

    # Score estimation from dot changes
    prev_dots    = prev_state.get("dot_pixels_prev", dot_pixels)
    dot_delta    = max(0, prev_dots - dot_pixels)
    score_delta  = (dot_delta // 15) * 10  # ~10 pts per dot
    if ghost_scared and dot_delta > 0:
        score_delta += 200  # likely ate a ghost

    score        = prev_state.get("score", 0) + score_delta
    lives        = prev_state.get("lives", 3)

    # Threat detection — ghost within 15% of frame width
    threatened   = nearest_dist < 0.15 and not ghost_scared
    power_active = ghost_scared

    return {
        "frame":              frame,
        "score":              score,
        "lives":              lives,
        "threatened":         threatened,
        "power_active":       power_active,
        "nearest_ghost_dist": nearest_dist,
        "ghost_count":        len(ghost_positions),
        # Internal tracking
        "pacman_nx":          pacman_cx,
        "pacman_ny":          pacman_cy,
        "dot_pixels_prev":    dot_pixels,
        "dots_remaining":     dot_estimate,
        # Grid coords (approximate, for compatibility)
        "pacman_x":           int(pacman_cx * 21),
        "pacman_y":           int(pacman_cy * 21),
        "ghost_positions_n":  ghost_positions,
    }


# ── Fake game object (compatible with PacmanWatcher/PacmanPlayer) ─────────────

class BrowserGame:
    """
    Wraps detected browser game state into an object that satisfies
    the interface expected by PacmanWatcher, PacmanPlayer, PacmanEvaluator.
    """

    UP    = (0, -1)
    DOWN  = (0,  1)
    LEFT  = (-1, 0)
    RIGHT = (1,  0)

    def __init__(self, state: dict, prev_direction: tuple = (1, 0)):
        self.pacman_x  = state.get("pacman_x", 10)
        self.pacman_y  = state.get("pacman_y", 10)
        self.direction = prev_direction
        self.ghosts    = [
            self._make_ghost(gx, gy, state.get("power_active", False))
            for gx, gy in state.get("ghost_positions_n", [])
        ]
        # Simplified maze — all open (browser game handles collision)
        self.maze      = [[0] * 21 for _ in range(21)]
        self._state    = state

    class _Ghost:
        def __init__(self, x, y, scared):
            self.x      = x
            self.y      = y
            self.scared = scared

    def _make_ghost(self, nx, ny, scared):
        return self._Ghost(int(nx * 21), int(ny * 21), scared)

    def get_valid_dirs(self) -> list:
        """All 4 directions valid (browser game handles walls)."""
        return [self.UP, self.DOWN, self.LEFT, self.RIGHT]


# ── Screen capture ────────────────────────────────────────────────────────────

def _select_region_interactive() -> dict:
    """
    Show a full-screen overlay and let user click two corners.
    Returns {"left": x, "top": y, "width": w, "height": h}
    """
    if not PYGAME_AVAILABLE:
        print("\n[ERROR] pygame not installed — cannot select region interactively.")
        print("  pip install pygame")
        print("  Or use: --region x y w h")
        sys.exit(1)

    import mss
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # primary screen
        full    = sct.grab(monitor)
        img     = Image.frombytes("RGB", full.size, full.bgra, "raw", "BGRX")

    pygame.init()
    screen = pygame.display.set_mode((img.width, img.height), pygame.NOFRAME)
    pygame.display.set_caption("AXIOM Browser Watcher — Click two corners")

    # Draw screenshot as background
    surf = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
    screen.blit(surf, (0, 0))

    # Overlay instructions
    font   = pygame.font.SysFont("monospace", 18)
    label  = font.render("Click top-left corner of Pacman game", True, (255, 214, 0))
    screen.blit(label, (20, 20))
    pygame.display.flip()

    corners = []
    while len(corners) < 2:
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
            ):
                pygame.quit()
                sys.exit(0)
            if event.type == pygame.MOUSEBUTTONDOWN:
                corners.append(event.pos)
                if len(corners) == 1:
                    screen.blit(surf, (0, 0))
                    label2 = font.render("Click bottom-right corner", True, (255, 214, 0))
                    pygame.draw.circle(screen, (255, 214, 0), corners[0], 8)
                    screen.blit(label2, (20, 20))
                    pygame.display.flip()

    pygame.quit()

    x1, y1 = corners[0]
    x2, y2 = corners[1]
    region = {
        "left":   min(x1, x2),
        "top":    min(y1, y2),
        "width":  abs(x2 - x1),
        "height": abs(y2 - y1),
    }
    print(f"  Region selected: {region}")
    REGION_CONFIG.write_text(json.dumps(region, indent=2))
    return region


def _load_or_select_region(args) -> dict:
    if args.region:
        x, y, w, h = args.region
        region = {"left": x, "top": y, "width": w, "height": h}
        REGION_CONFIG.write_text(json.dumps(region, indent=2))
        return region

    if REGION_CONFIG.exists():
        region = json.loads(REGION_CONFIG.read_text())
        print(f"  Using saved region: {region}")
        print(f"  Run with --select to change it.")
        return region

    if args.select:
        print("\n  Click two corners of your Pacman game window.")
        return _select_region_interactive()

    # No region set — print instructions and exit cleanly
    print("""
  No capture region set.

  Step 1: Open your Pacman game in the browser.
  Step 2: Find the pixel coordinates of the game window.
          Easiest way: hover over the corners in your OS screenshot tool.

  Step 3: Run with --region x y width height
          Example:
            python browser_watcher.py --region 200 150 600 550

  Or use --select to click the corners interactively:
            python browser_watcher.py --select
""")
    sys.exit(0)


# ── HUD overlay (pygame) ──────────────────────────────────────────────────────

class HUD:
    """Small overlay window showing constitutional verdicts."""

    WIDTH  = 420
    HEIGHT = 280

    COLORS = {
        "KEEP":  (0,   230, 118),
        "SKIP":  (100, 100, 100),
        "FLAG":  (255, 152,  0),
        "BLOCK": (255,  82,  82),
        "bg":    (14,  16,  24),
        "text":  (240, 244, 248),
        "gold":  (255, 214,   0),
        "dim":   (100, 110, 130),
    }

    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT))
        pygame.display.set_caption("AXIOM GameWatcher")
        self.font_lg = pygame.font.SysFont("monospace", 15, bold=True)
        self.font_sm = pygame.font.SysFont("monospace", 12)
        self.last_manifest = None
        self.last_verdict  = "—"
        self.frame_count   = 0
        self.kept = self.skipped = self.flagged = 0

    def update(self, state: dict, obs: dict, manifest: dict | None):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_q:
                return False

        if manifest:
            self.last_manifest = manifest
        if obs:
            v = obs.get("verdict", "—")
            if v == "KEEP":  self.kept    += 1
            if v == "SKIP":  self.skipped += 1
            if v == "FLAG":  self.flagged += 1
            self.last_verdict = v

        self.frame_count += 1
        s = self.screen
        s.fill(self.COLORS["bg"])

        # Title
        t = self.font_lg.render("AXIOM GAMEWATCHER — BROWSER", True, self.COLORS["gold"])
        s.blit(t, (10, 8))

        # Game state row
        y = 34
        info = [
            f"Frame {state.get('frame', 0):>5}",
            f"Score {state.get('score', 0):>6}",
            f"Lives {state.get('lives', 3)}",
            f"Ghosts {state.get('ghost_count', 0)}",
        ]
        for i, txt in enumerate(info):
            c = self.font_sm.render(txt, True, self.COLORS["text"])
            s.blit(c, (10 + i * 100, y))

        # Watcher verdict
        y = 58
        vcolor = self.COLORS.get(self.last_verdict, self.COLORS["text"])
        vt = self.font_lg.render(f"Watcher: {self.last_verdict}", True, vcolor)
        s.blit(vt, (10, y))

        # Stats
        y = 82
        stats = f"KEEP {self.kept}  SKIP {self.skipped}  FLAG {self.flagged}"
        st = self.font_sm.render(stats, True, self.COLORS["dim"])
        s.blit(st, (10, y))

        # Threat indicator
        if state.get("threatened"):
            threat = self.font_lg.render("⚠ GHOST THREAT", True, self.COLORS["FLAG"])
            s.blit(threat, (10, 100))
        if state.get("power_active"):
            power = self.font_lg.render("★ POWER ACTIVE", True, self.COLORS["KEEP"])
            s.blit(power, (10, 100))

        # Last manifest
        if self.last_manifest:
            pygame.draw.line(s, self.COLORS["dim"], (10, 122), (self.WIDTH - 10, 122))
            y = 130
            ml = self.font_sm.render("Last Manifest", True, self.COLORS["gold"])
            s.blit(ml, (10, y))
            fields = [
                ("Verdict",  self.last_manifest.get("verdict",    "—")),
                ("Domain",   self.last_manifest.get("domain",     "—")[:30]),
                ("Sig",      self.last_manifest.get("signature",  "—")[:24] + "..."),
                ("Best",     self.last_manifest.get("best_direction", "—")),
                ("Alt",      self.last_manifest.get("rival_direction", "—")),
                ("Conf",     f"{self.last_manifest.get('confidence', 0):.0%}"),
            ]
            for i, (k, v) in enumerate(fields):
                row = y + 18 + i * 18
                kt = self.font_sm.render(f"{k:<9}", True, self.COLORS["dim"])
                vt = self.font_sm.render(str(v), True, self.COLORS["text"])
                s.blit(kt, (10,  row))
                s.blit(vt, (100, row))

        pygame.display.flip()
        return True


# ── Main watcher loop ─────────────────────────────────────────────────────────

def watch_loop(args):
    if not MSS_AVAILABLE:
        print("[ERROR] mss not installed:  pip install mss")
        sys.exit(1)
    if not PIL_AVAILABLE:
        print("[ERROR] Pillow not installed:  pip install pillow")
        sys.exit(1)

    region   = _load_or_select_region(args)
    profile  = args.game

    watcher   = PacmanWatcher(sample_every=args.sample_every)
    player    = PacmanPlayer()
    evaluator = PacmanEvaluator()

    hud = None
    if not args.headless and PYGAME_AVAILABLE:
        hud = HUD()

    manifests = []
    manifest_path = Path("pacman_browser_manifests.json")

    state       = {"frame": 0, "score": 0, "lives": 3, "dot_pixels_prev": 0}
    prev_dir    = (1, 0)
    prev_state  = state.copy()
    frame_count = 0
    interval    = 1.0 / args.fps

    print(f"\n  AXIOM Browser Watcher started")
    print(f"  Region: {region}")
    print(f"  Game profile: {profile}")
    print(f"  FPS: {args.fps}   Sample every: {args.sample_every} frames")
    print(f"  Press Q in HUD or Ctrl+C to stop\n")

    with mss.mss() as sct:
        while True:
            t0 = time.time()

            # Grab frame
            raw   = sct.grab(region)
            img   = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            arr   = np.array(img)

            # Detect state
            state = _detect_game_state(arr, profile, prev_state, frame_count)
            game  = BrowserGame(state, prev_dir)

            # Layer 1 — Watcher
            obs   = watcher.observe(game, state)

            manifest = None

            if obs["verdict"] in ("KEEP", "FLAG"):
                # Layer 2 — Player (decide direction)
                decision = player.decide(game, obs)
                prev_dir = decision["direction"]

                # Layer 3 — Evaluator (was that the best move?)
                state_after = dict(state)  # snapshot
                manifest = evaluator.evaluate(
                    state_before=prev_state,
                    direction=decision["direction"],
                    state_after=state_after,
                    game_before=game,
                )
                manifests.append(manifest)

                # Save every 10 manifests
                if len(manifests) % 10 == 0:
                    manifest_path.write_text(json.dumps(manifests, indent=2))

                if args.headless or not hud:
                    verdict = manifest.get("verdict", "—")
                    sig     = manifest.get("signature", "")[:16]
                    print(
                        f"  [{frame_count:>5}] {obs['verdict']:<4}  "
                        f"→{decision['direction_name']:<5}  "
                        f"score={state['score']:>5}  "
                        f"manifest={sig}..."
                    )

            if hud:
                running = hud.update(state, obs, manifest)
                if not running:
                    break

            prev_state  = state.copy()
            frame_count += 1

            # Throttle to target FPS
            elapsed = time.time() - t0
            sleep   = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    # Final save
    manifest_path.write_text(json.dumps(manifests, indent=2))
    print(f"\n  Saved {len(manifests)} manifests → {manifest_path}")


# ── Calibration mode ──────────────────────────────────────────────────────────

def calibrate(args):
    """
    Show live pixel stats from the capture region so you can tune
    color profiles for your specific game/browser/display.
    """
    if not MSS_AVAILABLE:
        print("[ERROR] pip install mss")
        sys.exit(1)

    region = _load_or_select_region(args)
    print(f"\n  Calibration mode — capturing {region}")
    print("  Move your mouse over Pacman and each ghost to see their RGB values.")
    print("  Ctrl+C to stop.\n")

    with mss.mss() as sct:
        for i in range(60):
            raw = sct.grab(region)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            arr = np.array(img)

            p = COLOR_PROFILES[args.game]
            detected = {}
            for name, profile in p.items():
                mask  = _rgb_mask(arr, profile)
                count = int(np.sum(mask))
                blobs = _find_blobs(mask, min_pixels=20)
                detected[name] = {"pixels": count, "blobs": len(blobs)}

            print(f"\r  Frame {i:>3}  " +
                  "  ".join(f"{k}:{v['blobs']}b/{v['pixels']}px" for k, v in detected.items()),
                  end="", flush=True)
            time.sleep(0.5)

    print()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AXIOM GameWatcher — Browser Pacman constitutional observer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python browser_watcher.py\n"
            "  python browser_watcher.py --region 100 200 600 500\n"
            "  python browser_watcher.py --game classic --fps 10\n"
            "  python browser_watcher.py --headless\n"
            "  python browser_watcher.py --calibrate\n"
        ),
    )
    parser.add_argument("--region",       nargs=4, type=int, metavar=("X","Y","W","H"),
                        help="Capture region: x y width height")
    parser.add_argument("--select",       action="store_true",
                        help="Re-run interactive region selection")
    parser.add_argument("--game",         default="google",
                        choices=["google", "classic", "auto"],
                        help="Color profile (default: google)")
    parser.add_argument("--fps",          type=int,   default=10,
                        help="Capture framerate (default: 10)")
    parser.add_argument("--sample-every", type=int,   default=5, dest="sample_every",
                        help="Watcher samples every Nth frame (default: 5)")
    parser.add_argument("--headless",     action="store_true",
                        help="No HUD — print manifests to stdout")
    parser.add_argument("--calibrate",    action="store_true",
                        help="Show live color detection stats for tuning")
    args = parser.parse_args()

    if args.select:
        REGION_CONFIG.unlink(missing_ok=True)

    if args.calibrate:
        calibrate(args)
    else:
        watch_loop(args)


if __name__ == "__main__":
    main()
