"""Planner — turns a task description into an ordered list of subgoals.

One LLM call per `plan()` / `replan()`. Output must conform to:

    ```plan
    {"subgoals": [
        {"id": "s1", "description": "write primes.py with first_primes(n)"},
        {"id": "s2", "description": "write test_primes.py covering 3 cases"},
        {"id": "s3", "description": "run pytest and confirm 3 passed"}
    ]}
    ```

We do NOT model the planner as a stateful agent — replan starts fresh
each time with the full chain history in the prompt, so the planner
can drop subgoals that proved infeasible without us tracking removal
explicitly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from .models import Plan, Subgoal
from .parser import ParseError, parse_plan


PLANNER_SYSTEM = """You are the planner inside the AXIOM autonomous coding agent.

Given a coding TASK, decompose it into a short ordered list of subgoals
(3–8 typically). Each subgoal MUST be achievable by a single tool call
(write_file, read_file, list_dir, apply_patch, run_shell, run_tests, finish).

Output ONLY a fenced JSON block:
```plan
{"subgoals": [
  {"id": "s1", "description": "<imperative one-line subgoal>"},
  {"id": "s2", "description": "<...>"}
]}
```

Rules:
- subgoal ids are short stable strings (s1, s2, ...).
- The LAST subgoal should always be running tests, unless the task
  explicitly says no tests are needed.
- No preamble, no explanation, no markdown headers — just the
  ```plan block.
"""


REPLANNER_SYSTEM = """You are the planner inside the AXIOM autonomous coding agent,
called to REPLAN after the original plan stalled. Examine the recent
step history and produce a fresh subgoal list that adapts to what was
learned. Keep already-completed work; restate or refine open subgoals.

Same output format as the initial planner:
```plan
{"subgoals": [...]}
```
"""


class PlannerError(RuntimeError):
    """Planner LLM produced unparseable output after the retry budget."""


class Planner:
    """LLM-backed planner. Backend is shared across plan/replan calls.

    The planner is lazy about replan correctness — it accepts the
    LLM's subgoal list verbatim. The orchestrator owns the safety
    gates that decide whether a subgoal's actions can run.
    """

    def __init__(self, backend, *, max_reparse_attempts: int = 2) -> None:
        self._backend = backend
        self._max_reparse = max_reparse_attempts

    def plan(self, task: str, sandbox_snapshot: dict) -> Plan:
        prompt = self._build_initial_prompt(task, sandbox_snapshot)
        subgoals = self._call_and_parse(
            system=PLANNER_SYSTEM, prompt=prompt, max_output_tokens=800,
        )
        return Plan(
            task=task,
            subgoals=[
                Subgoal(id=str(s["id"]), description=str(s["description"]))
                for s in subgoals
            ],
        )

    def replan(
        self,
        task: str,
        prior_plan: Plan,
        recent_history: List[dict],
    ) -> Plan:
        prompt = self._build_replan_prompt(task, prior_plan, recent_history)
        subgoals = self._call_and_parse(
            system=REPLANNER_SYSTEM, prompt=prompt, max_output_tokens=800,
        )
        return Plan(
            task=task,
            subgoals=[
                Subgoal(id=str(s["id"]), description=str(s["description"]))
                for s in subgoals
            ],
            changed_files=list(prior_plan.changed_files),
            last_pass=prior_plan.last_pass,
            last_fail=prior_plan.last_fail,
        )

    # ── internals ────────────────────────────────────────────────────

    def _call_and_parse(
        self, *, system: str, prompt: str, max_output_tokens: int,
    ) -> list:
        last_err: Optional[str] = None
        for attempt in range(self._max_reparse + 1):
            result = self._backend.generate(
                system=system,
                prompt=prompt,
                max_output_tokens=max_output_tokens,
            )
            try:
                subgoals, _raw = parse_plan(result.text)
                return subgoals
            except ParseError as e:
                last_err = str(e)
                # Re-prompt with the parser's complaint so the next
                # attempt has a chance.
                prompt = (
                    prompt
                    + f"\n\n--- previous attempt failed to parse: {e} ---\n"
                    + "Output ONLY the ```plan fenced JSON block."
                )
        raise PlannerError(
            f"planner output unparseable after "
            f"{self._max_reparse + 1} attempts: {last_err}"
        )

    @staticmethod
    def _build_initial_prompt(task: str, snap: dict) -> str:
        entries = snap.get("entries", [])
        listing = "\n".join(f"  - {e}" for e in entries[:20]) or "  (empty)"
        return (
            f"TASK:\n{task}\n\n"
            f"SANDBOX WORKDIR ({snap.get('workdir', '?')}):\n{listing}\n\n"
            f"Decompose this into 3–8 subgoals."
        )

    @staticmethod
    def _build_replan_prompt(
        task: str, prior: Plan, recent_history: list,
    ) -> str:
        done = [sg for sg in prior.subgoals if sg.done]
        open_ = [sg for sg in prior.subgoals if not sg.done]
        history_lines = []
        for h in recent_history:
            history_lines.append(
                f"  - step {h.get('step_idx', '?')} "
                f"{h.get('step_kind', '?')}: "
                f"{json.dumps(h.get('summary', ''), default=str)[:200]}"
            )
        history_block = "\n".join(history_lines) or "  (no history)"
        done_block = "\n".join(f"  - {sg.id}: {sg.description}" for sg in done) or "  (none)"
        open_block = "\n".join(
            f"  - {sg.id}: {sg.description} (attempts={sg.attempts})"
            for sg in open_
        ) or "  (none)"
        return (
            f"ORIGINAL TASK:\n{task}\n\n"
            f"COMPLETED SUBGOALS:\n{done_block}\n\n"
            f"OPEN SUBGOALS:\n{open_block}\n\n"
            f"RECENT HISTORY:\n{history_block}\n\n"
            f"Propose a fresh, adapted subgoal list to drive this to "
            f"completion. Drop or rewrite subgoals that have proven "
            f"infeasible."
        )
