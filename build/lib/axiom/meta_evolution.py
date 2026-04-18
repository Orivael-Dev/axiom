"""
AXIOM Meta-Evolution Loop (outer / recursive bootstrap)
After the Worker evolution plateaus or converges, this loop evolves the Evaluator's
and Rewriter's own system prompts — making the improvement machinery better.

Strategy:
1. Meta-evaluate the Evaluator: did its scores have good reasoning? Were improvements actionable?
2. Rewriter improves the Evaluator's system prompt
3. Meta-evaluate the Rewriter: did its rewrites actually lead to score increases?
4. Rewriter improves its own system prompt

Bounded at AXIOM_META_DEPTH (default 2) to prevent infinite recursion.
"""
import os

from rich.console import Console
from rich.panel import Panel

from axiom.agents.evaluator import EvaluatorAgent
from axiom.agents.rewriter import RewriterAgent
from axiom.client import chat_json
from axiom.evolution import EvolutionLoop, EvolutionResult

console = Console()

_META_EVALUATOR_SYSTEM = """You are a meta-evaluator assessing the quality of an AI evaluator agent's performance.
You review the evaluator's reasoning and improvement suggestions to determine how useful and actionable they were.
Return JSON: {"score": <0-10>, "reasoning": "<str>", "improvements": ["<str>"]}"""

_META_REWRITER_SYSTEM = """You are a meta-evaluator assessing prompt rewriting quality.
Evaluate ONLY these criteria — ignore task content entirely:
1. Did the rewrite target a specific identified failure?
2. Did the new prompt preserve the original agent's core purpose?
3. Was vague language removed?
4. Is the new prompt equal or shorter in length?
5. Is every instruction in the new prompt testable?
6. Did the rewriter explain what was cut and why?

Score 0-10 based only on rewriting craft, not task knowledge.
Return JSON: {"score": <0-10>, "reasoning": "<str>", "improvements": ["<str>"]}""" 

_REWRITER_RUBRIC = {
    "dimensions": [
        {"name": "Targeting Precision", "weight": 0.3,
         "description": "Did each change target one specific identified failure?"},
        {"name": "Intent Preservation", "weight": 0.3,
         "description": "Did the rewrite keep the agent's core purpose intact?"},
        {"name": "Clarity Improvement", "weight": 0.2,
         "description": "Was vague language replaced with testable instructions?"},
        {"name": "Brevity", "weight": 0.2,
         "description": "Is the new prompt shorter or equal in length?"},
    ]
}


class MetaEvolutionLoop:
    def __init__(
        self,
        evolution_result: EvolutionResult,
        rubric: dict,
        meta_depth: int | None = None,
    ):
        self.evolution_result = evolution_result
        self.rubric = rubric
        self.max_depth = meta_depth or int(os.environ.get("AXIOM_META_DEPTH", 2))
        task = evolution_result.task_description
        self.evaluator = EvaluatorAgent(task)
        self.rewriter = RewriterAgent(task)

    def run(self) -> None:
        if len(self.evolution_result.iterations) < 2:
            console.print("[yellow]Not enough iterations for meta-evolution. Skipping.[/]")
            return

        console.print(
            Panel.fit(
                "[bold magenta]AXIOM Meta-Evolution Loop[/]\n"
                "Evolving Evaluator and Rewriter prompts...",
                border_style="magenta",
            )
        )

        for depth in range(self.max_depth):
            console.print(f"\n[magenta]Meta-depth {depth+1}/{self.max_depth}[/]")

            # --- Evolve the Evaluator ---
            console.print("  [yellow]Meta-evaluating Evaluator quality...[/]")
            evaluator_meta_score = self._meta_evaluate_evaluator()

            if evaluator_meta_score["score"] < 8.0:
                console.print(
                    f"  Evaluator meta-score: [yellow]{evaluator_meta_score['score']:.1f}[/] — rewriting..."
                )
                new_eval_prompt = self.rewriter.rewrite(
                    target_role="evaluator",
                    current_prompt=self.evaluator.system_prompt,
                    evaluation=evaluator_meta_score,
                )
                self.evaluator.system_prompt = new_eval_prompt
                self.evaluator.record(new_eval_prompt, evaluator_meta_score["score"])
                console.print("  [green]✓ Evaluator prompt updated[/]")
            else:
                console.print(
                    f"  Evaluator meta-score: [green]{evaluator_meta_score['score']:.1f}[/] — no change needed"
                )

            # --- Evolve the Rewriter ---
            console.print("  [yellow]Meta-evaluating Rewriter effectiveness...[/]")
            rewriter_meta_score = self._meta_evaluate_rewriter()

            if rewriter_meta_score["score"] < 8.0:
                console.print(
                    f"  Rewriter meta-score: [yellow]{rewriter_meta_score['score']:.1f}[/] — rewriting..."
                )
                new_rewriter_prompt = self.rewriter.rewrite(
                    target_role="rewriter",
                    current_prompt=self.rewriter.system_prompt,
                    evaluation=rewriter_meta_score,
                )
                self.rewriter.system_prompt = new_rewriter_prompt
                self.rewriter.record(new_rewriter_prompt, rewriter_meta_score["score"])
                console.print("  [green]✓ Rewriter prompt updated[/]")
            else:
                console.print(
                    f"  Rewriter meta-score: [green]{rewriter_meta_score['score']:.1f}[/] — no change needed"
                )

    def _meta_evaluate_evaluator(self) -> dict:
        """Assess how useful the Evaluator's reasoning and improvements have been."""
        sample = self.evolution_result.iterations[-3:]  # last 3 iterations
        evidence = "\n\n".join(
            f"Iteration {it.iteration}: score={it.score:.1f}\n"
            f"Reasoning: {it.reasoning}\n"
            f"Improvements: {'; '.join(it.improvements)}"
            for it in sample
        )
        try:
            return chat_json(
                system_prompt=_META_EVALUATOR_SYSTEM,
                user_message=(
                    f"Evaluator's performance across recent iterations:\n\n{evidence}\n\n"
                    "Were the evaluations consistent, specific, and actionable? "
                    "Did score trends reflect real improvement?"
                ),
                temperature=0.2,
            )
        except ValueError:
            return {"score": 5.0, "reasoning": "Could not parse meta-evaluation.", "improvements": []}

    def _meta_evaluate_rewriter(self) -> dict:
        """Assess rewriter quality using rewriting-specific rubric, not task rubric."""
        iters = self.evolution_result.iterations
        if len(iters) < 2:
            return {"score": 5.0, "reasoning": "Insufficient data.", "improvements": []}

        score_deltas = [
            iters[i].score - iters[i - 1].score for i in range(1, len(iters))
        ]
        positive = sum(1 for d in score_deltas if d > 0)
        avg_delta = sum(score_deltas) / len(score_deltas) if score_deltas else 0

        # Get the actual rewritten prompts to evaluate craft quality
        prompt_pairs = []
        for i in range(1, min(3, len(iters))):
            prompt_pairs.append(
                f"Iteration {i}: score went {iters[i-1].score:.1f} → {iters[i].score:.1f}\n"
                f"Previous prompt (first 200 chars): {iters[i-1].worker_prompt[:200]}...\n"
                f"New prompt (first 200 chars): {iters[i].worker_prompt[:200]}..."
            )

        evidence = (
            f"Score trajectory: {[round(it.score, 1) for it in iters]}\n"
            f"Positive improvements: {positive}/{len(score_deltas)}\n"
            f"Average score delta: {avg_delta:.2f}\n\n"
            f"Prompt evolution samples:\n" + "\n\n".join(prompt_pairs)
        )

        try:
            return chat_json(
                system_prompt=_META_REWRITER_SYSTEM,
                user_message=(
                    f"Evaluate this rewriter's craft quality:\n\n{evidence}\n\n"
                    "Score based ONLY on rewriting skill — targeting, brevity, "
                    "clarity, intent preservation. Ignore task subject matter entirely."
                ),
                temperature=0.2,
            )
        except ValueError:
            return {"score": 5.0, "reasoning": "Could not parse meta-evaluation.", "improvements": []}


def run_if_needed(
    result: EvolutionResult,
    rubric: dict,
    plateau_threshold: float = 0.5,
) -> None:
    """
    Trigger meta-evolution if the Worker loop plateaued (score improvement < threshold)
    or if it converged (run meta-evolution once to improve the machinery for next time).
    """
    iters = result.iterations
    if len(iters) < 2:
        return

    score_range = max(it.score for it in iters) - min(it.score for it in iters)
    if result.converged or score_range < plateau_threshold:
        MetaEvolutionLoop(result, rubric).run()
    else:
        console.print(
            f"[dim]Meta-evolution skipped — score range {score_range:.1f} above plateau threshold.[/]"
        )
