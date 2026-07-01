"""Open-source web search via a SearXNG instance (JSON API).

SearXNG (https://github.com/searxng/searxng) is a self-hosted open-source
metasearch engine — it aggregates Google / Bing / DuckDuckGo / etc., so research
goes beyond the single-source providers (Wikipedia, PubMed, …). This gives the
research console broad general-web recall while staying fully open-source and
self-hosted (no proprietary API key, no data leaving your infra).

Instance URL from `AXIOM_SEARXNG_URL` (default: the `axiom-searxng` container on
axiom-net). The instance must enable the JSON output format
(`search.formats: [html, json]` in settings.yml).

Domains ("*",) — supplements every query. Stdlib only; graceful [] on any
failure so one flaky search endpoint never poisons the fan-out.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

from axiom_research_retriever import RetrievedSource
from axiom_research_providers import tier_for_uri

LOG = logging.getLogger("axiom.research.websearch")

_DEFAULT_URL = os.environ.get("AXIOM_SEARXNG_URL", "http://axiom-searxng:8080")
_TIMEOUT_S = 5.0
_USER_AGENT = "AXIOM-research-console/1.0 (+https://orivael.dev)"
_CACHE_TTL_S = 900


class SearxngProvider:
    """General web search through a SearXNG instance."""

    name:    str = "websearch"
    domains: Tuple[str, ...] = ("*",)

    def __init__(self, *, base_url: str = "", timeout_s: float = _TIMEOUT_S) -> None:
        self._url = (base_url or _DEFAULT_URL).rstrip("/")
        self._timeout_s = timeout_s
        self._cache: dict[Tuple[str, int], Tuple[float, List[RetrievedSource]]] = {}

    def stats(self) -> dict:
        return {
            "name":      self.name,
            "domains":   list(self.domains),
            "instance":  self._url,
            "timeout_s": self._timeout_s,
        }

    def retrieve(self, query: str, *, k: int = 5) -> List[RetrievedSource]:
        q = (query or "").strip()
        if not q or not self._url:
            return []
        cache_key = (q, k)
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_S:
            return cached[1]
        params = {"q": q, "format": "json", "safesearch": "1"}
        url = f"{self._url}/search?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _USER_AGENT,
                              "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, ValueError, TimeoutError, OSError) as e:
            LOG.warning("searxng retrieve failed for %r: %s", q[:80], e)
            return []

        out: List[RetrievedSource] = []
        for rank, hit in enumerate((data.get("results") or [])):
            uri = (hit.get("url") or "").strip()
            if not uri:
                continue
            title = (hit.get("title") or uri).strip()
            content = (hit.get("content") or "").strip()
            engine = (hit.get("engine") or "web").strip()
            out.append(RetrievedSource(
                title=title[:240],
                uri=uri,
                kind=f"web · {engine}",
                score=1.0 / (rank + 1),
                snippet=content[:500] or "(no extract)",
                provider=self.name,
                evidence_tier=tier_for_uri(uri),
            ))
            if len(out) >= k:
                break
        self._cache[cache_key] = (time.time(), out)
        return out
