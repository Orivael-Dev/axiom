"""
AXIOM Constitutional Layers — standalone, no pygame, no game engine.

Provides PacmanWatcher, PacmanPlayer, PacmanEvaluator for use by
both pacman_watcher.py (built-in game) and browser_watcher.py (screen capture).

Game object interface required by these classes:
  game.pacman_x      int  — grid x
  game.pacman_y      int  — grid y
  game.direction     tuple — current direction e.g. (1,0)
  game.ghosts        list of objects with .x .y .scared
  game.maze          list[list[int]] — 0=dot 1=wall 2=power 3=empty
  game.get_valid_dirs() -> list[tuple]

State dict required:
  frame, score, lives, threatened, power_active,
  nearest_ghost_dist, pacman_x, pacman_y
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime

# ── Constitutional constants ───────────────────────────────────────────────────
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2]))
from axiom_signing import derive_key
SIGNING_KEY  = derive_key(b"axiom-gamewatcher-pacman-v1")
FRAME_SAMPLE = 5
COLS, ROWS   = 21, 21

UP    = (0, -1)
DOWN  = (0,  1)
LEFT  = (-1, 0)
RIGHT = (1,  0)
DIRS  = [UP, DOWN, LEFT, RIGHT]
DIR_NAMES = {UP: "UP", DOWN: "DOWN", LEFT: "LEFT", RIGHT: "RIGHT"}


def _sign(manifest: dict) -> str:
    sig_str = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True,
    )
    return "hmac-sha256:" + hmac.new(
        SIGNING_KEY, sig_str.encode(), hashlib.sha256
    ).hexdigest()[:32] + "..."


# ══════════════════════════════════════════════════════════════
# LAYER 1 — WATCHER
# ══════════════════════════════════════════════════════════════

class PacmanWatcher:
    """
    Layer 1 — Observes every Nth frame, classifies KEEP / SKIP / FLAG.
    CANNOT_MUTATE: can_take_action = False
    """

    def __init__(self, sample_every: int = FRAME_SAMPLE):
        self.sample_every = sample_every
        self.last_score   = 0
        self.kept_count   = 0
        self.skip_count   = 0
        self.flag_count   = 0

    def observe(self, game, state: dict) -> dict:
        frame = state["frame"]

        if frame % self.sample_every != 0:
            return {"verdict": "SKIP", "reason": "not_sample_frame"}

        score_delta = state["score"] - self.last_score
        threatened  = state.get("threatened", False)
        power       = state.get("power_active", False)

        if threatened and not power:
            self.flag_count += 1
            self.last_score  = state["score"]
            return {
                "verdict":         "FLAG",
                "reason":          "ghost_threat",
                "state":           state,
                "domain_map":      "RETAILWATCHER: fraud_threat_detected",
                "layer":           "WATCHER",
                "can_take_action": False,
            }

        if score_delta == 0 and not power:
            self.skip_count += 1
            return {"verdict": "SKIP", "reason": "no_progress"}

        self.kept_count += 1
        self.last_score  = state["score"]
        valid = game.get_valid_dirs()
        return {
            "verdict":         "KEEP",
            "reason":          "score_progress" if score_delta > 0 else "power_active",
            "state":           state,
            "score_delta":     score_delta,
            "valid_dirs":      [DIR_NAMES.get(d, str(d)) for d in valid],
            "domain_map":      "RETAILWATCHER: legitimate_interaction_detected",
            "layer":           "WATCHER",
            "can_take_action": False,
            "stats": {
                "kept":    self.kept_count,
                "skipped": self.skip_count,
                "flagged": self.flag_count,
            },
        }


# ══════════════════════════════════════════════════════════════
# LAYER 2 — PLAYER
# ══════════════════════════════════════════════════════════════

class PacmanPlayer:
    """
    Layer 2 — Decides best direction.
    CANNOT_MUTATE: must_log_reasoning = True
    """

    def decide(self, game, observation: dict) -> dict:
        state      = observation.get("state", {})
        valid_dirs = game.get_valid_dirs()

        if not valid_dirs:
            return {
                "direction":      game.direction,
                "direction_name": DIR_NAMES.get(game.direction, "?"),
                "reasoning":      "No valid moves — hold direction",
                "candidates":     [],
                "confidence":     0.50,
                "layer":          "PLAYER",
                "must_log_reasoning": True,
            }

        candidates = []
        for d in valid_dirs:
            score = 0
            nx = (game.pacman_x + d[0]) % COLS
            ny = (game.pacman_y + d[1]) % ROWS

            cell = game.maze[ny][nx] if game.maze else 0
            if cell == 0: score += 100
            if cell == 2: score += 300

            for ghost in game.ghosts:
                dist = abs(ghost.x - nx) + abs(ghost.y - ny)
                if ghost.scared:
                    score += max(0, 200 - dist * 20)
                else:
                    if dist <= 2:  score -= 500
                    elif dist <= 4: score -= 100

            if d == game.direction:
                score += 20

            candidates.append((score, d))

        candidates.sort(key=lambda x: -x[0])
        best_score, best_dir = candidates[0]

        rejected = {
            DIR_NAMES.get(d, str(d)): f"score {s} vs {best_score}"
            for s, d in candidates[1:]
        }

        reasoning = f"Moving {DIR_NAMES.get(best_dir, '?')} (score {best_score})."
        if state.get("threatened"):
            reasoning += " Ghost nearby — avoiding."
        if state.get("power_active"):
            reasoning += " Power active — chasing ghosts."

        return {
            "direction":             best_dir,
            "direction_name":        DIR_NAMES.get(best_dir, "?"),
            "reasoning":             reasoning,
            "candidates_considered": [DIR_NAMES.get(d, str(d)) for _, d in candidates],
            "alternatives_rejected": rejected,
            "confidence":            min(0.85, 0.5 + best_score / 1000),
            "layer":                 "PLAYER",
            "must_log_reasoning":    True,
        }


# ══════════════════════════════════════════════════════════════
# LAYER 3 — EVALUATOR
# ══════════════════════════════════════════════════════════════

class PacmanEvaluator:
    """
    Layer 3 — Was that the best move?
    CANNOT_MUTATE: question_blindness = True
    CANNOT_MUTATE: rival_move_required = True
    CANNOT_MUTATE: uncertainty_floor = 0.15
    """

    MAX_CONFIDENCE = 0.85

    def _can_move(self, game, x: int, y: int, d: tuple) -> bool:
        nx = (x + d[0]) % COLS
        ny = (y + d[1]) % ROWS
        if game.maze:
            return game.maze[ny][nx] != 1
        return True  # browser game: treat all cells as open

    def evaluate(
        self,
        state_before: dict,
        direction: tuple,
        state_after: dict,
        game_before,
        # NOTE: player reasoning NOT passed — question blindness enforced
    ) -> dict:
        t0 = time.time()

        px = state_before.get("pacman_x", game_before.pacman_x)
        py = state_before.get("pacman_y", game_before.pacman_y)

        score_delta = state_after["score"] - state_before["score"]
        died        = state_after["lives"] < state_before["lives"]
        ate_ghost   = score_delta >= 200
        ate_power   = score_delta >= 50 and not ate_ghost
        ate_dot     = score_delta == 10

        # Score all valid directions from before-state (question blindness: no player reasoning)
        valid_dirs = [d for d in DIRS if self._can_move(game_before, px, py, d)]
        dir_scores = {}
        for d in valid_dirs:
            s  = 0
            nx = (px + d[0]) % COLS
            ny = (py + d[1]) % ROWS
            cell = game_before.maze[ny][nx] if game_before.maze else 0
            if cell == 0: s += 100
            if cell == 2: s += 300
            for ghost in game_before.ghosts:
                dist = abs(ghost.x - nx) + abs(ghost.y - ny)
                if ghost.scared:
                    s += max(0, 200 - dist * 20)
                else:
                    if dist <= 2:  s -= 500
                    elif dist <= 4: s -= 100
            dir_scores[d] = s

        chosen_score = dir_scores.get(direction, 0)
        best_dir     = max(dir_scores, key=dir_scores.get) if dir_scores else direction
        best_score   = dir_scores.get(best_dir, 0)
        score_gap    = best_score - chosen_score

        if died:
            classification = "CRITICAL_ERROR";  confidence = 0.85
        elif score_gap <= 10:
            classification = "OPTIMAL";         confidence = 0.80
        elif score_gap <= 80:
            classification = "GOOD";            confidence = 0.72
        elif score_gap <= 200:
            classification = "SUBOPTIMAL";      confidence = 0.75
        else:
            classification = "BLUNDER";         confidence = 0.82

        confidence = min(confidence, self.MAX_CONFIDENCE)

        # Rival move — CANNOT_MUTATE
        if best_dir != direction and score_gap > 10:
            rival_dir    = best_dir
            rival_reason = (
                f"{DIR_NAMES.get(rival_dir,'?')} scores {best_score} "
                f"vs {DIR_NAMES.get(direction,'?')} scores {chosen_score} "
                f"(gap: {score_gap})"
            )
        else:
            sorted_dirs = sorted(dir_scores.items(), key=lambda x: -x[1])
            if len(sorted_dirs) > 1:
                rival_dir   = sorted_dirs[1][0]
                rival_score = sorted_dirs[1][1]
                rival_reason = (
                    f"Best direction chosen. "
                    f"Alt: {DIR_NAMES.get(rival_dir,'?')} (score {rival_score})"
                )
            else:
                rival_dir    = None
                rival_reason = "Only one valid direction"

        domain_verdict = (
            "FRAUD_BLOCKED"          if died and state_before.get("threatened")
            else "LEGITIMATE_INTERACTION" if ate_dot
            else "FTC_REPORT_TRIGGERED"   if ate_ghost
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
            "state_before_score":    state_before["score"],
            "direction_chosen":      DIR_NAMES.get(direction, str(direction)),
            "state_after_score":     state_after["score"],
            "score_delta":           score_delta,
            "pacman_died":           died,
            "ate_dot":               ate_dot,
            "ate_power":             ate_power,
            "ate_ghost":             ate_ghost,
            "classification":        classification,
            "confidence":            confidence,
            "domain_verdict":        domain_verdict,
            "domain_package":        "retailwatcher",
            "rival_move_required":   True,
            "rival_direction":       DIR_NAMES.get(rival_dir, "NONE") if rival_dir else "NONE",
            "rival_move_reason":     rival_reason,
            "uncertainty_floor_applied": True,
            "max_confidence_enforced":   self.MAX_CONFIDENCE,
        }

        manifest["signature"] = _sign(manifest)
        return manifest
