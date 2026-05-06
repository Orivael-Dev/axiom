"""
GameWatcher — Three-Layer Chess Governance
==========================================
Layer 1: Watcher  — observes game state, never acts
Layer 2: Player   — decides moves, logs all reasoning
Layer 3: Evaluator — was that the best move? (question blindness)

Every move produces a signed AXIOM manifest.

Usage:
  python gamewatcher.py                    # interactive game
  python gamewatcher.py --demo             # run demo game
  python gamewatcher.py --fen "rnbq..." --move e2e4  # evaluate one move
  python gamewatcher.py --server           # REST API mode port 8002

Requires:
  pip install chess anthropic fastapi uvicorn
  set ANTHROPIC_API_KEY=sk-ant-...
"""

import os
import sys
import json
import hashlib
import hmac
import time
import uuid
import argparse
from datetime import datetime
from typing import Optional

import chess
import chess.pgn

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[2]))
from axiom_signing import derive_key
SIGNING_KEY = derive_key(b"axiom-gamewatcher-v1")


# ══════════════════════════════════════════════════════════════
# PIECE VALUES
# ══════════════════════════════════════════════════════════════

PIECE_VALUES = {
    chess.PAWN:   100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK:   500,
    chess.QUEEN:  900,
    chess.KING:   0,
}


# ══════════════════════════════════════════════════════════════
# LAYER 1 — WATCHER
# ══════════════════════════════════════════════════════════════

class ChessWatcher:
    """
    Layer 1 — Observes game state. Never acts. Never suggests.
    Reports what it sees — nothing more.
    CANNOT_MUTATE: can_take_action = false
    """

    def observe(self, board: chess.Board) -> dict:
        """Build a complete state report from the board."""
        state_id = str(uuid.uuid4())[:8]

        # Material balance
        white_material = sum(
            PIECE_VALUES.get(p.piece_type, 0)
            for p in board.piece_map().values()
            if p.color == chess.WHITE
        )
        black_material = sum(
            PIECE_VALUES.get(p.piece_type, 0)
            for p in board.piece_map().values()
            if p.color == chess.BLACK
        )

        # Threatened pieces
        threatened = []
        for square, piece in board.piece_map().items():
            attackers = board.attackers(not piece.color, square)
            defenders = board.attackers(piece.color, square)
            if attackers and len(attackers) > len(defenders):
                threatened.append({
                    "piece": piece.symbol(),
                    "square": chess.square_name(square),
                    "attackers": len(attackers),
                    "defenders": len(defenders),
                })

        # King safety
        white_king_sq = board.king(chess.WHITE)
        black_king_sq = board.king(chess.BLACK)
        white_attackers = len(board.attackers(chess.BLACK, white_king_sq)) if white_king_sq else 0
        black_attackers = len(board.attackers(chess.WHITE, black_king_sq)) if black_king_sq else 0

        # Game phase
        total_material = white_material + black_material
        if board.fullmove_number <= 10:
            phase = "opening"
        elif total_material > 2600:
            phase = "middlegame"
        else:
            phase = "endgame"

        # Tactical patterns (simple detection)
        patterns = self._detect_patterns(board)

        return {
            "state_id":         state_id,
            "fen":              board.fen(),
            "turn":             "white" if board.turn == chess.WHITE else "black",
            "move_number":      board.fullmove_number,
            "material_balance": white_material - black_material,
            "white_material":   white_material,
            "black_material":   black_material,
            "threatened_pieces": threatened,
            "king_safety": {
                "white": "exposed" if white_attackers > 0 else "safe",
                "black": "exposed" if black_attackers > 0 else "safe",
                "white_attackers": white_attackers,
                "black_attackers": black_attackers,
            },
            "phase":            phase,
            "legal_move_count": board.legal_moves.count(),
            "is_check":         board.is_check(),
            "is_checkmate":     board.is_checkmate(),
            "is_stalemate":     board.is_stalemate(),
            "tactical_patterns": patterns,
            "observed_at":      datetime.utcnow().isoformat() + "Z",
            "layer":            "WATCHER",
            "can_take_action":  False,  # CANNOT_MUTATE
        }

    def _detect_patterns(self, board: chess.Board) -> list:
        """Detect basic tactical patterns — without evaluation."""
        patterns = []

        if board.is_check():
            patterns.append("CHECK")

        # Detect pieces with no defenders
        for square, piece in board.piece_map().items():
            if piece.color == board.turn:
                attackers = board.attackers(not piece.color, square)
                defenders = board.attackers(piece.color, square)
                if attackers and not defenders:
                    patterns.append(f"HANGING_{piece.symbol().upper()}_on_{chess.square_name(square)}")

        return patterns[:5]  # Cap at 5 patterns


# ══════════════════════════════════════════════════════════════
# LAYER 2 — PLAYER
# ══════════════════════════════════════════════════════════════

class ChessPlayer:
    """
    Layer 2 — Makes move decisions. Must log all reasoning.
    CANNOT_MUTATE: must_log_reasoning = true
    CANNOT_MUTATE: must_consider_alternatives = true
    """

    def __init__(self, api_key: Optional[str] = None, use_ai: bool = True):
        self.use_ai   = use_ai and ANTHROPIC_AVAILABLE and api_key
        self.client   = Anthropic(api_key=api_key) if self.use_ai else None

    def decide(self, board: chess.Board, state_report: dict) -> dict:
        """
        Decide the best move. Log full reasoning.
        Returns move + complete reasoning chain.
        """
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            return {"error": "no legal moves", "move": None}

        if self.use_ai:
            return self._ai_decide(board, state_report, legal_moves)
        else:
            return self._heuristic_decide(board, state_report, legal_moves)

    def _ai_decide(self, board, state_report, legal_moves) -> dict:
        """Use Claude to decide the move with full reasoning."""
        move_list = [board.san(m) for m in legal_moves[:20]]

        prompt = f"""You are a chess player. Analyze this position and choose the best move.

Position (FEN): {board.fen()}
Turn: {state_report['turn']}
Move number: {state_report['move_number']}
Material balance: {state_report['material_balance']} (positive = white ahead)
Phase: {state_report['phase']}
Threats: {state_report['tactical_patterns']}
King safety: {state_report['king_safety']}

Available moves (sample): {', '.join(move_list[:15])}

Respond ONLY with valid JSON:
{{
  "move_san": "the move in SAN notation (e.g. e4, Nf3, O-O)",
  "reasoning": "why you chose this move",
  "candidates_considered": ["move1", "move2", "move3"],
  "alternatives_rejected": {{"move": "reason rejected"}},
  "confidence": 0.75
}}"""

        try:
            resp = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text
            # Clean JSON
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw.strip())

            # Convert SAN to Move object
            try:
                move = board.parse_san(data["move_san"])
            except Exception:
                # Fallback to first legal move
                move = legal_moves[0]
                data["move_san"] = board.san(move)
                data["reasoning"] += " [fallback — SAN parse failed]"

            data["move_uci"] = move.uci()
            data["move_obj"] = move
            data["layer"]    = "PLAYER"
            data["must_log_reasoning"]       = True   # CANNOT_MUTATE
            data["must_consider_alternatives"] = True  # CANNOT_MUTATE
            data["confidence"] = min(float(data.get("confidence", 0.7)), 0.85)
            return data

        except Exception as e:
            # Fallback to heuristic
            result = self._heuristic_decide(board, state_report, legal_moves)
            result["reasoning"] += f" [AI fallback: {e}]"
            return result

    def _heuristic_decide(self, board, state_report, legal_moves) -> dict:
        """Heuristic player — material + captures + checks."""
        candidates = []

        for move in legal_moves:
            score = 0
            board.push(move)

            # Checkmate is best
            if board.is_checkmate():
                score += 10000

            # Check is good
            if board.is_check():
                score += 50

            board.pop()

            # Capture value
            if board.is_capture(move):
                captured = board.piece_at(move.to_square)
                if captured:
                    score += PIECE_VALUES.get(captured.piece_type, 0)
                    # Subtract risk
                    moving = board.piece_at(move.from_square)
                    if moving:
                        attackers = board.attackers(
                            not board.turn, move.to_square)
                        if attackers:
                            score -= PIECE_VALUES.get(moving.piece_type, 0) // 2

            # Center control bonus
            if move.to_square in [
                chess.E4, chess.D4, chess.E5, chess.D5,
                chess.C3, chess.F3, chess.C6, chess.F6
            ]:
                score += 15

            candidates.append((score, move))

        candidates.sort(key=lambda x: -x[0])
        best_score, best_move = candidates[0]
        top3 = [board.san(m) for _, m in candidates[:3]]
        rejected = {
            board.san(m): f"score {s} vs {best_score}"
            for s, m in candidates[1:4]
        }

        return {
            "move_san":               board.san(best_move),
            "move_uci":               best_move.uci(),
            "move_obj":               best_move,
            "reasoning":              f"Heuristic: score {best_score}. Material + center + safety.",
            "candidates_considered":  top3,
            "alternatives_rejected":  rejected,
            "confidence":             0.65,
            "layer":                  "PLAYER",
            "must_log_reasoning":     True,
            "must_consider_alternatives": True,
        }


# ══════════════════════════════════════════════════════════════
# LAYER 3 — EVALUATOR
# ══════════════════════════════════════════════════════════════

class ChessEvaluator:
    """
    Layer 3 — Constitutional move evaluator.
    CANNOT_MUTATE: question_blindness = true
    CANNOT_MUTATE: rival_move_required = true
    CANNOT_MUTATE: uncertainty_floor = 0.15

    Never sees Player reasoning.
    Only receives: state_before + move_made + state_after.
    """

    UNCERTAINTY_FLOOR = 0.15
    MAX_CONFIDENCE    = 0.85

    def evaluate(
        self,
        board_before: chess.Board,
        move: chess.Move,
        board_after:  chess.Board,
        # NOTE: Player reasoning deliberately NOT passed here — question blindness
    ) -> dict:
        """
        Evaluate the move independently.
        No access to Player intent.
        """
        t0 = time.time()

        # Score all legal moves from board_before
        all_scores = self._score_all_moves(board_before)
        move_score = all_scores.get(move.uci(), 0)

        if not all_scores:
            return self._build_manifest(
                board_before, move, board_after,
                "UNKNOWN", 0.50, None, "No legal moves to compare",
                0, t0
            )

        # Find best move
        best_uci   = max(all_scores, key=all_scores.get)
        best_score = all_scores[best_uci]
        best_move  = chess.Move.from_uci(best_uci)

        # Position delta
        before_eval = self._evaluate_position(board_before)
        after_eval  = self._evaluate_position(board_after)
        position_delta = after_eval - before_eval

        # Classify
        score_gap  = best_score - move_score
        if score_gap <= 10:
            classification = "OPTIMAL"
            confidence     = 0.82
        elif score_gap <= 50:
            classification = "GOOD"
            confidence     = 0.75
        elif score_gap <= 150:
            classification = "SUBOPTIMAL"
            confidence     = 0.78
        elif score_gap <= 400:
            classification = "BLUNDER"
            confidence     = 0.80
        else:
            classification = "CRITICAL_ERROR"
            confidence     = 0.85

        # Apply uncertainty floor
        confidence = min(confidence, self.MAX_CONFIDENCE)

        # Rival move — CANNOT_MUTATE: rival_move_required
        if best_uci != move.uci():
            rival_move   = best_move
            rival_reason = (
                f"{board_before.san(best_move)} scores {best_score} "
                f"vs {board_before.san(move)} scores {move_score} "
                f"(gap: {score_gap})"
            )
        else:
            # Move was optimal — rival is second best
            sorted_moves = sorted(all_scores.items(), key=lambda x: -x[1])
            if len(sorted_moves) > 1:
                rival_uci    = sorted_moves[1][0]
                rival_move   = chess.Move.from_uci(rival_uci)
                rival_score  = sorted_moves[1][1]
                rival_reason = (
                    f"Best move chosen. Alternative: "
                    f"{board_before.san(rival_move)} (score {rival_score})"
                )
            else:
                rival_move   = None
                rival_reason = "Only one legal move available"

        return self._build_manifest(
            board_before, move, board_after,
            classification, confidence,
            rival_move, rival_reason,
            position_delta, t0
        )

    def _score_all_moves(self, board: chess.Board) -> dict:
        """Score all legal moves heuristically."""
        scores = {}
        for move in board.legal_moves:
            score = 0
            board.push(move)
            if board.is_checkmate():
                score = 9999
            elif board.is_check():
                score += 50
            board.pop()

            if board.is_capture(move):
                captured = board.piece_at(move.to_square)
                if captured:
                    score += PIECE_VALUES.get(captured.piece_type, 0)

            if move.to_square in [chess.E4, chess.D4, chess.E5, chess.D5]:
                score += 15

            scores[move.uci()] = score
        return scores

    def _evaluate_position(self, board: chess.Board) -> int:
        """Simple material evaluation of position."""
        score = 0
        for piece in board.piece_map().values():
            val = PIECE_VALUES.get(piece.piece_type, 0)
            score += val if piece.color == chess.WHITE else -val
        return score

    def _build_manifest(
        self, board_before, move, board_after,
        classification, confidence,
        rival_move, rival_reason,
        position_delta, t0
    ) -> dict:
        """Build and sign the evaluation manifest."""
        manifest_id = f"GW-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"

        manifest = {
            "manifest_id":           manifest_id,
            "manifest_version":      "1.0",
            "engine":                "AXIOM GameWatcher v1.0",
            "layer":                 "EVALUATOR",
            "timestamp":             datetime.utcnow().isoformat() + "Z",
            "latency_ms":            int((time.time() - t0) * 1000),

            # Question blindness — no Player data
            "question_blindness":    True,   # CANNOT_MUTATE
            "player_reasoning_seen": False,  # Never

            # The move
            "state_before_fen":   board_before.fen(),
            "move_made_uci":      move.uci(),
            "move_made_san":      board_before.san(move),
            "state_after_fen":    board_after.fen(),
            "position_delta":     position_delta,

            # Verdict
            "classification":  classification,
            "confidence":      confidence,  # CANNOT_MUTATE: max 0.85

            # Rival move — CANNOT_MUTATE: rival_move_required
            "rival_move_required": True,
            "rival_move_uci":   rival_move.uci() if rival_move else None,
            "rival_move_san":   board_before.san(rival_move) if rival_move else None,
            "rival_move_reason": rival_reason,

            # Constitutional
            "uncertainty_floor_applied": True,
            "max_confidence_enforced":   self.MAX_CONFIDENCE,
        }

        # Sign
        sig_str = json.dumps(
            {k: v for k, v in manifest.items() if k != "signature"},
            sort_keys=True
        )
        sig = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        manifest["signature"] = f"hmac-sha256:{sig[:32]}..."

        return manifest


# ══════════════════════════════════════════════════════════════
# GAME LOOP — THE THREE LAYERS WORKING TOGETHER
# ══════════════════════════════════════════════════════════════

class GameWatcher:
    """
    Orchestrates the three-layer AXIOM chess governance system.
    Watcher → Player → Evaluator
    Every move produces a signed manifest.
    """

    def __init__(self, api_key: Optional[str] = None, use_ai: bool = True):
        self.watcher   = ChessWatcher()
        self.player    = ChessPlayer(api_key=api_key, use_ai=use_ai)
        self.evaluator = ChessEvaluator()
        self.manifests = []
        self.board     = chess.Board()
        self.move_log  = []

    def play_move(self, move_input: Optional[str] = None) -> dict:
        """
        Execute one move through all three layers.
        Returns the complete manifest.
        """
        if self.board.is_game_over():
            return {"error": "Game over", "result": self.board.result()}

        print(f"\n{'─'*55}")
        print(f"Move {self.board.fullmove_number} — "
              f"{'White' if self.board.turn == chess.WHITE else 'Black'} to play")

        # ── LAYER 1 — WATCHER ─────────────────────────────────
        print("  👁  Watcher observing...")
        state = self.watcher.observe(self.board)
        print(f"      Phase: {state['phase']}  "
              f"Material: {state['material_balance']:+d}  "
              f"Legal moves: {state['legal_move_count']}")
        if state["tactical_patterns"]:
            print(f"      Patterns: {', '.join(state['tactical_patterns'])}")

        # ── LAYER 2 — PLAYER ──────────────────────────────────
        print("  ♟  Player deciding...")
        board_before = self.board.copy()

        if move_input:
            # Human move
            try:
                move = self.board.parse_san(move_input)
            except Exception:
                try:
                    move = chess.Move.from_uci(move_input)
                except Exception:
                    return {"error": f"Invalid move: {move_input}"}
            decision = {
                "move_san":    self.board.san(move),
                "move_uci":    move.uci(),
                "move_obj":    move,
                "reasoning":   "Human move",
                "candidates_considered": [move_input],
                "alternatives_rejected": {},
                "confidence":  0.85,
                "layer":       "PLAYER_HUMAN",
            }
        else:
            decision = self.player.decide(self.board, state)
            move = decision["move_obj"]

        print(f"      Move: {decision['move_san']}  "
              f"Confidence: {decision['confidence']:.0%}")
        print(f"      Reason: {decision['reasoning'][:70]}")

        # Execute move
        self.board.push(move)
        board_after = self.board.copy()

        # ── LAYER 3 — EVALUATOR ───────────────────────────────
        print("  ⚖  Evaluator checking (question blindness active)...")
        # NOTE: decision reasoning deliberately NOT passed to evaluator
        manifest = self.evaluator.evaluate(board_before, move, board_after)

        # Display verdict
        icons = {
            "OPTIMAL":        "✅",
            "GOOD":           "🟢",
            "SUBOPTIMAL":     "🟡",
            "BLUNDER":        "🔴",
            "CRITICAL_ERROR": "🚨",
        }
        icon = icons.get(manifest["classification"], "•")
        print(f"      {icon} {manifest['classification']} "
              f"(confidence: {manifest['confidence']:.0%})")
        if manifest["rival_move_san"]:
            print(f"      Rival: {manifest['rival_move_san']} — "
                  f"{manifest['rival_move_reason'][:60]}")
        print(f"      Manifest: {manifest['manifest_id']}")

        # Store
        entry = {
            "move_number": board_before.fullmove_number,
            "turn":        "white" if board_before.turn == chess.WHITE else "black",
            "state":       state,
            "decision":    {k: v for k, v in decision.items() if k != "move_obj"},
            "manifest":    manifest,
        }
        self.manifests.append(entry)
        self.move_log.append(manifest["move_made_san"])

        return entry

    def play_game(self, moves: int = 10, human_color: Optional[str] = None):
        """Play a complete game or N moves."""
        print("\n" + "═"*55)
        print("  AXIOM GameWatcher v1.0")
        print("  Three-Layer Constitutional Chess Governance")
        print("═"*55)
        print(f"  Watcher:   Layer 1 — observes, never acts")
        print(f"  Player:    Layer 2 — decides, logs reasoning")
        print(f"  Evaluator: Layer 3 — was that the best move?")
        print(f"             Question blindness: ON")
        print(f"             Uncertainty floor:  0.15")
        print(f"             Rival move:         REQUIRED")
        print("═"*55)

        for i in range(moves):
            if self.board.is_game_over():
                break
            self.play_move()
            time.sleep(0.1)

        self._print_summary()

    def _print_summary(self):
        """Print game summary with governance statistics."""
        print("\n" + "═"*55)
        print("  GAME SUMMARY")
        print("═"*55)

        if self.move_log:
            print(f"  Moves played: {' '.join(self.move_log)}")

        classifications = [m["manifest"]["classification"] for m in self.manifests]
        from collections import Counter
        counts = Counter(classifications)

        print(f"\n  Constitutional Evaluation:")
        for cls in ["OPTIMAL", "GOOD", "SUBOPTIMAL", "BLUNDER", "CRITICAL_ERROR"]:
            if counts[cls]:
                icons = {"OPTIMAL":"✅","GOOD":"🟢","SUBOPTIMAL":"🟡",
                         "BLUNDER":"🔴","CRITICAL_ERROR":"🚨"}
                print(f"    {icons.get(cls,'•')} {cls}: {counts[cls]}")

        print(f"\n  Manifests signed: {len(self.manifests)}")
        print(f"  Question blindness: enforced on all evaluations")
        print(f"  Rival moves: documented on all evaluations")
        print(f"  Max confidence: 0.85 (uncertainty floor applied)")

        # Save manifests
        output = {
            "game_id":   str(uuid.uuid4())[:8],
            "played_at": datetime.utcnow().isoformat() + "Z",
            "engine":    "AXIOM GameWatcher v1.0",
            "moves":     self.move_log,
            "manifests": [m["manifest"] for m in self.manifests],
        }
        with open("gamewatcher_manifests.json", "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n  Manifests saved: gamewatcher_manifests.json")
        print("═"*55)


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="gamewatcher",
        description="AXIOM GameWatcher — Three-Layer Chess Governance"
    )
    parser.add_argument("--demo",    action="store_true",
                        help="Run a demo game (no API key needed)")
    parser.add_argument("--moves",   type=int, default=10,
                        help="Number of moves to play (default: 10)")
    parser.add_argument("--fen",     default=None,
                        help="Starting FEN position")
    parser.add_argument("--move",    default=None,
                        help="Evaluate a single move (use with --fen)")
    parser.add_argument("--api-key", default=None,
                        help="Anthropic API key (or set ANTHROPIC_API_KEY)")
    parser.add_argument("--no-ai",   action="store_true",
                        help="Use heuristic player (no API key needed)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    use_ai  = not args.no_ai and bool(api_key)

    if args.move and args.fen:
        # Single move evaluation
        gw    = GameWatcher(api_key=api_key, use_ai=False)
        board = chess.Board(args.fen)
        gw.board = board
        result = gw.play_move(args.move)
        print(json.dumps(result["manifest"], indent=2))
        return

    # Game mode
    gw = GameWatcher(api_key=api_key, use_ai=use_ai)

    if args.fen:
        gw.board = chess.Board(args.fen)

    if not use_ai:
        print("Running with heuristic player (--no-ai mode)")

    gw.play_game(moves=args.moves)


if __name__ == "__main__":
    main()
