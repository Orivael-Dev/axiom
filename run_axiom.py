"""
AXIOM DSL v0 — First Language Test
Runs the evolution loop driven entirely by .axiom files.

The agents load their system prompts from:
  axiom_files/worker.axiom
  axiom_files/evaluator.axiom
  axiom_files/rewriter.axiom

After each iteration the Rewriter asks the LLM to produce an updated
parsed .axiom dict for the Worker, which is written back to disk.
This is the first test of AXIOM as a self-modifying language.

Run with:
  .\venv\Scripts\python run_axiom.py
"""
import json
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from axiom_files.parser import load_axiom, save_axiom, to_system_prompt, get_prompt
from axiom import client as nim
from axiom import store as prompt_store
from axiom import rubric as rubric_module

console = Console()

MAX_ITERATIONS = int(os.environ.get("AXIOM_MAX_ITERATIONS", 5))
THRESHOLD = float(os.environ.get("AXIOM_QUALITY_THRESHOLD", 8.0))


# ── Validate env ──────────────────────────────────────────────────────────────
api_key = os.environ.get("NVIDIA_API_KEY", "")
if not api_key or api_key == "your_nvidia_api_key_here":
    console.print("[red]NVIDIA_API_KEY not set in .env[/]")
    sys.exit(1)


# ── Show loaded .axiom definitions ───────────────────────────────────────────
console.print(Panel.fit(
    "[bold green]AXIOM DSL v0 — First Language Test[/]\n"
    "Agents are defined by [cyan].axiom[/] files, not hardcoded prompts.",
    border_style="green",
))

for role in ("worker", "evaluator", "rewriter"):
    try:
        prompt = get_prompt(role)
        console.print(f"\n[bold cyan]{role.upper()}.axiom[/] → system prompt preview:")
        console.print(f"[dim]{prompt[:200]}{'...' if len(prompt) > 200 else ''}[/]")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        sys.exit(1)


# ── Get task ─────────────────────────────────────────────────────────────────
task = Prompt.ask("\n[bold cyan]Task for the Worker agent[/]")
if not task.strip():
    sys.exit(0)


# ── Generate rubric ───────────────────────────────────────────────────────────
console.print("\n[yellow]Generating rubric...[/]")
rubric = rubric_module.generate(task)
console.print(f"[green]✓[/] {rubric.get('task_summary', '')[:80]}")
from axiom.rubric import format_for_prompt
rubric_text = format_for_prompt(rubric)


# ── Build system prompts from .axiom ─────────────────────────────────────────
worker_prompt  = get_prompt("worker")
eval_prompt    = get_prompt("evaluator")
rewrite_prompt = get_prompt("rewriter")

best_score = 0.0
best_output = ""

console.print(f"\n[bold]Running up to {MAX_ITERATIONS} iterations (threshold {THRESHOLD})[/]\n")

for i in range(MAX_ITERATIONS):
    console.print(f"[bold cyan]▶ Iteration {i+1}[/]")

    # ── Worker ────────────────────────────────────────────────────────────────
    console.print("  [cyan]WORKER[/] executing...")
    worker_output = nim.chat(
        system_prompt=worker_prompt,
        user_message=f"Task:\n{task}",
        temperature=0.7,
    )

    # ── Evaluator ─────────────────────────────────────────────────────────────
    console.print("  [yellow]EVALUATOR[/] scoring...")
    eval_user = f"""RUBRIC:
{rubric_text}

TASK:
{task}

WORKER OUTPUT:
{worker_output}

Return JSON: {{"score": <0-10>, "reasoning": "<str>", "failures": ["<str>"], "suggested_changes": ["<str>"]}}"""

    try:
        evaluation = nim.chat_json(
            system_prompt=eval_prompt,
            user_message=eval_user,
            temperature=0.2,
        )
    except ValueError as e:
        console.print(f"  [red]Evaluator parse error: {e}[/]")
        continue

    score = float(evaluation.get("score", 0.0))
    reasoning = evaluation.get("reasoning", "")
    failures = evaluation.get("failures", [])
    suggested = evaluation.get("suggested_changes", [])

    color = "green" if score >= THRESHOLD else ("yellow" if score >= 5 else "red")
    console.print(f"  Score: [{color}]{score:.1f}/10[/]  {reasoning[:100]}")

    # Save to prompt store
    prompt_store.save_iteration(task, "worker", worker_prompt, score)

    if score > best_score:
        best_score = score
        best_output = worker_output

    if score >= THRESHOLD:
        console.print(f"\n[bold green]✓ Converged at iteration {i+1} — {score:.1f}/10[/]")
        break

    if i == MAX_ITERATIONS - 1:
        break

    # ── Rewriter — updates .axiom file ────────────────────────────────────────
    console.print("  [magenta]REWRITER[/] updating worker.axiom...")

    current_axiom = load_axiom("worker")

    rewrite_user = f"""Current worker.axiom definition (parsed):
{json.dumps(current_axiom, indent=2)}

Evaluation failures:
{chr(10).join(f'- {f}' for f in failures)}

Suggested changes:
{chr(10).join(f'- {s}' for s in suggested)}

Return an updated version of the parsed axiom dict as JSON.
Only change weak sections. Keep the original goal and valid constraints.
Explain mutations under a top-level "mutations" key: [{{"field": ..., "cut": ..., "added": ..., "why": ...}}]
"""

    try:
        new_axiom_raw = nim.chat_json(
            system_prompt=rewrite_prompt,
            user_message=rewrite_user,
            temperature=0.4,
        )
    except ValueError as e:
        console.print(f"  [red]Rewriter parse error (skipping axiom update): {e}[/]")
        continue

    # Log mutations
    mutations = new_axiom_raw.pop("mutations", [])
    if mutations:
        console.print("  Mutations:")
        for m in mutations:
            console.print(
                f"    [dim]{m.get('field','?')}:[/] cut=[red]{m.get('cut','')}[/]  "
                f"added=[green]{m.get('added','')}[/]  why={m.get('why','')}"
            )

    # Merge — only update keys that exist in the parsed schema
    valid_keys = set(current_axiom.keys())
    for k, v in new_axiom_raw.items():
        if k in valid_keys:
            current_axiom[k] = v

    # Write back to disk and update prompt for next iteration
    save_axiom("worker", current_axiom)
    worker_prompt = to_system_prompt(current_axiom)
    console.print("  [green]✓ worker.axiom updated[/]")


# ── Final output ──────────────────────────────────────────────────────────────
console.print(Panel(
    best_output,
    title=f"[green]Best Output — score {best_score:.1f}/10[/]",
    border_style="green",
))
console.print(f"\n[dim]Evolved prompts saved to prompts/  |  .axiom files updated in axiom_files/[/]")
