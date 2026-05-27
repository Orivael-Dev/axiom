"""Plain data types for the autonomous-agent loop.

Frozen dataclasses everywhere — every value flowing through the loop
ends up on disk in a signed ledger entry, so accidental mutation
would break replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Tuple


@dataclass
class Subgoal:
    """One unit of work inside a Plan.

    `attempts` is mutable — the executor bumps it on each retry so
    the orchestrator can decide when to escalate to replanning.
    `done` is also mutable for the same reason. The rest is fixed
    at plan time.
    """
    id: str
    description: str
    attempts: int = 0
    done: bool = False


@dataclass
class Plan:
    """A list of subgoals with bookkeeping for "what's next" / "all done".

    Mutable so the orchestrator can call mark_done(); replan() returns
    a fresh Plan rather than mutating the old one.
    """
    task: str
    subgoals: List[Subgoal] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    last_pass: int = 0
    last_fail: int = 0
    final_diff_summary: str = ""

    def next_open_subgoal(self) -> Optional[Subgoal]:
        for sg in self.subgoals:
            if not sg.done:
                return sg
        return None

    def mark_done(self, subgoal_id: str) -> None:
        for sg in self.subgoals:
            if sg.id == subgoal_id:
                sg.done = True
                return

    def is_done(self) -> bool:
        return all(sg.done for sg in self.subgoals) and self.subgoals != []

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "subgoals": [
                {"id": s.id, "description": s.description,
                 "attempts": s.attempts, "done": s.done}
                for s in self.subgoals
            ],
            "changed_files": list(self.changed_files),
            "last_pass": int(self.last_pass),
            "last_fail": int(self.last_fail),
        }


@dataclass(frozen=True)
class ToolCall:
    """One model-decided action to dispatch into the sandbox."""
    tool: str
    args: Mapping[str, Any]
    thought: str = ""

    def to_dict(self) -> dict:
        return {"tool": self.tool, "args": dict(self.args),
                "thought": self.thought}


@dataclass(frozen=True)
class Observation:
    """The sandbox's response to one ToolCall."""
    ok: bool
    output: str               # human-readable; may be stdout, file contents, etc.
    structured: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": bool(self.ok),
            "output": self.output,
            "structured": dict(self.structured),
            "duration_ms": int(self.duration_ms),
            "error": self.error,
        }


@dataclass(frozen=True)
class Verdict:
    """Verifier output. `kind` drives the orchestrator's next move."""
    kind: str                  # "success" | "retry" | "replan" | "abort"
    reason: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "reason": self.reason}


@dataclass(frozen=True)
class AutonomousRunResult:
    """Returned from AutonomousAgent.run(). Everything you need to
    audit the run after the fact lives here or in the signed ledger.
    """
    run_id: str
    success: bool
    steps: int
    chain_head_token_id: str
    plan: Plan                                  # final plan state
    aborted_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "success": bool(self.success),
            "steps": int(self.steps),
            "chain_head_token_id": self.chain_head_token_id,
            "plan": self.plan.to_dict(),
            "aborted_reason": self.aborted_reason,
        }
