"""
Knowledge RAG — ground answers in AXIOM's real docs + .axiom examples.

So the model can answer what it doesn't know (e.g. "how do I build an AXIOM agent?")
by *retrieving* the spec / agent docs / working .axiom examples instead of guessing
syntax. Self-contained SQLite FTS5/BM25 over a chunked corpus. Fail-soft: every
function returns "" / [] / no-op on error so it can never break a run.

Corpus = top-level *.md + docs/**/*.md + axiom_files/**/*.{axiom,md}.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # repo root

try:
    from axiom_constitutional.store import PROMPTS_DIR  # type: ignore
except Exception:
    PROMPTS_DIR = Path(os.environ.get("AXIOM_PROMPTS_DIR", "prompts"))

KB_DIR = PROMPTS_DIR / "_knowledge"
DB_PATH = KB_DIR / "knowledge.db"

CORPUS_GLOBS = ["*.md", "docs/**/*.md", "axiom_files/**/*.axiom", "axiom_files/**/*.md"]
_MAX_CHUNK = 1600

_CREATE = ("CREATE VIRTUAL TABLE IF NOT EXISTS kb USING fts5("
           "title, body, source UNINDEXED, tokenize='porter ascii')")


def _conn() -> sqlite3.Connection:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.execute(_CREATE)
    c.commit()
    return c


def _terms(t: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]{3,}", (t or "").lower())


def _chunk_md(text: str, source: str) -> list[tuple[str, str]]:
    """Split markdown on headings, then size-cap each section."""
    name = Path(source).name
    segs: list[tuple[str, str]] = []
    cur_title, cur = name, ""
    for seg in re.split(r"(?m)^(#{1,4}\s.*)$", text):
        if re.match(r"^#{1,4}\s", seg or ""):
            if cur.strip():
                segs.append((cur_title, cur))
            cur_title = seg.strip("# ").strip() or name
            cur = ""
        else:
            cur += seg
    if cur.strip():
        segs.append((cur_title, cur))
    out: list[tuple[str, str]] = []
    for ttl, body in segs:
        body = body.strip()
        for i in range(0, len(body), _MAX_CHUNK):
            out.append((ttl, body[i:i + _MAX_CHUNK]))
    return out


def _chunk_axiom(text: str, source: str) -> list[tuple[str, str]]:
    name = Path(source).name
    text = text.strip()
    win = _MAX_CHUNK * 2  # keep agents mostly whole
    if len(text) <= win:
        return [(name, text)]
    return [(name, text[i:i + win]) for i in range(0, len(text), win)]


def build_index(force: bool = False) -> int:
    """Build (or reuse) the FTS5 index over the corpus. Returns chunk count."""
    try:
        if DB_PATH.exists() and not force:
            c = _conn()
            n = c.execute("SELECT COUNT(*) FROM kb").fetchone()[0]
            c.close()
            if n > 0:
                return int(n)
        try:
            DB_PATH.unlink()
        except OSError:
            pass
        c = _conn()
        total = 0
        seen: set = set()
        for g in CORPUS_GLOBS:
            for p in BASE.glob(g):
                if not p.is_file() or p in seen:
                    continue
                seen.add(p)
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if not txt.strip():
                    continue
                src = str(p.relative_to(BASE))
                chunks = _chunk_axiom(txt, src) if p.suffix == ".axiom" else _chunk_md(txt, src)
                for ttl, body in chunks:
                    if body.strip():
                        c.execute("INSERT INTO kb(title, body, source) VALUES (?,?,?)", (ttl, body, src))
                        total += 1
        c.commit()
        c.close()
        return total
    except Exception:
        return 0


def retrieve(query: str, k: int = 5, per_source: int = 2) -> list[dict]:
    """Top-k corpus chunks for the query (BM25), capped per source."""
    try:
        if not DB_PATH.exists():
            build_index()
        toks = _terms(query)
        if not toks:
            return []
        match = " OR ".join(toks)
        c = _conn()
        rows = c.execute(
            "SELECT title, body, source, -bm25(kb) AS rel FROM kb WHERE kb MATCH ? "
            "ORDER BY rel DESC LIMIT ?",
            (match, k * 4),
        ).fetchall()
        c.close()
    except Exception:
        return []
    out: list[dict] = []
    counts: dict = {}
    for ttl, body, src, _rel in rows:
        if counts.get(src, 0) >= per_source:
            continue
        counts[src] = counts.get(src, 0) + 1
        out.append({"title": ttl, "body": body, "source": src})
        if len(out) >= k:
            break
    return out


def context_for(task: str, k: int = 5, max_chars: int = 4000) -> str:
    """A formatted reference block to prepend to the Worker's input."""
    hits = retrieve(task, k=k)
    if not hits:
        return ""
    lines = ["AXIOM REFERENCE (retrieved from the docs/specs/examples — "
             "use this to ground your answer; do not invent syntax):"]
    used = 0
    for h in hits:
        block = f"--- [{h['source']} — {h['title']}] ---\n{h['body'].strip()}"
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    return "\n\n".join(lines)


def stats() -> int:
    try:
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM kb").fetchone()[0]
        c.close()
        return int(n)
    except Exception:
        return 0
