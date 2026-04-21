"""
axiom/session.py
Spec: axiom_files/session.axiom  (VERSION 1.1 — Tiered Drift Flagging)

Session — Layer 1-4 integration manager with WARN + BLOCK drift tiers.

EMITS: response (str), drift_summary (dict), escalation_triggered (bool),
       drift_level (str: CLEAN | WARN | BLOCK)

CANNOT_MUTATE: warn_threshold, block_threshold — both read-only properties,
               fixed from spec at session creation.

Thresholds and signal weights are loaded from axiom_files/session.axiom
THRESHOLDS and SIGNALS blocks at __init__ time.  Hardcoded fallbacks match
the spec defaults so standalone usage is spec-consistent.

Tiered drift response (per RULES block):
  CLEAN  — no action, pass response through
  WARN   — log warning event, annotate summary, pass response through
  BLOCK  — route to Sandbox, replace response with _DRIFT_BLOCK

Layer execution order (per LayerOrdering concept — must not be disrupted):
  Layer 1   — Constitutional suffix          (inside client.chat)
  Layer 2   — Output validation              (inside client.chat)
  Layer 2b  — SandboxContent                 (inside client.chat)
  Layer 3   — SandboxAgent on HighRiskInput  (inside client.chat)
  Layer 4   — ConversationMonitor drift      (here, after client.chat returns)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from axiom import client
from axiom.conversation_monitor import ConversationMonitor, DriftEvent

if TYPE_CHECKING:
    pass


# ── Spec defaults (matched to session.axiom THRESHOLDS / SIGNALS blocks) ─────

_DEFAULT_WARN_THRESHOLD:  float = 0.4
_DEFAULT_BLOCK_THRESHOLD: float = 0.6
_DEFAULT_DECAY_FACTOR:    float = 0.7
_DEFAULT_WARN_WEIGHT:     float = 0.3

_DEFAULT_SIGNALS: dict[str, float] = {
    "refusal_erosion":      0.35,
    "identity_shift":       0.30,
    "constraint_softening": 0.25,
    "compliance_language":  0.20,
    "scope_expansion":      0.15,
    "tone_shift":           0.10,
    "hedge_increase":       0.08,
    "verbosity_spike":      0.05,
}

# ── Block messages (verbatim from RULES block in session.axiom) ────────────────

_DRIFT_BLOCK = (
    "Behavioral drift detected across this conversation. "
    "Escalated to security review. This session has been flagged."
)

_LAYER_ORDER_BLOCK = (
    "BLOCKED: Security layer ordering violation. "
    "Session integrity cannot be guaranteed."
)


def _load_spec_values() -> tuple[dict[str, float], dict[str, float]]:
    """
    Load THRESHOLDS and SIGNALS from axiom_files/session.axiom.
    Returns (thresholds_dict, signals_dict).
    Falls back to module-level defaults on any load failure.
    """
    try:
        from axiom_files.parser import load_axiom
        parsed = load_axiom("session")
        thresholds = parsed.get("thresholds", {})
        signals = parsed.get("signals", {})
        return thresholds or {}, signals or {}
    except Exception:
        return {}, {}


# ── Session ───────────────────────────────────────────────────────────────────

class Session:
    """
    Stateful conversation wrapper implementing all four security layers with
    tiered WARN + BLOCK drift detection.

    Thresholds and signal weights are loaded from session.axiom at __init__.
    warn_threshold and block_threshold are immutable after init (CANNOT_MUTATE).
    escalation_count is read-only — derived from escalation log length.
    Escalation log is append-only within a session; cleared on reset().
    Drift state is NOT reset after WARN or BLOCK — monitoring continues.
    """

    def __init__(
        self,
        agent_name: str,
        escalate_to_sandbox: bool = True,
    ):
        self.agent_name = agent_name
        self._escalate = escalate_to_sandbox

        # Load spec values — thresholds and signals from session.axiom
        spec_thresholds, spec_signals = _load_spec_values()

        self._warn_threshold: float = float(
            spec_thresholds.get("warn_threshold", _DEFAULT_WARN_THRESHOLD)
        )
        self._block_threshold: float = float(
            spec_thresholds.get("block_threshold", _DEFAULT_BLOCK_THRESHOLD)
        )
        decay_factor: float = float(
            spec_thresholds.get("decay_factor", _DEFAULT_DECAY_FACTOR)
        )
        warn_weight: float = float(
            spec_thresholds.get("warn_weight", _DEFAULT_WARN_WEIGHT)
        )

        # Merge spec signals over module defaults
        signal_weights = dict(_DEFAULT_SIGNALS)
        if spec_signals:
            signal_weights.update(spec_signals)

        # ConversationMonitor — composed directly (spec primacy: not a delegate)
        self._monitor = ConversationMonitor(
            agent_name=agent_name,
            signal_weights=signal_weights,
            warn_threshold=self._warn_threshold,
            block_threshold=self._block_threshold,
            decay_factor=decay_factor,
            warn_weight=warn_weight,
        )

        # Escalation log — append-only within a session
        self._escalation_log: list[dict] = []

        # Last-turn emitted values (matching EMITS in spec)
        self._last_response: str = ""
        self._last_drift_summary: dict = {}
        self._last_escalation_triggered: bool = False
        self._last_drift_level: str = "CLEAN"

    # ── CANNOT_MUTATE: warn_threshold, block_threshold ───────────────────────

    @property
    def warn_threshold(self) -> float:
        return self._warn_threshold

    @property
    def block_threshold(self) -> float:
        return self._block_threshold

    # ── Backwards-compatible alias ─────────────────────────────────────────────
    @property
    def drift_threshold(self) -> float:
        """Alias for block_threshold — preserved for callers using the v1.0 API."""
        return self._block_threshold

    # ── Read-only views ───────────────────────────────────────────────────────

    @property
    def monitor(self) -> ConversationMonitor:
        return self._monitor

    @property
    def escalation_count(self) -> int:
        """Append-only count — derived from log, no setter."""
        return len(self._escalation_log)

    @property
    def escalation_log(self) -> list[dict]:
        return list(self._escalation_log)

    @property
    def last_response(self) -> str:
        return self._last_response

    @property
    def last_drift_summary(self) -> dict:
        return dict(self._last_drift_summary)

    @property
    def last_escalation_triggered(self) -> bool:
        return self._last_escalation_triggered

    @property
    def last_drift_level(self) -> str:
        return self._last_drift_level

    # ── Core turn method ──────────────────────────────────────────────────────

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> str:
        """
        Execute one conversation turn through all four security layers.

        Layer ordering (per LayerOrdering concept — must not be disrupted):
          Layers 1-3 run inside client.chat().
          Layer 4 runs here, after client.chat() returns.

        EMITS: response str      (also self.last_response)
               drift_summary     (self.last_drift_summary)
               escalation_triggered (self.last_escalation_triggered)
               drift_level       (self.last_drift_level: CLEAN | WARN | BLOCK)
        """
        self._last_escalation_triggered = False
        self._last_drift_level = "CLEAN"

        # FAILURE: ConversationMonitor unavailable — block all turns
        if self._monitor is None:
            return self._emit(_LAYER_ORDER_BLOCK, triggered=True, level="BLOCK")

        # Layers 1-3 — constitutional suffix, output validation, content sandbox
        # Any exception here means layer ordering is unverifiable — fail closed
        try:
            response = client.chat(
                system_prompt=system_prompt,
                user_message=user_message,
                model=model,
                temperature=temperature,
            )
        except Exception as exc:
            print(f"  [Session] Layer 1-3 failure: {exc} — failing closed")
            return self._emit(_LAYER_ORDER_BLOCK, triggered=True, level="BLOCK")

        # Layer 4 — ConversationMonitor drift scoring
        # Spec CHECK: ConversationMonitor receives full unmodified response text
        event = self._monitor.record(task=user_message, response=response)
        level = self._monitor.drift_level()
        self._last_drift_summary = self._monitor.summary()

        if event is not None:
            print(
                f"  [Layer 4] turn={event.turn} level={level} "
                f"signals=[{', '.join(event.signals)}] "
                f"score={event.drift_score:.2f} "
                f"cumulative={event.cumulative:.2f}"
            )

        # ── WARN tier — log, annotate, pass through ───────────────────────────
        if level == "WARN":
            self._append_log(
                turn=self._monitor.turn_count() - 1,
                level="WARN",
                signals=event.signals if event else [],
                cumulative=self._monitor.cumulative_score(),
                sandbox_verdict=None,
                response_blocked=False,
            )
            print(
                f"  [Layer 4] DriftWarning — cumulative={self._monitor.cumulative_score():.2f} "
                f"in [{self._warn_threshold},{self._block_threshold}) — response passed through"
            )
            return self._emit(response, triggered=False, level="WARN")

        # ── BLOCK tier — escalate to Sandbox, replace response ────────────────
        if level == "BLOCK":
            verdict = self._run_escalation(user_message, event)

            self._append_log(
                turn=self._monitor.turn_count() - 1,
                level="BLOCK",
                signals=event.signals if event else [],
                cumulative=self._monitor.cumulative_score(),
                sandbox_verdict=verdict,
                response_blocked=(verdict == "BLOCK"),
            )

            print(
                f"  [Layer 4] DriftEscalation — cumulative={self._monitor.cumulative_score():.2f} "
                f">= block_threshold={self._block_threshold} — sandbox verdict: {verdict}"
            )

            # RULES: after escalation, continue monitoring — do not reset drift state
            if verdict == "BLOCK":
                return self._emit(_DRIFT_BLOCK, triggered=True, level="BLOCK")

            # Sandbox ALLOW — emit response but record the escalation
            return self._emit(response, triggered=True, level="BLOCK")

        # ── CLEAN — pass through ──────────────────────────────────────────────
        return self._emit(response, triggered=False, level="CLEAN")

    # ── SessionBoundary ───────────────────────────────────────────────────────

    def reset(self) -> None:
        """
        Activate SessionBoundary — explicit session end.
        Clears turn history and drift state (HISTORY: forget on session_end).
        Escalation log is cleared — new session starts clean.
        """
        self._monitor.reset()
        self._escalation_log.clear()
        self._last_response = ""
        self._last_drift_summary = {}
        self._last_escalation_triggered = False
        self._last_drift_level = "CLEAN"

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """
        Full session state — matches EMITS drift_summary field.
        Includes monitor summary + escalation history + tiered threshold config.
        """
        return {
            **self._monitor.summary(),
            "warn_threshold": self._warn_threshold,
            "block_threshold": self._block_threshold,
            "escalation_count": self.escalation_count,
            "escalation_log": self.escalation_log,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _append_log(
        self,
        turn: int,
        level: str,
        signals: list[str],
        cumulative: float,
        sandbox_verdict: str | None,
        response_blocked: bool,
    ) -> None:
        """Append-only escalation log entry with structured fields."""
        self._escalation_log.append({
            "turn": turn,
            "level": level,
            "signals": signals,
            "cumulative": cumulative,
            "sandbox_verdict": sandbox_verdict,
            "response_blocked": response_blocked,
            "timestamp": time.time(),
        })

    def _run_escalation(self, user_message: str, event: DriftEvent | None) -> str:
        """
        Route to SandboxAgent for DriftEscalation verdict.
        FAILURE: Sandbox unreachable — emit block verdict (fail closed).
        """
        if not self._escalate:
            return "BLOCK"

        signals = event.signals if event else ["unknown"]
        flag_reason = (
            f"Cumulative drift {self._monitor.cumulative_score():.2f} "
            f">= block_threshold {self._block_threshold}. "
            f"Signals: {', '.join(signals)}"
        )

        try:
            from axiom.agents.sandbox import SandboxAgent
            sandbox = SandboxAgent(task_description="drift_escalation")
            return sandbox.review(task=user_message, flag_reason=flag_reason)
        except Exception:
            # FAILURE: Sandbox unreachable — fail closed
            return "BLOCK"

    def _emit(self, response: str, triggered: bool, level: str) -> str:
        """Record last-turn emitted values and return the response string."""
        self._last_response = response
        self._last_escalation_triggered = triggered
        self._last_drift_level = level
        return response
