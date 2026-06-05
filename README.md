# ax-os-companion — courier branch (transient)

Not part of Axiom. Transfer into `Orivael-Dev/ax-os`, then delete.
Two things this turn: (1) the **search-panel UI** that completes
`ax-os-search` (symbol-forward), (2) a text-only **AI companion** (à la
"Her") with `test_companion.py` as the contract.

## Companion (NEW) — for the team that's still writing it
- `aui/companion.py` — **reference skeleton**. `Companion.say(text)` →
  `CompanionReply`. Text only (`voice_enabled = False`), constitutional
  (HARM/DECEIVE refused **in-persona, model never called**), present
  (history threaded into every turn, constant persona). Model-agnostic:
  `generate(messages)->str` is injected; defaults to a reflective offline
  voice. `build_companion(bridge)` wires immune screening + the local LLM.
  **Replace the internals freely — keep the tests green.**
- `tests/test_companion.py` — the contract (12 tests): text reply, no voice,
  memory/context threading, constant persona, harm refusal without calling
  the model, empty-input + model-failure resilience, reset.
- `aui/server.py` — `POST /companion/say {text, reset?}`; logs a signed
  `companion_turn` audit event. Holds one companion per process.

## Search panel (completes ax-os-search) — symbols where available
- `desktop/src/components/SearchPanel.tsx` — 🔎 search bar; results list with
  🌐 engine, ↗ external link, 💡 instant answers, and 🛡 for hits the immune
  system filtered (struck-through, content redacted, detection tag).
- `App.tsx` renders it in the stage; `api.search()` + `styles.css` included.
- Backend `GET /search` + `aui/websearch.py` carried here too so the snapshot
  is self-contained (same as on ax-os-search).

## Verify
    pytest tests/test_companion.py tests/test_server.py -q   # 36 pass
    cd desktop && npm run build                              # typecheck the UI

Backend tested here (68 pass / 6 e2e skipped). Live SearXNG + the React build
not exercised in the sandbox. No voice in the companion yet (by design).
Delete after transfer.
