"""Retriever — pluggable source-grounding for the research engine.

The Protocol defines the contract every retriever must satisfy:
take a query, return ranked (path, snippet, score) tuples. Concrete
implementations:

  LocalFilesRetriever  — grep-based search over a configured directory.
                         Zero external deps, perfect for dev + Sovereign Box.
  (future) WebRetriever  — Brave / SerpAPI / Bing — out-of-band, not shipped here
  (future) VectorRetriever — FAISS / sqlite-vss over an embedded corpus

For Phase 1 we ship LocalFilesRetriever only — it's the simplest
honest source-grounding and gives the demo a real evidence trail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol


@dataclass(frozen=True)
class RetrievedDoc:
    """One retrieved document.

    path     — relative or absolute path / URL
    snippet  — short excerpt that matched (≤ 300 chars typical)
    score    — relevance score in [0, 1]; higher = more relevant
    metadata — free-form dict for retriever-specific extras
    """
    path: str
    snippet: str
    score: float
    metadata: dict = field(default_factory=dict)


class Retriever(Protocol):
    """Any retriever the engine accepts."""

    def retrieve(self, query: str, *, top_k: int = 5) -> list[RetrievedDoc]:
        ...


# ─── LocalFilesRetriever ────────────────────────────────────────────────


class LocalFilesRetriever:
    """Grep-style retriever over a directory tree.

    Each query splits into lowercase keyword tokens; each file gets a
    score = `tokens_matched / tokens_total`. Returns the top-K files
    with non-zero scores plus a short snippet around the first match.

    No external deps — pure stdlib. Good for dev, for the Sovereign Box
    SKU's offline mode, and for grounding the demo on the repo's own
    documentation directories.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        extensions: Iterable[str] = (".md", ".txt", ".axiom"),
        max_file_bytes: int = 200_000,
    ) -> None:
        self.root = Path(root)
        self.extensions = tuple(extensions)
        self.max_file_bytes = max_file_bytes

    def retrieve(self, query: str, *, top_k: int = 5) -> list[RetrievedDoc]:
        tokens = _tokenize(query)
        if not tokens:
            return []
        if not self.root.exists():
            return []

        scored: list[tuple[float, Path, str]] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in self.extensions:
                continue
            try:
                size = path.stat().st_size
                if size > self.max_file_bytes:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            text_lc = text.lower()
            matched = sum(1 for t in tokens if t in text_lc)
            if matched == 0:
                continue
            score = matched / len(tokens)
            snippet = _snippet_around_first(text, tokens)
            scored.append((score, path, snippet))

        # Sort by score desc, then by path for determinism
        scored.sort(key=lambda t: (-t[0], str(t[1])))
        out = []
        for score, path, snippet in scored[:top_k]:
            rel = path.relative_to(self.root) if path.is_absolute() else path
            out.append(RetrievedDoc(
                path=str(rel),
                snippet=snippet,
                score=round(score, 3),
                metadata={"size_bytes": path.stat().st_size},
            ))
        return out


# ─── Internals ──────────────────────────────────────────────────────────


_TOKEN_RE = re.compile(r"[a-zA-Z0-9]{3,}")  # 3+ chars only — skip "a", "is"


def _tokenize(query: str) -> list[str]:
    """Lowercase keyword tokens. Drops words ≤ 2 chars + duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _TOKEN_RE.finditer(query.lower()):
        tok = m.group(0)
        if tok not in seen and tok not in _STOPWORDS:
            seen.add(tok)
            out.append(tok)
    return out


def _snippet_around_first(text: str, tokens: list[str], radius: int = 120) -> str:
    """Return text[max(0, i-radius) : i+radius] around the first matched token."""
    text_lc = text.lower()
    for tok in tokens:
        idx = text_lc.find(tok)
        if idx >= 0:
            start = max(0, idx - radius)
            end = min(len(text), idx + radius)
            return text[start:end].replace("\n", " ").strip()
    # No keyword matched — return the file's first ~240 chars
    return text[:radius * 2].replace("\n", " ").strip()


_STOPWORDS = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "are",
    "was", "were", "have", "has", "had", "but", "not", "all",
    "any", "can", "will", "would", "could", "should", "what",
    "when", "where", "why", "how", "which", "who",
})
