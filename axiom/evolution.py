"""
AXIOM Evolution Loop (inner)
Worker → Evaluator → Rewriter → repeat until quality threshold or max iterations.

Each iteration:
1. Worker executes the task with its current system prompt
2. Evaluator scores the output using the rubric
3. If score >= threshold: save best, stop
4. Rewriter generates an improved Worker system prompt
5. Repeat

All state is logged to logs/{run_id}.jsonl for full mutation traceability.
"""
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

from axiom.agents.evaluator import EvaluatorAgent
from axiom.agents.rewriter import RewriterAgent
from axiom.agents.worker import WorkerAgent

LOGS_DIR = Path(os.environ.get("AXIOM_LOGS_DIR", "logs"))
console = Console()


@dataclass
class IterationResult:
    iteration: int
    worker_prompt: str
    worker_output: str
    score: float
    reasoning: str
    improvements: list[str]
    dimension_scores: dict = field(default_factory=dict)


@dataclass
class EvolutionResult:
    task_description: str
    run_id: str
    iterations: list[IterationResult] = field(default_factory=list)
    best_iteration: int = 0
    best_score: float = 0.0
    converged: bool = False

    @property
    def best(self) -> IterationResult:
        return self.iterations[self.best_iteration]


class EvolutionLoop:
    def __init__(
        self,
        task_description: str,
        rubric: dict,
        max_iterations: int | None = None,
        quality_threshold: float | None = None,
    ):
        self.task_description = task_description
        self.rubric = rubric
        self.max_iterations = max_iterations or int(
            os.environ.get("AXIOM_MAX_ITERATIONS", 10)
        )
        self.quality_threshold = quality_threshold or float(
            os.environ.get("AXIOM_QUALITY_THRESHOLD", 8.0)
        )
        self.run_id = uuid.uuid4().hex[:8]

        self.worker = WorkerAgent(task_description)
        self.evaluator = EvaluatorAgent(task_description)
        self.rewriter = RewriterAgent(task_description)

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._log_file = LOGS_DIR / f"{self.run_id}.jsonl"

    def run(self) -> EvolutionResult:
        result = EvolutionResult(
            task_description=self.task_description,
            run_id=self.run_id,
        )

        console.print(
            Panel.fit(
                f"[bold green]AXIOM Evolution Loop[/] — run [cyan]{self.run_id}[/]\n"
                f"Task: [white]{self.task_description[:120]}[/]\n"
                f"Threshold: [yellow]{self.quality_threshold}[/]  "
                f"Max iterations: [yellow]{self.max_iterations}[/]",
                border_style="green",
            )
        )

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task("Evolving worker prompt...", total=self.max_iterations)

            for i in range(self.max_iterations):
                progress.update(task, description=f"Iteration {i+1}/{self.max_iterations}")

                # Step 1: Worker executes
                console.print(f"\n[bold cyan]▶ Iteration {i+1}[/] — Worker executing...")
                worker_output = self.worker.execute(self.task_description)

                # Step 2: Evaluator scores
                console.print("  [yellow]⚖  Evaluator scoring...[/]")
                try:
                    evaluation = self.evaluator.score(
                        task=self.task_description,
                        output=worker_output,
                        rubric=self.rubric,
                    )
                except (ValueError, KeyError) as e:
                    console.print(f"  [red]Evaluator failed: {e}. Skipping iteration.[/]")
                    progress.advance(task)
                    continue

                score = float(evaluation.get("score", 0.0))
                reasoning = evaluation.get("reasoning", "")
                improvements = evaluation.get("improvements", [])

                iter_result = IterationResult(
                    iteration=i,
                    worker_prompt=self.worker.system_prompt,
                    worker_output=worker_output,
                    score=score,
                    reasoning=reasoning,
                    improvements=improvements,
                    dimension_scores=evaluation.get("dimension_scores", {}),
                )
                result.iterations.append(iter_result)

                # Track best
                if score > result.best_score:
                    result.best_score = score
                    result.best_iteration = len(result.iterations) - 1
                    try:
                        from axiom_files.parser import save_snapshot
                        save_snapshot(
                            "worker",
                            score,
                            run_id=self.run_id,
                            task=self.task_description,
                        )
                    except Exception:
                        pass
                    # Promote to shared memory when score meets global threshold
                    try:
                        from axiom.shared_memory import promote
                        promoted = promote(
                            role="worker",
                            prompt=self.worker.system_prompt,
                            score=score,
                            task_id=self.run_id,
                        )
                        if promoted:
                            console.print(f"  [cyan]↑ Shared memory promoted — worker global best: {score:.1f}[/]")
                    except Exception:
                        pass

                # Persist prompt + log
                self.worker.record(self.worker.system_prompt, score)
                self._log(i, "worker", self.worker.system_prompt, worker_output, score, evaluation)

                console.print(
                    f"  [{'green' if score >= self.quality_threshold else 'yellow'}]"
                    f"Score: {score:.1f}/10[/]  {reasoning[:120]}"
                )

                if score >= self.quality_threshold:
                    result.converged = True
                    console.print(
                        f"\n[bold green]✓ Converged at iteration {i+1} "
                        f"with score {score:.1f}[/]"
                    )
                    # Delegate to Evaluator on output_ready (via DELEGATES machinery)
                    try:
                        from axiom_files.parser import load_axiom, get_delegates_for
                        _w_parsed = load_axiom("worker")
                        _targets = get_delegates_for("Worker", _w_parsed, "output_ready")
                        if _targets:
                            console.print(f"  [green]→ Worker delegates to {_targets[0]} (on: output_ready)[/]")
                    except Exception:
                        pass
                    progress.advance(task)
                    break

                # Step 3: Determine routing via DELEGATES — RecoveryMode when below threshold
                _delegate_target = None
                try:
                    from axiom_files.parser import load_axiom, get_delegates_for
                    _w_parsed = load_axiom("worker")
                    _targets = get_delegates_for("Worker", _w_parsed, "RecoveryMode")
                    if _targets:
                        _delegate_target = _targets[0]
                        console.print(f"  [magenta]→ Worker delegates to {_delegate_target} (on: RecoveryMode)[/]")
                except Exception:
                    _delegate_target = "Rewriter"  # fallback to direct call

                if i < self.max_iterations - 1 and _delegate_target in (None, "Rewriter"):
                    console.print("  [magenta]✎  Rewriter improving worker prompt...[/]")
                    new_prompt = self.rewriter.rewrite(
                        target_role="worker",
                        current_prompt=self.worker.system_prompt,
                        evaluation=evaluation,
                    )
                    self.worker.system_prompt = new_prompt
                    self._log(i, "rewriter", self.rewriter.system_prompt, new_prompt, score, {})

                    # Write evolved prompt back to worker.axiom
                    try:
                        from axiom_files.parser import load_axiom, save_axiom
                        current_axiom = load_axiom("worker")
                        console.print(f"  [cyan]DEBUG: loaded axiom keys: {list(current_axiom.keys())}[/]")

                        # Parse evolved prompt back into axiom sections
                        lines = new_prompt.strip().split("\n")
                        new_constraints = [
                            l.strip().lstrip("-").strip()
                            for l in lines
                            if l.strip().startswith("-") or "constraint" in l.lower()
                        ]
                        console.print(f"  [cyan]DEBUG: found {len(new_constraints)} constraints[/]")

                        # Only update constraints if rewriter found new ones AND they pass validation
                        if new_constraints:
                            from axiom_files.validator import validate_parsed
                            test_axiom = dict(current_axiom)
                            test_axiom["constraints"] = new_constraints
                            val_result = validate_parsed(test_axiom)
                            if val_result["status"] == "invalid":
                                console.print(
                                    f"  [red]⚠ Rewriter produced invalid constraints — skipping write[/]"
                                )
                                for iss in val_result["issues"]:
                                    if iss["level"] == "error":
                                        console.print(f"    [red]→ {iss['message']}[/]")
                                # do not update — keep existing constraints
                            else:
                                current_axiom["constraints"] = new_constraints

                        # Bump version
                        version = float(current_axiom.get("version", "1.0")) + 0.1
                        current_axiom["version"] = f"{version:.1f}"

                        save_axiom("worker", current_axiom)
                        console.print(f"  [green]✓ worker.axiom updated → v{version:.1f}[/]")

                        # Log axiom mutation to JSONL
                        self._log(i, "axiom_mutation", new_prompt, "", score, {
                            "axiom_version": f"{version:.1f}",
                            "constraints_updated": bool(new_constraints),
                            "constraints_count": len(new_constraints),
                            "axiom_keys": list(current_axiom.keys()),
                        })
                    except Exception as axiom_err:
                        console.print(f"  [red]DEBUG ERROR: {type(axiom_err).__name__}: {axiom_err}[/]")
                        self._log(i, "axiom_mutation_error", "", str(axiom_err), score, {
                            "error_type": type(axiom_err).__name__,
                        })

                progress.advance(task)

        if not result.converged:
            console.print(
                f"\n[yellow]Max iterations reached. Best score: {result.best_score:.1f}[/]"
            )

        return result

    def _log(
        self,
        iteration: int,
        agent_role: str,
        prompt: str,
        output: str,
        score: float,
        evaluation: dict,
    ) -> None:
        entry = {
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "iteration": iteration,
            "agent_role": agent_role,
            "prompt": prompt,
            "output": output,
            "score": score,
            "evaluation": evaluation,
        }
        with self._log_file.open("a") as f:
            f.write(json.dumps(entry) + "\n")
