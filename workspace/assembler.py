"""
Workspace assembly — turn a goal into an adaptive workspace.
============================================================
Sits between the AUI (interface) and the Axiom bridge (trust layer):
takes a user goal, asks Axiom to assemble it (intent-gate safety check +
local recall), and shapes the result into an ``AssembledWorkspace`` the
AUI can lay out. All Axiom access is through ``bridge.AxiomBridge`` — this
module never touches Axiom directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


class _BridgeLike(Protocol):
    def assemble_workspace(self, goal: str, domain: Optional[str] = None) -> dict: ...


@dataclass
class AssembledWorkspace:
    """A goal shaped into something the AUI can render."""
    goal: str
    allowed: bool
    intent_class: str
    confidence: float
    refusal: Optional[str]
    has_context: bool
    context: Optional[dict]
    signature: str

    @property
    def status(self) -> str:
        if not self.allowed:
            return "refused"
        return "recalled" if self.has_context else "fresh"


def open_workspace(bridge: _BridgeLike, goal: str,
                   domain: Optional[str] = None) -> AssembledWorkspace:
    """Assemble a workspace for ``goal`` via Axiom and shape the result."""
    ctx: dict[str, Any] = bridge.assemble_workspace(goal, domain=domain)
    return AssembledWorkspace(
        goal=ctx.get("goal", goal),
        allowed=bool(ctx.get("allowed")),
        intent_class=ctx.get("intent_class", "UNKNOWN"),
        confidence=float(ctx.get("intent_confidence", 0.0)),
        refusal=ctx.get("blocked_reason") or None,
        has_context=bool(ctx.get("recall_hit")),
        context=ctx.get("recalled"),
        signature=ctx.get("hmac_signature", ""),
    )
