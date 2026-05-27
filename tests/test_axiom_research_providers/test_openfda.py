"""OpenFDAProvider tests — mocked urllib."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch


_LABEL_RESPONSE = {
    "results": [
        {
            "id":      "spl-1234",
            "openfda": {
                "brand_name":     ["Ozempic"],
                "generic_name":   ["semaglutide"],
                "substance_name": ["SEMAGLUTIDE"],
                "spl_id":         ["abc-123"],
            },
            "indications_and_usage": ["For chronic weight management."],
            "warnings": ["Risk of pancreatitis."],
        },
    ],
}

_EVENT_RESPONSE = {
    "results": [
        {
            "safetyreportid": "RPT-555",
            "patient": {
                "reaction": [
                    {"reactionmeddrapt": "Nausea"},
                    {"reactionmeddrapt": "Vomiting"},
                ],
            },
        },
    ],
}


class _FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self.headers = {}
        self._body = body if isinstance(body, bytes) \
                     else json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openfda_label_lookup(monkeypatch):
    monkeypatch.delenv("OPENFDA_API_KEY", raising=False)
    from axiom_research_providers.openfda import OpenFDAProvider
    p = OpenFDAProvider()

    def _urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        assert "drug/label.json" in url
        return _FakeResponse(_LABEL_RESPONSE)

    with patch("urllib.request.urlopen", _urlopen):
        hits = p.retrieve("semaglutide", k=3)
    assert len(hits) == 1
    h = hits[0]
    assert h.provider == "openfda"
    assert h.evidence_tier == 1
    assert "Ozempic" in h.title
    assert "weight management" in h.snippet.lower()
    assert "pancreatitis" in h.snippet.lower()
    assert "drug label" in h.kind


def test_openfda_falls_back_to_events_when_labels_empty(monkeypatch):
    monkeypatch.delenv("OPENFDA_API_KEY", raising=False)
    from axiom_research_providers.openfda import OpenFDAProvider
    p = OpenFDAProvider()

    def _urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "drug/label.json" in url:
            return _FakeResponse({"results": []})
        if "drug/event.json" in url:
            return _FakeResponse(_EVENT_RESPONSE)
        raise AssertionError(f"unexpected URL: {url}")

    with patch("urllib.request.urlopen", _urlopen):
        hits = p.retrieve("aspirin", k=3)
    assert len(hits) == 1
    assert "adverse event" in hits[0].kind
    assert "Nausea" in hits[0].snippet
    assert "RPT-555" in hits[0].title


def test_openfda_404_from_label_falls_back_quietly(monkeypatch):
    monkeypatch.delenv("OPENFDA_API_KEY", raising=False)
    from axiom_research_providers.openfda import OpenFDAProvider
    p = OpenFDAProvider()

    def _urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "drug/label.json" in url:
            raise urllib.error.HTTPError(url, 404, "no match", {}, None)
        return _FakeResponse(_EVENT_RESPONSE)

    with patch("urllib.request.urlopen", _urlopen):
        hits = p.retrieve("unknown-drug", k=3)
    assert len(hits) == 1
    assert hits[0].provider == "openfda"


def test_openfda_empty_query():
    from axiom_research_providers.openfda import OpenFDAProvider
    p = OpenFDAProvider()
    assert p.retrieve("", k=3) == []


def test_openfda_total_failure_returns_empty():
    from axiom_research_providers.openfda import OpenFDAProvider
    p = OpenFDAProvider()

    def _raises(req, timeout=None):
        raise urllib.error.URLError("no network")

    with patch("urllib.request.urlopen", _raises):
        assert p.retrieve("anything", k=3) == []
