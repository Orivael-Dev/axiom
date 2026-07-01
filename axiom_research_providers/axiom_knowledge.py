"""AXIOM Knowledge provider — the constitutional knowledge base.

Wraps `axiom_constitutional.knowledge_rag`, whose corpus is the AXIOM spec +
agent docs + working `.axiom` examples (`axiom_files/**/*.axiom` and the docs).
This is the "how AXIOM actually works" source, and it replaces the generic
repo-docs local corpus so research grounds in real specs/examples rather than the
firewall's API reference.

`docs/firewall/**` hits are filtered out — that's product/API reference, not
knowledge about how AXIOM works, and surfacing it was the exact problem this
provider fixes.

Domains ("*",) — always consulted. Stdlib + the in-repo FTS5 index only; any
failure returns [] so MultiProviderRetriever isn't poisoned.
"""
from __future__ import annotations

import logging
from typing import List, Tuple

from axiom_research_retriever import RetrievedSource

LOG = logging.getLogger("axiom.research.axiom_knowledge")


class AxiomKnowledgeProvider:
    """The AXIOM knowledge base (spec / agent docs / .axiom examples) as a
    SourceProvider, backed by axiom_constitutional.knowledge_rag."""

    name:    str = "axiom-knowledge"
    domains: Tuple[str, ...] = ("*",)

    def __init__(self, *, per_source: int = 2) -> None:
        self._per_source = per_source

    def stats(self) -> dict:
        kb: dict = {}
        try:
            from axiom_constitutional import knowledge_rag
            kb = knowledge_rag.stats()
        except Exception as e:  # pragma: no cover - defensive
            kb = {"error": f"{type(e).__name__}: {e}"}
        return {"name": self.name, "domains": list(self.domains), "kb": kb}

    def retrieve(self, query: str, *, k: int = 5) -> List[RetrievedSource]:
        q = (query or "").strip()
        if not q:
            return []
        try:
            from axiom_constitutional import knowledge_rag
            # Pull extra so the firewall-doc filter still leaves ~k results.
            hits = knowledge_rag.retrieve(q, k=k * 2, per_source=self._per_source)
        except Exception as e:
            LOG.warning("axiom-knowledge retrieve failed for %r: %s", q[:80], e)
            return []

        out: List[RetrievedSource] = []
        for rank, h in enumerate(hits):
            src = (h.get("source") or "").replace("\\", "/")
            # Skip firewall product/API docs — not "how AXIOM works" knowledge.
            if src.startswith("docs/firewall"):
                continue
            title = (h.get("title") or src or "AXIOM doc").strip()
            body = (h.get("body") or "").strip()
            out.append(RetrievedSource(
                title=title[:240],
                uri=(f"axiom://{src}" if src else "axiom://knowledge"),
                kind="axiom · knowledge base",
                # Position-rank score; max-normalised by MultiProviderRetriever.
                score=1.0 / (rank + 1),
                snippet=body[:500] or "(no extract)",
                provider=self.name,
                evidence_tier=None,
            ))
            if len(out) >= k:
                break
        return out
