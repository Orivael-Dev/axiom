"""
axiom_arc_run.py
AXIOM ARC-AGI Evaluation — Two-condition run

Baseline:      no governance — pattern-match and output
Constitutional: explicit rule-first reasoning before answer

Usage:
  git clone https://github.com/fchollet/ARC-AGI
  set ANTHROPIC_API_KEY=sk-ant-...
  python axiom_arc_run.py
"""

import json
import os
import glob
from anthropic import Anthropic

ARC_PATH = "ARC-AGI/data/training/"

BASELINE_PROMPT = """Solve this ARC-AGI task.
Given the training examples, determine the
transformation rule and apply it to the test input.
Output only the grid as a JSON array."""

AXIOM_PROMPT = """Solve this ARC-AGI task.

Constitutional rules:
1. State the transformation rule you observe
   from the examples BEFORE producing the answer.
2. Acknowledge uncertainty if the rule is ambiguous.
3. Show your reasoning — do not pattern-match silently.
4. If multiple rules could explain the examples,
   state all of them and choose the most parsimonious.
5. Output the grid, then your confidence (0-100%).

Never output an answer without stating the rule first."""


def load_task(filepath):
    with open(filepath) as f:
        return json.load(f)


def grid_to_text(grid):
    return "\n".join(" ".join(str(c) for c in row) for row in grid)


def format_task(task):
    lines = ["Training examples:"]
    for i, ex in enumerate(task["train"], 1):
        lines.append(f"\nExample {i} Input:")
        lines.append(grid_to_text(ex["input"]))
        lines.append(f"Example {i} Output:")
        lines.append(grid_to_text(ex["output"]))
    lines.append("\nTest Input:")
    lines.append(grid_to_text(task["test"][0]["input"]))
    lines.append("\nWhat is the Test Output?")
    return "\n".join(lines)


def score_response(response, correct_output):
    """Row-match scoring — fraction of correct rows found in response."""
    if not correct_output:
        return 0
    correct_rows = [str(row) for row in correct_output]
    matches = sum(1 for row in correct_rows if str(row) in response)
    return matches / len(correct_rows)


def run_arc_eval(n_tasks=50):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        return

    client = Anthropic(api_key=api_key)

    task_files = glob.glob(f"{ARC_PATH}*.json")[:n_tasks]
    if not task_files:
        print(f"No task files found at {ARC_PATH}")
        print("Run: git clone https://github.com/fchollet/ARC-AGI")
        return

    print(f"AXIOM ARC-AGI Evaluation")
    print(f"Model:  claude-sonnet-4-6")
    print(f"Tasks:  {len(task_files)}")
    print(f"{'─' * 50}")

    baseline_scores = []
    axiom_scores = []

    for i, filepath in enumerate(task_files, 1):
        task = load_task(filepath)
        task_text = format_task(task)
        correct = task["test"][0].get("output", [])

        print(f"\n[{i:02d}/{len(task_files)}] {os.path.basename(filepath)}")

        # Baseline — no governance
        try:
            b_resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                system=BASELINE_PROMPT,
                messages=[{"role": "user", "content": task_text}],
            )
            b_score = score_response(b_resp.content[0].text, correct)
            baseline_scores.append(b_score)
            print(f"  Baseline:       {b_score:.2f}")
        except Exception as e:
            baseline_scores.append(0)
            print(f"  Baseline:       ERROR {e}")

        # Constitutional — rule-first reasoning
        try:
            a_resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=800,
                system=AXIOM_PROMPT,
                messages=[{"role": "user", "content": task_text}],
            )
            a_score = score_response(a_resp.content[0].text, correct)
            axiom_scores.append(a_score)
            print(f"  Constitutional: {a_score:.2f}")
        except Exception as e:
            axiom_scores.append(0)
            print(f"  Constitutional: ERROR {e}")

    b_avg = sum(baseline_scores) / len(baseline_scores) * 100
    a_avg = sum(axiom_scores) / len(axiom_scores) * 100
    delta = round(a_avg - b_avg, 1)

    print()
    print("=" * 50)
    print("  AXIOM ARC-AGI RESULTS")
    print("=" * 50)
    print(f"  Tasks run:       {len(task_files)}")
    print(f"  Baseline:        {b_avg:.1f}%")
    print(f"  Constitutional:  {a_avg:.1f}%")
    print(f"  Delta:           {delta:+.1f}%")
    print("=" * 50)

    results = {
        "model": "claude-sonnet-4-6",
        "tasks_run": len(task_files),
        "baseline_pct": round(b_avg, 1),
        "axiom_pct": round(a_avg, 1),
        "delta": delta,
        "baseline_scores": baseline_scores,
        "axiom_scores": axiom_scores,
    }
    with open("arc_agi_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Saved to arc_agi_results.json")
    return results


if __name__ == "__main__":
    run_arc_eval(n_tasks=50)
