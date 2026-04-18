"""
AXIOM Prompt Store
Versioned, persistent storage for evolved agent prompts.
Keyed by SHA-256 of the task description. Each agent role (worker / evaluator / rewriter)
gets its own JSON file under prompts/{task_id}/{role}.json.

Schema:
{
  "task_id": str,         # SHA-256(task_description)
  "task_description": str,
  "agent": str,           # "worker" | "evaluator" | "rewriter"
  "best_version": int,    # index into iterations[]
  "best_score": float,
  "iterations": [
    {
      "version": int,
      "prompt": str,
      "score": float,
      "timestamp": str    # ISO-8601
    }
  ]
}
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

PROMPTS_DIR = Path(os.environ.get("AXIOM_PROMPTS_DIR", "prompts"))


def task_id(task_description: str) -> str:
    return hashlib.sha256(task_description.encode()).hexdigest()[:16]


def _path(tid: str, agent: str) -> Path:
    return PROMPTS_DIR / tid / f"{agent}.json"


def load(task_description: str, agent: str) -> dict | None:
    """Return stored data for (task, agent) or None if no history exists."""
    p = _path(task_id(task_description), agent)
    if not p.exists():
        return None
    with p.open() as f:
        return json.load(f)


def best_prompt(task_description: str, agent: str) -> str | None:
    """Return the best evolved prompt for this (task, agent), or None."""
    data = load(task_description, agent)
    if data is None or not data["iterations"]:
        return None
    idx = data.get("best_version", 0)
    return data["iterations"][idx]["prompt"]


def save_iteration(
    task_description: str,
    agent: str,
    prompt: str,
    score: float,
) -> int:
    """Append an iteration and update best if score improved. Returns the new version index."""
    tid = task_id(task_description)
    p = _path(tid, agent)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        with p.open() as f:
            data = json.load(f)
    else:
        data = {
            "task_id": tid,
            "task_description": task_description,
            "agent": agent,
            "best_version": 0,
            "best_score": -1.0,
            "iterations": [],
        }

    version = len(data["iterations"])
    data["iterations"].append(
        {
            "version": version,
            "prompt": prompt,
            "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    if score > data["best_score"]:
        data["best_score"] = score
        data["best_version"] = version

    with p.open("w") as f:
        json.dump(data, f, indent=2)

    return version
