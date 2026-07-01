"""Patents provider — BM25 over the AXIOM patent corpus.

Wraps a LocalRetriever pointed at the patents corpus dir (extracted patent text
as .md/.txt — the base retriever skips PDFs). Corpus dir from
`AXIOM_PATENTS_DIR` (default: `<repo>/patents`). Re-tags every hit with
provider="patents" so patent sources are distinguishable from the AXIOM
knowledge base in the console.

Domains ("*",) — patents are relevant across research queries. Returns [] when
the corpus dir is absent, so this is a no-op until patents are extracted.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from axiom_research_retriever import LocalRetriever, RetrievedSource

LOG = logging.getLogger("axiom.research.patents")


class PatentsProvider:
    name:    str = "patents"
    domains: Tuple[str, ...] = ("*",)

    def __init__(self, roots: List[Path]) -> None:
        self._roots = [Path(r) for r in roots]
        self._inner: Optional[LocalRetriever] = None
        try:
            self._inner = LocalRetriever(roots=self._roots)
        except Exception as e:  # pragma: no cover - defensive
            LOG.warning("patents corpus init failed (%s); provider disabled", e)
            self._inner = None

    def stats(self) -> dict:
        return {
            "name":    self.name,
            "domains": list(self.domains),
            "roots":   [str(r) for r in self._roots],
            "inner":   self._inner.stats() if self._inner else {"disabled": True},
        }

    def retrieve(self, query: str, *, k: int = 5) -> List[RetrievedSource]:
        if self._inner is None or not (query or "").strip():
            return []
        try:
            hits = self._inner.retrieve(query, k=k)
        except Exception as e:
            LOG.warning("patents retrieve failed for %r: %s", query[:80], e)
            return []
        # Re-tag with provider="patents" (LocalRetriever stamps "local").
        return [
            RetrievedSource(
                title=h.title, uri=h.uri, kind="patent · " + h.kind,
                score=h.score, snippet=h.snippet,
                provider=self.name, evidence_tier=h.evidence_tier,
            )
            for h in hits
        ]
