"""
AXIOM RewriterAgent
Rewrites *any* agent's system prompt based on evaluation feedback.
Being target-agnostic is the key design decision — it can improve the Worker,
the Evaluator, or itself (bounded recursive bootstrap).
"""
from axiom.agents.base import BaseAgent
from axiom_files.parser import get_prompt


class RewriterAgent(BaseAgent):
    role = "rewriter"
    seed_prompt = get_prompt("rewriter")

    def rewrite(
        self,
        target_role: str,
        current_prompt: str,
        evaluation: dict,
    ) -> str:
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
