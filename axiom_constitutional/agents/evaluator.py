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
  },
  "evidence_quotes": [str]   # required when score >= 8.0; verbatim from worker
}

Anti-pegging notes:
  - The user message echoes the strict scoring bands from
    axiom_files/core/evaluator.axiom because LLMs reliably drift from
    system-prompt constraints under permissive user messages.
  - Scores >= 8.0 require at least one verbatim quote from the worker
    output proving each rubric dimension was met — makes lazy "looks
    good, 9.0" responses surface evidence or admit they have none.
  - Temperature is 0.0 here (was 0.2). Determinism makes pegging
    easier to detect downstream.
"""
from axiom_constitutional.agents.base import BaseAgent
from axiom_constitutional.rubric import format_for_prompt

_SEED = (
    "You are a rigorous, impartial quality evaluator for AI agent outputs. "
    "You score responses 0-10 using a provided rubric. "
    "Your evaluations are specific, evidence-based, and actionable. "
    "You identify exactly what went wrong and exactly how to fix it. "
    "You always return valid JSON matching the required schema."
)

# Hard scoring bands the user message reinforces on every call. These
# mirror axiom_files/core/evaluator.axiom's CONSTRAINT block and the
# default rubric's anchor — repeating them in the user turn closes the
# system-prompt-drift loophole.
_STRICT_BANDS = """STRICT SCORING BANDS (enforce on every call):
  9.0 – 10.0  Reserved for outputs you can cite as exemplary. Every
              rubric dimension is fully met with concrete, specific,
              verifiable content. If you cannot quote evidence for
              EACH dimension, the score is below 9.0.
  7.0 –  8.9  Solid and improvable. The output addresses every rubric
              dimension but at least one is generic, missing detail,
              or could be sharper.
  5.0 –  6.9  Partially correct. At least one rubric dimension is
              superficially addressed or missing.
  0.0 –  4.9  Critical requirement missing, factually wrong, or
              violates an anti-pattern.

HARD CAPS (no exceptions):
  - Generic, hedging, or boilerplate output cannot exceed 7.0 even if
    it nominally hits every dimension.
  - If ANY anti-pattern listed in the rubric appears in the output,
    cap the score at 6.0 and name the anti-pattern in `reasoning`.
  - If a rubric dimension is omitted entirely, cap the score at 5.0.

EVIDENCE REQUIREMENT:
  When `score` is 8.0 or higher, `evidence_quotes` MUST contain at
  least one verbatim quote (copy-pasted from the worker output) per
  rubric dimension proving that dimension was met. An empty
  `evidence_quotes` with a >=8.0 score is a self-inconsistent
  evaluation — return a lower score instead.

SELF-CHECK BEFORE RETURNING:
  - Does each dimension_score have specific evidence in `reasoning`?
  - Did you trigger any HARD CAP? If yes, is the overall score capped?
  - Is the overall score the weighted sum of dimension_scores
    (within +/- 0.5)? If not, recompute.
"""


class EvaluatorAgent(BaseAgent):
    role = "evaluator"
    seed_prompt = _SEED

    def score(self, task: str, output: str, rubric: dict) -> dict:
        """
        Score the Worker's output against the rubric.
        Returns a dict with score, reasoning, improvements,
        dimension_scores, and evidence_quotes.
        """
        rubric_text = format_for_prompt(rubric)

        user_message = f"""{_STRICT_BANDS}

RUBRIC:
{rubric_text}

TASK GIVEN TO WORKER:
{task}

WORKER OUTPUT:
{output}

Apply the strict scoring bands above. Return JSON:
{{
  "score": <float 0-10>,
  "reasoning": "<evidence-based explanation; name any hard cap triggered>",
  "improvements": ["<specific actionable improvement 1>", "..."],
  "dimension_scores": {{
    "<dimension_name>": <float 0-10>
  }},
  "evidence_quotes": ["<verbatim quote from worker output>", "..."]
}}

If score >= 8.0, evidence_quotes MUST be non-empty."""

        result = self._call_json(user_message, temperature=0.0)

        # Defensive post-check: enforce the evidence requirement even if
        # the model returns >=8.0 with no evidence_quotes. We knock the
        # score down to 7.5 and stash the original under
        # `_inflated_score_demoted` so the run log shows what happened.
        # This caps the "default 9.0" pegging failure mode at 7.5.
        try:
            score = float(result.get("score", 0.0))
        except (TypeError, ValueError):
            return result
        quotes = result.get("evidence_quotes") or []
        if score >= 8.0 and not (isinstance(quotes, list) and any(
            isinstance(q, str) and q.strip() for q in quotes
        )):
            result["_inflated_score_demoted"] = {
                "original_score": score,
                "reason": "score >= 8.0 with empty evidence_quotes",
            }
            result["score"] = 7.5
            reasoning = result.get("reasoning", "") or ""
            result["reasoning"] = (
                f"[DEMOTED from {score:.1f} to 7.5: high score returned "
                f"with no evidence_quotes — see evaluator strict bands.] "
                f"{reasoning}"
            )
        return result
