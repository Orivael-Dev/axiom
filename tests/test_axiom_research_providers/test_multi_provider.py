"""MultiProviderRetriever tests — fake providers, no network."""
from __future__ import annotations

import time
from typing import List, Tuple

import pytest

from axiom_research_retriever import RetrievedSource


def _hit(uri: str, title: str, *, provider: str, score: float = 1.0,
         tier: int = 1) -> RetrievedSource:
    return RetrievedSource(
        title=title, uri=uri, kind=f"{provider} · test",
        score=score, snippet=f"snippet for {title}",
        provider=provider, evidence_tier=tier,
    )


class _FakeProvider:
    def __init__(self, name: str, hits: List[RetrievedSource],
                 *, domains: Tuple[str, ...] = ("medical",),
                 delay: float = 0.0, exc: Exception | None = None):
        self.name    = name
        self.domains = domains
        self._hits   = hits
        self._delay  = delay
        self._exc    = exc

    def retrieve(self, query: str, *, k: int = 5,
                 domain: str | None = None) -> List[RetrievedSource]:
        if self._delay:
            time.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._hits[:k]

    def stats(self) -> dict:
        return {"name": self.name, "domains": list(self.domains)}


def test_multi_fans_out_and_interleaves():
    from axiom_research_providers.multi import MultiProviderRetriever

    local_p = _FakeProvider("local",
        [_hit("docs/a.md", "Local A", provider="local", score=2.0),
         _hit("docs/b.md", "Local B", provider="local", score=1.0)],
        domains=("*",))
    pubmed_p = _FakeProvider("pubmed",
        [_hit("https://pubmed.ncbi.nlm.nih.gov/1/", "Pubmed 1", provider="pubmed"),
         _hit("https://pubmed.ncbi.nlm.nih.gov/2/", "Pubmed 2", provider="pubmed", score=0.5)])
    ct_p = _FakeProvider("clinicaltrials",
        [_hit("https://clinicaltrials.gov/study/NCT1", "CT 1", provider="clinicaltrials")])

    r = MultiProviderRetriever([local_p, pubmed_p, ct_p])
    hits = r.retrieve("anything", k=2, domain="medical")
    # Round-robin: first hit from each provider, then second pass.
    providers_in_order = [h.provider for h in hits]
    # All three providers should be represented in the first round.
    assert set(providers_in_order[:3]) == {"local", "pubmed", "clinicaltrials"}


def test_multi_skips_providers_with_wrong_domain():
    from axiom_research_providers.multi import MultiProviderRetriever

    local_p = _FakeProvider("local",
        [_hit("docs/a.md", "Local A", provider="local")],
        domains=("*",))
    medical_only_p = _FakeProvider("pubmed",
        [_hit("https://pubmed.ncbi.nlm.nih.gov/1/", "PM 1", provider="pubmed")],
        domains=("medical",))

    r = MultiProviderRetriever([local_p, medical_only_p])
    hits = r.retrieve("anything", k=5, domain="finance")
    # Only local should run for finance queries.
    assert all(h.provider == "local" for h in hits)


def test_multi_handles_provider_exception():
    from axiom_research_providers.multi import MultiProviderRetriever

    good = _FakeProvider("local",
        [_hit("docs/a.md", "Local A", provider="local")],
        domains=("*",))
    broken = _FakeProvider("pubmed", [], exc=RuntimeError("boom"))

    r = MultiProviderRetriever([good, broken])
    hits = r.retrieve("anything", k=3, domain="medical")
    # Broken provider doesn't poison the result.
    assert len(hits) == 1
    assert hits[0].provider == "local"


def test_multi_enforces_timeout():
    from axiom_research_providers.multi import MultiProviderRetriever

    fast = _FakeProvider("local",
        [_hit("docs/a.md", "Local A", provider="local")],
        domains=("*",))
    slow = _FakeProvider("pubmed",
        [_hit("https://pubmed.ncbi.nlm.nih.gov/1/", "PM 1", provider="pubmed")],
        delay=2.0)

    r = MultiProviderRetriever([fast, slow], timeout_s=0.2)
    t0 = time.time()
    hits = r.retrieve("anything", k=3, domain="medical")
    elapsed = time.time() - t0
    assert elapsed < 1.5, "should not wait for the slow provider"
    assert any(h.provider == "local" for h in hits)


def test_multi_dedupes_by_uri():
    from axiom_research_providers.multi import MultiProviderRetriever

    p1 = _FakeProvider("local",
        [_hit("https://shared.example/x", "Local X", provider="local")],
        domains=("*",))
    p2 = _FakeProvider("pubmed",
        [_hit("https://shared.example/x", "Pubmed X", provider="pubmed"),
         _hit("https://pubmed.ncbi.nlm.nih.gov/2/", "Pubmed 2", provider="pubmed")])

    r = MultiProviderRetriever([p1, p2])
    hits = r.retrieve("anything", k=5, domain="medical")
    uris = [h.uri for h in hits]
    assert len(uris) == len(set(uris)), "duplicate URIs must be dropped"


def test_multi_normalises_scores_per_provider():
    from axiom_research_providers.multi import MultiProviderRetriever

    p = _FakeProvider("pubmed",
        [_hit("https://pubmed.ncbi.nlm.nih.gov/1/", "PM 1", provider="pubmed", score=10.0),
         _hit("https://pubmed.ncbi.nlm.nih.gov/2/", "PM 2", provider="pubmed", score=5.0)])
    r = MultiProviderRetriever([p])
    hits = r.retrieve("anything", k=5, domain="medical")
    assert hits[0].score == 1.0
    assert hits[1].score == 0.5


def test_multi_requires_at_least_one_provider():
    from axiom_research_providers.multi import MultiProviderRetriever
    with pytest.raises(ValueError):
        MultiProviderRetriever([])
