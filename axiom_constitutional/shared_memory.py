"""
axiom/shared_memory.py
Spec: axiom_files/shared_memory.axiom

Persists the globally best evolved prompt per agent role across all tasks
and sessions. Complements axiom/store.py (task-scoped) with role-scoped
cross-session memory.

Seed priority in BaseAgent.system_prompt (applied by base.py):
  1. Task-scoped best prompt  (store.best_prompt)
  2. Global best prompt       (SharedMemoryStore.best_global)   ← this module
  3. .axiom file default      (get_prompt)
  4. Hardcoded seed_prompt

Promotion happens in EvolutionLoop when score > current global best.
Threshold is configurable via AXIOM_SHARED_MEMORY_THRESHOLD (default 8.0).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_SHARED_DIR = Path(os.environ.get("AXIOM_PROMPTS_DIR", "prompts")) / "shared"
_PROMOTION_THRESHOLD = float(os.environ.get("AXIOM_SHARED_MEMORY_THRESHOLD", "8.0"))


def _path(role: str) -> Path:
    return _SHARED_DIR / f"{role.lower()}.json"


def best_global(role: str) -> str | None:
    """
    CrossSessionSeed — return the globally best prompt for role, or None.
    Called by BaseAgent when no task-specific history exists.
    """
    p = _path(role)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("best_prompt")
    except (json.JSONDecodeError, OSError):
        return None


def global_best_score(role: str) -> float:
    """Return the current global best score for role, or -1.0 if no record exists."""
    p = _path(role)
    if not p.exists():
        return -1.0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return float(data.get("best_score", -1.0))
    except (json.JSONDecodeError, OSError):
        return -1.0


def promote(
    role: str,
    prompt: str,
    score: float,
    task_id: str = "",
) -> bool:
    """
    PromotionGate — promote prompt to global best when score strictly exceeds
    both the current global best AND the promotion threshold.

    Returns True if promotion occurred, False otherwise.
    Appends to promotion_history regardless of promotion outcome.
    """
    if score < _PROMOTION_THRESHOLD:
        return False

    _SHARED_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(role)

    # Load or initialise store
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = _empty(role)
    else:
        data = _empty(role)

    did_promote = score > data["best_score"]

    # Append to history before updating best (history is append-only)
    data["history"].append({
        "score": score,
        "task_id": task_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "previous_best_score": data["best_score"],
        "promoted": did_promote,
    })

    if did_promote:
        data["best_score"] = score
        data["best_prompt"] = prompt

    try:
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        # Store persistence failure — in-memory state updated, disk write failed
        pass

    return did_promote


def summary(role: str) -> dict:
    """Return a summary of the global memory for role."""
    p = _path(role)
    if not p.exists():
        return {"role": role, "best_score": -1.0, "promotions": 0, "has_global_best": False}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        promotions = sum(1 for h in data.get("history", []) if h.get("promoted"))
        return {
            "role": role,
            "best_score": data.get("best_score", -1.0),
            "promotions": promotions,
            "history_entries": len(data.get("history", [])),
            "has_global_best": data.get("best_prompt") is not None,
        }
    except (json.JSONDecodeError, OSError):
        return {"role": role, "best_score": -1.0, "promotions": 0, "has_global_best": False}


def _empty(role: str) -> dict:
    return {
        "role": role.lower(),
        "best_score": -1.0,
        "best_prompt": None,
        "history": [],
    }
