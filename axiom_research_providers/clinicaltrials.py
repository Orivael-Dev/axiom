"""ClinicalTrials.gov source provider — v2 REST API.

Single JSON GET to /api/v2/studies. No auth, no API key. Returns up
to `pageSize` study records with title / brief-summary / status /
phase. Each record becomes a `RetrievedSource` pointing at the
canonical /study/<NCT> URL.

Stdlib only — `urllib.request` + `json`. Failure model mirrors
`PubMedProvider`: every error returns `[]` with a warning log.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, List, Tuple

from axiom_research_retriever import RetrievedSource
from axiom_research_providers import tier_for_uri


LOG = logging.getLogger("axiom.research.clinicaltrials")

_STUDIES_URL  = "https://clinicaltrials.gov/api/v2/studies"
_TIMEOUT_S    = 4.0
_USER_AGENT   = "AXIOM-research-console/1.0 (+https://orivael.dev)"
_CACHE_TTL_S  = 3600


class ClinicalTrialsProvider:
    """ClinicalTrials.gov v2 study search."""

    name:    str = "clinicaltrials"
    domains: Tuple[str, ...] = ("medical",)

    def __init__(self, *, timeout_s: float = _TIMEOUT_S) -> None:
        self._timeout_s = timeout_s
        self._cache: dict[Tuple[str, int], Tuple[float, List[RetrievedSource]]] = {}

    def stats(self) -> dict:
        return {
            "name":       self.name,
            "domains":    list(self.domains),
            "timeout_s":  self._timeout_s,
            "cache_size": len(self._cache),
        }

    def retrieve(self, query: str, *, k: int = 5) -> List[RetrievedSource]:
        q = (query or "").strip()
        if not q:
            return []
        cache_key = (q, k)
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_S:
            return cached[1]

        params = {
            "query.term":  q,
            "pageSize":    str(max(1, min(k, 20))),
            "format":      "json",
            "countTotal":  "false",
        }
        url = f"{_STUDIES_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                if resp.status != 200:
                    raise urllib.error.HTTPError(
                        url, resp.status, "non-200", resp.headers, None,
                    )
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, TimeoutError) as e:
            LOG.warning("ClinicalTrials retrieve failed for %r: %s", q[:80], e)
            return []

        studies = payload.get("studies", []) or []
        out: List[RetrievedSource] = []
        for rank, study in enumerate(studies):
            hit = _study_to_source(study, rank)
            if hit is not None:
                out.append(hit)
        self._cache[cache_key] = (time.time(), out)
        return out


def _study_to_source(study: dict, rank: int) -> RetrievedSource | None:
    protocol = study.get("protocolSection") or {}
    ident    = protocol.get("identificationModule") or {}
    desc     = protocol.get("descriptionModule") or {}
    status   = protocol.get("statusModule") or {}
    design   = protocol.get("designModule") or {}

    nct = ident.get("nctId")
    if not nct:
        return None
    title = (ident.get("briefTitle") or ident.get("officialTitle") or
             f"ClinicalTrials.gov {nct}").strip()
    summary = (desc.get("briefSummary") or
               desc.get("detailedDescription") or "").strip()
    snippet = summary[:500] or "(no summary available)"
    phase   = ",".join(design.get("phases") or []) or "n/a"
    overall = status.get("overallStatus") or "unknown"
    uri = f"https://clinicaltrials.gov/study/{nct}"

    return RetrievedSource(
        title=title[:240],
        uri=uri,
        kind=f"clinicaltrials · {overall.lower()} · phase {phase.lower()}",
        score=1.0 / (rank + 1),
        snippet=snippet,
        provider="clinicaltrials",
        evidence_tier=tier_for_uri(uri),
    )
