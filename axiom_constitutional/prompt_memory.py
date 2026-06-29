"""
Prompt memory — Experience RAG for the evolution loop.

Indexes every iteration (task -> evolved prompt + score) in a local SQLite FTS5
store and recalls the best prompts from *similar past tasks*. This lets evolution
warm-start from proven ground and compound across runs, instead of starting cold
every time (the existing store.py is keyed by exact-task hash only).

Same BM25/FTS5 technique as axiom_semantic_cosmos; self-contained for portability.
All functions fail soft (return [] / no-op) so they can never break a run.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

try:
    from axiom_constitutional.store import PROMPTS_DIR  # type: ignore
except Exception:
    PROMPTS_DIR = Path("prompts")

MEM_DIR = PROMPTS_DIR / "_memory"
DB_PATH = MEM_DIR / "prompt_memory.db"

# Only `task` is full-text indexed (we retrieve by task similarity); the rest ride along.
_CREATE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS pm USING fts5("
    "task, prompt UNINDEXED, role UNINDEXED, payload UNINDEXED, score UNINDEXED, "
    "tokenize='porter ascii')"
)


def _conn() -> sqlite3.Connection:
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.execute(_CREATE)
    c.commit()
    return c


def _terms(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]{3,}", (text or "").lower())


def index_iteration(task: str, prompt: str, score: float,
                    evaluation: Optional[dict] = None, role: str = "worker") -> None:
    """Record one iteration's evolved prompt + score for future recall."""
    try:
        ev = evaluation or {}
        payload = json.dumps({
            "failures": ev.get("failures") or ev.get("improvements") or [],
            "dims": ev.get("dimension_scores", {}),
            "ts": time.time(),
        })
        c = _conn()
        c.execute("INSERT INTO pm(task, prompt, role, payload, score) VALUES (?,?,?,?,?)",
                  (task, prompt, role, payload, float(score)))
        c.commit()
        c.close()
    except Exception:
        pass  # memory is best-effort; never break the run


def recall(task: str, k: int = 3, min_score: float = 8.0, role: str = "worker") -> list[dict]:
    """Best evolved prompts from similar past tasks, score-floored, best-first."""
    try:
        toks = _terms(task)
        if not toks:
            return []
        match = " OR ".join(toks)  # OR so partial overlap still recalls
        c = _conn()
        rows = c.execute(
            "SELECT task, prompt, role, payload, score, -bm25(pm) AS rel "
            "FROM pm WHERE pm MATCH ? AND role = ? ORDER BY rel DESC LIMIT ?",
            (match, role, max(k * 4, 12)),
        ).fetchall()
        c.close()
    except Exception:
        return []
    out: list[dict] = []
    for t, p, rl, pl, sc, _rel in rows:
        try:
            sc = float(sc)
        except Exception:
            continue
        if sc < float(min_score):
            continue
        try:
            meta = json.loads(pl)
        except Exception:
            meta = {}
        out.append({"task": t, "prompt": p, "role": rl, "score": sc,
                    "failures": meta.get("failures", []), "dims": meta.get("dims", {})})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:k]


def exemplar_text(mem: list[dict], limit: int = 2) -> str:
    """Compact 'what worked before' block to hand the Rewriter."""
    if not mem:
        return ""
    lines = ["PRIOR WINNING PATTERNS (from similar past tasks — adapt, do not copy verbatim):"]
    for m in mem[:limit]:
        lines.append(f"- scored {m['score']:.1f}/10 on a similar task. Effective prompt excerpt:")
        lines.append("  " + " ".join((m.get("prompt") or "").split())[:400])
    return "\n".join(lines)


def stats() -> int:
    """How many iterations are remembered."""
    try:
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM pm").fetchone()[0]
        c.close()
        return int(n)
    except Exception:
        return 0
