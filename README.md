# ax-os-full — consolidated AX OS snapshot (transient courier)

Not part of Axiom. **One coherent snapshot** of the entire AX OS product —
backend + desktop — guaranteed to connect with no branch ordering to remember.
Transfer into `Orivael-Dev/ax-os`, then delete. Supersedes every incremental
`ax-os-*` courier (widgets, search, companion, fusion-voice, …).

## What's inside
**Backend (Python):**
- `bridge/` — the one seam to Axiom (`AxiomBridge`): generic `call_tool` +
  typed wrappers (`immune_scan`, `mkb_*`, `crl_*`, `cas_*`, `fuse`,
  `remember/recall`, marketplace, workspace). No Axiom import.
- `aui/` — FastAPI service + workspace planner (rules / Claude / **local LLM**),
  `settings.py` (LLM + voice), `companion.py` (text companion à la "Her" —
  **multimodal fusion** + memory + retrospect), `websearch.py` (SearXNG), panels.
- `workspace/`, `marketplace/`, `packaging/`, `tests/` (83 pass / 6 skipped).

**Desktop (Tauri + React):** `desktop/` — GoalBar (local/cloud planner symbol),
StatusStrip (weather / clock / ⚙ settings with **Local LLM + Voice**),
SearchPanel (🔎 / 🛡 immune-filtered), CompanionPanel (Aria chat + **🔊 TTS**),
panels, themes, Tauri sidecar config.

## Capabilities wired
- Intent-gated workspace assembly; local/cloud planner indicator.
- Weather/clock/settings widgets (click-to-expand).
- SearXNG web search with an `axiom_immune` screen on results.
- Companion "Aria": local-LLM replies, ORVL-015 cross-session memory,
  **axiom-fusion-v1** intent fusion (HARM/DECEIVE risk cluster refuses
  in-persona), per-turn retrospect recording, **browser TTS** (🔊) + Piper/cloud
  `/tts` stub. STT input seam stubbed (`/companion/listen`).

## Requires
Axiom `main` with the MCP tools the bridge calls — including **`axiom_fusion`**
(branch `claude/axiom-fusion`, **VERSION ≥ 1.11.0**). If `axiom_fusion` isn't
present, the companion's fuse step fails soft to the immune-only gate.

## Verified (from this tree)
- Wiring audit: every `bridge.*` and `api.*` call resolves.
- `pytest` → 83 passed, 6 skipped.
- Not run here: the React build (`cd desktop && npm i && npm run build`), live
  SearXNG / Open-Meteo / Piper, and the Tauri/PyInstaller build.

## Run
    pip install fastapi uvicorn          # + Axiom on PYTHONPATH or AX_OS_MCP_BIN
    python -m aui.server                 # 127.0.0.1:8800
    cd desktop && npm install && npm run tauri dev
