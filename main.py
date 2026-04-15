"""
AXIOM — Phase 1 CLI
Run with: python main.py

The user describes a task in plain English. AXIOM generates a scoring rubric,
runs the evolution loop (Worker → Evaluator → Rewriter × N), then triggers
meta-evolution to improve the evaluation and rewriting machinery itself.
"""
import os
import sys
from pathlib import Path

# Load .env before any AXIOM imports
from dotenv import load_dotenv
load_dotenv()

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
console = Console()


def _print_final_report(result) -> None:
    table = Table(title="Evolution Summary", border_style="green")
    table.add_column("Iteration", style="cyan", justify="right")
    table.add_column("Score", style="yellow", justify="right")
    table.add_column("Reasoning (preview)", style="white")

    for it in result.iterations:
        marker = " ★" if it.iteration == result.best_iteration else ""
        table.add_row(
            str(it.iteration + 1) + marker,
            f"{it.score:.1f}",
            it.reasoning[:80] + ("..." if len(it.reasoning) > 80 else ""),
        )
    console.print(table)

    console.print(
        Panel(
            result.best.worker_output,
            title=f"[green]Best Output (iteration {result.best_iteration + 1}, "
                  f"score {result.best_score:.1f})[/]",
            border_style="green",
        )
    )
    console.print(
        f"\n[dim]Run ID: {result.run_id}  |  Log: logs/{result.run_id}.jsonl  |  "
        f"Prompts: prompts/{result.task_description[:20]}...[/]"
    )


@app.command()
def main(
    task: str = typer.Argument(
        default="",
        help="Task description. Leave empty to enter interactively.",
    ),
    max_iterations: int = typer.Option(
        0,
        "--max-iterations", "-n",
        help="Override max evolution iterations (0 = use .env default).",
    ),
    threshold: float = typer.Option(
        0.0,
        "--threshold", "-t",
        help="Override quality threshold 0-10 (0 = use .env default).",
    ),
    no_meta: bool = typer.Option(
        False,
        "--no-meta",
        help="Skip meta-evolution (Evaluator + Rewriter self-improvement).",
    ),
) -> None:
    console.print(
        Panel.fit(
            "[bold green]AXIOM[/] — An AI-Native Language for Building Self-Evolving Intelligence\n"
            "[dim]Phase 1: Self-Improving Prompt Agent[/]",
            border_style="green",
        )
    )

    # --- Validate environment ---
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key or api_key == "your_nvidia_api_key_here":
        console.print(
            "[bold red]Error:[/] NVIDIA_API_KEY is not configured.\n"
            "Edit [cyan].env[/] and set your key from [link]https://build.nvidia.com[/link]"
        )
        raise typer.Exit(code=1)

    # --- Get task ---
    if not task:
        task = Prompt.ask("\n[bold cyan]Describe the task for the agent to master[/]")
    if not task.strip():
        console.print("[red]No task provided. Exiting.[/]")
        raise typer.Exit(code=1)

    # --- Generate rubric ---
    console.print("\n[yellow]Generating scoring rubric...[/]")
    try:
        from axiom import rubric as rubric_module
        rubric = rubric_module.generate(task)
    except Exception as e:
        console.print(f"[red]Rubric generation failed: {e}[/]")
        raise typer.Exit(code=1)

    console.print(f"[green]✓ Rubric ready:[/] {rubric.get('task_summary', task[:60])}")

    # --- Run evolution loop ---
    from axiom.evolution import EvolutionLoop
    loop_kwargs: dict = {}
    if max_iterations > 0:
        loop_kwargs["max_iterations"] = max_iterations
    if threshold > 0:
        loop_kwargs["quality_threshold"] = threshold

    loop = EvolutionLoop(task, rubric, **loop_kwargs)
    try:
        result = loop.run()
    except Exception as e:
        console.print(f"\n[bold red]Evolution loop error:[/] {e}")
        raise typer.Exit(code=1)

    # --- Print report ---
    _print_final_report(result)

    # --- Meta-evolution ---
    if not no_meta:
        console.print("\n[dim]Running meta-evolution to improve Evaluator and Rewriter...[/]")
        try:
            from axiom import meta_evolution
            meta_evolution.run_if_needed(result, rubric)
        except Exception as e:
            console.print(f"[yellow]Meta-evolution error (non-fatal): {e}[/]")

    console.print("\n[bold green]Done.[/] Evolved prompts saved to [cyan]prompts/[/]")


if __name__ == "__main__":
    app()
