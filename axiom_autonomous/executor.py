"""Executor — turns a subgoal + history into a single ToolCall.

One LLM call per `decide_action`. Output must conform to:

    <thought>I'll scaffold primes.py.</thought>
    ```tool
    {"tool": "write_file",
     "args": {"path": "primes.py", "content": "..."}}
    ```
"""
from __future__ import annotations

import json
from typing import List, Optional

from .models import Subgoal, ToolCall
from .parser import MAX_REPARSE_ATTEMPTS, ParseError, parse_tool_call


EXECUTOR_SYSTEM = """You are the executor inside the AXIOM autonomous coding agent.

Given an open SUBGOAL, the recent step HISTORY, and the available TOOLS,
emit EXACTLY ONE tool call to make progress on the subgoal.

Output format (and ONLY this format):

<thought>One-line reasoning about why this tool call.</thought>
```tool
{"tool": "<tool_name>", "args": {<args>}}
```

Rules:
- Pick exactly ONE tool — never chain multiple calls.
- Use the tool that most directly advances the subgoal.
- For tests, use run_tests (not run_shell) so counts are parsed.
- For new files, use write_file. For surgical edits to existing files,
  use apply_patch.
- Never invent tool names — pick from the TOOLS inventory.
- Never claim a file exists if you didn't write it in a prior step.
- No preamble or commentary outside <thought> and the ```tool block.
"""


class ExecutorError(RuntimeError):
    """Executor LLM produced unparseable output after the retry budget."""


class Executor:
    """LLM-backed action picker."""

    def __init__(
        self, backend, *,
        max_reparse_attempts: int = MAX_REPARSE_ATTEMPTS,
        per_step_token_budget: int = 1200,
    ) -> None:
        self._backend = backend
        self._max_reparse = max_reparse_attempts
        self._token_budget = per_step_token_budget

    def decide_action(
        self,
        *,
        subgoal: Subgoal,
        history: List[dict],
        tools_schema: str,
    ) -> tuple[ToolCall, dict]:
        """Returns (ToolCall, llm_facts) where llm_facts carries the
        backend / model / token counts the orchestrator records into
        the step token."""
        prompt = self._build_prompt(subgoal, history, tools_schema)
        last_err: Optional[str] = None
        last_facts: dict = {}
        for _attempt in range(self._max_reparse + 1):
            result = self._backend.generate(
                system=EXECUTOR_SYSTEM,
                prompt=prompt,
                max_output_tokens=self._token_budget,
            )
            last_facts = {
                "backend": getattr(result, "backend", "unknown"),
                "model":   getattr(result, "model", "unknown"),
                "input_tokens":  int(getattr(result, "input_tokens", 0)),
                "output_tokens": int(getattr(result, "output_tokens", 0)),
                "latency_ms":    int(getattr(result, "latency_ms", 0)),
                "raw_text":      result.text,
            }
            try:
                call = parse_tool_call(result.text)
                return call, last_facts
            except ParseError as e:
                last_err = str(e)
                prompt = (
                    prompt
                    + f"\n\n--- previous attempt failed to parse: {e} ---\n"
                    + "Output ONLY <thought>...</thought> + ```tool fenced JSON."
                )
        raise ExecutorError(
            f"executor output unparseable after "
            f"{self._max_reparse + 1} attempts: {last_err}"
        )

    @staticmethod
    def _build_prompt(
        subgoal: Subgoal, history: List[dict], tools_schema: str,
    ) -> str:
        history_lines = []
        for h in history[-8:]:
            kind = h.get("step_kind", "?")
            summary = h.get("summary", "")
            history_lines.append(
                f"  step {h.get('step_idx', '?')} {kind}: {summary}"[:240]
            )
        history_block = "\n".join(history_lines) or "  (no prior steps)"
        return (
            f"SUBGOAL:\n  id={subgoal.id} attempts={subgoal.attempts}\n"
            f"  {subgoal.description}\n\n"
            f"RECENT HISTORY:\n{history_block}\n\n"
            f"TOOLS:\n{tools_schema}\n\n"
            f"Emit the single tool call that advances this subgoal."
        )
