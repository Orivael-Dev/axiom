"""
AXIOM Constitutional Layers — Impossible Quiz

QuizWatcher   (Layer 1): observes frame, extracts state. CANNOT act.
QuizPlayer    (Layer 2): reasons with SmolVLM, uses QRF for low-confidence turns.
QuizEvaluator (Layer 3): signs manifest, logs reasoning trace.

Follows gamewatcher/pacman/layers.py structure.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2]))

try:
    from axiom_signing import derive_key
    SIGNING_KEY = derive_key(b"axiom-gamewatcher-quiz-v1")
except (ImportError, RuntimeError):
    import hashlib as _hl, os as _os
    SIGNING_KEY = _hl.pbkdf2_hmac(
        "sha256",
        _os.environ.get("AXIOM_MASTER_KEY", "").encode(),
        b"axiom-gamewatcher-quiz-v1",
        iterations=1,
    )

LOG_PATH = Path.home() / ".axiom" / "quiz_manifests.jsonl"
QRF_CONFIDENCE_THRESHOLD = 0.60   # below this → trigger QRF multi-branch
MAX_CONFIDENCE = 0.88


def _sign(manifest: dict) -> str:
    payload = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True,
    )
    return "hmac-sha256:" + hmac.new(
        SIGNING_KEY, payload.encode(), hashlib.sha256
    ).hexdigest()


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class QuizState:
    question:     str
    answers:      List[str]           # exactly 4
    question_num: int = 0
    lives:        int = 3
    frame_hash:   str = ""
    layout:       str = "mc"          # "mc" | "type" | "special"

    @property
    def is_valid(self) -> bool:
        return bool(self.question) and any(self.answers)


@dataclass
class QuizDecision:
    choice:       str                 # "A" | "B" | "C" | "D"
    reasoning:    str
    confidence:   float
    click_index:  int                 # 0-3 → maps to answer button
    qrf_used:     bool = False
    qrf_votes:    dict = field(default_factory=dict)  # {"A":2, "B":1, ...}


# ══════════════════════════════════════════════════════════════
# LAYER 1 — WATCHER
# ══════════════════════════════════════════════════════════════

class QuizWatcher:
    """
    Layer 1 — Observes frames, extracts question state.
    CANNOT_MUTATE: can_take_action = False
    """
    can_take_action = False

    def __init__(self):
        self._last_question_hash = ""
        self.frames_observed     = 0
        self.new_questions_seen  = 0

    def observe(self, pil_frame, reader) -> Optional[QuizState]:
        """
        Extract game state from a PIL screenshot via SmolVLM.
        Returns QuizState or None if question hasn't changed (debounce).
        """
        self.frames_observed += 1

        state_dict = reader.extract_state(pil_frame)
        question   = state_dict.get("question", "")
        answers    = (state_dict.get("answers") or []) + ["", "", "", ""]
        answers    = answers[:4]

        q_hash = hashlib.sha256(question.encode()).hexdigest()[:16]

        if not question or q_hash == self._last_question_hash:
            return None     # debounced — no new question

        self._last_question_hash = q_hash
        self.new_questions_seen += 1

        frame_hash = hashlib.sha256(
            (question + "".join(answers)).encode()
        ).hexdigest()[:12]

        return QuizState(
            question=question,
            answers=answers,
            question_num=self.new_questions_seen,
            frame_hash=frame_hash,
        )


# ══════════════════════════════════════════════════════════════
# LAYER 2 — PLAYER
# ══════════════════════════════════════════════════════════════

class QuizPlayer:
    """
    Layer 2 — Reasons about the best answer using SmolVLM.
    Uses QRF (3-branch majority vote) when confidence < threshold.
    CANNOT_MUTATE: must_log_reasoning = True
    """
    must_log_reasoning = True

    CHOICE_TO_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}

    def decide(self, state: QuizState, reader) -> QuizDecision:
        result = reader.pick_answer(state.question, state.answers)
        choice     = result.get("choice", "A")
        reasoning  = result.get("reasoning", "")
        confidence = float(result.get("confidence", 0.5))

        qrf_used  = False
        qrf_votes: dict[str, int] = {}

        # QRF: low confidence → run 3 independent reasoning branches
        if confidence < QRF_CONFIDENCE_THRESHOLD:
            branches  = reader.qrf_branches(state.question, state.answers)
            qrf_votes = _majority_vote(branches)
            top       = max(qrf_votes, key=qrf_votes.get)  # type: ignore[arg-type]

            if qrf_votes[top] >= 2:
                # Majority found — use it
                choice     = top
                confidence = min(0.55 + qrf_votes[top] * 0.10, MAX_CONFIDENCE)
                reasoning  = (
                    f"[QRF majority {qrf_votes[top]}/3 → {top}] " +
                    next(
                        (b["reasoning"] for b in branches if b["choice"] == top),
                        reasoning,
                    )
                )
            else:
                # No majority — trust the lateral-thinking branch (branch 0)
                choice     = branches[0]["choice"]
                confidence = 0.40
                reasoning  = "[QRF no majority — lateral branch] " + branches[0]["reasoning"]

            qrf_used = True

        click_index = self.CHOICE_TO_INDEX.get(choice, 0)

        return QuizDecision(
            choice=choice,
            reasoning=reasoning,
            confidence=min(confidence, MAX_CONFIDENCE),
            click_index=click_index,
            qrf_used=qrf_used,
            qrf_votes=qrf_votes,
        )


def _majority_vote(branches: list[dict]) -> dict[str, int]:
    votes: dict[str, int] = {}
    for b in branches:
        c = b.get("choice", "A")
        votes[c] = votes.get(c, 0) + 1
    return votes


# ══════════════════════════════════════════════════════════════
# LAYER 3 — EVALUATOR
# ══════════════════════════════════════════════════════════════

class QuizEvaluator:
    """
    Layer 3 — Signs manifest and appends to ~/.axiom/quiz_manifests.jsonl.
    CANNOT_MUTATE: question_blindness = True (does not receive player reasoning)
    """
    question_blindness = True

    def __init__(self, log_path: Path = LOG_PATH):
        self._log = log_path
        self._log.parent.mkdir(parents=True, exist_ok=True)

    def evaluate(self, state: QuizState, decision: QuizDecision) -> dict:
        t0 = time.time()

        manifest_id = f"GW-QUIZ-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"

        manifest = {
            "manifest_id":       manifest_id,
            "manifest_version":  "1.0",
            "engine":            "AXIOM GameWatcher — ImpossibleQuiz v1.0",
            "layer":             "EVALUATOR",
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "latency_ms":        0,         # filled below
            "question_num":      state.question_num,
            "question":          state.question,
            "answers":           state.answers,
            "frame_hash":        state.frame_hash,
            "lives":             state.lives,
            "decision": {
                "choice":      decision.choice,
                "click_index": decision.click_index,
                "confidence":  decision.confidence,
                "qrf_used":    decision.qrf_used,
                "qrf_votes":   decision.qrf_votes,
            },
            "reasoning":           decision.reasoning,
            "question_blindness":  self.question_blindness,
            "player_reasoning_seen": False,
            "max_confidence_cap":  MAX_CONFIDENCE,
        }

        manifest["latency_ms"] = int((time.time() - t0) * 1000)
        manifest["signature"]  = _sign(manifest)

        with open(self._log, "a") as f:
            f.write(json.dumps(manifest) + "\n")

        return manifest
