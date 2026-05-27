"""MultiProviderRetriever — fan retrieval out across N SourceProviders.

Implements the same `retrieve(query, k, domain)` shape as
`LocalRetriever` / `DomainRoutedRetriever` so it's a drop-in for
`axiom_research_server._state.retriever`.

Per-query flow:

  1. Filter providers by `domain` (provider.domains contains domain
     OR "*").
  2. Submit each filtered provider to a ThreadPoolExecutor with a
     hard `timeout_s` deadline.
  3. Catch + swallow per-provider exceptions / timeouts — one bad
     provider doesn't poison the others.
  4. Max-normalise each provider's scores into [0, 1] so external
     position-rank scores share a scale with local BM25.
  5. Interleave round-robin across providers (so the UI isn't
     dominated by a single high-volume source), cap at `2 * k`.
  6. Dedupe by URI in case the same canonical URL surfaces twice.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Iterable, List, Optional

from axiom_research_retriever import RetrievedSource
from axiom_research_providers import SourceProvider
from axiom_research_providers.local import LocalCorpusProvider


LOG = logging.getLogger("axiom.research.multi")


class MultiProviderRetriever:
    """Parallel fan-out + merge across multiple SourceProviders."""

    def __init__(self, providers: Iterable[SourceProvider],
                 *, timeout_s: float = 4.0) -> None:
        self._providers: List[SourceProvider] = list(providers)
        if not self._providers:
            raise ValueError("MultiProviderRetriever requires ≥1 provider")
        self._timeout_s = timeout_s
        # Reusable pool — small, capped at provider count.
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, len(self._providers)),
            thread_name_prefix="axiom-providers",
        )
        self._lock = threading.Lock()

    @property
    def primary_root(self) -> Path:
        # Defer to the first local provider for stats / debug; if there
        # isn't one, just return CWD. Routing logic doesn't use this.
        for p in self._providers:
            if isinstance(p, LocalCorpusProvider):
                return p.primary_root
        return Path.cwd()

    def stats(self) -> dict:
        # Sum `indexed_files` across any LocalCorpusProvider so the
        # research server's _meta block reports a useful number even
        # when local lives inside a MultiProvider wrapper.
        indexed = 0
        for p in self._providers:
            try:
                inner = p.stats().get("inner") or {}
                # inner may itself be {"indexed_files": N, ...} (plain
                # LocalRetriever) or {"default": {...}, "per_domain": {...}}
                # (DomainRoutedRetriever). Walk both shapes.
                if "indexed_files" in inner:
                    indexed += int(inner["indexed_files"])
                else:
                    default_stats = inner.get("default") or {}
                    indexed += int(default_stats.get("indexed_files", 0))
                    for d in (inner.get("per_domain") or {}).values():
                        indexed += int(d.get("indexed_files", 0))
            except (AttributeError, TypeError, ValueError):
                continue
        return {
            "kind":           "multi-provider",
            "timeout_s":      self._timeout_s,
            "indexed_files":  indexed,
            "providers":      [p.stats() for p in self._providers],
        }

    def retrieve(self, query: str, *, k: int = 5,
                 domain: Optional[str] = None) -> List[RetrievedSource]:
        if not query or not query.strip():
            return []
        active = [p for p in self._providers if _matches(p, domain)]
        if not active:
            return []

        per_provider: dict[str, List[RetrievedSource]] = {}
        # Submit all, then collect with a shared deadline. Each
        # future's exception is caught individually so one slow /
        # broken provider doesn't drag down the rest.
        futures = {}
        for p in active:
            futures[self._executor.submit(_safe_retrieve, p, query, k, domain)] = p

        for fut, prov in futures.items():
            try:
                hits = fut.result(timeout=self._timeout_s)
            except FuturesTimeout:
                LOG.warning("provider %s timed out after %.1fs",
                            prov.name, self._timeout_s)
                hits = []
            except Exception as e:
                LOG.warning("provider %s raised: %s", prov.name, e)
                hits = []
            per_provider[prov.name] = _max_normalise(hits)

        return _round_robin_merge(per_provider, cap=max(k, 2 * k))


def _matches(provider: SourceProvider, domain: Optional[str]) -> bool:
    if "*" in provider.domains:
        return True
    if not domain:
        # No domain filter requested → only "*" providers run.
        # (The local corpus is "*", so this still returns local hits.)
        return False
    return domain.lower() in {d.lower() for d in provider.domains}


def _safe_retrieve(provider: SourceProvider, query: str, k: int,
                   domain: Optional[str]) -> List[RetrievedSource]:
    # LocalCorpusProvider accepts `domain`; external providers don't.
    # Inspect the provider's signature opportunistically by trying
    # the kwarg-rich call first and falling back.
    try:
        return provider.retrieve(query, k=k, domain=domain)  # type: ignore[call-arg]
    except TypeError:
        return provider.retrieve(query, k=k)


def _max_normalise(hits: List[RetrievedSource]) -> List[RetrievedSource]:
    if not hits:
        return []
    top = max((h.score for h in hits), default=0.0)
    if top <= 0:
        return hits
    return [
        RetrievedSource(
            title=h.title, uri=h.uri, kind=h.kind,
            score=round(h.score / top, 4),
            snippet=h.snippet,
            provider=h.provider,
            evidence_tier=h.evidence_tier,
        )
        for h in hits
    ]


def _round_robin_merge(per_provider: dict[str, List[RetrievedSource]],
                       *, cap: int) -> List[RetrievedSource]:
    """Interleave one hit from each provider per round, dedupe by URI."""
    queues = [list(v) for v in per_provider.values() if v]
    seen: set[str] = set()
    out: List[RetrievedSource] = []
    while queues and len(out) < cap:
        next_round: list[list[RetrievedSource]] = []
        for q in queues:
            if not q:
                continue
            hit = q.pop(0)
            if hit.uri in seen:
                # Skip the duplicate but keep this provider's queue
                # alive so its next hit still gets a slot.
                if q:
                    next_round.append(q)
                continue
            seen.add(hit.uri)
            out.append(hit)
            if len(out) >= cap:
                break
            if q:
                next_round.append(q)
        queues = next_round
    return out
