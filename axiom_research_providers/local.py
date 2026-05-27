"""LocalCorpusProvider — adapts the existing BM25 LocalRetriever (or
DomainRoutedRetriever) to the SourceProvider Protocol so it can
participate in MultiProviderRetriever's fan-out alongside external
APIs.

No new retrieval logic — pure delegation. The wrapped retriever stays
in charge of its own indexing, scoring, and per-domain dispatch.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from axiom_research_retriever import (
    DomainRoutedRetriever, LocalRetriever, RetrievedSource,
)


class LocalCorpusProvider:
    """Wrap a LocalRetriever / DomainRoutedRetriever as a SourceProvider."""

    name:    str = "local"
    # Local corpus runs for every query — "*" means "all domains".
    domains: Tuple[str, ...] = ("*",)

    def __init__(self, inner: "LocalRetriever | DomainRoutedRetriever") -> None:
        self._inner = inner

    @property
    def primary_root(self) -> Path:
        return self._inner.primary_root

    def stats(self) -> dict:
        return {
            "name":    self.name,
            "domains": list(self.domains),
            "inner":   self._inner.stats(),
        }

    def retrieve(self, query: str, *, k: int = 5,
                 domain: Optional[str] = None) -> List[RetrievedSource]:
        # DomainRoutedRetriever uses `domain`; plain LocalRetriever
        # accepts and ignores it. We pass it through unconditionally.
        hits = self._inner.retrieve(query, k=k, domain=domain)
        # `provider` defaults to "local" on RetrievedSource already, so
        # tagging is automatic for any new hits. Older callers that
        # built RetrievedSource without specifying provider also get
        # "local" via the default.
        return hits
