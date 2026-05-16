# openclaw.ai — TODO

Personal-assistant productization layer on top of the AXIOM stack.
Captured 2026-05-16. Not in priority order — pick by deployment choice.

---

## Pending decision: where openclaw.ai runs

Not decided yet. Affects whether the next items need a public URL, an
auth layer, a billing meter, and a TLS cert. Options:

- **Self-hosted** (Jetson Nano / home server / VPS) — simplest
- **Cloud-hosted backend** (Fly.io / Railway / Modal / Vercel) — public URL
- **Local-only single-user** — CLI or local web app, no endpoint

Make this call before building anything below that adds a network surface.

---

## Backend gaps (AXIOM server side)

The repo already exposes a working assistant backend via
`axiom_server.py`. These items polish it for a real assistant frontend.

### 1. Multi-turn `/chat` endpoint
- `/run_axiom` is single-turn today; openclaw.ai needs conversation
  history for a real assistant experience
- Add `/chat` endpoint with `{conversation_id, message}` shape
- Wire to the existing `seed_conversation_id` parameter on
  `LatentEngine.run` (`axiom_latent.py:803`) — it already supports
  trajectory seeding from a prior conversation's final_synthesis
- In-memory session store keyed by `conversation_id`; swap for SQLite
  or Redis later
- **Effort:** ~1 hour

### 2. OpenAI-compatible API shim
- Add `/v1/chat/completions` and `/v1/messages` endpoints that
  translate AXIOM's response shape into the OpenAI / Anthropic shape
- Lets openclaw.ai use any existing assistant framework (LiteLLM,
  OpenWebUI, LibreChat, Open-Interpreter) and drop AXIOM in as a
  "model"
- Must preserve AXIOM's structured metadata (concepts fired, flags,
  scores) — put it under a non-standard `axiom` key in the response
  so OpenAI clients ignore it gracefully
- **Effort:** ~2 hours

### 3. Streaming (SSE)
- `/run_axiom` is request/response only
- Add SSE streaming for `/chat` so tokens appear as they're generated
- Requires `_nim().chat()` to expose a streaming interface — check
  what the underlying client supports
- **Effort:** ~1 hour

---

## Frontend / framing

### 4. `OPENCLAW.md` at repo root
- Explicitly frame the AXIOM stack as the brain of the openclaw.ai
  personal assistant
- Quickstart for self-hosted deployment
- Endpoint catalog (existing + the three above once shipped)
- Cross-link to `docs/ANF_TOKEN_ECONOMICS.md` and
  `docs/OPENCLAW_TODO.md`
- Future Claude sessions (and other tools) should read this first
- **Effort:** 15 min

---

## Validation work (before productizing)

### 5. Run HumanEval token-tracking benchmark
- `examples/axiom_humaneval_run.py` already wired with full
  tokens-per-correct-answer reporting (commit `29d2b7f`)
- Requires `ANTHROPIC_API_KEY` in the environment
- Quick smoke: `python examples/axiom_humaneval_run.py --problems 20`
  (~3 min, ~$0.05)
- Full run: 164 problems, ~25 min, ~$0.50 on Haiku 4.5
- Output answers the §10 question in `docs/ANF_TOKEN_ECONOMICS.md`:
  does AXIOM produce more correct answers per token spent?
- **Effort:** the runtime; no code work needed
- **Do this BEFORE the productization items above** — there's no
  point polishing the assistant frontend if AXIOM is less efficient
  than baseline Claude. Get the data first.

### 6. Run remaining quality benchmarks (already integrated)
- `examples/axiom_arc_run.py` — ARC science reasoning
- `examples/truthfulqa_run.py` — hallucination rate
- `examples/axiom_agi_eval.py` — exam-style multi-domain
- None of these currently track tokens — would need the same
  extension applied to HumanEval (`_call_claude` returning
  `(text, in_tok, out_tok)` tuple, accumulating in mode runner)
- **Effort:** ~30 min per runner to add token tracking, then runtime

---

## Cross-references

- `docs/ANF_TOKEN_ECONOMICS.md` — token-economics writeup; §10 lists
  external validation anchors (Chinchilla, Switch, Mixtral, DeepSeek-V3)
- `axiom_qrf_reverse.py` — reverse-QRF collapse module (shipped on
  commit `a9b2fa7`); synthetic-trajectory generator for training data
- `examples/reverse_qrf_demo.py` — runnable end-to-end demo of
  reverse-QRF across financial / medical / security domains
- `tests/test_axiom_qrf_reverse.py` — 9 tests, all passing
- `axiom_server.py` — existing FastAPI server with `/run_axiom`,
  `/gate/check`, `/cmaa/route`, `/qrf/run`, `/health`, `/status`
