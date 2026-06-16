# Project notes for Claude Code sessions

## Post-beta monetization plan

After the public beta period ends, **some skill packs and some MCP patent
tools will move behind a paywall.** Treat this as a future product
constraint when:

- Adding new packs under `packs/` — flag whether the pack is intended to
  be free-tier or paid-tier at design time (decision belongs in the
  manifest, not bolted on later).
- Adding new MCP tools in `axiom_mcp_server.py` / `axiom_packs/` — same
  question. The patent-emulator tools (ORVL-001 / 013 / 016 / 017 / 019
  / 022 / 023) are the primary paywall candidates; the core five
  (`axiom_guard_check`, `axiom_lint`, `axiom_trace`, `axiom_qrf`,
  `axiom_status`) are expected to stay free.
- Touching the firewall billing / tier surfaces
  (`axiom_firewall/billing.py`, `auth.TIER_*`, `templates/landing.html`'s
  pricing block) — make sure the language and gating doesn't assume the
  current "everything is free during beta" state.
- Wiring new pack-install or MCP-tool-invocation paths — leave room for
  a tier check at the entry point, even if it's a no-op during beta
  (`AXIOM_FIREWALL_BETA_MODE=1`).

The free / paid split itself isn't finalized — don't hardcode pack names
or tool names into a paywall list yet. The right shape is probably a
`tier` field on `SkillPackManifest` and an analogous attribute on
registered MCP tools, defaulting to `"free"`. When the user is ready to
flip the switch, the gate is one place to edit.

Beta mode is controlled by `AXIOM_FIREWALL_BETA_MODE=1` (default on);
the /billing page already swaps Stripe checkout for Contact-Sales while
beta is active.

## Industry-gap themes (long-term direction)

Four observations about gaps in the broader AI ecosystem that Axiom is
positioned to address. Capture them here so every future Claude Code
session sees the directional context and can flag opportunities — or
push back if a change conflicts with a theme's first-step direction —
when touching the named footprints.

### Theme 1 — Universal AI protocol layer ("USB for AI")

**Observation.** MCP is a starting point, but the industry needs an
open, universal standard letting any hosted model (HuggingFace,
OpenRouter, NIM, vLLM) securely read / write / execute across servers
without per-model API shims.

**Axiom today.**
- `axiom_mcp_server.py` — MCP stdio server with 5 tools; HMAC-signed
  verdicts (the "trust envelope" half of the universal-protocol vision)
- `axiom_event_token/backends.py` — `SLMBackend` protocol abstracts
  NIM / Ollama / Deepseek / Custom; `ChainedBackend` + per-domain
  routing (PR #47)
- `docs/mcp.json` — public manifest declaring the tool surface

**Gap.** MCP transport today is stdio-only — no cross-origin
signed-envelope HTTPS transport for hostile networks. No per-model
identity binding. No standard for granular read / write / execute
permission grants.

**First-step hint.** Define an `axiom-mcp-v2` envelope spec —
JSON-RPC over HTTPS with mutual signed headers (model identity +
tenant identity + scope grant), backwards-compatible with v1 stdio.

### Theme 2 — Auto-quantization / distillation / sparsity

**Observation.** Plug-and-play local runtimes that auto-apply
4-bit / 2-bit quantization + knowledge distillation + sparsity to
maximize VRAM efficiency on consumer / edge hardware, without forcing
developers to wrestle with raw kernels.

**Axiom today.**
- `axiom_axm.py` — `AXMHeader.quant_map` exists as a string
  placeholder (e.g. `"elastic_per_layer"`); schema only, no kernels
- `axiom_training_to_axm.py` — references the placeholder values
- `axiom_memory_engine.py._quantize_vec` — vector quantization for
  embedding storage (not weights — different problem)
- *No weight-quantization kernels anywhere*

**Gap.** Zero 4-bit / 2-bit kernels; no distillation loop; no sparsity
pruning; no automatic-policy layer ("if VRAM < X, auto-pick scheme Y").

**First-step hint.** Build SRD (Stochastic Residual Dithering) on
TinyLlama-1.1B, measure WikiText-2 perplexity vs Q4_K_M / Q5_K_M / Q6_K
at matched bpw. If real, SRD becomes Axiom's first weight-quant kernel
and `quant_map` widens from `str` to a structured dict.

**Next-step hint (post-SRD).** The current SRD selective sidecar uses
fixed MET chunk boundaries (40-77% of depth) as the correction target —
a flat EQ preset. This works for general instruction models but is the
wrong curve for specialized architectures:

- Code models (e.g. Qwen Coder): precision-sensitive layers are earlier
  (~15-50%); syntax and identifier encoding lives in factual chunk, not
  reasoning chunk. Current boundaries undershoots.
- Chat/instruction models (e.g. Gemma3): reasoning chunk is correctly
  targeted — validated by TruthfulQA MC1 results (selective +1.9%).
- Tiny models (<200M): insufficient layer specialization for chunk-based
  correction; uniform correction or no correction preferred.
- Multimodal models: the cross-modal connector is the highest-leverage
  correction target, not a standard MET layer range at all.

The pattern mirrors audio EQ: not all music is mixed the same. Bass-heavy
genres (code, early-layer precision) need a different curve than
vocal-forward genres (reasoning, mid-depth layers).

**Architecture-fingerprinted chunk detection** is the next research step:
derive correction boundaries from per-layer activation variance or
gradient norms during a short calibration pass, rather than hardcoding
40-77%. Each architecture tells you where its precision-sensitive bands
actually live. This makes `quant_map` genuinely elastic — the structured
dict would carry per-layer correction weights, not a single chunk range.
See `research/quant/srd_selective_sidecar.py:_REASONING_START_FRAC` and
`research/quant/bench_sidecar_hallucination.py` for current baselines.

### Theme 3 — Continuous evaluation / smart routing

**Observation.** Software has CI/CD; AI needs **Continuous Evaluation**
— real-time pipelines that test production inputs against candidate
models and route queries by accuracy / latency / drift, not just cost.

**Axiom today.**
- `axiom_5cat_benchmark/` — 5-category benchmark with HMAC-signed
  per-trial results
- `axiom_intent_classifier.py` — 6-class intent verdicts; blocks
  HARM / DECEIVE
- `axiom_latent_v2.py` — drift detection via `constitutional_distance`
  per trajectory stage; `DRIFT_THRESHOLD = 0.10`
- `axiom_exoskeleton_ledger.py` — JSONL ledger of every backend call
  (token_id, backend, model, latency_ms, verified)
- `axiom_event_token/router.py` — `DelegateRouter` picks delegates by
  intent class

**Gap.** All signals exist; **none feed routing decisions.** No A/B
routing on accuracy or latency. No drift-driven model swap. 5cat
results are evaluated offline, never consulted at request time. The
exoskeleton ledger records facts but the router doesn't read it.

**First-step hint.** Extend `axiom_event_token/router.py` with a
`RouterPolicy.score(backend, domain)` that consults a running EWMA of
latency_ms + verified-rate per (backend, domain) tuple in the
exoskeleton ledger.

### Theme 4 — CXL memory pooling

**Observation.** Compute Express Link (CXL) lets inference engines
transparently pool physical system memory across multiple local nodes
or expansion cards, breaking the "memory wall" for complex models on
mid-tier enterprise hardware.

**Axiom today.** **Zero footprint.** `SLMBackend` protocol has no
memory-management hooks. `BackendResult` carries no memory metadata.
No multi-node inference, no distributed KV-cache, no paged-attention
backend.

**Gap.** Entire space unentered. CXL itself is hardware infrastructure
— not Axiom's lane to build directly.

**First-step hint.** Don't enter the hardware space directly. Extend
`SLMBackend` with an optional `MemoryProfile` (max_kv_tokens,
swap_policy) so when CXL-aware backends ship in vLLM / tgi, Axiom can
route around their constraints. Defer until a CXL-aware backend
actually exists upstream.

