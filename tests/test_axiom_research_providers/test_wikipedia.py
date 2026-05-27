"""WikipediaProvider tests — urllib mocked, no network hit."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest


_SEARCH_JSON = json.dumps({
    "batchcomplete": "",
    "query": {
        "search": [
            {
                "ns":      0,
                "pageid":  12345,
                "title":   "GPT-4",
                "snippet": (
                    "Generative <span class=\"searchmatch\">Pre-trained</span> "
                    "Transformer 4 is a <span class=\"searchmatch\">large language "
                    "model</span> developed by OpenAI &amp; released in 2023."
                ),
            },
            {
                "ns":      0,
                "pageid":  67890,
                "title":   "Large language model",
                "snippet": (
                    "A <span class=\"searchmatch\">large language model</span> "
                    "is a language model with many parameters."
                ),
            },
        ],
    },
}).encode("utf-8")


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


def _urlopen_ok(req, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    assert "wikipedia.org/w/api.php" in url
    assert "list=search" in url
    return _FakeResponse(_SEARCH_JSON)


def test_wikipedia_returns_hits_with_provider_tag():
    from axiom_research_providers.wikipedia import WikipediaProvider
    p = WikipediaProvider()
    with patch("urllib.request.urlopen", _urlopen_ok):
        hits = p.retrieve("GPT-4 large language model", k=5)
    assert len(hits) == 2
    h = hits[0]
    assert h.provider == "wikipedia"
    assert h.title == "GPT-4"
    assert h.kind == "wikipedia · article"
    # Stable curid form, not title-redirect URI.
    assert h.uri == "https://en.wikipedia.org/?curid=12345"
    # Position-rank score (top hit > second).
    assert hits[0].score > hits[1].score


def test_wikipedia_strips_html_tags_and_entities_from_snippet():
    """MediaWiki search returns snippets with <span class="searchmatch">…</span>
    highlight markup and HTML-encoded entities. The provider must strip
    both so the research console renders clean text."""
    from axiom_research_providers.wikipedia import WikipediaProvider
    p = WikipediaProvider()
    with patch("urllib.request.urlopen", _urlopen_ok):
        hits = p.retrieve("GPT-4", k=2)
    snip = hits[0].snippet
    # Tags gone.
    assert "<span" not in snip
    assert "</span>" not in snip
    # Entities resolved.
    assert "&amp;" not in snip
    assert "&" in snip   # the literal '&' came back from decoding
    # Content survives.
    assert "Pre-trained" in snip
    assert "large language model" in snip


def test_wikipedia_caches_repeated_query():
    from axiom_research_providers.wikipedia import WikipediaProvider
    p = WikipediaProvider()
    call_count = {"n": 0}
    def _count_urlopen(req, timeout=None):
        call_count["n"] += 1
        return _FakeResponse(_SEARCH_JSON)
    with patch("urllib.request.urlopen", _count_urlopen):
        a = p.retrieve("GPT-4", k=3)
        b = p.retrieve("GPT-4", k=3)
    assert a == b
    assert call_count["n"] == 1
    assert p.stats()["cache_size"] == 1


def test_wikipedia_empty_query_returns_empty_no_http():
    from axiom_research_providers.wikipedia import WikipediaProvider
    p = WikipediaProvider()
    def _explode(*_a, **_kw):
        raise AssertionError("urlopen called for empty query")
    with patch("urllib.request.urlopen", _explode):
        assert p.retrieve("", k=5) == []
        assert p.retrieve("   ", k=5) == []


def test_wikipedia_http_error_returns_empty_not_raise():
    """Single bad provider must not poison MultiProviderRetriever —
    swallow + log, return []."""
    from axiom_research_providers.wikipedia import WikipediaProvider
    p = WikipediaProvider()
    def _urlopen_500(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 503, "Service Unavailable", {}, None,
        )
    with patch("urllib.request.urlopen", _urlopen_500):
        hits = p.retrieve("anything", k=5)
    assert hits == []


def test_wikipedia_malformed_json_returns_empty_not_raise():
    from axiom_research_providers.wikipedia import WikipediaProvider
    p = WikipediaProvider()
    def _urlopen_garbage(req, timeout=None):
        return _FakeResponse(b"this is not json {{{")
    with patch("urllib.request.urlopen", _urlopen_garbage):
        assert p.retrieve("anything", k=5) == []


def test_wikipedia_domains_cover_general_and_medical():
    """Wikipedia is broadly useful for both general info and as a
    layperson backstop for medical queries when the specialised
    providers (PubMed/CT/openFDA) don't cover a topic."""
    from axiom_research_providers.wikipedia import WikipediaProvider
    p = WikipediaProvider()
    assert "general" in p.domains
    assert "medical" in p.domains
    # NOT a "*" provider — finance / security / hr / supply_chain
    # don't get Wikipedia hits (they have their own corpora).
    assert "*" not in p.domains


def test_wikipedia_stats_shape():
    from axiom_research_providers.wikipedia import WikipediaProvider
    p = WikipediaProvider(lang="en")
    s = p.stats()
    assert s["name"] == "wikipedia"
    assert s["lang"] == "en"
    assert "general" in s["domains"]
    assert "cache_size" in s


def test_default_retriever_wires_wikipedia(monkeypatch):
    """default_retriever() must include WikipediaProvider in the
    MultiProviderRetriever so general + medical queries pick it up."""
    monkeypatch.setenv("AXIOM_EXTERNAL_RETRIEVAL", "1")
    # Force a fresh build of _DEFAULT.
    import axiom_research_retriever as mod
    monkeypatch.setattr(mod, "_DEFAULT", None)
    from axiom_research_retriever import default_retriever
    r = default_retriever()
    # Drill into stats() to find the wikipedia provider entry.
    stats = r.stats()
    providers = stats.get("providers", [])
    names = {p.get("name") for p in providers}
    assert "wikipedia" in names, f"providers wired: {names}"
