# ax-os-search — courier branch (transient)

Not part of Axiom. Transfer into `Orivael-Dev/ax-os`, then delete.
Open-source web search (SearXNG) wired into AX OS, with a constitutional
immune screen on results.

## NEW
- `aui/websearch.py` — `search(query, n, screen)` queries a self-hosted
  **SearXNG** JSON API (`AX_OS_SEARXNG_URL`, default `http://localhost:8080`),
  stdlib-only, **fails soft** when SearXNG is down. External service — called
  directly by the service, NOT through the bridge (bridge stays Axiom-only).

## UPDATE (overwrite)
- `aui/server.py` — `GET /search?q=&n=&screen=` proxies SearXNG and, unless
  `screen=false`, runs every result snippet through **axiom_immune** (ORVL-012)
  via the bridge. Flagged results are marked `blocked` and their content
  redacted (url+title kept) — this defuses indirect prompt injection in search
  results. Logs a signed `search` audit event (query, returned, blocked).
- `desktop/src/{api.ts,types.ts}` — `api.search(q, n)` + `SearchResults` /
  `SearchHit` types, ready for a search panel.

## Run SearXNG
    docker run -d -p 8080:8080 searxng/searxng
    # settings.yml:  search: { formats: [html, json] }   # JSON is off by default

## Verify
    pytest tests/test_server.py -q     # parse + immune-screen + fail-soft (21 pass)

Backend tested here (53 pass / 6 e2e skipped). Live SearXNG fetch not exercised
in the build sandbox (no egress); the parse path is tested with canned JSON and
the offline path fails soft. No search UI panel yet — `api.search` is wired so
one can be added. Delete after transfer.
