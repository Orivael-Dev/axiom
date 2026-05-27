"""AXIOM autonomous coding agent — planner / executor / verifier loop on
top of the existing exoskeleton signing + governance stack.

Public surface:
    from axiom_autonomous import (
        AutonomousAgent, AutonomousRunResult,
        Plan, Subgoal, ToolCall, Observation, Verdict,
    )

The CLI entrypoint lives in `axiom_autonomous_agent` (top-level shim)
so `python3 -m axiom_autonomous_agent run --task ...` works the same
way as `python3 -m axiom_exoskeleton`.
"""
from __future__ import annotations

from .models import (
    AutonomousRunResult, Plan, Subgoal, ToolCall, Observation, Verdict,
)
from .orchestrator import AutonomousAgent

__all__ = [
    "AutonomousAgent", "AutonomousRunResult",
    "Plan", "Subgoal", "ToolCall", "Observation", "Verdict",
]
