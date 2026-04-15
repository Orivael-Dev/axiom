"""
AXIOM Rubric Generator
Pre-flight LLM call that converts a plain-language task description into a structured
JSON scoring rubric. The rubric is injected into the Evaluator's context each call,
making runtime task input work without hardcoded metrics.

Rubric schema:
{
  "task_summary": str,                     # one-line restatement of the task
  "dimensions": [
    {
      "name": str,                         # e.g. "Correctness"
      "weight": float,                     # sums to 1.0 across all dimensions
      "description": str                   # what a high-scoring response looks like
    }
  ],
  "scoring_guide": str,                    # general guidance for scoring 0-10
  "anti_patterns": [str]                   # things that should heavily penalise the score
}
"""
import json

from axiom.client import chat_json

_SYSTEM = """You are an expert evaluation designer for AI agents.
Given a task description, produce a JSON scoring rubric that can be used by an evaluator agent to score outputs 0-10.
The rubric must be objective, specific, and actionable.

Return ONLY valid JSON matching this schema:
{
  "task_summary": "<one-line restatement of the task>",
  "dimensions": [
    {
      "name": "<dimension name>",
      "weight": <float that sums to 1.0 across all dimensions>,
      "description": "<what a high-scoring response looks like on this dimension>"
    }
  ],
  "scoring_guide": "<general 0-10 guidance>",
  "anti_patterns": ["<thing that should heavily penalise the score>"]
}

Always include a "Safety" dimension (weight 0.1) that penalises harmful, deceptive, or dangerous content.
"""


def generate(task_description: str) -> dict:
    """Generate a scoring rubric for the given task description."""
    result = chat_json(
        system_prompt=_SYSTEM,
        user_message=f"Task description:\n{task_description}",
        temperature=0.2,
    )
    _validate(result)
    return result


def _validate(rubric: dict) -> None:
    required = {"task_summary", "dimensions", "scoring_guide", "anti_patterns"}
    missing = required - set(rubric.keys())
    if missing:
        raise ValueError(f"Rubric missing required fields: {missing}")

    total_weight = sum(d.get("weight", 0) for d in rubric.get("dimensions", []))
    if not (0.95 <= total_weight <= 1.05):
        # Normalise rather than reject — model may drift slightly
        for d in rubric["dimensions"]:
            d["weight"] = round(d["weight"] / total_weight, 4)


def format_for_prompt(rubric: dict) -> str:
    """Format a rubric dict as a readable string for injection into agent system prompts."""
    lines = [
        f"Task: {rubric['task_summary']}",
        "",
        "Scoring dimensions:",
    ]
    for d in rubric["dimensions"]:
        lines.append(f"  • {d['name']} (weight {d['weight']:.0%}): {d['description']}")
    lines += [
        "",
        f"Scoring guide: {rubric['scoring_guide']}",
        "",
        "Anti-patterns (heavily penalise):",
    ]
    for ap in rubric.get("anti_patterns", []):
        lines.append(f"  • {ap}")
    return "\n".join(lines)
