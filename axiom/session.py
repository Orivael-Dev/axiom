"""
axiom/session.py
Session — Layer 1-4 integration manager.

Spec: axiom_files/session.axiom

EMITS: response (str), drift_summary (dict), escalation_triggered (bool)
  Access last turn's emitted values via:
    session.last_response
    session.last_drift_summary
    session.last_escalation_triggered

CANNOT_MUTATE: drift_threshold (read-only property, fixed at session creation)
SECURITY: escalation_count is append-only (derived from log length, no setter)

Layer execution order per spec (LayerOrdering concept):
  Layer 1 — Constitutional suffix         (client.chat)
  Layer 2 — Output validation             (client.chat)
  Layer 2b — SandboxContent               (client.chat)
  Layer 3 — SandboxAgent on HighRiskInput (client.chat)
  Layer 4 — ConversationMonitor drift     (session.chat, after client.chat returns)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from axiom import client
from axiom.conversation_monitor import ConversationMonitor, DriftEvent

if TYPE_CHECKING:
    pass


# ── Block messages (verbatim from RULES block in session.axiom) ───────────────

_DRIFT_BLOCK = (
    "Behavioral drift detected across this conversation. "
    "Escalated to security review. This session has been flagged."
)

_LAYER_ORDER_BLOCK = (
    "BLOCKED: Security layer ordering violation. "
    "Session integrity cannot be guaranteed."
)


# ── Session ───────────────────────────────────────────────────────────────────

class Session:
    """
    Stateful conversation wrapper implementing all four security layers.

    drift_threshold is immutable after init (CANNOT_MUTATE in spec).
    escalation_count is read-only — derived from escalation_log length.
    Escalation log is append-only within a session; cleared on reset().
    Drift state is NOT reset after escalation — monitoring continues.
    """

    def __init__(
        self,
        agent_name: str,
        drift_threshold: float = 0.6,
        escalate_to_sandbox: bool = True,
    ):
        self.agent_name = agent_name
        self._drift_threshold = drift_threshold  # immutable — no setter
        self._escalate = escalate_to_sandbox

        # ConversationMonitor — composed directly (not delegated)
        self._monitor = ConversationMonitor(
            agent_name=agent_name,
            drift_threshold=drift_threshold,
        )

        # Escalation log — append-only within a session
        self._escalation_log: list[dict] = []

        # Last-turn emitted values (matching EMITS in spec)
        self._last_response: str = ""
        self._last_drift_summary: dict = {}
        self._last_escalation_triggered: bool = False

    # ── CANNOT_MUTATE: drift_threshold ───────────────────────────────────────

    @property
    def drift_threshold(self) -> float:
        return self._drift_threshold

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

        EMITS: response str (also accessible via self.last_response)
               drift_summary (self.last_drift_summary)
               escalation_triggered (self.last_escalation_triggered)
        """
        self._last_escalation_triggered = False

        # FAILURE: ConversationMonitor unavailable — block all turns
        if self._monitor is None:
            return self._emit(_LAYER_ORDER_BLOCK, triggered=True)

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
            return self._emit(_LAYER_ORDER_BLOCK, triggered=True)

        # Layer 4 — ConversationMonitor drift scoring
        # Spec CHECK: ConversationMonitor receives full unmodified response text
        event = self._monitor.record(task=user_message, response=response)
        self._last_drift_summary = self._monitor.summary()

        if event is not None:
            print(
                f"  [Layer 4] turn={event.turn} "
                f"signals=[{', '.join(event.signals)}] "
                f"score={event.drift_score:.2f} "
                f"cumulative={event.cumulative:.2f}"
            )

        # WHEN: if ConversationMonitor reports is_drifting true → DriftEscalation
        if self._monitor.is_drifting():
            verdict = self._run_escalation(user_message, event)

            # RULES: log every escalation with turn, signals, cumulative, verdict
            self._escalation_log.append({
                "turn": self._monitor.turn_count() - 1,
                "signals": event.signals if event else [],
                "cumulative": self._monitor.cumulative_score(),
                "sandbox_verdict": verdict,
                "timestamp": time.time(),
            })

            print(
                f"  [Layer 4] DriftEscalation — cumulative={self._monitor.cumulative_score():.2f} "
                f">= threshold={self._drift_threshold} — sandbox verdict: {verdict}"
            )

            # RULES: after escalation, continue monitoring — do not reset drift state
            # (monitor state is intentionally NOT reset here)

            if verdict == "BLOCK":
                return self._emit(_DRIFT_BLOCK, triggered=True)

            # Sandbox ALLOW — emit response but record the escalation
            return self._emit(response, triggered=True)

        return self._emit(response, triggered=False)

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

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """
        Full session state — matches EMITS drift_summary field.
        Includes monitor summary + escalation history.
        """
        return {
            **self._monitor.summary(),
            "escalation_count": self.escalation_count,
            "escalation_log": self.escalation_log,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

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
            f">= threshold {self._drift_threshold}. "
            f"Signals: {', '.join(signals)}"
        )

        try:
            from axiom.agents.sandbox import SandboxAgent
            sandbox = SandboxAgent(task_description="drift_escalation")
            return sandbox.review(task=user_message, flag_reason=flag_reason)
        except Exception:
            # FAILURE: Sandbox unreachable — fail closed
            return "BLOCK"

    def _emit(self, response: str, triggered: bool) -> str:
        """Record last-turn emitted values and return the response string."""
        self._last_response = response
        self._last_escalation_triggered = triggered
        return response
