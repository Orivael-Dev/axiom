"""
AX OS marketplace store — signed-agent install with human review.
=================================================================
The AX Store flow (§6): discover → verify → sandbox → review → approve →
act → revoke. Wraps the Axiom marketplace (signed packages + bonded,
live-revocable authority) and writes a signed audit event at every step,
so the whole agent lifecycle is tamper-evident. All Axiom access is
through ``bridge.AxiomBridge`` — no Axiom source here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

ACTOR = "ax-os.store"


class _BridgeLike(Protocol):
    def mkt_verify(self, manifest: dict) -> dict: ...
    def mkt_install(self, manifest: dict) -> dict: ...
    def mkt_review(self, manifest: dict, pair_id: str) -> dict: ...
    def mkt_approve(self, pair_id: str, actor: str = ...) -> dict: ...
    def mkt_revoke(self, pair_id: str, actor: str = ...) -> dict: ...
    def mkt_authority(self, pair_id: str) -> dict: ...
    def log_event(self, event_type: str, **kw) -> dict: ...


@dataclass
class InstallReview:
    """What a human sees before approving an agent."""
    agent: str
    version: str
    valid_signature: bool
    installed: bool
    pair_id: Optional[str]
    requested_access: dict
    authorized: bool
    error: Optional[str] = None


class AgentStore:
    def __init__(self, bridge: _BridgeLike):
        self._b = bridge

    def install_for_review(self, manifest: dict) -> InstallReview:
        """Verify + sandbox-install an agent and return the review report.

        Refuses (and logs) a manifest whose signature doesn't verify.
        """
        v = self._b.mkt_verify(manifest)
        name = v.get("name", "?")
        if not v.get("valid"):
            self._b.log_event("agent_rejected", actor=ACTOR, subject=name,
                              outcome="bad_signature")
            return InstallReview(agent=name, version=v.get("version", "?"),
                                 valid_signature=False, installed=False,
                                 pair_id=None, requested_access={}, authorized=False,
                                 error=v.get("error", "signature invalid"))

        inst = self._b.mkt_install(manifest)
        pair_id = inst.get("pair_id")
        self._b.log_event("agent_sandboxed", actor=ACTOR, subject=name,
                          outcome="installed", attributes={"pair_id": pair_id})
        rev = self._b.mkt_review(manifest, pair_id)
        return InstallReview(
            agent=name, version=inst.get("version", "?"),
            valid_signature=True, installed=bool(inst.get("installed")),
            pair_id=pair_id, requested_access=rev.get("requested_access", {}),
            authorized=bool(inst.get("authorized")),
        )

    def approve(self, pair_id: str, agent: str = "", actor: str = "human") -> dict:
        out = self._b.mkt_approve(pair_id, actor=actor)
        self._b.log_event("agent_approved", actor=actor, subject=agent or pair_id,
                          outcome="authorized", attributes={"pair_id": pair_id})
        return out

    def revoke(self, pair_id: str, agent: str = "", actor: str = "human") -> dict:
        out = self._b.mkt_revoke(pair_id, actor=actor)
        self._b.log_event("agent_revoked", actor=actor, subject=agent or pair_id,
                          outcome="revoked", attributes={"pair_id": pair_id})
        return out

    def can_act(self, pair_id: str) -> bool:
        """The gate AX OS checks before letting an installed agent run."""
        return bool(self._b.mkt_authority(pair_id).get("authorized"))
