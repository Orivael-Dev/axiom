# ax-os-fusion-voice — courier branch (transient)

Not part of Axiom. Transfer into `Orivael-Dev/ax-os`, then delete. Wires the
companion to **axiom-fusion-v1** + retrospect, and adds the **TTS** increment.
Requires Axiom main with `axiom_fusion` (branch `claude/axiom-fusion`, ≥ v1.11.0).

## Companion uses fusion + retrospect
- `bridge/client.py` — `fuse(token)` → `axiom_fusion` MCP tool (axiom-fusion-v1).
- `aui/companion.py` — each turn builds a minimal EventToken (text layer +
  governance layer from the immune verdict) and calls `fuse`; a HARM/DECEIVE
  **risk cluster is authoritative** and refuses in-persona without calling the
  model. The same path scales to audio/voice/video once STT + camera populate
  those layers. Every turn is recorded to a retrospect manifest
  (`AX_OS_RETROSPECT_MANIFEST`) for `axiom_retrospect` to review. Both hooks are
  **injectable**, so the 16-test contract stays green.

## TTS (first increment)
- `aui/settings.py` + `/settings/voice` — voice config (enabled, engine:
  browser|piper|cloud, voice, rate, base_url).
- `desktop CompanionPanel` — 🔊/🔇 toggle; speaks each reply via the **Web
  Speech API** (browser engine, fully on-device) or `/tts` (piper/cloud).
- `POST /tts` — server-side TTS stub proxying a Piper-style server, **fails
  soft** (browser engine speaks client-side and never calls it).
- ⚙ Settings panel gains a **Voice** section (enable + engine).

## STT seam (voice input — not built)
- `POST /companion/listen` returns `stt_not_implemented` with the contract:
  `audio → transcript → immune_scan → companion.say`. Voice input is the
  safety-relevant half, so it lands behind the immune gate when built.

## Verify
    pytest tests/test_companion.py tests/test_server.py -q   # 41 pass
    cd desktop && npm run build                              # typecheck UI

Backend 83 pass / 6 e2e skipped. React build + live Piper/STT unverified here.
Delete after transfer.
