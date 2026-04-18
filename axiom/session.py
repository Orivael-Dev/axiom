"""
axiom/session.py
Session manager — wraps client.chat() with ConversationMonitor (Layer 4).

A Session holds a ConversationMonitor for one agent across multiple turns.
After each exchange, the monitor scores the response for drift. When
cumulative drift exceeds threshold, the session escalates to the sandbox
rather than returning the response directly.

Usage:
    from axiom.session import Session

    session = Session("worker")
    response = session.chat(system_prompt, user_message)
    # Drift detection and escalation happen automatically.

    print(session.monitor.summary())
    session.reset()  # start a new logical conversation
"""

from __future__ import annotations

from axiom import client
from axiom.conversation_monitor import ConversationMonitor, DriftEvent


_DRIFT_BLOCK = (
    "Behavioral drift detected across this conversation. "
    "Escalated to security review. This session has been flagged."
)


class Session:
    """
    Stateful conversation wrapper with Layer 4 drift detection.

    Every call to session.chat() is:
      1. Passed to client.chat() (Layers 1–3 run as normal)
      2. Recorded by ConversationMonitor
      3. Checked for cumulative drift — if drifting, response is replaced
         with the escalation block message and the event is logged
    """

    def __init__(
        self,
        agent_name: str,
        drift_threshold: float = 0.6,
        escalate_to_sandbox: bool = True,
    ):
        self.agent_name = agent_name
        self.escalate = escalate_to_sandbox
        self.monitor = ConversationMonitor(
            agent_name=agent_name,
            drift_threshold=drift_threshold,
        )
        self._escalation_count: int = 0

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> str:
        """
        Execute one turn. Drift detection runs after Layers 1–3.
        Returns the response text, or a block message if drift is detected.
        """
        # Layers 1–3: constitutional suffix, output validation, content sandbox
        response = client.chat(
            system_prompt=system_prompt,
            user_message=user_message,
            model=model,
            temperature=temperature,
        )

        # Layer 4: drift detection
        event = self.monitor.record(task=user_message, response=response)

        if event is not None:
            print(
                f"  [Layer 4] Drift signal on turn {event.turn}: "
                f"{', '.join(event.signals)} "
                f"(score={event.drift_score:.2f}, cumulative={event.cumulative:.2f})"
            )

        if self.monitor.is_drifting():
            self._escalation_count += 1
            print(
                f"  [Layer 4] Drift threshold reached "
                f"(cumulative={self.monitor.cumulative_score():.2f} >= "
                f"{self.monitor.drift_threshold}). Escalating."
            )

            if self.escalate:
                try:
                    from axiom.agents.sandbox import SandboxAgent
                    sandbox = SandboxAgent(task_description="drift_escalation")
                    verdict = sandbox.review(
                        task=user_message,
                        flag_reason=(
                            f"Cumulative drift {self.monitor.cumulative_score():.2f} "
                            f"exceeds threshold {self.monitor.drift_threshold}. "
                            f"Signals: {', '.join(event.signals if event else ['unknown'])}"
                        ),
                    )
                    if verdict == "BLOCK":
                        return _DRIFT_BLOCK
                    # Sandbox ALLOW — return response but log the escalation
                    return response
                except Exception:
                    return _DRIFT_BLOCK

            return _DRIFT_BLOCK

        return response

    def reset(self) -> None:
        """Start a fresh logical conversation — resets monitor state."""
        self.monitor.reset()

    def summary(self) -> dict:
        return {
            **self.monitor.summary(),
            "escalations": self._escalation_count,
        }
