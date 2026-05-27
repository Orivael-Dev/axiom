"""Wikipedia source provider — MediaWiki search API.

Single-call lookup: `action=query&list=search` returns title + HTML
snippet + pageid for the top-k matches. URIs are constructed via the
stable `?curid=` form (title-redirects don't invalidate the link).

Stdlib only — `urllib.request` + `json`. Mirrors the failure model in
`pubmed.py`: any network/parse error is caught, logged, returns []
so `MultiProviderRetriever` isn't poisoned by a single slow source.

Domains: ("general", "medical"). Open and free, no API key. For
medical it's a layperson-level supplement to PubMed/ClinicalTrials/
openFDA — useful when those specialised providers don't cover a
topic (drug history, condition overviews, regulatory bodies).
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

from axiom_research_retriever import RetrievedSource
from axiom_research_providers import tier_for_uri


LOG = logging.getLogger("axiom.research.wikipedia")

_API_URL    = "https://en.wikipedia.org/w/api.php"
_TIMEOUT_S  = 4.0
_USER_AGENT = "AXIOM-research-console/1.0 (+https://orivael.dev)"
_CACHE_TTL_S = 3600

# Wikipedia returns snippets with <span class="searchmatch">…</span>
# highlight tags. We strip all tags rather than escape them so the
# snippet renders cleanly in the research console.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# &quot; / &amp; / &nbsp; etc. — a minimal sweep is enough for snippets.
_HTML_ENTITY_RE = re.compile(r"&(quot|amp|lt|gt|nbsp|#39);")
_HTML_ENTITY_MAP = {
    "quot": '"', "amp": "&", "lt": "<", "gt": ">",
    "nbsp": " ", "#39": "'",
}


def _strip_html(s: str) -> str:
    if not s:
        return ""
    out = _HTML_TAG_RE.sub("", s)
    out = _HTML_ENTITY_RE.sub(
        lambda m: _HTML_ENTITY_MAP.get(m.group(1), m.group(0)), out,
    )
    return out.strip()


class WikipediaProvider:
    """Live Wikipedia lookup via the public MediaWiki search API."""

    name:    str = "wikipedia"
    domains: Tuple[str, ...] = ("general", "medical")

    def __init__(self, *, timeout_s: float = _TIMEOUT_S,
                 lang: str = "en") -> None:
        self._timeout_s = timeout_s
        self._lang = lang
        self._api_url = (
            f"https://{lang}.wikipedia.org/w/api.php"
            if lang != "en" else _API_URL
        )
        self._cache: dict[Tuple[str, int], Tuple[float, List[RetrievedSource]]] = {}

    def stats(self) -> dict:
        return {
            "name":       self.name,
            "domains":    list(self.domains),
            "lang":       self._lang,
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
        try:
            results = self._search(q, k)
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, ValueError, TimeoutError) as e:
            LOG.warning("Wikipedia retrieve failed for %r: %s", q[:80], e)
            return []
        self._cache[cache_key] = (time.time(), results)
        return results

    def _search(self, query: str, k: int) -> List[RetrievedSource]:
        params = {
            "action":   "query",
            "format":   "json",
            "list":     "search",
            "srsearch": query,
            "srlimit":  str(max(1, min(k, 20))),
            "srprop":   "snippet",
            "utf8":     "1",
        }
        url = f"{self._api_url}?{urllib.parse.urlencode(params)}"
        body = self._http_get(url)
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            raise
        results: List[RetrievedSource] = []
        hits = (data.get("query") or {}).get("search") or []
        for rank, hit in enumerate(hits):
            pageid = hit.get("pageid")
            title  = (hit.get("title") or "").strip()
            if not pageid or not title:
                continue
            snippet = _strip_html(hit.get("snippet") or "")[:500] or \
                      "(no extract)"
            uri = f"https://{self._lang}.wikipedia.org/?curid={pageid}"
            results.append(RetrievedSource(
                title=title[:240],
                uri=uri,
                kind="wikipedia · article",
                # Position-rank score; max-normalised by MultiProviderRetriever.
                score=1.0 / (rank + 1),
                snippet=snippet,
                provider=self.name,
                evidence_tier=tier_for_uri(uri),
            ))
        return results

    def _http_get(self, url: str) -> bytes:
        req = urllib.request.Request(
            url,
            headers={
                "Accept":     "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
            if resp.status != 200:
                raise urllib.error.HTTPError(
                    url, resp.status, "non-200", resp.headers, None,
                )
            return resp.read()
