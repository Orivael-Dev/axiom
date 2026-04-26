#!/usr/bin/env python3
"""
AXIOM GameWatcher — Browser Chess Adapter
==========================================
Watches any browser-based chess game (Lichess, Chess.com, etc.)
by capturing the screen and detecting moves via highlight changes.
Every detected move produces a signed AXIOM constitutional manifest.

How it works:
  1. Set capture region once (--region x y w h  or  --select)
  2. Frames captured at ~4fps — looks for square highlights (last-move indicator)
  3. When highlight changes: new move detected → applied to python-chess board
  4. Built-in engine evaluates the move → signed manifest produced
  5. HUD shows live evaluation + last manifest

Usage:
  pip install mss pillow numpy chess pygame
  python browser_watcher.py --region 100 200 600 600
  python browser_watcher.py --select
  python browser_watcher.py --region 100 200 600 600 --play-as black
  python browser_watcher.py --region 100 200 600 600 --headless
  python browser_watcher.py --calibrate   # tune highlight colors

Site profiles (--site):
  lichess   Lichess.org default brown theme   (default)
  chesscom  Chess.com    green theme
  custom    Use --highlight-rgb r g b tolerance
"""

import argparse
import json
import os
import sys
import time
import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

try:
    import chess
    import chess.pgn
    CHESS_AVAILABLE = True
except ImportError:
    CHESS_AVAILABLE = False

# ── Import constitutional evaluation from gamewatcher ────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from gamewatcher import build_manifest, select_move_engine, CHESS_SYSTEM, VERSION

SIGNING_KEY  = b"axiom-gamewatcher-v1"
REGION_CONFIG = Path(__file__).parent / "browser_region.json"

# ── Site highlight color profiles ─────────────────────────────────────────────
# Colors that chess sites use to highlight the last-move squares

SITE_PROFILES = {
    "lichess": [
        {"r": (190, 220), "g": (200, 230), "b": (90,  130)},  # yellow-green #cdd16e
        {"r": (170, 210), "g": (190, 225), "b": (80,  120)},  # variant
    ],
    "chesscom": [
        {"r": (230, 255), "g": (230, 255), "b": (80,  120)},  # yellow #f6f669
        {"r": (160, 210), "g": (190, 220), "b": (20,   70)},  # green  #baca2b
    ],
    "custom": [],  # filled from --highlight-rgb
}


# ── Color helpers ─────────────────────────────────────────────────────────────

def _color_mask(arr: np.ndarray, profile: dict) -> np.ndarray:
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return (
        (r >= profile["r"][0]) & (r <= profile["r"][1]) &
        (g >= profile["g"][0]) & (g <= profile["g"][1]) &
        (b >= profile["b"][0]) & (b <= profile["b"][1])
    )


def _highlight_mask(arr: np.ndarray, profiles: list) -> np.ndarray:
    mask = np.zeros(arr.shape[:2], dtype=bool)
    for p in profiles:
        mask |= _color_mask(arr, p)
    return mask


def _find_highlighted_squares(
    arr: np.ndarray,
    profiles: list,
    board_w: int,
    board_h: int,
    flipped: bool = False,
) -> list[str]:
    """
    Divide the capture region into an 8x8 grid.
    Return algebraic names of cells where highlight pixels are dominant.
    """
    mask     = _highlight_mask(arr, profiles)
    cell_w   = board_w / 8
    cell_h   = board_h / 8
    squares  = []

    for row in range(8):
        for col in range(8):
            x0 = int(col * cell_w)
            x1 = int((col + 1) * cell_w)
            y0 = int(row * cell_h)
            y1 = int((row + 1) * cell_h)
            cell_mask  = mask[y0:y1, x0:x1]
            pct        = cell_mask.sum() / ((x1 - x0) * (y1 - y0))
            if pct > 0.10:  # >10% of cell is highlight color
                file_ = col if not flipped else 7 - col
                rank  = 7 - row if not flipped else row
                sq    = chess.square_name(chess.square(file_, rank))
                squares.append(sq)

    return squares


# ── Move inference ────────────────────────────────────────────────────────────

def _infer_move(
    board: chess.Board,
    highlighted: list[str],
    prev_highlighted: list[str],
) -> "chess.Move | None":
    """
    Given old and new highlighted squares, find the legal move that
    matches the from→to transition.
    Returns None if ambiguous or not a legal move.
    """
    if len(highlighted) < 2:
        return None

    # Try all pairs in highlighted squares as from→to
    for sq_from in highlighted:
        for sq_to in highlighted:
            if sq_from == sq_to:
                continue
            try:
                move = board.parse_uci(sq_from + sq_to)
                if move in board.legal_moves:
                    return move
            except chess.InvalidMoveError:
                pass

    # Try with promotion (queen default)
    for sq_from in highlighted:
        for sq_to in highlighted:
            if sq_from == sq_to:
                continue
            for promo in [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT]:
                try:
                    move = board.parse_uci(sq_from + sq_to + chess.piece_symbol(promo))
                    if move in board.legal_moves:
                        return move
                except chess.InvalidMoveError:
                    pass

    return None


# ── Region helpers ────────────────────────────────────────────────────────────

def _select_region_interactive() -> dict:
    if not PYGAME_AVAILABLE:
        print("[ERROR] pip install pygame  (needed for interactive selection)")
        sys.exit(1)
    if not MSS_AVAILABLE:
        print("[ERROR] pip install mss")
        sys.exit(1)

    with mss.mss() as sct:
        monitor = sct.monitors[1]
        raw     = sct.grab(monitor)
        img     = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    pygame.init()
    screen = pygame.display.set_mode((img.width, img.height), pygame.NOFRAME)
    pygame.display.set_caption("AXIOM Chess Watcher — click two corners")

    surf  = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
    font  = pygame.font.SysFont("monospace", 18)
    screen.blit(surf, (0, 0))
    lbl   = font.render("Click TOP-LEFT corner of chess board", True, (255, 214, 0))
    screen.blit(lbl, (20, 20))
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
                    pygame.draw.circle(screen, (255, 214, 0), corners[0], 8)
                    lbl2 = font.render("Click BOTTOM-RIGHT corner", True, (255, 214, 0))
                    screen.blit(lbl2, (20, 20))
                    pygame.display.flip()

    pygame.quit()
    x1, y1 = corners[0]
    x2, y2 = corners[1]
    region = {
        "left": min(x1, x2), "top": min(y1, y2),
        "width": abs(x2 - x1), "height": abs(y2 - y1),
    }
    REGION_CONFIG.write_text(json.dumps(region, indent=2))
    print(f"  Region saved: {region}")
    return region


def _load_region(args) -> dict:
    if args.region:
        x, y, w, h = args.region
        r = {"left": x, "top": y, "width": w, "height": h}
        REGION_CONFIG.write_text(json.dumps(r, indent=2))
        return r
    if REGION_CONFIG.exists():
        r = json.loads(REGION_CONFIG.read_text())
        print(f"  Using saved region: {r}  (--select to change)")
        return r
    if args.select:
        return _select_region_interactive()
    print("""
  No capture region set.

  Step 1: Open your chess game in the browser.
  Step 2: Find pixel coords of the board corners.
          On Linux:  xdotool getmouselocation
          On Mac:    hover in Preview screenshot tool
          On Windows: use Snipping Tool

  Step 3: Run with --region x y width height  (must be square)
          Example:
            python browser_watcher.py --region 200 150 600 600

  Or click-to-select:
            python browser_watcher.py --select
""")
    sys.exit(0)


# ── HUD ───────────────────────────────────────────────────────────────────────

class ChessHUD:
    W, H = 460, 340

    COLORS = {
        "bg":   (14,  16,  24),
        "gold": (255, 214,  0),
        "text": (240, 244, 248),
        "dim":  (100, 110, 130),
        "good": (0,   230, 118),
        "warn": (255, 152,   0),
        "bad":  (255,  82,  82),
    }

    def __init__(self):
        pygame.init()
        self.screen  = pygame.display.set_mode((self.W, self.H))
        pygame.display.set_caption("AXIOM Chess Watcher")
        self.font_lg = pygame.font.SysFont("monospace", 15, bold=True)
        self.font_sm = pygame.font.SysFont("monospace", 12)
        self.moves   = 0
        self.last    = None

    def update(self, board: chess.Board, manifest: "dict | None", status: str) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_q:
                return False

        if manifest:
            self.last  = manifest
            self.moves += 1

        s = self.screen
        s.fill(self.COLORS["bg"])

        t = self.font_lg.render("AXIOM GAMEWATCHER — BROWSER CHESS", True, self.COLORS["gold"])
        s.blit(t, (10, 8))

        # Board info
        turn  = "White" if board.turn == chess.WHITE else "Black"
        check = " CHECK" if board.is_check() else ""
        info  = f"Move {board.fullmove_number}  {turn} to move{check}"
        it    = self.font_sm.render(info, True, self.COLORS["text"])
        s.blit(it, (10, 32))

        st = self.font_sm.render(f"Status: {status}", True, self.COLORS["dim"])
        s.blit(st, (10, 50))

        mt = self.font_sm.render(f"Manifests: {self.moves}", True, self.COLORS["dim"])
        s.blit(mt, (10, 68))

        # Last manifest
        if self.last:
            pygame.draw.line(s, self.COLORS["dim"], (10, 88), (self.W - 10, 88))
            y = 96
            mh = self.font_lg.render("Last Manifest", True, self.COLORS["gold"])
            s.blit(mh, (10, y))

            fields = [
                ("Move",    self.last.get("move_san", "—")),
                ("UCI",     self.last.get("move_uci", "—")),
                ("Conf",    f"{self.last.get('confidence', 0):.0%}"),
                ("Mode",    self.last.get("mode", "—")),
                ("Latency", f"{self.last.get('latency_ms', 0)}ms"),
                ("ID",      self.last.get("manifest_id", "—")),
                ("Sig",     self.last.get("signature", "—")[:28] + "..."),
            ]
            for i, (k, v) in enumerate(fields):
                row = y + 20 + i * 18
                kt = self.font_sm.render(f"{k:<9}", True, self.COLORS["dim"])
                vt = self.font_sm.render(str(v), True, self.COLORS["text"])
                s.blit(kt, (10,  row))
                s.blit(vt, (100, row))

        # FEN (truncated)
        fen_str = board.fen()[:60] + "..."
        ft = self.font_sm.render(fen_str, True, self.COLORS["dim"])
        s.blit(ft, (10, self.H - 20))

        pygame.display.flip()
        return True


# ── Main watcher loop ─────────────────────────────────────────────────────────

def watch_loop(args):
    if not MSS_AVAILABLE:
        print("[ERROR] pip install mss")
        sys.exit(1)
    if not PIL_AVAILABLE:
        print("[ERROR] pip install pillow")
        sys.exit(1)
    if not CHESS_AVAILABLE:
        print("[ERROR] pip install chess")
        sys.exit(1)

    region   = _load_region(args)
    profiles = SITE_PROFILES.get(args.site, SITE_PROFILES["lichess"])

    if args.site == "custom" and args.highlight_rgb:
        r, g, b, tol = args.highlight_rgb
        profiles = [{"r": (r-tol, r+tol), "g": (g-tol, g+tol), "b": (b-tol, b+tol)}]

    flipped  = (args.play_as == "black")
    board    = chess.Board(args.fen) if args.fen else chess.Board()
    manifests = []
    manifest_path = Path("chess_browser_manifests.json")

    hud = None
    if not args.headless and PYGAME_AVAILABLE:
        hud = ChessHUD()

    prev_highlighted: list = []
    interval = 1.0 / args.fps
    status   = "watching — make a move in browser..."
    frame_n  = 0

    play_as = "flipped (black at bottom)" if flipped else "normal (white at bottom)"
    print("\n  AXIOM Browser Chess Watcher")
    print("  Site   : %s" % args.site)
    print("  Region : %s" % region)
    print("  Board  : %s" % play_as)
    print("  FPS    : %d   Depth: %d" % (args.fps, args.depth))
    print("  Press Q (in HUD) or Ctrl+C to stop")
    print("  Make a move in your browser...\n")

    with mss.mss() as sct:
        while True:
            t0 = time.time()

            # Capture frame
            raw  = sct.grab(region)
            img  = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            arr  = np.array(img)
            w, h = region["width"], region["height"]

            # Detect highlighted squares
            highlighted = _find_highlighted_squares(arr, profiles, w, h, flipped)

            # Live debug every 20 frames
            frame_n += 1
            if frame_n % 20 == 0:
                sq_str = ",".join(highlighted) if highlighted else "none"
                print("  [frame %d] highlighted: %-20s  moves recorded: %d" % (
                    frame_n, sq_str, len(manifests)))

            # Detect new move: highlight changed and we have exactly 2 squares
            # Allow first move (prev_highlighted may be empty)
            highlight_changed = set(highlighted) != set(prev_highlighted)
            new_move_candidate = (
                highlight_changed
                and len(highlighted) == 2
                and len(prev_highlighted) in (0, 2)
            )

            if new_move_candidate:
                move = _infer_move(board, highlighted, prev_highlighted)

                if move and move in board.legal_moves:
                    t_move     = time.time()
                    fen_before = board.fen()
                    san        = board.san(move)
                    board.push(move)
                    latency    = int((time.time() - t_move) * 1000)

                    try:
                        best, reasoning, confidence = select_move_engine(
                            board.copy(), depth=args.depth
                        )
                        mode = "engine-d%d" % args.depth
                    except Exception as e:
                        best       = move
                        reasoning  = "eval error: %s" % e
                        confidence = 0.5
                        mode       = "fallback"

                    manifest = build_manifest(
                        move_san   = san,
                        move_uci   = move.uci(),
                        fen        = fen_before,
                        reasoning  = reasoning,
                        confidence = confidence,
                        mode       = mode,
                        latency_ms = latency,
                    )
                    manifests.append(manifest)

                    if len(manifests) % 5 == 0:
                        manifest_path.write_text(json.dumps(manifests, indent=2))

                    n = board.fullmove_number - 1
                    status = "Move %d: %s  [%s]" % (n, san, manifest["manifest_id"])
                    sig = manifest["signature"][:20]
                    print("  Move %3d: %-8s  conf=%d%%  %s  %s..." % (
                        n, san, int(confidence * 100),
                        manifest["manifest_id"], sig))

                elif highlighted and prev_highlighted:
                    # Highlight changed but move not legal — show for debugging
                    print("  [debug] highlight %s->%s  not a legal move (check --play-as / --site)" % (
                        prev_highlighted, highlighted))

            prev_highlighted = highlighted

            if hud:
                running = hud.update(board, manifests[-1] if manifests else None, status)
                if not running:
                    break

            elapsed = time.time() - t0
            sleep   = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    manifest_path.write_text(json.dumps(manifests, indent=2))
    print(f"\n  Saved {len(manifests)} manifests → {manifest_path}")


# ── Calibrate mode ────────────────────────────────────────────────────────────

def calibrate(args):
    if not MSS_AVAILABLE:
        print("[ERROR] pip install mss")
        sys.exit(1)
    region   = _load_region(args)
    profiles = SITE_PROFILES.get(args.site, SITE_PROFILES["lichess"])
    print(f"\n  Calibration — make a move in your browser, watch for highlight detection.")
    print(f"  Site: {args.site}   Region: {region}\n")

    with mss.mss() as sct:
        for i in range(120):
            raw  = sct.grab(region)
            img  = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            arr  = np.array(img)
            squares = _find_highlighted_squares(
                arr, profiles, region["width"], region["height"]
            )
            sq_str = str(squares) if squares else "(none)"
            print("\r  Frame %4d  highlighted: %-40s" % (i, sq_str), end="", flush=True)
            time.sleep(0.25)
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AXIOM GameWatcher — Browser Chess constitutional observer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python browser_watcher.py --region 100 200 600 600\n"
            "  python browser_watcher.py --region 100 200 600 600 --site chesscom\n"
            "  python browser_watcher.py --region 100 200 600 600 --play-as black\n"
            "  python browser_watcher.py --calibrate\n"
            "  python browser_watcher.py --headless --region 100 200 600 600\n"
        ),
    )
    parser.add_argument("--region",       nargs=4, type=int, metavar=("X","Y","W","H"))
    parser.add_argument("--select",       action="store_true",
                        help="Click to select board region")
    parser.add_argument("--site",         default="lichess",
                        choices=["lichess", "chesscom", "custom"],
                        help="Chess site color profile (default: lichess)")
    parser.add_argument("--highlight-rgb", nargs=4, type=int,
                        metavar=("R","G","B","TOL"),
                        help="Custom highlight color + tolerance (for --site custom)")
    parser.add_argument("--play-as",      default="white",
                        choices=["white", "black"],
                        help="Your color — flips board detection (default: white)")
    parser.add_argument("--fen",          default=None,
                        help="Starting FEN (default: standard start)")
    parser.add_argument("--fps",          type=int, default=4,
                        help="Capture framerate (default: 4)")
    parser.add_argument("--depth",        type=int, default=3,
                        help="Engine evaluation depth (default: 3)")
    parser.add_argument("--headless",     action="store_true",
                        help="No HUD — print manifests to stdout")
    parser.add_argument("--calibrate",    action="store_true",
                        help="Show live highlight detection for color tuning")
    args = parser.parse_args()

    if args.calibrate:
        calibrate(args)
    else:
        watch_loop(args)


if __name__ == "__main__":
    main()
