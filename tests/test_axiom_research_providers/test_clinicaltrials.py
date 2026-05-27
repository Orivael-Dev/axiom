"""ClinicalTrialsProvider tests — mocked urllib."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch


_CT_RESPONSE = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId":      "NCT05000001",
                    "briefTitle": "Semaglutide in adolescent obesity",
                },
                "descriptionModule": {
                    "briefSummary": "A multi-centre trial of weekly semaglutide.",
                },
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule":  {"phases": ["PHASE3"]},
            },
        },
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId":      "NCT05000002",
                    "briefTitle": "Lifestyle vs semaglutide comparison",
                },
                "descriptionModule": {"briefSummary": "Comparator arm."},
                "statusModule": {"overallStatus": "COMPLETED"},
                "designModule":  {"phases": ["PHASE2"]},
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


def test_clinicaltrials_parses_studies():
    from axiom_research_providers.clinicaltrials import ClinicalTrialsProvider
    p = ClinicalTrialsProvider()
    with patch("urllib.request.urlopen",
               lambda req, timeout=None: _FakeResponse(_CT_RESPONSE)):
        hits = p.retrieve("semaglutide adolescent", k=5)
    assert len(hits) == 2
    assert hits[0].provider == "clinicaltrials"
    assert hits[0].evidence_tier == 1
    assert hits[0].uri == "https://clinicaltrials.gov/study/NCT05000001"
    assert "Semaglutide" in hits[0].title
    assert "recruiting" in hits[0].kind
    assert "phase3" in hits[0].kind
    assert "weekly semaglutide" in hits[0].snippet
    assert hits[0].score > hits[1].score


def test_clinicaltrials_empty_query():
    from axiom_research_providers.clinicaltrials import ClinicalTrialsProvider
    p = ClinicalTrialsProvider()
    assert p.retrieve("", k=3) == []


def test_clinicaltrials_network_failure_returns_empty():
    from axiom_research_providers.clinicaltrials import ClinicalTrialsProvider
    p = ClinicalTrialsProvider()

    def _raises(req, timeout=None):
        raise urllib.error.URLError("nope")

    with patch("urllib.request.urlopen", _raises):
        assert p.retrieve("anything", k=3) == []


def test_clinicaltrials_missing_nct_skipped():
    from axiom_research_providers.clinicaltrials import ClinicalTrialsProvider
    p = ClinicalTrialsProvider()
    bad_payload = {"studies": [{"protocolSection": {}}]}    # no nctId
    with patch("urllib.request.urlopen",
               lambda req, timeout=None: _FakeResponse(bad_payload)):
        assert p.retrieve("anything", k=3) == []


def test_clinicaltrials_caches():
    from axiom_research_providers.clinicaltrials import ClinicalTrialsProvider
    p = ClinicalTrialsProvider()
    count = [0]

    def _counting(req, timeout=None):
        count[0] += 1
        return _FakeResponse(_CT_RESPONSE)

    with patch("urllib.request.urlopen", _counting):
        p.retrieve("aspirin", k=3)
        first = count[0]
        p.retrieve("aspirin", k=3)
    assert count[0] == first, "should hit cache on second call"
