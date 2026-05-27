"""Verifier — examines a (subgoal, action, observation) triple and
classifies it as success | retry | replan | abort.

The verifier is deliberately conservative:
  - "success" needs the observation to clearly satisfy the subgoal.
  - "retry"   when the action failed but the subgoal still looks
              tractable.
  - "replan"  when the current plan is no longer the right shape
              (e.g. a tool failure suggests a totally different
              approach is needed).
  - "abort"   only for terminal cases — repeated governance blocks,
              unbounded loops, etc. Rarely fired by the verifier
              itself; mostly comes from the orchestrator's hard caps.

A rule-based fast path handles the common cases (run_tests with all
passed → success; tool ok=False → retry) without burning an LLM call.
The LLM is consulted only when the verdict is ambiguous.
"""
from __future__ import annotations

import json
from typing import List, Optional

from .models import Observation, Subgoal, ToolCall, Verdict
from .parser import MAX_REPARSE_ATTEMPTS, ParseError, parse_verdict


VERIFIER_SYSTEM = """You are the verifier inside the AXIOM autonomous coding agent.

Given an OPEN SUBGOAL, the ACTION just taken, and the OBSERVATION
returned, classify the outcome with ONE verdict:

  success — observation clearly satisfies the subgoal
  retry   — action failed but subgoal is still tractable; try again
  replan  — the plan needs reshape (try a different approach entirely)
  abort   — we've hit a terminal failure (rare; use sparingly)

Output ONLY a fenced JSON block:

```verdict
{"kind": "success" | "retry" | "replan" | "abort",
 "reason": "<one short sentence>"}
```

No preamble, no markdown headers — just the ```verdict block.
"""


class VerifierError(RuntimeError):
    """Verifier LLM produced unparseable output after the retry budget."""


class Verifier:
    """Rule-based fast path + LLM fallback."""

    def __init__(
        self, backend, *,
        max_reparse_attempts: int = MAX_REPARSE_ATTEMPTS,
    ) -> None:
        self._backend = backend
        self._max_reparse = max_reparse_attempts

    def verify(
        self,
        *,
        subgoal: Subgoal,
        action: ToolCall,
        observation: Observation,
        history: List[dict],
    ) -> tuple[Verdict, dict]:
        """Return (Verdict, llm_facts).

        llm_facts is empty when the rule-based path resolves the
        verdict; otherwise it carries the backend / model / token
        counts from the consult call.
        """
        fast = self._rule_based(subgoal, action, observation)
        if fast is not None:
            return fast, {}
        prompt = self._build_prompt(subgoal, action, observation, history)
        last_err: Optional[str] = None
        last_facts: dict = {}
        for _attempt in range(self._max_reparse + 1):
            result = self._backend.generate(
                system=VERIFIER_SYSTEM, prompt=prompt,
                max_output_tokens=400,
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
                d = parse_verdict(result.text)
                return Verdict(kind=d["kind"],
                               reason=str(d.get("reason", ""))), last_facts
            except ParseError as e:
                last_err = str(e)
                prompt = (
                    prompt
                    + f"\n\n--- previous attempt failed to parse: {e} ---\n"
                    + "Output ONLY the ```verdict fenced JSON block."
                )
        raise VerifierError(
            f"verifier output unparseable after "
            f"{self._max_reparse + 1} attempts: {last_err}"
        )

    # ── rule-based fast path ──────────────────────────────────────

    @staticmethod
    def _rule_based(
        subgoal: Subgoal, action: ToolCall, obs: Observation,
    ) -> Optional[Verdict]:
        # 1. The model explicitly signalled completion.
        if action.tool == "finish" and obs.ok:
            return Verdict(kind="success", reason="finish signalled")

        # 2. run_tests: green tests are an unambiguous success for the
        #    "run the tests" subgoal; red tests are retry-worthy.
        if action.tool == "run_tests":
            failed = int(obs.structured.get("failed", 0))
            errors = int(obs.structured.get("errors", 0))
            passed = int(obs.structured.get("passed", 0))
            if failed == 0 and errors == 0 and passed > 0:
                return Verdict(
                    kind="success",
                    reason=f"pytest reports {passed} passed, 0 failed",
                )
            if failed > 0 or errors > 0:
                return Verdict(
                    kind="retry",
                    reason=f"pytest reports {failed} failed, {errors} errored",
                )
            # No tests collected — that's a real problem worth replanning.
            return Verdict(
                kind="replan",
                reason="pytest collected no tests",
            )

        # 3. Plain write_file / apply_patch success — promote to
        #    "success" for write-ish subgoals so we don't bother the
        #    LLM. The orchestrator decides when to move on.
        if action.tool in ("write_file", "apply_patch") and obs.ok:
            return Verdict(
                kind="success",
                reason=f"{action.tool} reported ok",
            )

        # 4. Any tool returning ok=False with an error is a retry.
        if not obs.ok and obs.error:
            return Verdict(
                kind="retry",
                reason=f"tool failed: {obs.error[:120]}",
            )

        return None

    @staticmethod
    def _build_prompt(
        subgoal: Subgoal, action: ToolCall, obs: Observation,
        history: List[dict],
    ) -> str:
        action_block = json.dumps(action.to_dict(), indent=2, default=str)[:600]
        obs_block = json.dumps(obs.to_dict(), indent=2, default=str)[:1200]
        history_lines = [
            f"  step {h.get('step_idx', '?')} {h.get('step_kind', '?')}: "
            f"{h.get('summary', '')[:120]}"
            for h in history[-6:]
        ]
        history_block = "\n".join(history_lines) or "  (no prior steps)"
        return (
            f"OPEN SUBGOAL:\n  id={subgoal.id}\n  {subgoal.description}\n\n"
            f"ACTION TAKEN:\n{action_block}\n\n"
            f"OBSERVATION:\n{obs_block}\n\n"
            f"RECENT HISTORY:\n{history_block}\n\n"
            f"Classify the outcome."
        )
