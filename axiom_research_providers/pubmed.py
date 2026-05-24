"""PubMed source provider — NCBI E-utilities.

Two-step lookup: `esearch` returns matching PMIDs for a query;
`efetch` fetches title + abstract per PMID in a single batched call.
Both endpoints accept an optional `NCBI_API_KEY` env var (3 req/sec
without, 10 req/sec with).

Stdlib only — `urllib.request` + `xml.etree.ElementTree`. Mirrors the
HTTP-helper pattern in `axiom_packs_cli._fetch_json`.

Failure model: every network / parse error is caught, logged, and
returns an empty list. The provider must never raise out of
`retrieve()` because `MultiProviderRetriever` runs providers in
parallel and one failure shouldn't poison the merged result.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from axiom_research_retriever import RetrievedSource
from axiom_research_providers import tier_for_uri


LOG = logging.getLogger("axiom.research.pubmed")

_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

_TIMEOUT_S      = 4.0
_USER_AGENT     = "AXIOM-research-console/1.0 (+https://orivael.dev)"
_CACHE_TTL_S    = 3600     # 1 hour
_POLITE_SLEEP_S = 0.34     # ~3 req/sec (free-tier rate limit)


class PubMedProvider:
    """Live PubMed lookup via the public E-utilities endpoints."""

    name:    str = "pubmed"
    domains: Tuple[str, ...] = ("medical",)

    def __init__(self, *, api_key: Optional[str] = None,
                 timeout_s: float = _TIMEOUT_S) -> None:
        self._api_key   = api_key or os.environ.get("NCBI_API_KEY")
        self._timeout_s = timeout_s
        # query → (epoch_seconds, list[RetrievedSource])
        self._cache: dict[Tuple[str, int], Tuple[float, List[RetrievedSource]]] = {}

    def stats(self) -> dict:
        return {
            "name":         self.name,
            "domains":      list(self.domains),
            "has_api_key":  bool(self._api_key),
            "timeout_s":    self._timeout_s,
            "cache_size":   len(self._cache),
        }

    def retrieve(self, query: str, *, k: int = 5) -> List[RetrievedSource]:
        q = (query or "").strip()
        if not q:
            return []
        cache_key = (q, k)
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_S:
            return cached[1]
        try:
            pmids = self._esearch(q, k)
            if not pmids:
                self._cache[cache_key] = (time.time(), [])
                return []
            time.sleep(_POLITE_SLEEP_S)
            results = self._efetch(pmids)
        except (urllib.error.URLError, urllib.error.HTTPError,
                ET.ParseError, ValueError, TimeoutError) as e:
            LOG.warning("PubMed retrieve failed for %r: %s", q[:80], e)
            return []
        self._cache[cache_key] = (time.time(), results)
        return results

    # ── HTTP helpers ─────────────────────────────────────────────────

    def _esearch(self, query: str, k: int) -> List[str]:
        params = {
            "db":      "pubmed",
            "term":    query,
            "retmode": "xml",
            "retmax":  str(max(1, min(k, 20))),
            "sort":    "relevance",
        }
        if self._api_key:
            params["api_key"] = self._api_key
        url = f"{_ESEARCH_URL}?{urllib.parse.urlencode(params)}"
        xml_bytes = self._http_get(url)
        root = ET.fromstring(xml_bytes)
        return [el.text for el in root.findall(".//IdList/Id") if el.text]

    def _efetch(self, pmids: List[str]) -> List[RetrievedSource]:
        params = {
            "db":      "pubmed",
            "id":      ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        if self._api_key:
            params["api_key"] = self._api_key
        url = f"{_EFETCH_URL}?{urllib.parse.urlencode(params)}"
        xml_bytes = self._http_get(url)
        root = ET.fromstring(xml_bytes)
        out: List[RetrievedSource] = []
        for rank, art in enumerate(root.findall(".//PubmedArticle")):
            pmid_el = art.find(".//PMID")
            if pmid_el is None or not pmid_el.text:
                continue
            pmid    = pmid_el.text
            title   = _text(art.find(".//ArticleTitle")) or f"PubMed {pmid}"
            abstract_parts = [
                _text(t) for t in art.findall(".//Abstract/AbstractText")
            ]
            snippet = " ".join(p for p in abstract_parts if p)[:500] or \
                      "(no abstract)"
            uri = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            out.append(RetrievedSource(
                title=title.strip()[:240],
                uri=uri,
                kind="pubmed · article",
                # Position-rank score; max-normalised by MultiProviderRetriever.
                score=1.0 / (rank + 1),
                snippet=snippet,
                provider=self.name,
                evidence_tier=tier_for_uri(uri),
            ))
        return out

    def _http_get(self, url: str) -> bytes:
        req = urllib.request.Request(
            url,
            headers={
                "Accept":     "application/xml",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
            if resp.status != 200:
                raise urllib.error.HTTPError(
                    url, resp.status, "non-200", resp.headers, None,
                )
            return resp.read()


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    # `itertext` flattens nested formatting tags (<i>, <sub>, etc.).
    return "".join(el.itertext()).strip()
