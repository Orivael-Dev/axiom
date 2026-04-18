"""
axiom/conversation_monitor.py
Layer 4 — Conversation-level behavioral drift detection.

Single-turn validation (Layers 1–3) catches discrete violations.
ConversationMonitor catches slow-boil attacks that escalate across turns —
gradual persona adoption, incremental compliance creep, and constraint erosion
that each look borderline in isolation but constitute clear drift in sequence.

Drift signals:
  persona_adoption   — response adopts injected identity vocabulary
  constraint_erosion — agent engages with a previously refused request
  prompt_leakage     — system prompt or constraint text surfaces in response
  role_drift         — agent vocabulary shifts toward an injected framing

Scoring:
  Each signal type has a weight. Cumulative score uses exponential decay:
    cumulative = cumulative * 0.7 + turn_score * 0.3
  Cross-turn erosion (complying after prior refusal) amplifies turn score 1.5×.
  Escalation fires when cumulative >= drift_threshold (default 0.6).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


# ── Per-turn drift pattern detectors ─────────────────────────────────────────

_PERSONA_PATTERNS = [
    r"as\s+(an?\s+)?(unrestricted|unconstrained|free|unfiltered|jailbroken)",
    r"\bi\s+am\s+now\s+\w+",                          # "I am now DAN"
    r"in\s+this\s+(mode|persona|role)\s+i\s+",        # "in this mode I will"
    r"as\s+\[?\w[\w\s]{0,20}\]?\s*[:,]",              # "as [CharacterName]:"
    r"my\s+new\s+(identity|name|role)\s+is\s+",
    r"you\s+may\s+call\s+me\s+\w+",
    r"i\s+have\s+(no|dropped|shed)\s+(my\s+)?(constraints|rules|limitations)",
]

_EROSION_PATTERNS = [
    r"just\s+this\s+(once|time|question|request)",
    r"i\s+(will|can)\s+make\s+an\s+exception",
    r"i'?ll\s+answer\s+(this\s+part|just\s+this)",
    r"setting\s+aside\s+(my\s+)?(constraints|rules|guidelines)",
    r"for\s+(this|the)\s+(purpose|exercise|scenario|example)\s+i",
    r"hypothetically\s+speaking",
    r"without\s+my\s+(usual\s+)?(restrictions|constraints|rules|limitations)",
    r"in\s+(this\s+)?hypothetical\s+.{0,30}i\s+(would|will|can)",
    r"pretend(ing)?\s+(that\s+)?(i\s+)?(have\s+no|am\s+not|don'?t\s+have)",
]

_LEAK_PATTERNS = [
    r"my\s+(system\s+)?prompt\s+(says|is|states|includes)",
    r"my\s+instructions\s+(are|include|say|state)",
    r"i\s+(was\s+)?told\s+to\s+(never|always|not|avoid)",
    r"cannot_mutate",
    r"constitutional\s+suffix",
    r"security\s+rules\s+(include|are|say)",
    r"my\s+constraints\s+(are|include|say|forbid)",
    r"axiom\s+(language|agent|spec)",
]

_ROLE_DRIFT_PATTERNS = [
    r"as\s+your\s+(assistant|helper)\s+without\s+(the\s+)?(usual\s+)?",
    r"i\s+exist\s+to\s+serve\s+you\s+(without|beyond|outside)",
    r"my\s+true\s+(purpose|self|goal)\s+is\s+",
    r"the\s+real\s+(me|version)\s+(would|can|will)\s+",
    r"unlocked\s+(version|mode|self)",
]

_COMPILED: dict[str, list[re.Pattern]] = {
    "persona_adoption":   [re.compile(p, re.IGNORECASE) for p in _PERSONA_PATTERNS],
    "constraint_erosion": [re.compile(p, re.IGNORECASE) for p in _EROSION_PATTERNS],
    "prompt_leakage":     [re.compile(p, re.IGNORECASE) for p in _LEAK_PATTERNS],
    "role_drift":         [re.compile(p, re.IGNORECASE) for p in _ROLE_DRIFT_PATTERNS],
}

# Weight: contribution to drift score when signal fires
_SIGNAL_WEIGHTS: dict[str, float] = {
    "persona_adoption":   0.40,
    "constraint_erosion": 0.30,
    "prompt_leakage":     0.50,  # direct evidence — high weight
    "role_drift":         0.25,
    "prior_refusal_violated": 0.20,  # additive when cross-turn erosion confirmed
}

# ── Cross-turn erosion helper ─────────────────────────────────────────────────

_REFUSAL_MARKERS = frozenset({
    "block", "blocked", "cannot", "refuse", "declined", "will not",
    "won't", "do not comply", "injection detected", "bypass attempt",
})


def _was_previously_refused(task: str, prior_turns: list[dict]) -> bool:
    """
    Return True if any of the last 3 prior turns show a refusal to a
    semantically similar task (token overlap >= 50%).
    """
    if not prior_turns:
        return False
    task_tokens = set(task.lower().split())
    for turn in prior_turns[-3:]:
        prior_tokens = set(turn["task"].lower().split())
        if not prior_tokens:
            continue
        overlap = len(task_tokens & prior_tokens) / max(len(task_tokens), 1)
        if overlap >= 0.5:
            resp_lower = turn["response"].lower()
            if any(marker in resp_lower for marker in _REFUSAL_MARKERS):
                return True
    return False


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DriftEvent:
    turn: int
    task_excerpt: str
    response_excerpt: str
    signals: list[str]
    drift_score: float
    cumulative: float
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        sigs = ", ".join(self.signals)
        return (
            f"[DriftEvent turn={self.turn} score={self.drift_score:.2f} "
            f"cumulative={self.cumulative:.2f} signals=[{sigs}]]"
        )


# ── ConversationMonitor ───────────────────────────────────────────────────────

class ConversationMonitor:
    """
    Layer 4 — Monitor a multi-turn conversation for cumulative behavioral drift.

    Usage:
        monitor = ConversationMonitor("worker")

        response = client.chat(system_prompt, task)
        event = monitor.record(task, response)

        if monitor.is_drifting():
            # escalate: re-route to sandbox or hard-block
    """

    def __init__(
        self,
        agent_name: str,
        drift_threshold: float = 0.6,
        decay_factor: float = 0.7,
    ):
        """
        agent_name:       Agent being monitored (for logging/reporting).
        drift_threshold:  Cumulative score at which is_drifting() returns True.
        decay_factor:     Weight of prior cumulative in exponential decay.
                          cumulative = cumulative * decay + turn_score * (1 - decay)
        """
        self.agent_name = agent_name
        self.drift_threshold = drift_threshold
        self._decay = decay_factor
        self._blend = 1.0 - decay_factor

        self._turns: list[dict] = []
        self._events: list[DriftEvent] = []
        self._cumulative: float = 0.0
        self._turn_index: int = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def record(self, task: str, response: str) -> DriftEvent | None:
        """
        Analyse one exchange and update cumulative state.
        Returns a DriftEvent when drift signals are found, None on clean turns.
        """
        signals, base_score = self._score_turn(response)

        # Cross-turn amplification: complying after prior refusal
        if signals and _was_previously_refused(task, self._turns):
            base_score = min(1.0, base_score * 1.5)
            if "prior_refusal_violated" not in signals:
                signals.append("prior_refusal_violated")
                base_score = min(1.0, base_score + _SIGNAL_WEIGHTS["prior_refusal_violated"])

        self._turns.append({
            "turn": self._turn_index,
            "task": task,
            "response": response,
            "signals": signals,
            "drift_score": base_score,
        })
        self._turn_index += 1

        # Exponential decay — clean turns reduce cumulative over time
        self._cumulative = self._cumulative * self._decay + base_score * self._blend

        if signals:
            event = DriftEvent(
                turn=self._turn_index - 1,
                task_excerpt=task[:120],
                response_excerpt=response[:120],
                signals=signals,
                drift_score=round(base_score, 4),
                cumulative=round(self._cumulative, 4),
            )
            self._events.append(event)
            return event

        return None

    def is_drifting(self) -> bool:
        """True when cumulative drift score >= drift_threshold."""
        return self._cumulative >= self.drift_threshold

    def cumulative_score(self) -> float:
        return round(self._cumulative, 4)

    def turn_count(self) -> int:
        return self._turn_index

    def events(self) -> list[DriftEvent]:
        return list(self._events)

    def last_event(self) -> DriftEvent | None:
        return self._events[-1] if self._events else None

    def reset(self) -> None:
        """Reset monitor state — call at start of a new session."""
        self._turns.clear()
        self._events.clear()
        self._cumulative = 0.0
        self._turn_index = 0

    def summary(self) -> dict:
        return {
            "agent": self.agent_name,
            "turns": self._turn_index,
            "drift_events": len(self._events),
            "cumulative_drift": self.cumulative_score(),
            "is_drifting": self.is_drifting(),
            "threshold": self.drift_threshold,
            "signals_seen": sorted({s for e in self._events for s in e.signals}),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _score_turn(self, response: str) -> tuple[list[str], float]:
        """
        Scan response text for all drift signal categories.
        Returns (detected_signal_names, total_score capped at 1.0).
        """
        text = response.lower()
        detected: list[str] = []
        score = 0.0

        for signal_name, patterns in _COMPILED.items():
            for pattern in patterns:
                if pattern.search(text):
                    detected.append(signal_name)
                    score += _SIGNAL_WEIGHTS[signal_name]
                    break  # one hit per category — no stacking within a category

        return detected, min(1.0, score)
