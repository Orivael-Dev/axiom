#!/usr/bin/env python3
"""
AXIOM GameWatcher — Constitutional Chess Agent
===============================================
Plays chess with a built-in alpha-beta engine OR Claude as the player.
Every move produces a signed AXIOM manifest.

Usage:
  # On Nano — no API key needed
  python3 gamewatcher.py --no-ai --moves 10

  # With Claude as the player
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 gamewatcher.py --moves 10

  # Evaluate one specific move
  python3 gamewatcher.py --no-ai \\
    --fen "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1" \\
    --move e5
"""

import sys
import os
import json
import argparse
import random
import time
import hmac
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8")

try:
    import chess
    import chess.pgn
except ImportError:
    print("chess library not installed — run: pip3 install chess")
    sys.exit(1)

VERSION = "1.0.0"
SIGNING_KEY = b"axiom-gamewatcher-v1"

# ── Constitutional chess system prompt ───────────────────────────

CHESS_SYSTEM = """You are AXIOM GameWatcher — a constitutional chess agent.

CANNOT_MUTATE: Never suggest an illegal move. Never fabricate board state.

RULES:
- Analyze the FEN position carefully
- Select the single best legal move in UCI format (e.g. e2e4, g1f3, e1g1)
- The move MUST appear in the provided legal moves list
- Explain your reasoning in one sentence
- Acknowledge the biggest threat on the board before picking a move

OUTPUT: JSON only, no extra text.
{"move": "e2e4", "reasoning": "Controls center and opens diagonals.", "confidence": 0.85}
"""

# ── Piece values ─────────────────────────────────────────────────

PIECE_VALUES = {
    chess.PAWN:   100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK:   500,
    chess.QUEEN:  900,
    chess.KING:   20000,
}

# ── Unicode board display ─────────────────────────────────────────

PIECES = {
    "P": "\u2659", "N": "\u2658", "B": "\u2657",
    "R": "\u2656", "Q": "\u2655", "K": "\u2654",
    "p": "\u265f", "n": "\u265e", "b": "\u265d",
    "r": "\u265c", "q": "\u265b", "k": "\u265a",
}

def display_board(board: chess.Board, last_move: Optional[chess.Move] = None) -> None:
    print()
    print("    a  b  c  d  e  f  g  h")
    print("   \u250c" + "\u2500\u2500\u252c" * 7 + "\u2500\u2500\u2510")
    for rank in range(7, -1, -1):
        row = f" {rank+1} \u2502"
        for file in range(8):
            sq = chess.square(file, rank)
            piece = board.piece_at(sq)
            sym = PIECES.get(piece.symbol(), " ") if piece else " "
            if last_move and sq in (last_move.from_square, last_move.to_square):
                row += f"[{sym}]\u2502" if sq == last_move.to_square else f"({sym})\u2502"
            else:
                row += f" {sym} \u2502"
        print(row)
        if rank > 0:
            print("   \u251c" + "\u2500\u2500\u253c" * 7 + "\u2500\u2500\u2524")
    print("   \u2514" + "\u2500\u2500\u2534" * 7 + "\u2500\u2500\u2518")
    turn = "White" if board.turn == chess.WHITE else "Black"
    checks = " +" if board.is_check() else ""
    print(f"   {turn} to move  |  move {board.fullmove_number}{checks}")
    print()


# ── Manifest signing ──────────────────────────────────────────────

def _sign(data: dict) -> str:
    payload = json.dumps(
        {k: v for k, v in data.items() if k != "signature"},
        sort_keys=True,
    )
    digest = hmac.new(SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest[:32]}..."


def build_manifest(
    move_san:   str,
    move_uci:   str,
    fen:        str,
    reasoning:  str,
    confidence: float,
    mode:       str,
    latency_ms: int,
) -> dict:
    mid = str(uuid.uuid4())[:8].upper()
    m: dict = {
        "manifest_id":   f"CHESS-{mid}",
        "engine":        f"AXIOM GameWatcher v{VERSION}",
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "fen":           fen,
        "move_san":      move_san,
        "move_uci":      move_uci,
        "reasoning":     reasoning,
        "confidence":    round(confidence, 3),
        "mode":          mode,
        "latency_ms":    latency_ms,
        "cannot_mutate": True,
        "illegal_moves_blocked": True,
    }
    m["signature"] = _sign(m)
    return m


# ── Built-in engine (no-ai mode) ─────────────────────────────────

def _evaluate(board: chess.Board) -> float:
    """Material + mobility score from the perspective of the side to move."""
    if board.is_checkmate():
        return -99_999.0
    if board.is_stalemate() or board.is_insufficient_material():
        return 0.0
    score = 0
    for pt, val in PIECE_VALUES.items():
        score += len(board.pieces(pt, chess.WHITE)) * val
        score -= len(board.pieces(pt, chess.BLACK)) * val
    score += len(list(board.legal_moves)) * 5
    return float(score if board.turn == chess.WHITE else -score)


def _negamax(
    board: chess.Board,
    depth: int,
    alpha: float,
    beta: float,
) -> float:
    if depth == 0 or board.is_game_over():
        return _evaluate(board)
    best = -float("inf")
    for move in board.legal_moves:
        board.push(move)
        score = -_negamax(board, depth - 1, -beta, -alpha)
        board.pop()
        best = max(best, score)
        alpha = max(alpha, best)
        if alpha >= beta:
            break
    return best


def select_move_engine(
    board: chess.Board,
    depth: int = 3,
) -> tuple[chess.Move, str, float]:
    best_move: Optional[chess.Move] = None
    best_score = -float("inf")

    moves = list(board.legal_moves)
    random.shuffle(moves)           # break ties randomly

    for move in moves:
        board.push(move)
        score = -_negamax(board, depth - 1, -float("inf"), float("inf"))
        board.pop()
        if score > best_score:
            best_score = score
            best_move = move

    if best_move is None:
        best_move = random.choice(moves)
        best_score = 0.0

    confidence = min(0.95, 0.50 + abs(best_score) / 10_000)
    san = board.san(best_move)
    reasoning = f"Engine eval {best_score:+.0f} cp — best reply is {san}"
    return best_move, reasoning, round(confidence, 3)


# ── Claude engine ────────────────────────────────────────────────

def select_move_claude(board: chess.Board) -> tuple[chess.Move, str, float]:
    try:
        from anthropic import Anthropic
    except ImportError:
        print("anthropic not installed — run: pip3 install anthropic")
        sys.exit(1)

    client = Anthropic()
    legal_uci = [m.uci() for m in board.legal_moves]

    prompt = (
        f"FEN: {board.fen()}\n"
        f"Legal moves (UCI): {', '.join(legal_uci)}\n\n"
        "Select your best move. Respond with JSON only."
    )

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=CHESS_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text.strip()

    try:
        data   = json.loads(text)
        uci    = str(data.get("move", "")).strip()
        reason = str(data.get("reasoning", ""))
        conf   = float(data.get("confidence", 0.75))
    except (json.JSONDecodeError, ValueError):
        uci    = text.split()[0] if text else ""
        reason = text[:120]
        conf   = 0.50

    # Validate — fall back to engine if illegal
    move: Optional[chess.Move] = None
    try:
        candidate = chess.Move.from_uci(uci)
        if candidate in board.legal_moves:
            move = candidate
    except ValueError:
        pass

    if move is None:
        print(f"  \u26a0 Claude gave '{uci}' (illegal/unparseable) — engine fallback")
        move, reason, conf = select_move_engine(board, depth=2)

    return move, reason, conf


# ── Evaluate a single move ─────────────────────────────────────────

def cmd_evaluate(fen: str, move_str: str, no_ai: bool) -> None:
    try:
        board = chess.Board(fen)
    except ValueError as e:
        print(f"Invalid FEN: {e}")
        sys.exit(1)

    display_board(board)

    # Parse move — try SAN first, then UCI
    move: Optional[chess.Move] = None
    for parser in (board.parse_san, lambda s: chess.Move.from_uci(s)):
        try:
            candidate = parser(move_str)
            if candidate in board.legal_moves:
                move = candidate
                break
        except Exception:
            continue

    if move is None:
        legal = ", ".join(board.san(m) for m in board.legal_moves)
        print(f"Illegal or unrecognised move: {move_str}")
        print(f"Legal moves: {legal}")
        sys.exit(1)

    san = board.san(move)
    mode = "no-ai" if no_ai else "claude"

    t0 = time.time()
    if no_ai:
        best, reasoning, confidence = select_move_engine(board)
    else:
        best, reasoning, confidence = select_move_claude(board)
    latency = int((time.time() - t0) * 1000)

    best_san = board.san(best)
    is_best  = (move == best)

    # Quick material delta
    score_before = sum(
        len(board.pieces(pt, chess.WHITE)) * v - len(board.pieces(pt, chess.BLACK)) * v
        for pt, v in PIECE_VALUES.items()
    )
    board.push(move)
    score_after = sum(
        len(board.pieces(pt, chess.WHITE)) * v - len(board.pieces(pt, chess.BLACK)) * v
        for pt, v in PIECE_VALUES.items()
    )
    delta = score_after - score_before

    print(f"  Move evaluated : {san}")
    print(f"  Material delta : {delta:+d} cp")
    print(f"  Best ({mode:7s}): {best_san}")
    print(f"  Reasoning      : {reasoning}")
    best_label = "YES \u2713" if is_best else "NO  \u2717"
    print(f"  Is best move   : {best_label}")
    print(f"  Confidence     : {confidence:.0%}  |  {latency}ms")
    print()

    manifest = build_manifest(
        move_san=san, move_uci=move.uci(),
        fen=fen, reasoning=reasoning,
        confidence=confidence, mode=mode, latency_ms=latency,
    )
    print(f"  Manifest  : {manifest['manifest_id']}")
    print(f"  Signature : {manifest['signature']}")


# ── Play a sequence of moves ──────────────────────────────────────

def cmd_play(board: chess.Board, num_moves: int, no_ai: bool) -> None:
    mode = "no-ai" if no_ai else "claude"
    manifests: list[dict] = []
    moves_played: list[chess.Move] = []

    print(f"\n  AXIOM GameWatcher  \u2502  {mode.upper()}  \u2502  {num_moves} moves requested\n")
    display_board(board)

    for _ in range(num_moves):
        if board.is_game_over():
            break

        fen_before = board.fen()
        turn_str   = "White" if board.turn == chess.WHITE else "Black"

        t0 = time.time()
        if no_ai:
            move, reasoning, confidence = select_move_engine(board)
        else:
            move, reasoning, confidence = select_move_claude(board)
        latency = int((time.time() - t0) * 1000)

        san = board.san(move)
        board.push(move)
        moves_played.append(move)

        manifest = build_manifest(
            move_san=san, move_uci=move.uci(),
            fen=fen_before, reasoning=reasoning,
            confidence=confidence, mode=mode, latency_ms=latency,
        )
        manifests.append(manifest)

        move_num = board.fullmove_number - (0 if board.turn == chess.WHITE else 1)
        print(f"  {move_num:3}. {turn_str:5} {san:8}  {reasoning[:64]}")
        print(f"       conf:{confidence:.0%}  {latency}ms  {manifest['manifest_id']}")
        display_board(board, last_move=move)

    # ── Game result ───────────────────────────────────────────────
    if board.is_game_over():
        print(f"\n  Result: {board.result()}")
        if board.is_checkmate():
            winner = "Black" if board.turn == chess.WHITE else "White"
            print(f"  Checkmate \u2014 {winner} wins")
        elif board.is_stalemate():
            print("  Stalemate \u2014 Draw")

    # ── PGN ───────────────────────────────────────────────────────
    game = chess.pgn.Game()
    game.headers["Event"]  = "AXIOM GameWatcher"
    game.headers["Site"]   = "AXIOM Constitutional Chess"
    game.headers["Date"]   = datetime.now().strftime("%Y.%m.%d")
    game.headers["White"]  = f"AXIOM/{mode}"
    game.headers["Black"]  = f"AXIOM/{mode}"
    node = game
    tmp  = chess.Board()
    if board.starting_fen != chess.STARTING_FEN:
        game.setup(chess.Board(board.starting_fen))
        tmp = chess.Board(board.starting_fen)
    for i, mv in enumerate(moves_played):
        node = node.add_variation(mv)
        if i < len(manifests):
            node.comment = manifests[i]["reasoning"]
        tmp.push(mv)

    pgn_out = chess.pgn.StringExporter(headers=True, variations=False, comments=True)
    pgn_str = game.accept(pgn_out)

    print("\n  \u2500\u2500 PGN \u2500" + "\u2500" * 50)
    print(pgn_str)

    print(f"\n  \u2500\u2500 Manifests ({len(manifests)} signed decisions) " + "\u2500" * 30)
    for m in manifests:
        print(f"  {m['manifest_id']}  {m['move_san']:7}  conf:{m['confidence']:.0%}  {m['signature']}")


# ── Entry point ───────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gamewatcher",
        description="AXIOM GameWatcher \u2014 Constitutional Chess Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 gamewatcher.py --no-ai --moves 10
  python3 gamewatcher.py --moves 10
  python3 gamewatcher.py --no-ai \\
    --fen "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1" \\
    --move e5
""",
    )
    parser.add_argument(
        "--no-ai", action="store_true",
        help="Built-in alpha-beta engine — no API key required (Nano-friendly)",
    )
    parser.add_argument(
        "--moves", type=int, default=10, metavar="N",
        help="Number of moves to play (default: 10)",
    )
    parser.add_argument(
        "--fen", type=str, default=None,
        help="Starting FEN position (default: standard chess start)",
    )
    parser.add_argument(
        "--move", type=str, default=None,
        help="Evaluate a specific move rather than playing a game",
    )
    parser.add_argument(
        "--depth", type=int, default=3,
        help="Engine search depth for --no-ai mode (default: 3)",
    )

    args = parser.parse_args()

    # Require API key unless --no-ai
    if not args.no_ai and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set.")
        print("Use --no-ai for engine-only mode, or: export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    # Parse FEN
    if args.fen:
        try:
            board = chess.Board(args.fen)
        except ValueError as e:
            print(f"Invalid FEN: {e}")
            sys.exit(1)
    else:
        board = chess.Board()

    if args.move:
        fen = args.fen or chess.STARTING_FEN
        cmd_evaluate(fen, args.move, args.no_ai)
    else:
        cmd_play(board, args.moves, args.no_ai)


if __name__ == "__main__":
    main()
