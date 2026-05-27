"""External + local research source providers.

Each provider implements a tiny Protocol (`SourceProvider`) and returns
`axiom_research_retriever.RetrievedSource` instances tagged with its
own `provider` name. `MultiProviderRetriever` fans queries out across
the configured providers in parallel, normalises scores, and merges
the results into the same shape the research server already consumes.

Adding a new provider is one file: implement `name`, `domains`,
`retrieve(query, *, k)`, and `stats()`, then list it in
`axiom_research_retriever.default_retriever()`.

The whole package uses stdlib only — `urllib.request` for HTTP,
`xml.etree.ElementTree` / `json` for parsing. No new install deps.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Tuple
from urllib.parse import urlparse

from axiom_research_retriever import RetrievedSource


class SourceProvider(Protocol):
    """A single retrieval source — local corpus or external API.

    `domains` is a tuple of routing keys; "*" means "always run". The
    research console passes a `domain` to `retrieve()` (e.g. "medical");
    `MultiProviderRetriever` only consults providers whose `domains`
    list contains that key or `"*"`.
    """

    name:    str
    domains: Tuple[str, ...]

    def retrieve(self, query: str, *, k: int = 5) -> List[RetrievedSource]: ...

    def stats(self) -> dict: ...


def tier_for_uri(uri: str) -> Optional[int]:
    """Look up an evidence tier by the host of `uri`.

    Returns None when nothing matches. Domain comparison is suffix-based
    so subdomains (e.g. "www.fda.gov") still hit the registry entry
    for "fda.gov".
    """
    if not uri:
        return None
    # axiom_medical_safety isn't always available in minimal builds;
    # import lazily so a missing optional dep just disables tagging.
    try:
        from axiom_medical_safety import EVIDENCE_TIER_REGISTRY
    except ImportError:
        return None
    try:
        host = (urlparse(uri).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    if host in EVIDENCE_TIER_REGISTRY:
        return EVIDENCE_TIER_REGISTRY[host]
    for domain, tier in EVIDENCE_TIER_REGISTRY.items():
        if host.endswith("." + domain):
            return tier
    return None


__all__ = ["SourceProvider", "tier_for_uri", "RetrievedSource"]
