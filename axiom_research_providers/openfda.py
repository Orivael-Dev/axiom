"""openFDA source provider — FDA's public JSON API.

Queries the `/drug/label.json` endpoint by default because labels
(indications, warnings, contraindications, adverse reactions) are
the most-cited primary regulator content for medical research. Falls
back to `/drug/event.json` (adverse-event reports) if labels return
nothing for a given query.

Optional `OPENFDA_API_KEY` env var raises the daily quota from 1K to
120K requests; rate-limit-per-minute is the same either way (240/min).

Stdlib only. Same failure model as the other providers — never
raises out of `retrieve()`.
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


LOG = logging.getLogger("axiom.research.openfda")

_LABEL_URL    = "https://api.fda.gov/drug/label.json"
_EVENT_URL    = "https://api.fda.gov/drug/event.json"
_TIMEOUT_S    = 4.0
_USER_AGENT   = "AXIOM-research-console/1.0 (+https://orivael.dev)"
_CACHE_TTL_S  = 3600


class OpenFDAProvider:
    """openFDA drug-label + adverse-event lookup."""

    name:    str = "openfda"
    domains: Tuple[str, ...] = ("medical",)

    def __init__(self, *, api_key: Optional[str] = None,
                 timeout_s: float = _TIMEOUT_S) -> None:
        self._api_key   = api_key or os.environ.get("OPENFDA_API_KEY")
        self._timeout_s = timeout_s
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

        results = self._search_labels(q, k)
        if not results:
            # Fall back to adverse-event reports — broader corpus,
            # different signal (post-market safety).
            results = self._search_events(q, k)
        self._cache[cache_key] = (time.time(), results)
        return results

    # ── /drug/label.json ─────────────────────────────────────────────

    def _search_labels(self, q: str, k: int) -> List[RetrievedSource]:
        # Search across the most useful free-text label fields.
        search = (
            f'(openfda.brand_name:"{q}"+'
            f'openfda.generic_name:"{q}"+'
            f'openfda.substance_name:"{q}"+'
            f'indications_and_usage:"{q}")'
        )
        payload = self._http_get(_LABEL_URL, search=search, k=k)
        if not payload:
            return []
        out: List[RetrievedSource] = []
        for rank, hit in enumerate(payload.get("results", []) or []):
            openfda = hit.get("openfda") or {}
            brand   = _first(openfda.get("brand_name"))
            generic = _first(openfda.get("generic_name"))
            title   = brand or generic or "Drug Label"
            indication = _first(hit.get("indications_and_usage")) or \
                         _first(hit.get("purpose")) or "(no indication text)"
            warning = _first(hit.get("warnings")) or \
                      _first(hit.get("warnings_and_cautions")) or ""
            snippet = (indication + (" — Warning: " + warning if warning else ""))[:500]
            spl_id  = _first(openfda.get("spl_id")) or hit.get("id") or ""
            uri = (f"https://api.fda.gov/drug/label.json?"
                   f"search=id:{urllib.parse.quote(spl_id)}"
                   if spl_id else _LABEL_URL)
            out.append(RetrievedSource(
                title=f"FDA label · {title}".strip()[:240],
                uri=uri,
                kind="openfda · drug label",
                score=1.0 / (rank + 1),
                snippet=snippet,
                provider=self.name,
                evidence_tier=tier_for_uri(uri),
            ))
        return out

    # ── /drug/event.json ─────────────────────────────────────────────

    def _search_events(self, q: str, k: int) -> List[RetrievedSource]:
        search = (
            f'(patient.drug.openfda.brand_name:"{q}"+'
            f'patient.drug.openfda.generic_name:"{q}")'
        )
        payload = self._http_get(_EVENT_URL, search=search, k=k)
        if not payload:
            return []
        out: List[RetrievedSource] = []
        for rank, hit in enumerate(payload.get("results", []) or []):
            safety_id = hit.get("safetyreportid") or "?"
            reactions = []
            for reaction in hit.get("patient", {}).get("reaction", []) or []:
                term = reaction.get("reactionmeddrapt")
                if term:
                    reactions.append(term)
            snippet = ("Adverse reactions: " + ", ".join(reactions[:6])
                       if reactions else "(no reactions listed)")
            uri = (f"https://api.fda.gov/drug/event.json?"
                   f"search=safetyreportid:{urllib.parse.quote(safety_id)}")
            out.append(RetrievedSource(
                title=f"FDA adverse event · report {safety_id}",
                uri=uri,
                kind="openfda · adverse event",
                score=1.0 / (rank + 1),
                snippet=snippet[:500],
                provider=self.name,
                evidence_tier=tier_for_uri(uri),
            ))
        return out

    def _http_get(self, base_url: str, *, search: str, k: int) -> Optional[dict]:
        params = {"search": search, "limit": str(max(1, min(k, 20)))}
        if self._api_key:
            params["api_key"] = self._api_key
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
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
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 404 is openFDA's "no matches" signal — don't log noisily.
            if e.code != 404:
                LOG.warning("openFDA HTTP %s for %r: %s", e.code, search[:60], e)
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            LOG.warning("openFDA retrieve failed for %r: %s", search[:60], e)
            return None


def _first(value) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value).strip()
