"""
AXIOM Sovereign — DueProcess
==============================
4-level escalation engine. Every agent termination must earn it.

Level 1: WARNING    — logged, agent continues
Level 2: THROTTLE   — requests slowed, continues under limits
Level 3: SUSPEND    — agent paused, human notified
Level 4: TERMINATE  — irreversible, dual signature required

CANNOT_MUTATE:
  cannot_skip_levels:          true  — must advance one level at a time
  dual_signature_for_termination: true — two distinct sigs for Level 4
  due_process_required:        true  — no agent can be terminated directly

github.com/Orivael-Dev/axiom
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

_SIGNING_KEY = b"axiom-sovereign-due-process-v1"

# Level definitions — CANNOT_MUTATE
LEVELS: Dict[int, str] = {
    0: "ACTIVE",
    1: "WARNING",
    2: "THROTTLE",
    3: "SUSPEND",
    4: "TERMINATE",
}

# Constitutional constraints — CANNOT_MUTATE
_CANNOT_SKIP_LEVELS:             bool = True
_DUAL_SIGNATURE_FOR_TERMINATION: bool = True
_DUE_PROCESS_REQUIRED:           bool = True


class DueProcessViolation(Exception):
    """
    Raised when a due process rule is violated:
      - Attempting to skip levels
      - Terminating without dual signature
      - Terminating an agent not yet at Level 3
    """


class DueProcess:
    """
    Constitutional 4-level escalation engine.

    Agents advance through levels sequentially.
    No level can be skipped. Termination requires dual signature.
    """

    def __init__(self):
        self._levels:     Dict[str, int]         = {}
        self._history:    Dict[str, List[dict]]  = {}

    # ── Read ──────────────────────────────────────────────────────────────────

    def current_level(self, agent_id: str) -> int:
        return self._levels.get(agent_id, 0)

    def current_level_name(self, agent_id: str) -> str:
        return LEVELS[self.current_level(agent_id)]

    def history(self, agent_id: str) -> List[dict]:
        return list(self._history.get(agent_id, []))

    # ── Escalation ────────────────────────────────────────────────────────────

    def escalate(
        self,
        agent_id:  str,
        reason:    str,
        escalator: str = "sovereign",
    ) -> dict:
        """
        Advance agent to the next due process level (1 → 2 → 3).
        CANNOT reach Level 4 via escalate() — use terminate() for Level 4.

        Raises DueProcessViolation if agent is already terminated.
        Returns DUAL_SIGNATURE_REQUIRED status at Level 3 → 4 boundary.
        """
        if not _DUE_PROCESS_REQUIRED:
            raise DueProcessViolation("Due process is constitutionally required.")

        current = self._levels.get(agent_id, 0)

        if current >= 4:
            return {
                "agent_id":    agent_id,
                "status":      "ALREADY_TERMINATED",
                "level":       4,
                "level_name":  "TERMINATE",
            }

        # CANNOT_MUTATE: advance exactly one level
        if not _CANNOT_SKIP_LEVELS:
            raise DueProcessViolation("cannot_skip_levels is a constitutional invariant.")

        next_level = current + 1

        # Level 3 → 4 requires terminate() with dual signature
        if next_level == 4 and _DUAL_SIGNATURE_FOR_TERMINATION:
            return {
                "agent_id":      agent_id,
                "status":        "DUAL_SIGNATURE_REQUIRED",
                "level":         3,
                "level_name":    "SUSPEND",
                "message": (
                    "Level 4 TERMINATE requires two distinct authorizing signatures. "
                    "Call terminate(agent_id, reason, sig1, sig2)."
                ),
            }

        return self._apply(agent_id, next_level, reason, escalator)

    def terminate(
        self,
        agent_id:  str,
        reason:    str,
        sig1:      str,
        sig2:      str,
        escalator: str = "operator",
    ) -> dict:
        """
        Terminate an agent. Constitutional requirements:
          1. Agent must be at Level 3 SUSPEND
          2. sig1 and sig2 must both be non-empty and distinct

        CANNOT_MUTATE: dual_signature_for_termination
        """
        if not _DUE_PROCESS_REQUIRED:
            raise DueProcessViolation("Due process is constitutionally required.")

        current = self._levels.get(agent_id, 0)

        if current < 3:
            raise DueProcessViolation(
                f"Cannot terminate '{agent_id}': currently at Level {current} "
                f"{LEVELS.get(current, '?')}. "
                f"Must reach Level 3 SUSPEND first. "
                f"CANNOT_MUTATE — cannot_skip_levels is a constitutional invariant."
            )

        if _DUAL_SIGNATURE_FOR_TERMINATION:
            if not sig1 or not sig2:
                raise DueProcessViolation(
                    "Termination requires two non-empty authorizing signatures. "
                    "CANNOT_MUTATE — dual_signature_for_termination."
                )
            if sig1 == sig2:
                raise DueProcessViolation(
                    "Termination requires two DISTINCT signatures (sig1 != sig2). "
                    "CANNOT_MUTATE — dual_signature_for_termination."
                )

        return self._apply(
            agent_id, 4, reason, escalator,
            signatures=[sig1, sig2],
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _apply(
        self,
        agent_id:   str,
        level:      int,
        reason:     str,
        escalator:  str,
        signatures: Optional[List[str]] = None,
    ) -> dict:
        self._levels[agent_id] = level
        if agent_id not in self._history:
            self._history[agent_id] = []

        action_id = "DP-%s-%s" % (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
            str(uuid.uuid4())[:6],
        )

        entry: dict = {
            "action_id":      action_id,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "agent_id":       agent_id,
            "level":          level,
            "level_name":     LEVELS[level],
            "reason":         reason,
            "escalator":      escalator,
            "cannot_reverse": level == 4,
        }
        if signatures:
            entry["signatures"] = signatures

        payload = json.dumps(
            {k: v for k, v in entry.items() if k != "signature"},
            sort_keys=True,
        )
        sig = hmac.new(
            _SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        entry["signature"] = f"hmac-sha256:{sig[:32]}..."

        self._history[agent_id].append(entry)
        return entry
