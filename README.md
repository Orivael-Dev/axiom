# ax-os-full — consolidated AX OS snapshot (transient courier)

Not part of Axiom. **One coherent snapshot** of the entire AX OS product —
backend + desktop — that is guaranteed to connect with no branch ordering to
remember. Transfer into `Orivael-Dev/ax-os`, then delete. Supersedes the
incremental couriers (`ax-os-widgets`, `ax-os-search`, `ax-os-companion`, …).

## What's inside
**Backend (Python):**
- `bridge/` — the one seam to Axiom (`AxiomBridge`): generic `call_tool` +
  typed wrappers (`immune_scan`, `mkb_*`, `crl_*`, `cas_*`, `remember/recall`,
  marketplace, workspace). Spawns the Axiom MCP server (or a frozen sidecar
  via `AX_OS_MCP_BIN`). No Axiom import.
- `aui/` — FastAPI service (`server.py`) + workspace planner (rules / Claude /
  **local LLM**), `settings.py`, `companion.py` (text-only, à la "Her"),
  `websearch.py` (SearXNG), panels.
- `workspace/`, `marketplace/` — assembly + signed-agent bonded authority.
- `packaging/` — PyInstaller specs + build scripts for the one-click desktop.
- `tests/` — 73 pass / 6 e2e skipped.

**Desktop (Tauri + React):** `desktop/` — full shell: GoalBar (local/cloud
planner symbol), StatusStrip (weather / clock / ⚙ settings, click-to-expand),
SearchPanel (🔎 results, 🛡 immune-filtered), CompanionPanel (Aria chat),
panels, themes, Tauri sidecar config.

## Requires
Axiom `main` at **VERSION ≥ 1.10.0** (PR #67, merged) — the bridge calls
`axiom_immune / mkb / cas / crl / memory / ledger / marketplace / workspace /
guard_check`, all present there.

## Verified (from this tree)
- Wiring audit: every `bridge.*` call resolves to a bridge method; every
  `api.*` call resolves to an api method.
- `pytest` → 73 passed, 6 skipped.
- Not run in this sandbox: the React build (`cd desktop && npm i && npm run
  build`), live SearXNG / Open-Meteo fetches, and the Tauri/PyInstaller build.

## Run
    pip install fastapi uvicorn          # + Axiom on PYTHONPATH or AX_OS_MCP_BIN
    python -m aui.server                 # 127.0.0.1:8800
    cd desktop && npm install && npm run tauri dev
