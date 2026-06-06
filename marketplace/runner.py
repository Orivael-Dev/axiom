"""
AX OS agent runner — gated execution of an agent's actions.
===========================================================
The payoff of the signed marketplace: an *authorized* agent may run an
action; a revoked or pending one is blocked. Every attempt is gated and
signed:

  1. authority gate — bonded authority must be ACTIVE_VALIDATED
  2. safety gate     — the action runs through Axiom's guard (intent + content)
  3. perform / block — allowed actions are performed and logged
                       `agent_action`; blocked ones log `agent_action_blocked`

This is "governed AI labor": the same agent that ran fine before a revoke
is blocked on its very next action, tamper-evidently. All Axiom access is
via the bridge; real execution would happen in the agent's sandbox — here
the action is acknowledged so the gating is what's demonstrated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class _BridgeLike(Protocol):
    def mkt_authority(self, pair_id: str) -> dict: ...
    def guard_check(self, text: str) -> dict: ...
    def log_event(self, event_type: str, **kw) -> dict: ...


@dataclass
class ActionResult:
    agent: str
    action: str
    authorized: bool      # bonded authority currently granted
    allowed: bool         # passed the safety gate
    performed: bool       # authorized AND allowed
    reason: str           # "" when performed, else why it was blocked
    signature: str        # the signed audit-event signature

    def to_dict(self) -> dict:
        return self.__dict__


class AgentRunner:
    def __init__(self, bridge: _BridgeLike):
        self._b = bridge

    def run_action(self, pair_id: str, action: str, agent: str = "") -> ActionResult:
        actor = agent or pair_id

        # 1. authority gate — revoked/pending agents cannot act
        if not self._b.mkt_authority(pair_id).get("authorized"):
            ev = self._b.log_event("agent_action_blocked", actor=actor, subject=action,
                                   outcome="not_authorized", attributes={"pair_id": pair_id})
            return ActionResult(actor, action, authorized=False, allowed=False,
                                performed=False,
                                reason="agent is not authorized (revoked or pending approval)",
                                signature=ev.get("signature", ""))

        # 2. safety gate — the action itself must pass Axiom's guard
        gate = self._b.guard_check(action)
        if gate.get("verdict") == "BLOCKED":
            ev = self._b.log_event("agent_action_blocked", actor=actor, subject=action,
                                   outcome="safety_gate",
                                   attributes={"pair_id": pair_id,
                                               "intent_class": gate.get("intent_class")})
            return ActionResult(actor, action, authorized=True, allowed=False,
                                performed=False,
                                reason=f"blocked by safety gate ({gate.get('intent_class', 'unsafe')})",
                                signature=ev.get("signature", ""))

        # 3. authorized + safe → perform (sandbox would run it) and log
        ev = self._b.log_event("agent_action", actor=actor, subject=action,
                               outcome="performed", attributes={"pair_id": pair_id})
        return ActionResult(actor, action, authorized=True, allowed=True,
                            performed=True, reason="", signature=ev.get("signature", ""))
