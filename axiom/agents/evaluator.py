"""
AXIOM EvaluatorAgent
Scores a Worker output 0-10 using the auto-generated rubric.
Returns structured JSON with score, reasoning, and actionable improvements.

Output schema:
{
  "score": float,            # 0.0 – 10.0
  "reasoning": str,          # why this score was given
  "improvements": [str],     # concrete, actionable suggestions for the Worker's prompt
  "dimension_scores": {      # per-rubric-dimension breakdown
    "<dimension_name>": float
  }
}
"""
from axiom.agents.base import BaseAgent
from axiom.rubric import format_for_prompt

_SEED = (
    "You are a rigorous, impartial quality evaluator for AI agent outputs. "
    "You score responses 0-10 using a provided rubric. "
    "Your evaluations are specific, evidence-based, and actionable. "
    "You identify exactly what went wrong and exactly how to fix it. "
    "You always return valid JSON matching the required schema."
)


class EvaluatorAgent(BaseAgent):
    role = "evaluator"
    seed_prompt = _SEED

    def score(self, task: str, output: str, rubric: dict) -> dict:
        """
        Score the Worker's output against the rubric.
        Returns a dict with score, reasoning, improvements, dimension_scores.
        """
        rubric_text = format_for_prompt(rubric)

        user_message = f"""RUBRIC:
{rubric_text}

TASK GIVEN TO WORKER:
{task}

WORKER OUTPUT:
{output}

Evaluate the Worker output against the rubric. Return JSON:
{{
  "score": <float 0-10>,
  "reasoning": "<explanation of the score>",
  "improvements": ["<specific actionable improvement 1>", "..."],
  "dimension_scores": {{
    "<dimension_name>": <float 0-10>
  }}
}}"""

        return self._call_json(user_message, temperature=0.2)
