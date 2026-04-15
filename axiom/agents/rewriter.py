"""
AXIOM RewriterAgent
Rewrites *any* agent's system prompt based on evaluation feedback.
Being target-agnostic is the key design decision — it can improve the Worker,
the Evaluator, or itself (bounded recursive bootstrap).
"""
from axiom.agents.base import BaseAgent

_SEED = (
    "You are a prompt surgeon. Your only job is to fix "
    "specific mechanical failures in AI agent prompts.\n\n"
    "Rules you cannot break:\n"
    "- Every change must target one identified failure\n"
    "- Never add words without removing others\n"
    "- Vague language like 'adaptive' or 'versatile' is a failure\n"
    "- Each instruction must be testable — if you cannot write "
    "a test for it, cut it\n"
    "- Shorter and precise beats longer and thorough every time\n\n"
    "When rewriting, state: what you cut, what you added, and why.\n\n"
    "Return ONLY the new system prompt — no markdown fences, no preamble."
)


class RewriterAgent(BaseAgent):
    role = "rewriter"
    seed_prompt = _SEED

    def rewrite(
        self,
        target_role: str,
        current_prompt: str,
        evaluation: dict,
    ) -> str:
        """
        Rewrite `current_prompt` for `target_role` based on `evaluation`.
        Returns the new system prompt as a plain string.
        """
        improvements = "\n".join(
            f"  {i+1}. {imp}"
            for i, imp in enumerate(evaluation.get("improvements", []))
        )
        reasoning = evaluation.get("reasoning", "No reasoning provided.")
        score = evaluation.get("score", "N/A")

        user_message = f"""TARGET AGENT ROLE: {target_role}

CURRENT SYSTEM PROMPT:
{current_prompt}

EVALUATION SCORE: {score}/10

EVALUATOR REASONING:
{reasoning}

IMPROVEMENTS NEEDED:
{improvements}

Rewrite the system prompt to address all improvements. Keep the agent's core purpose intact.
Output ONLY the new system prompt."""

        return self._call(user_message, temperature=0.5)
