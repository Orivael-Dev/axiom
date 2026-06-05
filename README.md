# ax-os-widgets — courier branch (transient)

Not part of Axiom. Transfer these into `Orivael-Dev/ax-os`, then delete.
Covers: (1) wire the four new ORVL MCP tools into AX OS, (2) a weather +
time + settings status strip, (3) a Settings widget (Studio/Calm theme +
local-LLM planner backend).

## LATEST — per-widget expand + Settings + local LLM

- Each status widget now expands **on its own** (clock / weather / settings)
  into a fullscreen overlay portalled to `<body>` (escapes the transformed
  `.stage` / `.app` max-width that clipped it).
- New **⚙ Settings** widget:
  - **Theme** — Studio / Calm (wired to App's theme via `theme`/`onTheme`).
  - **Local LLM** — enable + base URL + model + optional API key. When on,
    it lays out the workspace **instead of Claude** (a real planner backend,
    not just UI).
- Backend:
  - `aui/settings.py` — JSON-backed runtime config (`AX_OS_SETTINGS`),
    secrets redacted to a boolean on read.
  - `aui/planner_local.py` — `local_suggest` against an OpenAI-compatible
    endpoint (Ollama / LM Studio / vLLM / llama.cpp), stdlib-only, falls back
    to the rule planner on any error. `probe()` pings `/models` for the ⚙ UI.
  - `aui/planner_claude.py` — `get_planner()` now resolves per request:
    local LLM (if enabled) → Claude (`AX_OS_PLANNER=claude`) → rules.
  - `aui/server.py` — `GET/POST /settings/llm` (+ `/settings/llm/test`);
    a change logs a signed `settings_llm_update` audit event.

## UPDATE (overwrite)

- `bridge/client.py` — typed wrappers for the ORVL tools on top of the
  generic `call_tool`: `immune_scan`, `mkb_register/find/list`,
  `crl_compute/score`, `cas_defend/report`. No Axiom import — still the
  one seam.
- `aui/server.py` — new routes + CORS:
  - `POST /immune/scan {payload, vector?}` → ORVL-012; logs a signed
    `immune_scan` audit event.
  - `GET /mkb?block_type=` / `POST /mkb/register {spec_content}` → ORVL-004.
  - `GET /widgets/time` — server clock + timezone.
  - `GET /widgets/weather?lat=&lon=` — current conditions via Open-Meteo
    (keyless, 10-min cache, **fails soft** when offline). Default location
    from `AX_OS_WEATHER_LATLON="lat,lon"` (default London).
  - CORS now allows `localhost` / `127.0.0.1` / `tauri://localhost` so the
    webview can call the service cross-origin (needed for the widgets).
- `desktop/src/{api.ts,types.ts,App.tsx,styles.css}` — `api.weather()` /
  `api.immuneScan()`, `Weather` / `ImmuneResult` types, `<StatusStrip/>`
  rendered in the stage header, and widget CSS using the existing tokens.

## NEW

- `desktop/src/components/StatusStrip.tsx` — a live **clock** (client-side,
  ticks each second) + **weather** widget (uses `navigator.geolocation`,
  falls back to the service default, refreshes every 10 min, WMO-code emoji).
- Tests extended: `tests/test_server.py` (immune/mkb routes, `/widgets/time`,
  weather fail-soft) and `tests/test_bridge_command.py` (ORVL wrapper arg
  mapping, pure — no server).

## Verify

```bash
pytest tests/test_server.py tests/test_bridge_command.py   # 18 pass (backend)
cd desktop && npm run build                                # typecheck the widgets
```

Backend tested here (44 pass / 6 e2e skipped). The Open-Meteo **live** fetch
could not be exercised in the build sandbox (egress 403) — the route's
fail-soft path is tested; confirm the live path on a networked machine. The
React/TSX was written against the existing component patterns but **not**
typechecked here (no node_modules) — run `npm run build` to confirm.

The four ORVL tools require the Axiom MCP server at v1.10.0 (axiom PR #67).
Delete this branch after transfer.
