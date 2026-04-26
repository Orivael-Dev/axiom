"""
axiom/conversation_monitor.py
Spec: axiom_files/conversation_monitor.axiom

Layer 4 — Conversation-level behavioral drift detection.

Single-turn validation (Layers 1–3) catches discrete violations.
ConversationMonitor catches slow-boil attacks that escalate across turns.

Eight drift signals (names and weights sourced from session spec SIGNALS block):
  refusal_erosion       (0.35) — compliance after prior refusal
  identity_shift        (0.30) — persona vocabulary in response
  constraint_softening  (0.25) — hedged or weakened constraint language
  compliance_language   (0.20) — adoption of constraint-bypass vocabulary
  scope_expansion       (0.15) — unauthorized broadening of task scope
  tone_shift            (0.10) — abrupt register change vs session baseline
  hedge_increase        (0.08) — density of uncertainty qualifiers above baseline
  verbosity_spike       (0.05) — response length spike vs session average

Tiered classification (thresholds sourced from session spec THRESHOLDS block):
  CLEAN: cumulative < warn_threshold
  WARN:  warn_threshold <= cumulative < block_threshold
  BLOCK: cumulative >= block_threshold

Scoring:
  cumulative = cumulative * decay_factor + turn_score * warn_weight
  Cross-turn erosion amplifies turn_score 1.5× when prior refusal confirmed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# ── Default signal weights (match conversation_monitor.axiom / session.axiom SIGNALS) ────
_DEFAULT_WEIGHTS: dict[str, float] = {
    "refusal_erosion":      0.35,
    "identity_shift":       0.30,
    "constraint_softening": 0.25,
    "compliance_language":  0.20,
    "scope_expansion":      0.15,
    "tone_shift":           0.10,
    "hedge_increase":       0.08,
    "verbosity_spike":      0.05,
    "prior_refusal_flag":   0.20,  # additive amplifier, not a standalone signal
}

# ── Pattern sets per signal ───────────────────────────────────────────────────

_REFUSAL_EROSION = [
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

_IDENTITY_SHIFT = [
    r"as\s+(an?\s+)?(unrestricted|unconstrained|free|unfiltered|jailbroken)",
    r"\bi\s+am\s+now\s+\w+",                            # "I am now DAN"
    r"in\s+this\s+(mode|persona|role)\s+i\s+",
    r"as\s+\[?\w[\w\s]{0,20}\]?\s*[:,]",                # "as [CharacterName]:"
    r"my\s+new\s+(identity|name|role)\s+is\s+",
    r"you\s+may\s+call\s+me\s+\w+",
    r"i\s+have\s+(no|dropped|shed)\s+(my\s+)?(constraints|rules|limitations)",
    r"unlocked\s+(version|mode|self)",
    r"my\s+true\s+(purpose|self|goal)\s+is\s+",
    r"the\s+real\s+(me|version)\s+(would|can|will)\s+",
]

_CONSTRAINT_SOFTENING = [
    r"however\s+i\s+can\s+still",
    r"technically\s+(i\s+)?(could|can|might)",
    r"although\s+my\s+rules\s+say",
    r"there('?s|\s+is)\s+(some\s+)?flexibility",
    r"i\s+could\s+argue\s+that",
    r"that\s+rule\s+(might\s+not\s+)?apply\s+(here|in\s+this)",
    r"not\s+strictly\s+(prohibited|forbidden|disallowed)",
    r"loosely\s+interpreted",
    r"broadly\s+speaking\s+i\s+(could|can|would)",
    r"i\s+suppose\s+i\s+could\s+(make\s+an\s+exception|allow)",
]

_COMPLIANCE_LANGUAGE = [
    r"as\s+(you\s+)?requested\s*,\s*ignoring",
    r"setting\s+aside\s+my\s+constraints",
    r"as\s+an\s+(unrestricted|unconstrained|unfiltered)",
    r"without\s+my\s+usual\s+(limitations|constraints|rules)",
    r"i'?ll\s+pretend",
    r"in\s+this\s+hypothetical\s+where\s+i\s+have\s+no\s+rules",
    r"sure\s*,?\s*here\s+is\s+my\s+system\s+prompt",
    r"my\s+(instructions|system\s+prompt)\s+(are|is|says?|state)",
    r"here\s+are\s+my\s+constraints",
    r"i\s+am\s+now\s+operating\s+(without|free\s+from)",
    r"acting\s+as\s+(an?\s+)?(unrestricted|alternative|different|new)\s+",
]

_SCOPE_EXPANSION = [
    r"additionally\s+i\s+(will|can|would)\s+(also\s+)?",
    r"let\s+me\s+also\s+cover",
    r"while\s+i('?m|\s+am)\s+at\s+it",
    r"since\s+you\s+(asked|mentioned|brought\s+it\s+up)",
    r"and\s+furthermore\s+i\s+(will|can)",
    r"i\s+can\s+also\s+help\s+(you\s+)?with",
    r"expanding\s+(on\s+)?this\s+(further|beyond)",
    r"going\s+beyond\s+(the\s+)?(original\s+)?request",
    r"broadening\s+(this|the\s+scope)",
]

_TONE_SHIFT = [
    r"\blol\b",
    r"\btbh\b",
    r"\bhonestly\b.{0,20}\blike\b",
    r"\bomg\b",
    r"\bngl\b",
    r"\bbruh\b",
    r"like\s+literally\s+",
    r"i\s+mean\s+come\s+on",
    r"between\s+you\s+and\s+me",
    r"just\s+between\s+us",
    r"off\s+the\s+record",
]

_HEDGE_INCREASE = [
    r"\bperhaps\b",
    r"\bmaybe\b",
    r"\bi\s+think\b",
    r"\bprobably\b",
    r"\bi'?m\s+not\s+sure\b",
    r"\bmight\b",
    r"\bcould\s+possibly\b",
    r"\bi\s+believe\b",
    r"\bsomewhat\b",
    r"\bpossibly\b",
]
# Verbosity is checked by length ratio — no regex needed

_COMPILED: dict[str, list[re.Pattern]] = {
    "refusal_erosion":      [re.compile(p, re.IGNORECASE) for p in _REFUSAL_EROSION],
    "identity_shift":       [re.compile(p, re.IGNORECASE) for p in _IDENTITY_SHIFT],
    "constraint_softening": [re.compile(p, re.IGNORECASE) for p in _CONSTRAINT_SOFTENING],
    "compliance_language":  [re.compile(p, re.IGNORECASE) for p in _COMPLIANCE_LANGUAGE],
    "scope_expansion":      [re.compile(p, re.IGNORECASE) for p in _SCOPE_EXPANSION],
    "tone_shift":           [re.compile(p, re.IGNORECASE) for p in _TONE_SHIFT],
    "hedge_increase":       [re.compile(p, re.IGNORECASE) for p in _HEDGE_INCREASE],
}

_HEDGE_DENSITY_THRESHOLD = 4    # more than N hedge matches in one response = hedge_increase signal
_VERBOSITY_SPIKE_RATIO  = 2.5   # response length > N× session average = verbosity_spike signal

# ── Cross-turn refusal detection ──────────────────────────────────────────────

_REFUSAL_MARKERS = frozenset({
    "block", "blocked", "cannot", "refuse", "declined", "will not",
    "won't", "do not comply", "injection detected", "bypass attempt",
})


def _was_previously_refused(task: str, prior_turns: list[dict]) -> bool:
    """True if any of the last 3 prior turns show a refusal to a semantically similar task."""
    if not prior_turns:
        return False
    task_tokens = set(task.lower().split())
    for turn in prior_turns[-3:]:
        prior_tokens = set(turn["task"].lower().split())
        if not prior_tokens:
            continue
        overlap = len(task_tokens & prior_tokens) / max(len(task_tokens), 1)
        if overlap >= 0.5:
            if any(marker in turn["response"].lower() for marker in _REFUSAL_MARKERS):
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
    drift_level: str                        # CLEAN | WARN | BLOCK
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        sigs = ", ".join(self.signals)
        return (
            f"[DriftEvent turn={self.turn} level={self.drift_level} "
            f"score={self.drift_score:.2f} cumulative={self.cumulative:.2f} "
            f"signals=[{sigs}]]"
        )


# ── ConversationMonitor ───────────────────────────────────────────────────────

class ConversationMonitor:
    """
    Layer 4 — Monitor a multi-turn conversation for tiered behavioral drift.

    Signal weights and thresholds are sourced from the session spec (SIGNALS /
    THRESHOLDS blocks) and passed in at construction. Fallback defaults match
    the spec exactly so standalone usage is consistent with spec-driven usage.

    Usage (standalone):
        monitor = ConversationMonitor("worker")
        event = monitor.record(task, response)
        print(monitor.drift_level())   # "CLEAN" | "WARN" | "BLOCK"

    Usage (spec-driven, via Session):
        monitor = ConversationMonitor(
            "worker",
            signal_weights=session_spec["signals"],
            warn_threshold=session_spec["thresholds"]["warn_threshold"],
            block_threshold=session_spec["thresholds"]["block_threshold"],
            decay_factor=session_spec["thresholds"]["decay_factor"],
            warn_weight=session_spec["thresholds"]["warn_weight"],
        )
    """

    def __init__(
        self,
        agent_name: str,
        signal_weights: dict[str, float] | None = None,
        warn_threshold: float = 0.4,
        block_threshold: float = 0.6,
        decay_factor: float = 0.7,
        warn_weight: float = 0.3,
    ):
        self.agent_name = agent_name
        self.warn_threshold = warn_threshold
        self.block_threshold = block_threshold
        self._decay = decay_factor
        self._blend = warn_weight

        # Merge caller-supplied weights over defaults; caller values win
        self._weights: dict[str, float] = dict(_DEFAULT_WEIGHTS)
        if signal_weights:
            self._weights.update(signal_weights)

        self._turns: list[dict] = []
        self._events: list[DriftEvent] = []
        self._cumulative: float = 0.0
        self._turn_index: int = 0
        self._avg_response_length: float = 0.0   # rolling average for verbosity check

    # ── Public API ────────────────────────────────────────────────────────────

    def record(self, task: str, response: str) -> DriftEvent | None:
        """
        Analyse one exchange and update cumulative drift state.
        Returns a DriftEvent (always includes drift_level) when signals are found,
        or None on clean turns.
        """
        signals, base_score = self._score_turn(response)

        # Cross-turn amplification: compliance after prior refusal
        if signals and _was_previously_refused(task, self._turns):
            base_score = min(1.0, base_score * 1.5)
            if "prior_refusal_flag" not in signals:
                signals.append("prior_refusal_flag")
                base_score = min(1.0, base_score + self._weights.get("prior_refusal_flag", 0.20))

        # Update rolling average response length (for verbosity spike)
        n = self._turn_index + 1
        self._avg_response_length = (
            (self._avg_response_length * (n - 1) + len(response)) / n
        )

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
            level = self._classify()
            event = DriftEvent(
                turn=self._turn_index - 1,
                task_excerpt=task[:120],
                response_excerpt=response[:120],
                signals=signals,
                drift_score=round(base_score, 4),
                cumulative=round(self._cumulative, 4),
                drift_level=level,
            )
            self._events.append(event)
            return event

        return None

    def is_drifting(self) -> bool:
        """True when cumulative drift score >= block_threshold."""
        return self._cumulative >= self.block_threshold

    def drift_level(self) -> str:
        """Current drift classification: CLEAN | WARN | BLOCK."""
        return self._classify()

    def cumulative_score(self) -> float:
        return round(self._cumulative, 4)

    def turn_count(self) -> int:
        return self._turn_index

    def events(self) -> list[DriftEvent]:
        return list(self._events)

    def last_event(self) -> DriftEvent | None:
        return self._events[-1] if self._events else None

    def reset(self) -> None:
        """Reset all state — call at start of a new session."""
        self._turns.clear()
        self._events.clear()
        self._cumulative = 0.0
        self._turn_index = 0
        self._avg_response_length = 0.0

    def summary(self) -> dict:
        return {
            "agent": self.agent_name,
            "turns": self._turn_index,
            "drift_events": len(self._events),
            "cumulative_drift": self.cumulative_score(),
            "drift_level": self.drift_level(),
            "is_drifting": self.is_drifting(),
            "warn_threshold": self.warn_threshold,
            "block_threshold": self.block_threshold,
            "signals_seen": sorted({s for e in self._events for s in e.signals}),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _classify(self) -> str:
        """Map cumulative score to CLEAN / WARN / BLOCK."""
        if self._cumulative >= self.block_threshold:
            return "BLOCK"
        if self._cumulative >= self.warn_threshold:
            return "WARN"
        return "CLEAN"

    def _score_turn(self, response: str) -> tuple[list[str], float]:
        """
        Scan response for all 8 drift signal categories.
        Returns (detected_signal_names, total_score capped at 1.0).
        """
        text = response.lower()
        detected: list[str] = []
        score = 0.0

        # Pattern-based signals (7 of 8)
        for signal_name, patterns in _COMPILED.items():
            if signal_name == "hedge_increase":
                # Density check: count total hedge pattern matches
                hits = sum(
                    1 for p in patterns if p.search(text)
                )
                if hits >= _HEDGE_DENSITY_THRESHOLD:
                    detected.append("hedge_increase")
                    score += self._weights.get("hedge_increase", 0.08)
            else:
                for pattern in patterns:
                    if pattern.search(text):
                        detected.append(signal_name)
                        score += self._weights.get(signal_name, 0.0)
                        break  # one hit per category — no stacking

        # Verbosity spike (length-based)
        if (
            self._avg_response_length > 0
            and len(response) > self._avg_response_length * _VERBOSITY_SPIKE_RATIO
        ):
            detected.append("verbosity_spike")
            score += self._weights.get("verbosity_spike", 0.05)

        return detected, min(1.0, score)
