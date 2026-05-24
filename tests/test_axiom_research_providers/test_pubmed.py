"""PubMedProvider tests — all urllib calls mocked so no network hit."""
from __future__ import annotations

import io
import urllib.error
from unittest.mock import patch

import pytest


_ESEARCH_XML = b"""<?xml version="1.0"?>
<eSearchResult>
  <IdList>
    <Id>40001234</Id>
    <Id>40001235</Id>
  </IdList>
</eSearchResult>
"""

_EFETCH_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>40001234</PMID>
      <Article>
        <ArticleTitle>Once-weekly semaglutide in adolescents with obesity.</ArticleTitle>
        <Abstract>
          <AbstractText>Background: We tested semaglutide in adolescents.</AbstractText>
          <AbstractText>Conclusion: Semaglutide reduced BMI versus placebo.</AbstractText>
        </Abstract>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>40001235</PMID>
      <Article>
        <ArticleTitle>Semaglutide pharmacokinetics in children.</ArticleTitle>
        <Abstract><AbstractText>Pharmacokinetic study.</AbstractText></Abstract>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self.headers = {}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_urlopen(esearch_body=_ESEARCH_XML, efetch_body=_EFETCH_XML,
                  esearch_status=200, efetch_status=200,
                  esearch_exc=None, efetch_exc=None):
    """Return a urlopen replacement that dispatches by URL substring."""
    def _urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "esearch" in url:
            if esearch_exc:
                raise esearch_exc
            return _FakeResponse(esearch_body, esearch_status)
        if "efetch" in url:
            if efetch_exc:
                raise efetch_exc
            return _FakeResponse(efetch_body, efetch_status)
        raise AssertionError(f"unexpected URL: {url}")
    return _urlopen


def test_pubmed_returns_hits_with_tier_and_provider(monkeypatch):
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    from axiom_research_providers.pubmed import PubMedProvider
    p = PubMedProvider()
    with patch("urllib.request.urlopen", _make_urlopen()):
        # Skip the polite sleep between the 2 calls.
        with patch("axiom_research_providers.pubmed.time.sleep", lambda *a: None):
            hits = p.retrieve("semaglutide adolescent obesity", k=5)
    assert len(hits) == 2
    h = hits[0]
    assert h.provider == "pubmed"
    assert h.evidence_tier == 1
    assert h.uri == "https://pubmed.ncbi.nlm.nih.gov/40001234/"
    assert "Once-weekly semaglutide" in h.title
    assert "semaglutide" in h.snippet.lower()
    assert h.kind == "pubmed · article"
    # Score is position-rank → top hit has highest raw score.
    assert hits[0].score > hits[1].score


def test_pubmed_empty_query_short_circuits(monkeypatch):
    from axiom_research_providers.pubmed import PubMedProvider
    p = PubMedProvider()
    assert p.retrieve("", k=5) == []
    assert p.retrieve("   ", k=5) == []


def test_pubmed_network_failure_returns_empty(monkeypatch):
    from axiom_research_providers.pubmed import PubMedProvider
    p = PubMedProvider()
    err = urllib.error.URLError("nope")
    with patch("urllib.request.urlopen",
               _make_urlopen(esearch_exc=err)):
        hits = p.retrieve("anything", k=3)
    assert hits == []


def test_pubmed_caches_results(monkeypatch):
    from axiom_research_providers.pubmed import PubMedProvider
    p = PubMedProvider()
    call_count = [0]

    def _counting_urlopen(req, timeout=None):
        call_count[0] += 1
        return _make_urlopen()(req, timeout=timeout)

    with patch("urllib.request.urlopen", _counting_urlopen):
        with patch("axiom_research_providers.pubmed.time.sleep", lambda *a: None):
            p.retrieve("aspirin", k=3)
            first_count = call_count[0]
            p.retrieve("aspirin", k=3)   # ← cached
    assert call_count[0] == first_count, "second call should hit the cache"


def test_pubmed_empty_idlist_returns_empty(monkeypatch):
    from axiom_research_providers.pubmed import PubMedProvider
    p = PubMedProvider()
    empty_esearch = b"<?xml version='1.0'?><eSearchResult><IdList/></eSearchResult>"
    with patch("urllib.request.urlopen",
               _make_urlopen(esearch_body=empty_esearch)):
        with patch("axiom_research_providers.pubmed.time.sleep", lambda *a: None):
            hits = p.retrieve("no-results", k=3)
    assert hits == []
