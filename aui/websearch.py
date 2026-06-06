"""
AX OS web search — SearXNG (open-source metasearch) over its JSON API.
=====================================================================
SearXNG is self-hosted, keyless, and aggregates many engines. We call it
directly over HTTP (stdlib, fail-soft) — it is an external service, not an
Axiom tool, so it does NOT go through the bridge. The optional ``screen``
callable (wired to the bridge's axiom_immune in the route) runs each result's
snippet through the Constitutional Immune System, because search results are
untrusted external content and a classic indirect-prompt-injection vector.

Run SearXNG locally and enable JSON:
    docker run -d -p 8080:8080 searxng/searxng
    # in settings.yml:  search: { formats: [html, json] }
Point AX OS at it with AX_OS_SEARXNG_URL (default http://localhost:8080).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional


def _http_get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={
        "accept": "application/json", "user-agent": "ax-os/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _classify_error(exc: Exception, base: str) -> tuple:
    """Map a fetch failure to (reason, friendly_message)."""
    text = str(exc).lower()
    if any(s in text for s in ("refused", "10061", "actively refused",
                               "connection", "failed to establish", "name or service")):
        return ("unreachable",
                f"No search engine is running at {base}. Start SearXNG "
                f"(docker run -d -p 8080:8080 searxng/searxng) or set AX_OS_SEARXNG_URL.")
    if "tim" in text:  # timed out / timeout
        return ("timeout", f"The search engine at {base} timed out.")
    if "403" in text or "forbidden" in text:
        return ("blocked",
                f"The search engine at {base} rejected the request — enable the "
                f"JSON format in SearXNG settings.yml (search: {{ formats: [html, json] }}).")
    return ("error", f"{type(exc).__name__}: {exc}")


def search(query: str, *, n: int = 5,
           screen: Optional[Callable[[str], dict]] = None,
           timeout: float = 8.0) -> dict:
    """Query SearXNG and return normalized, optionally immune-screened results.

    ``screen(text) -> verdict`` is called per result; a result whose snippet
    trips a detector is marked ``blocked`` and its content redacted (the URL +
    title are kept so the user still sees what was filtered and why).
    """
    base = os.environ.get("AX_OS_SEARXNG_URL", "http://localhost:8080").rstrip("/")
    if not query or not query.strip():
        return {"ok": False, "reason": "empty_query", "error": "Enter a search query.",
                "engine": base, "query": query}
    qs = urllib.parse.urlencode({"q": query, "format": "json"})
    try:
        raw: dict[str, Any] = _http_get_json(f"{base}/search?{qs}", timeout)
    except Exception as e:
        reason, message = _classify_error(e, base)
        return {"ok": False, "reason": reason, "error": message,
                "engine": base, "query": query}

    results = []
    for item in (raw.get("results") or [])[: max(1, n)]:
        results.append({
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "content": item.get("content", "") or "",
            "engine": item.get("engine", ""),
        })

    blocked = 0
    if screen:
        for r in results:
            text = r["content"] or r["title"]
            if not text:
                continue
            try:
                verdict = screen(text)
            except Exception:
                verdict = {}
            if verdict.get("detected"):
                r["blocked"] = True
                r["detection_method"] = verdict.get("detection_method", "")
                r["content"] = ""  # redact untrusted, flagged content
                blocked += 1

    return {"ok": True, "query": query, "engine": base,
            "answers": raw.get("answers") or [],
            "returned": len(results), "blocked": blocked, "results": results}
