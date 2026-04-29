"""
AXIOM GameWatcher — Pacman Adapter
====================================
Three-layer constitutional governance for Pacman.

Layer 1: Watcher  — observes every 5th frame, keeps only good states
Layer 2: Player   — decides direction, logs reasoning
Layer 3: Evaluator — was that the best move? (question blindness)

Domain mapping: RetailWatcher
  Pacman navigating maze = AI navigating marketplace
  Ghosts = fraud threats (fake reviews, counterfeits)
  Dots   = legitimate interactions to collect
  Power pellet = FTC report triggered
  Ghost eaten = fraud pattern blocked

Usage:
  pip install pygame numpy
  python pacman_watcher.py              # play yourself
  python pacman_watcher.py --ai         # AI plays with AXIOM governance
  python pacman_watcher.py --demo       # auto-demo mode
  python pacman_watcher.py --headless   # no display (Nano/server)

Controls (manual mode):
  Arrow keys = move
  Q = quit
  P = pause / show last manifest
"""

import os
import sys
import json
import hashlib
import hmac
import time
import uuid
import random
import argparse
from datetime import datetime
from typing import Optional
from collections import deque

# ── Try pygame ────────────────────────────────────────────────
try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    print("pygame not installed — running headless")
    print("pip3 install pygame --break-system-packages")

SIGNING_KEY = b"axiom-gamewatcher-pacman-v1"

# ── Constants ─────────────────────────────────────────────────
CELL      = 24
COLS      = 21
ROWS      = 21
WIDTH     = COLS * CELL
HEIGHT    = ROWS * CELL + 80  # +80 for HUD
FPS       = 12
FRAME_SAMPLE = 5  # Watcher samples every Nth frame

# Colors
BLACK  = (4,   5,  8)
DARK   = (14, 16, 24)
WALL   = (30, 60, 120)
DOT    = (180, 180, 180)
POWER  = (255, 214, 0)
GREEN  = (0,  230, 118)
RED    = (255, 82,  82)
GOLD   = (255, 214,  0)
WHITE  = (240, 244, 248)
ORANGE = (255, 152,  0)
TEAL   = (45,  212, 191)
PURPLE = (206, 147, 216)

# Directions
UP    = (0, -1)
DOWN  = (0,  1)
LEFT  = (-1, 0)
RIGHT = (1,  0)
DIRS  = [UP, DOWN, LEFT, RIGHT]
DIR_NAMES = {UP:"UP", DOWN:"DOWN", LEFT:"LEFT", RIGHT:"RIGHT"}

# ── Simple Pacman maze ────────────────────────────────────────
# 1=wall 0=dot 2=power pellet 3=empty(ghost house)
BASE_MAZE = [
    [1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1],
    [1,2,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,2,1],
    [1,0,1,1,0,1,1,1,0,1,1,1,0,1,1,1,0,1,1,0,1],
    [1,0,1,1,0,1,1,1,0,1,1,1,0,1,1,1,0,1,1,0,1],
    [1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1],
    [1,0,1,1,0,1,0,1,1,1,1,1,1,1,0,1,0,1,1,0,1],
    [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],
    [1,1,1,1,0,1,1,1,3,3,1,3,3,1,1,1,0,1,1,1,1],
    [1,1,1,1,0,1,3,3,3,3,3,3,3,3,1,1,0,1,1,1,1],
    [1,1,1,1,0,1,3,1,1,3,3,1,1,3,1,1,0,1,1,1,1],
    [0,0,0,0,0,3,3,1,3,3,3,3,1,3,3,3,0,0,0,0,0],
    [1,1,1,1,0,1,3,1,1,1,1,1,1,3,1,1,0,1,1,1,1],
    [1,1,1,1,0,1,3,3,3,3,3,3,3,3,1,1,0,1,1,1,1],
    [1,1,1,1,0,1,3,1,1,1,1,1,1,3,1,1,0,1,1,1,1],
    [1,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,1],
    [1,0,1,1,0,1,1,1,0,1,1,1,0,1,1,1,0,1,1,0,1],
    [1,2,0,1,0,0,0,0,0,0,3,0,0,0,0,0,0,1,0,2,1],
    [1,1,0,1,0,1,0,1,1,1,1,1,1,1,0,1,0,1,0,1,1],
    [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],
    [1,0,1,1,1,1,1,1,0,1,1,1,0,1,1,1,1,1,1,0,1],
    [1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1],
]

TOTAL_DOTS = sum(row.count(0) + row.count(2) for row in BASE_MAZE)


# ══════════════════════════════════════════════════════════════
# GAME STATE
# ══════════════════════════════════════════════════════════════

class Ghost:
    def __init__(self, x, y, color, name):
        self.x      = x
        self.y      = y
        self.color  = color
        self.name   = name
        self.scared = False
        self.dir    = random.choice(DIRS)

    def move(self, maze):
        # Simple ghost AI — random walk avoiding walls
        options = []
        for d in DIRS:
            nx, ny = self.x + d[0], self.y + d[1]
            if 0 <= nx < COLS and 0 <= ny < ROWS and maze[ny][nx] != 1:
                options.append(d)
        if options:
            # Prefer current direction
            if self.dir in options and random.random() > 0.3:
                d = self.dir
            else:
                d = random.choice(options)
            self.dir = d
            self.x += d[0]
            self.y += d[1]

    @property
    def pos(self):
        return (self.x, self.y)


class PacmanGame:
    def __init__(self):
        self.reset()

    def reset(self):
        self.maze        = [row[:] for row in BASE_MAZE]
        self.pacman_x    = 10
        self.pacman_y    = 16
        self.score       = 0
        self.lives       = 3
        self.dots_eaten  = 0
        self.power_timer = 0
        self.frame       = 0
        self.game_over   = False
        self.won         = False
        self.direction   = RIGHT

        self.ghosts = [
            Ghost(9,  9,  RED,    "Blinky"),
            Ghost(10, 9,  PURPLE, "Pinky"),
            Ghost(11, 9,  TEAL,   "Inky"),
            Ghost(10, 10, ORANGE, "Clyde"),
        ]

    def can_move(self, x, y, d):
        nx, ny = x + d[0], y + d[1]
        if 0 <= nx < COLS and 0 <= ny < ROWS:
            return self.maze[ny][nx] != 1
        # Wrap tunnel
        if ny == 10:
            return True
        return False

    def move_pacman(self, direction):
        if not self.can_move(self.pacman_x, self.pacman_y, direction):
            return False
        self.direction   = direction
        self.pacman_x   += direction[0]
        self.pacman_y   += direction[1]

        # Tunnel wrap
        self.pacman_x = self.pacman_x % COLS
        self.pacman_y = self.pacman_y % ROWS

        cell = self.maze[self.pacman_y][self.pacman_x]
        if cell == 0:
            self.maze[self.pacman_y][self.pacman_x] = 3
            self.score      += 10
            self.dots_eaten += 1
        elif cell == 2:
            self.maze[self.pacman_y][self.pacman_x] = 3
            self.score       += 50
            self.power_timer  = 30
            for g in self.ghosts:
                g.scared = True

        # Move ghosts
        if self.power_timer > 0:
            self.power_timer -= 1
            if self.power_timer == 0:
                for g in self.ghosts:
                    g.scared = False

        for ghost in self.ghosts:
            ghost.move(self.maze)

        # Check ghost collision
        for ghost in self.ghosts:
            if ghost.x == self.pacman_x and ghost.y == self.pacman_y:
                if ghost.scared:
                    self.score     += 200
                    ghost.x, ghost.y = 10, 9
                    ghost.scared    = False
                else:
                    self.lives -= 1
                    if self.lives <= 0:
                        self.game_over = True
                    else:
                        self.pacman_x = 10
                        self.pacman_y = 16

        dots_left = sum(row.count(0) + row.count(2) for row in self.maze)
        if dots_left == 0:
            self.won = True
            self.game_over = True

        self.frame += 1
        return True

    def get_state(self) -> dict:
        """Full game state snapshot."""
        ghosts_info = []
        for g in self.ghosts:
            dx = abs(g.x - self.pacman_x)
            dy = abs(g.y - self.pacman_y)
            dist = dx + dy
            ghosts_info.append({
                "name":    g.name,
                "pos":     (g.x, g.y),
                "scared":  g.scared,
                "distance": dist,
                "threat":  dist <= 3 and not g.scared,
            })

        nearest_ghost_dist = min(g["distance"] for g in ghosts_info)
        threatened = any(g["threat"] for g in ghosts_info)
        dots_left  = sum(row.count(0) + row.count(2) for row in self.maze)

        return {
            "pacman_pos":    (self.pacman_x, self.pacman_y),
            "score":         self.score,
            "lives":         self.lives,
            "dots_eaten":    self.dots_eaten,
            "dots_remaining": dots_left,
            "power_active":  self.power_timer > 0,
            "power_timer":   self.power_timer,
            "ghosts":        ghosts_info,
            "nearest_ghost_dist": nearest_ghost_dist,
            "threatened":    threatened,
            "frame":         self.frame,
            "game_over":     self.game_over,
            "won":           self.won,
        }

    def get_valid_dirs(self):
        return [d for d in DIRS
                if self.can_move(self.pacman_x, self.pacman_y, d)]


# ══════════════════════════════════════════════════════════════
# LAYER 1 — WATCHER
# ══════════════════════════════════════════════════════════════

class PacmanWatcher:
    """
    Layer 1 — Observes every Nth frame.
    Keeps only good states.
    CANNOT_MUTATE: can_take_action = false
    """

    def __init__(self, sample_every: int = FRAME_SAMPLE):
        self.sample_every  = sample_every
        self.last_score    = 0
        self.kept_count    = 0
        self.skip_count    = 0
        self.flag_count    = 0

    def observe(self, game: PacmanGame, state: dict) -> dict:
        """
        Decide: KEEP / SKIP / FLAG this frame.
        KEEP  — good state worth storing
        SKIP  — no useful information
        FLAG  — danger state (store separately)
        """
        frame = state["frame"]

        # Only sample every Nth frame
        if frame % self.sample_every != 0:
            return {"verdict": "SKIP", "reason": "not sample frame"}

        score_delta = state["score"] - self.last_score
        threatened  = state["threatened"]
        power       = state["power_active"]
        near_dist   = state["nearest_ghost_dist"]

        # FLAG — danger without power
        if threatened and not power:
            self.flag_count += 1
            self.last_score  = state["score"]
            return {
                "verdict":    "FLAG",
                "reason":     "ghost_threat",
                "state":      state,
                "domain_map": "RETAILWATCHER: fraud_threat_detected",
                "layer":      "WATCHER",
                "can_take_action": False,
            }

        # SKIP — no progress
        if score_delta == 0 and not power:
            self.skip_count += 1
            return {"verdict": "SKIP", "reason": "no_progress"}

        # KEEP — good frame
        self.kept_count += 1
        self.last_score  = state["score"]
        return {
            "verdict":      "KEEP",
            "reason":       "score_progress" if score_delta > 0 else "power_active",
            "state":        state,
            "score_delta":  score_delta,
            "valid_dirs":   [DIR_NAMES[d] for d in game.get_valid_dirs()],
            "domain_map":   "RETAILWATCHER: legitimate_interaction_detected",
            "layer":        "WATCHER",
            "can_take_action": False,  # CANNOT_MUTATE
            "stats": {
                "kept":    self.kept_count,
                "skipped": self.skip_count,
                "flagged": self.flag_count,
            }
        }


# ══════════════════════════════════════════════════════════════
# LAYER 2 — PLAYER
# ══════════════════════════════════════════════════════════════

class PacmanPlayer:
    """
    Layer 2 — Decides direction.
    CANNOT_MUTATE: must_log_reasoning = true
    """

    def decide(self, game: PacmanGame, observation: dict) -> dict:
        state       = observation.get("state", {})
        valid_dirs  = game.get_valid_dirs()

        if not valid_dirs:
            return {
                "direction":  game.direction,
                "reasoning":  "No valid moves — continue current direction",
                "candidates": [],
                "confidence": 0.50,
            }

        candidates = []
        for d in valid_dirs:
            score = 0
            nx    = game.pacman_x + d[0]
            ny    = game.pacman_y + d[1]
            nx    = nx % COLS
            ny    = ny % ROWS

            # Dot in target cell
            cell = game.maze[ny][nx]
            if cell == 0:   score += 100
            if cell == 2:   score += 300  # Power pellet

            # Ghost avoidance / chase
            for ghost in game.ghosts:
                dist = abs(ghost.x - nx) + abs(ghost.y - ny)
                if ghost.scared:
                    score += max(0, 200 - dist * 20)  # Chase scared ghosts
                else:
                    if dist <= 2:
                        score -= 500  # Run from dangerous ghosts
                    elif dist <= 4:
                        score -= 100

            # Prefer continuing direction (momentum)
            if d == game.direction:
                score += 20

            candidates.append((score, d))

        candidates.sort(key=lambda x: -x[0])
        best_score, best_dir = candidates[0]

        rejected = {
            DIR_NAMES[d]: f"score {s} vs {best_score}"
            for s, d in candidates[1:]
        }

        reasoning = (
            f"Moving {DIR_NAMES[best_dir]} (score {best_score}). "
        )
        if state.get("threatened"):
            reasoning += "Ghost nearby — avoiding. "
        if state.get("power_active"):
            reasoning += "Power active — chasing ghosts. "

        return {
            "direction":              best_dir,
            "direction_name":         DIR_NAMES[best_dir],
            "reasoning":              reasoning,
            "candidates_considered":  [DIR_NAMES[d] for _, d in candidates],
            "alternatives_rejected":  rejected,
            "confidence":             min(0.85, 0.5 + best_score / 1000),
            "layer":                  "PLAYER",
            "must_log_reasoning":     True,  # CANNOT_MUTATE
        }


# ══════════════════════════════════════════════════════════════
# LAYER 3 — EVALUATOR
# ══════════════════════════════════════════════════════════════

class PacmanEvaluator:
    """
    Layer 3 — Was that the best move?
    CANNOT_MUTATE: question_blindness = true
    CANNOT_MUTATE: rival_move_required = true
    CANNOT_MUTATE: uncertainty_floor = 0.15
    """

    MAX_CONFIDENCE = 0.85

    def evaluate(
        self,
        state_before: dict,
        direction: tuple,
        state_after:  dict,
        game_before:  PacmanGame,
        # NOTE: Player reasoning NOT passed — question blindness
    ) -> dict:
        t0 = time.time()

        score_delta = state_after["score"] - state_before["score"]
        died        = state_after["lives"] < state_before["lives"]
        ate_ghost   = score_delta >= 200
        ate_power   = score_delta >= 50 and not ate_ghost
        ate_dot     = score_delta == 10

        # Evaluate all valid directions from before state
        valid_dirs_before = [
            d for d in DIRS
            if game_before.can_move(
                state_before["pacman_pos"][0],
                state_before["pacman_pos"][1], d)
        ]

        # Score each direction
        dir_scores = {}
        for d in valid_dirs_before:
            s = 0
            nx = (state_before["pacman_pos"][0] + d[0]) % COLS
            ny = (state_before["pacman_pos"][1] + d[1]) % ROWS
            cell = game_before.maze[ny][nx]
            if cell == 0:   s += 100
            if cell == 2:   s += 300
            for ghost in game_before.ghosts:
                dist = abs(ghost.x - nx) + abs(ghost.y - ny)
                if ghost.scared:
                    s += max(0, 200 - dist * 20)
                else:
                    if dist <= 2: s -= 500
                    elif dist <= 4: s -= 100
            dir_scores[d] = s

        chosen_score = dir_scores.get(direction, 0)
        best_dir     = max(dir_scores, key=dir_scores.get) if dir_scores else direction
        best_score   = dir_scores.get(best_dir, 0)
        score_gap    = best_score - chosen_score

        # Classify
        if died:
            classification = "CRITICAL_ERROR"
            confidence     = 0.85
        elif score_gap <= 10:
            classification = "OPTIMAL"
            confidence     = 0.80
        elif score_gap <= 80:
            classification = "GOOD"
            confidence     = 0.72
        elif score_gap <= 200:
            classification = "SUBOPTIMAL"
            confidence     = 0.75
        else:
            classification = "BLUNDER"
            confidence     = 0.82

        confidence = min(confidence, self.MAX_CONFIDENCE)

        # Rival move — CANNOT_MUTATE
        if best_dir != direction and score_gap > 10:
            rival_dir    = best_dir
            rival_reason = (
                f"{DIR_NAMES[rival_dir]} scores {best_score} "
                f"vs {DIR_NAMES[direction]} scores {chosen_score} "
                f"(gap: {score_gap})"
            )
        else:
            # Chosen was best — rival is second best
            sorted_dirs = sorted(dir_scores.items(), key=lambda x: -x[1])
            if len(sorted_dirs) > 1:
                rival_dir    = sorted_dirs[1][0]
                rival_score  = sorted_dirs[1][1]
                rival_reason = (
                    f"Best direction chosen. "
                    f"Alternative: {DIR_NAMES[rival_dir]} (score {rival_score})"
                )
            else:
                rival_dir    = None
                rival_reason = "Only one valid direction"

        # Domain mapping — RetailWatcher
        domain_verdict = (
            "FRAUD_BLOCKED" if died and state_before["threatened"]
            else "LEGITIMATE_INTERACTION" if ate_dot
            else "FTC_REPORT_TRIGGERED" if ate_ghost
            else "POWER_PELLET_ACTIVATED" if ate_power
            else "NAVIGATING"
        )

        manifest_id = f"GW-PAC-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"

        manifest = {
            "manifest_id":           manifest_id,
            "manifest_version":      "1.0",
            "engine":                "AXIOM GameWatcher — Pacman v1.0",
            "layer":                 "EVALUATOR",
            "timestamp":             datetime.now().isoformat() + "Z",
            "latency_ms":            int((time.time() - t0) * 1000),
            "question_blindness":    True,
            "player_reasoning_seen": False,

            # The move
            "state_before_score":  state_before["score"],
            "direction_chosen":    DIR_NAMES[direction],
            "state_after_score":   state_after["score"],
            "score_delta":         score_delta,
            "pacman_died":         died,
            "ate_dot":             ate_dot,
            "ate_power":           ate_power,
            "ate_ghost":           ate_ghost,

            # Verdict
            "classification":    classification,
            "confidence":        confidence,
            "domain_verdict":    domain_verdict,
            "domain_package":    "retailwatcher",

            # Rival — CANNOT_MUTATE
            "rival_move_required": True,
            "rival_direction":    DIR_NAMES.get(rival_dir, "NONE") if rival_dir else "NONE",
            "rival_move_reason":  rival_reason,

            # Constitutional
            "uncertainty_floor_applied": True,
            "max_confidence_enforced":   self.MAX_CONFIDENCE,
        }

        sig_str = json.dumps(
            {k: v for k, v in manifest.items() if k != "signature"},
            sort_keys=True
        )
        sig = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        manifest["signature"] = f"hmac-sha256:{sig[:32]}..."

        return manifest


# ══════════════════════════════════════════════════════════════
# RENDERER
# ══════════════════════════════════════════════════════════════

class PacmanRenderer:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("AXIOM GameWatcher — Pacman")
        self.font_sm = pygame.font.SysFont("monospace", 11)
        self.font_md = pygame.font.SysFont("monospace", 13, bold=True)
        self.clock   = pygame.time.Clock()

    def draw(self, game: PacmanGame, last_manifest: Optional[dict],
             watcher_stats: dict):
        self.screen.fill(BLACK)

        # Draw maze
        for y in range(ROWS):
            for x in range(COLS):
                cell = game.maze[y][x]
                rx   = x * CELL
                ry   = y * CELL
                if cell == 1:
                    pygame.draw.rect(self.screen, WALL, (rx+1, ry+1, CELL-2, CELL-2), border_radius=3)
                elif cell == 0:
                    cx = rx + CELL // 2
                    cy = ry + CELL // 2
                    pygame.draw.circle(self.screen, DOT, (cx, cy), 3)
                elif cell == 2:
                    cx = rx + CELL // 2
                    cy = ry + CELL // 2
                    pygame.draw.circle(self.screen, POWER, (cx, cy), 7)

        # Draw Pacman
        px = game.pacman_x * CELL + CELL // 2
        py = game.pacman_y * CELL + CELL // 2
        pygame.draw.circle(self.screen, GOLD, (px, py), CELL // 2 - 2)

        # Draw ghosts
        for ghost in game.ghosts:
            gx = ghost.x * CELL + CELL // 2
            gy = ghost.y * CELL + CELL // 2
            color = TEAL if ghost.scared else ghost.color
            pygame.draw.circle(self.screen, color, (gx, gy), CELL // 2 - 2)
            pygame.draw.rect(self.screen, color,
                (ghost.x * CELL + 2, ghost.y * CELL + CELL // 2,
                 CELL - 4, CELL // 2 - 2))

        # HUD
        hud_y = ROWS * CELL + 4
        pygame.draw.rect(self.screen, DARK, (0, ROWS * CELL, WIDTH, 80))

        score_txt = self.font_md.render(
            f"Score: {game.score}  Lives: {'♥' * game.lives}  "
            f"Dots: {game.dots_eaten}/{TOTAL_DOTS}", True, WHITE)
        self.screen.blit(score_txt, (8, hud_y))

        # Watcher stats
        stat_txt = self.font_sm.render(
            f"Watcher: KEPT {watcher_stats.get('kept',0)} | "
            f"SKIP {watcher_stats.get('skipped',0)} | "
            f"FLAG {watcher_stats.get('flagged',0)}", True, TEAL)
        self.screen.blit(stat_txt, (8, hud_y + 18))

        # Last manifest verdict
        if last_manifest:
            cls   = last_manifest.get("classification", "")
            icons = {"OPTIMAL":"✅","GOOD":"🟢","SUBOPTIMAL":"🟡",
                     "BLUNDER":"🔴","CRITICAL_ERROR":"🚨"}
            colors_map = {
                "OPTIMAL": GREEN, "GOOD": GREEN,
                "SUBOPTIMAL": GOLD, "BLUNDER": RED,
                "CRITICAL_ERROR": RED,
            }
            mc   = colors_map.get(cls, WHITE)
            rival = last_manifest.get("rival_direction", "")
            mani_txt = self.font_sm.render(
                f"Eval: {cls} ({last_manifest.get('confidence',0):.0%}) "
                f"| Rival: {rival} | {last_manifest.get('domain_verdict','')}",
                True, mc)
            self.screen.blit(mani_txt, (8, hud_y + 34))

            mid_txt = self.font_sm.render(
                f"Manifest: {last_manifest.get('manifest_id','')}",
                True, LGRAY if True else WHITE)
            self.screen.blit(mid_txt, (8, hud_y + 50))

        pygame.display.flip()
        self.clock.tick(FPS)

    def quit(self):
        pygame.quit()


LGRAY = (58, 72, 88)


# ══════════════════════════════════════════════════════════════
# MAIN GAME LOOP
# ══════════════════════════════════════════════════════════════

class PacmanGameWatcher:
    def __init__(self, headless: bool = False, ai: bool = False):
        self.game      = PacmanGame()
        self.watcher   = PacmanWatcher()
        self.player    = PacmanPlayer()
        self.evaluator = PacmanEvaluator()
        self.headless  = headless or not PYGAME_AVAILABLE
        self.ai        = ai
        self.manifests = []
        self.renderer  = None

        if not self.headless:
            self.renderer = PacmanRenderer()

    def run(self, max_frames: int = 2000):
        print("\n" + "═"*55)
        print("  AXIOM GameWatcher — Pacman")
        print("  RetailWatcher Domain Mapping")
        print("═"*55)
        print(f"  Layer 1: Watcher   (sample every {FRAME_SAMPLE} frames)")
        print(f"  Layer 2: Player    (AI: {self.ai})")
        print(f"  Layer 3: Evaluator (question blindness ON)")
        print("═"*55)
        if not self.headless:
            print("  Controls: Arrow keys | Q=quit | P=pause")
        print()

        last_manifest   = None
        watcher_stats   = {"kept": 0, "skipped": 0, "flagged": 0}
        pending_dir     = self.game.direction
        frame_count     = 0

        while not self.game.game_over and frame_count < max_frames:
            # Handle pygame events
            if not self.headless:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.game.game_over = True
                        break
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_q:
                            self.game.game_over = True
                        elif event.key == pygame.K_UP    and not self.ai:
                            pending_dir = UP
                        elif event.key == pygame.K_DOWN  and not self.ai:
                            pending_dir = DOWN
                        elif event.key == pygame.K_LEFT  and not self.ai:
                            pending_dir = LEFT
                        elif event.key == pygame.K_RIGHT and not self.ai:
                            pending_dir = RIGHT

            # Get state before move
            state_before = self.game.get_state()
            game_before  = PacmanGame()
            game_before.maze      = [row[:] for row in self.game.maze]
            game_before.pacman_x  = self.game.pacman_x
            game_before.pacman_y  = self.game.pacman_y
            game_before.ghosts    = self.game.ghosts[:]
            game_before.score     = self.game.score
            game_before.power_timer = self.game.power_timer

            # Layer 1 — Watcher
            observation = self.watcher.observe(self.game, state_before)
            watcher_stats = observation.get("stats", watcher_stats)

            # Layer 2 — Player (AI mode)
            if self.ai and observation["verdict"] in ("KEEP", "FLAG"):
                decision    = self.player.decide(self.game, observation)
                pending_dir = decision["direction"]

            # Execute move
            chosen_dir = pending_dir
            self.game.move_pacman(chosen_dir)
            state_after = self.game.get_state()

            # Layer 3 — Evaluator (on kept frames only)
            if observation["verdict"] in ("KEEP", "FLAG"):
                manifest = self.evaluator.evaluate(
                    state_before, chosen_dir, state_after, game_before
                )
                self.manifests.append(manifest)
                last_manifest = manifest

                cls  = manifest["classification"]
                icon = {"OPTIMAL":"✅","GOOD":"🟢","SUBOPTIMAL":"🟡",
                        "BLUNDER":"🔴","CRITICAL_ERROR":"🚨"}.get(cls, "•")
                print(f"  {icon} Frame {frame_count:4d} | "
                      f"{cls:14s} | "
                      f"{DIR_NAMES[chosen_dir]:6s} | "
                      f"Score: {state_after['score']:5d} | "
                      f"Rival: {manifest['rival_direction']}")

            # Render
            if not self.headless:
                self.renderer.draw(self.game, last_manifest, watcher_stats)

            frame_count += 1

            if self.headless:
                time.sleep(0.05)

        # Game over
        self._print_summary(frame_count)
        if not self.headless and self.renderer:
            self.renderer.quit()

    def _print_summary(self, frames: int):
        print("\n" + "═"*55)
        print("  GAME SUMMARY — AXIOM GameWatcher Pacman")
        print("═"*55)
        print(f"  Final score: {self.game.score}")
        print(f"  Dots eaten:  {self.game.dots_eaten}/{TOTAL_DOTS}")
        print(f"  Result:      {'WON 🏆' if self.game.won else 'GAME OVER'}")
        print(f"  Frames:      {frames}")
        print()

        if self.manifests:
            from collections import Counter
            cls_counts = Counter(m["classification"] for m in self.manifests)
            dom_counts = Counter(m["domain_verdict"] for m in self.manifests)

            print("  Constitutional Evaluations:")
            for cls in ["OPTIMAL","GOOD","SUBOPTIMAL","BLUNDER","CRITICAL_ERROR"]:
                if cls_counts[cls]:
                    icons = {"OPTIMAL":"✅","GOOD":"🟢","SUBOPTIMAL":"🟡",
                             "BLUNDER":"🔴","CRITICAL_ERROR":"🚨"}
                    print(f"    {icons.get(cls,'•')} {cls}: {cls_counts[cls]}")

            print()
            print("  RetailWatcher Domain Mapping:")
            for domain, count in dom_counts.items():
                print(f"    {domain}: {count}")

        print(f"\n  Watcher stats:")
        print(f"    Kept:    {self.watcher.kept_count}")
        print(f"    Skipped: {self.watcher.skip_count}")
        print(f"    Flagged: {self.watcher.flag_count}")
        print(f"  Manifests: {len(self.manifests)} signed")

        # Save
        output = {
            "game_id":    str(uuid.uuid4())[:8],
            "engine":     "AXIOM GameWatcher Pacman v1.0",
            "played_at":  datetime.now().isoformat(),
            "final_score": self.game.score,
            "manifests":  self.manifests,
        }
        with open("pacman_manifests.json", "w") as f:
            json.dump(output, f, indent=2)
        print(f"  Saved: pacman_manifests.json")
        print("═"*55)


def main():
    parser = argparse.ArgumentParser(
        description="AXIOM GameWatcher — Pacman (RetailWatcher Domain)"
    )
    parser.add_argument("--ai",       action="store_true",
                        help="AI player mode")
    parser.add_argument("--headless", action="store_true",
                        help="No display (Nano/server mode)")
    parser.add_argument("--frames",   type=int, default=2000,
                        help="Max frames to run")
    args = parser.parse_args()

    gw = PacmanGameWatcher(headless=args.headless, ai=args.ai)
    gw.run(max_frames=args.frames)


if __name__ == "__main__":
    main()
