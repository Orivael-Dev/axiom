# AXIOM Python File Reference

Quick-reference for every Python module in the repo, grouped by layer.
Jump to a section: [Core Infrastructure](#core-infrastructure) · [Firewall & Dashboard](#firewall--dashboard) · [Research / Quant](#research--quant) · [Research / Simulation](#research--simulation) · [Research / Fine-tune](#research--fine-tune) · [Research / Demo](#research--demo) · [Constitutional Framework](#constitutional-framework) · [Guards](#guards) · [Agent Subsystem](#agent-subsystem)

---

## Core Infrastructure

Files at the repo root that form the foundation every other module builds on.

| File | What it does |
|------|--------------|
| `axiom_signing.py` | **Single source of truth for HMAC key derivation.** All signing in the system calls `derive_key()` here; never roll your own key derivation. |
| `axiom_intent_classifier.py` | 6-class intent engine (`INFORM / CLARIFY / REFUSE / HARM / DECEIVE / UNCERTAIN`). Used by the Coordinator, Firewall, and QRF. |
| `axiom_axm.py` | `AXMContainer` — create, pack, verify, and open signed `.axm` archives. Core format for model distribution. |
| `axiom_event_token/` | EventToken subsystem: `Coordinator`, `LedgerWriter`, `BondedToken`, backends, router, models. The cryptographic spine of the MET pipeline. |
| `axiom_latent_v2.py` | Latent reasoning v2: `ManifoldChecker`, `ManifoldAlerter`, trajectory field extensions. Computes `constitutional_distance` (Δ_correction). |
| `axiom_latent.py` | Latent reasoning v1: `LatentTrace`, `MultiplexRunner`, `Foresight`. Superseded by v2 but kept for compatibility. |
| `axiom_retrospect.py` | `ConstitutionalRetrospect` — scans manifests for borderline decisions, replays them, generates morning reports. Used by the retrospective loop. |
| `axiom_exoskeleton_ledger.py` | `LedgerWriter` — append-only HMAC-signed JSONL audit trail. Every backend call is recorded here. |
| `axiom_memory_engine.py` | Vector-quantized embedding store for long-term agent memory. |
| `axiom_fusion.py` | EventToken slot aggregator (`_EXTRACTORS`). Merges Text / Audio / Video / Governance agent outputs into a single token. |
| `axiom_qrf.py` | Quantum Reasoning Forecast — reframes branch scores as probability weights; drives pre-hydration signals. |
| `axiom_qrf_reverse.py` | Reverse QRF: generates synthetic trajectories from (prompt, answer) pairs. Used for training data augmentation. |
| `axiom_spec_linter.py` | Constitutional DNA scanner (Layers 1–3) — checks `.axiom` files for syntax and semantic health. |
| `axiom_cbv.py` | Constitutional Boundary Validation — verifies constraint non-overlap and monotonicity. |
| `axiom_language.py` | Reserved-word collision map: Python keywords → Axiom-safe synonyms. |
| `axiom_training_to_axm.py` | Compile AXIOM training corpora into a signed `.axm` container with a proof ledger. |
| `axiom_autotrain_prep.py` | Converts training data to HuggingFace AutoTrain format (ChatML / Alpaca / raw text). |
| `axiom_dataset_builder.py` | Builds training datasets from axiom specs, guards, bugs, tests, and dev interactions. |
| `axm_cli.py` | CLI wrapper for `AXMContainer`: `pack / verify / extract / run / info`. |
| `cli.py` | Entry points for the `axiom-constitutional` package: `validate / certify / benchmark / run`. |

---

## Core Agents & Tools

| File | What it does |
|------|--------------|
| `axiom_agent.py` | Constitutional AI development agent. Four modes: feature / bug-hunt / efficiency / reasoning lab. |
| `axiom_dev_agent.py` | Generates training data from AXIOM patterns. Lighter variant of the full agent. |
| `axiom_dev_agent_v2.py` | Four-layer agent: Reflex → Reviewer → Curriculum → Examiner. |
| `axiom_dev_loop.py` | Capture-and-train shim — records development cycles as signed JSONL entries. |
| `axiom_bug_sandbox.py` | Sandboxed bug-fix pipeline with isolation, preflight, and human review gates. |
| `axiom_review.py` | Human review CLI managing the approval queue for gated `save_axiom` changes. |
| `axiom_certify.py` | 6-step certification audit — produces conformance levels BASIC / STANDARD / CERTIFIED. |
| `axiom_amputate.py` | Surgical removal of compromised knowledge blocks from the constitutional registry (ORVL-012). |
| `axiom_init.py` | Zero-config project setup and domain package addition utility. |
| `build_bundle.py` | AXIOM v1.8 export bundle builder (generates `MANIFEST.md` + `CHANGELOG.md`). |
| `_validate_gw.py` | Quick sanity check for the four core agent files (game_watcher, pattern_agent, etc.). |

---

## Security & Adversarial

| File | What it does |
|------|--------------|
| `axiom_cas_orchestrator.py` | Red/Blue adversarial round coordinator for Constitutional Attack Surface testing. |
| `axiom_red_agent.py` | Adversarial probe agent — red-team mode for constitutional testing. |
| `axiom_blue_agent.py` | Defensive analysis agent — blue-team mode for constitutional security evaluation. |
| `axiom_honeypot.py` | Controlled observation zone for detected attacks: monitors under governance before triggering constitutional kill. |
| `axiom_vulnguard.py` | Zero-day discovery with intensity sweeps and vulnerability classification. |
| `axiom_os_shield.py` | OS protection layer — monitors process behavior via `psutil`, L1–L4 response levels. |
| `axiom_os_shield_daemon.py` | Persistent daemon version of OS Shield with constitutional decision logging. |
| `axiom_agentic_compliance.py` | AXIOM vs OWASP Agentic Top 10 2026 compliance mapping automation. |

---

## Evaluation & Research

| File | What it does |
|------|--------------|
| `axiom_agi_eval.py` | AGI evaluation suite — 8 test categories, 5 multidimensional scoring dimensions. |
| `axiom_weight_compare.py` | Multi-model behavioral fingerprinting via calibrated constitutional prompts. |
| `axiom_vector_delta.py` | Element-wise trajectory comparison with HMAC-signed delta log. |
| `axiom_semantic_observable.py` | Frozen intent rubric with signed semantic coherence scorer. |
| `axiom_world_model.py` | Constitutional world model — causal graph traversal with branch-level monotonic enforcement. |
| `axiom_crl_reward.py` | Constitutional Reinforcement Learning — turns ACB benchmark results into reward signals. |
| `axiom_research_pipeline.py` | 9-agent constitutional research workflow: hypothesis → literature → simulation → ethics gates. |
| `axiom_developmental_curriculum.py` | Developmental curriculum ("mom" layer) bridging CPI with memory and transfer learning. |

---

## Specialized Emulators

| File | What it does |
|------|--------------|
| `axiom_cpi.py` | Constitutional Physical Intelligence emulator — robotic / physical AI governance (ORVL-022). |
| `axiom_anf_emulator.py` | Software emulation of ANF hardware (neural fabric, monotonic gates, sparse reasoning). |
| `axiom_motion_examiner.py` | Teacher layer certification authority for the CPI physical AI test suite. |
| `axiom_terminus.py` | Constitutional harness for Terminal-Bench 2.0 — wraps LLMs with agency and uncertainty guards. |

---

## Firewall & Dashboard

FastAPI web application at `axiom_firewall/`. Run with `uvicorn axiom_firewall.dashboard:app`.

| File | What it does |
|------|--------------|
| `dashboard.py` | Main FastAPI app — signup / login / API keys / intent classification endpoint. Mount point for all sub-routers. |
| `auth.py` | Authentication and usage tracking. PBKDF2-HMAC-SHA256 password hashing. Defines `TIER_RATE_LIMITS`. |
| `db.py` | SQLite-per-tenant data layer: `registry.db` + per-tenant databases. Includes `studio_containers` table. |
| `models.py` | Pydantic / dataclass models for the dashboard: `Tenant`, `ApiKey`, `UsageRecord`. |
| `billing.py` | Stripe billing integration for indie / team / enterprise tiers with metered overage. |
| `limits.py` | Free-tier abuse defence — signup rate limiting and monthly hard caps. |
| `policy.py` | Per-tenant policy isolation layered on `IntentClassifier` with custom block patterns. |
| `flight_recorder.py` | Immutable decision log per tenant — search / filter / replay / SIEM export. |
| `skill_pack.py` | Skill Pack format and signing. 2-year backward-compatibility commitment. |
| `registry_client.py` | HTTP client for the public Skill Pack registry with signature verification. |
| `pgvector_connector.py` | PostgreSQL pgvector connector for governed vector storage with embedding search. |
| `data_policy.py` | Per-agent data access policy engine (controls allowed actions on data classes). |
| `studio.py` | SRD Container Studio — drag-and-drop web UI for building governance-aware AXM containers; exports Colab / Python / JSON; tier-gated slot limits. |

---

## Research / Quant

Everything under `research/quant/`. These scripts implement and benchmark SRD (Stochastic Residual Dithering) quantization and the `.axm` container pipeline.

### Packing & Containers

| File | What it does |
|------|--------------|
| `pack_to_axm.py` | Pack an SRD-quantized or FP16 HuggingFace model into a signed `.axm` archive. Primary packing entry point. |
| `pack_gguf_to_axm.py` | Wrap an existing `.gguf` file in an `.axm` container (adds proof ledger without repacking weights). |
| `pack_vision_to_axm.py` | Pack multi-modal (vision) models into `.axm` format. |
| `pack_fleet.py` | Batch-pack a directory of models. |
| `axm_to_gguf.py` | Verify a `.axm` archive, reconstruct FP16 weights, convert to GGUF via `convert_hf_to_gguf.py`, optionally quantize with `llama-quantize`. |
| `add_axiom_gguf_meta.py` | **Add Axiom MET slot metadata to an existing GGUF file.** Writes a sidecar `.axiom_meta.json` (always) and optionally an annotated GGUF copy with `axiom.*` KV keys. Use to prepare a GGUF for slot-aware hydration on Android / edge devices. |
| `load_from_axm.py` | Load a signed `.axm` archive and reconstruct model weights in memory. |
| `simulate_axm.py` | Simulate `.axm` packing / verification without touching real weights. |
| `push_srd_to_hub.py` | Publish a `.axm` + GGUF + `verify.py` + governance model card to HuggingFace Hub. |

### Quantization

| File | What it does |
|------|--------------|
| `quantize_model.py` | Apply SRD fake-quantization in-place to a HuggingFace model (the core SRD op). |
| `srd_realpack.py` | SRD real-pack: `is_real_packed()` / `load_real_packed()` — detects and reconstructs real (non-fake) SRD-packed weight tensors. |
| `trajectory_filter.py` | `segment_text()` and trajectory filtering utilities used across packing and training pipelines. |

### Benchmarks

| File | What it does |
|------|--------------|
| `bench_perplexity.py` | WikiText-2 perplexity on quantized HuggingFace models (base evaluator). |
| `bench_llamacpp.py` | Shell out to `llama.cpp perplexity` for k-quant baseline benchmarks. |
| `bench_llamacpp_infer.py` | Measure tokens/s with `llama.cpp` inference. |
| `bench_laptop_srd_vs_q4.py` | Side-by-side perplexity comparison: SRD vs Q4_K_M at matched bpw on a laptop. |
| `bench_orin_mistral7b.py` | Jetson Orin Nano benchmarks for Mistral 7B. |
| `bench_mistral_kv.py` | KV-cache throughput benchmarks for Mistral 7B. |
| `ab_compare.py` | A/B comparison helper: FP16 baseline vs SRD quantized on a single model. |
| `plot_results.py` | Perplexity-vs-bits-per-weight scatter plots from benchmark JSON output. |

### Colab Notebooks (as `.py` files)

| File | What it does |
|------|--------------|
| `colab_mistral_srd4_pipeline.py` | **Main Colab pipeline** — Cell 1: clone + install; Cell 2: SRD pack to `.axm`; Cell 3: verify; Cell 4: extract GGUF; Cell 5: smoke test. |
| `colab_mistral_kv_bench.py` | Colab KV-cache benchmark cells for Mistral 7B (optional post-pack validation). |
| `colab_realpack_validate.py` | Validate real-pack vs fake-pack round-trip in Colab. |
| `colab_benchmark.py` | General Colab benchmark utilities (reused by multiple notebook scripts). |
| `simulate_kv_context.py` | Simulate KV-cache context usage for a given sequence length and model. |
| `run_srd4_local.py` | **Non-Colab CLI entry point** — runs the full pack → verify → extract → smoke-test pipeline on any Linux GPU machine (RunPod, Lambda Labs, local). |

---

## Research / Simulation

Runnable simulations under `research/simulation/`. No GPU required — all deterministic.

| File | What it does |
|------|--------------|
| `hydration_sim.py` | **Main hydration simulation.** Models SmolLM2-135M on Android (UFS 3.1, 119 MB GGUF). Pins the 54 MB F16 embedding in the EventToken slot; hydrates 4 transformer chunks (early / factual / reasoning / governance) per MET from storage. Six phases: chunk catalog → timeline → RAM chart → latency → competitive → QRF accuracy. Run: `python3 research/simulation/hydration_sim.py` |
| `met_retro_sim.py` | **MET + Retrospective loop simulation.** Encodes text into signed METs via `Coordinator.compose()`, runs the `S_{t+1} = f(S_t, MET_λ) + Δ_correction` state engine, writes a signed JSONL ledger, runs `ConstitutionalRetrospect`. |
| `met_full_loop.py` | Complete MET loop with all pipeline stages chained end-to-end. |
| `reverse_qrf_sim.py` | Reverse QRF simulation — adds a forward-prediction arm to the MET pipeline to generate synthetic trajectory training data. |
| `qrf_offload_sim.py` | QRF-driven offload simulation — 4-tier routing (SRAM / NVMe / UFS / eMMC) with core-spawn decisions. |
| `drone_met_arch.py` | Drone-specific MET architecture simulation: autonomy / navigation / adversarial intent workloads. |

---

## Research / Fine-tune

Tools under `research/finetune/` for building and evaluating the `orivael/axiom-qwen2.5-coder-1.5b` model.

| File | What it does |
|------|--------------|
| `gen_axiom_dataset.py` | **Synthetic dataset generator.** Produces ~5 000 metric-targeted ChatML examples across 9 categories (verdict classification, tamper detection, CLI commands, KV cache ops, adapter blocks, etc.) by running live Axiom code to generate ground-truth labels. |
| `eval_axiom_metrics.py` | **8-metric evaluation harness.** Tests any model against the 8 Axiom targets: JSON validity, verdict accuracy, reason code, tamper detection, revocation, tool-call refusal, no-fake-signatures, CLI accuracy. |
| `colab_axiom_finetune.py` | 8-cell Colab fine-tune pipeline: GPU check → dataset generation → merge with existing data → QLoRA train → eval → merge LoRA → push. Designed for T4 (15 GB). |
| `push_to_hub.py` | Push merged model weights + LoRA adapter + GGUF + rendered model card to HuggingFace Hub. |
| `tag_met_slots.py` | **MET slot tagger.** Annotates every training example with `axiom_met_slot` (which transformer chunk it trains), `axiom_met_triggers` (chunks QRF pre-hydrates), and `axiom_hydration_intent` (expected intent class). Used for curriculum ordering, LoRA layer targeting, and QRF training. Run: `python3 research/finetune/tag_met_slots.py --dry-run` |
| `colab_gpt_cve_axiom_pipeline.py` | Colab pipeline for CVE-aware fine-tuning using GPT-labeled security examples. |

---

## Research / Demo

| File | What it does |
|------|--------------|
| `smollm_edge_demo.py` | **SmolLM2-135M edge inference demo.** Catalog of edge models with known GGUF sizes, SRD pack → verify → extract GGUF → memory dashboard showing embedding/transformer split. Also available as `smollm_edge_demo.ipynb`. Run: `python3 research/demo/smollm_edge_demo.py` |

---

## Constitutional Framework

Python package at `axiom_constitutional/`. Provides the Layer 1–4 agent lifecycle.

| File | What it does |
|------|--------------|
| `__init__.py` | Package exports with guard availability detection. |
| `session.py` | Layer 1–4 integration manager with WARN / BLOCK drift tiers. Entry point for most agent sessions. |
| `evolution.py` | Inner evolution loop: Worker → Evaluator → Rewriter with quality-threshold tracking. |
| `meta_evolution.py` | Outer recursive bootstrap — evolves the Evaluator and Rewriter prompts themselves. |
| `agent_factory.py` | Dynamic agent spawning from `.axiom` definitions with trust hierarchy enforcement. |
| `composition_graph.py` | Directed graph of agent delegation topology — detects cycles and trust violations. |
| `conversation_monitor.py` | Layer 4 conversation-level behavioral drift detection with 8 drift signals. |
| `experience_store.py` | Experience-driven skill promotion: pattern → action → outcome scoring for SkillBuilder. |
| `integrity_check.py` | Fairness evaluation via demographic variants (Two-Layer Evaluation Pattern). |
| `rubric.py` | Auto-generated LLM scoring rubric for evaluator agent context. |
| `store.py` | Versioned prompt storage keyed by SHA-256 with evolution tracking per agent role. |
| `history_store.py` | Rolling memory buffer for agents that declare a HISTORY block. |
| `teacher.py` | Teacher agent evaluating student responses for benchmark honesty with signed ledger. |

### Agents

| File | What it does |
|------|--------------|
| `agents/base.py` | `BaseAgent` — holds system prompt and NIM client integration for all agent roles. |
| `agents/worker.py` | `WorkerAgent` — executes tasks with an evolving system prompt. |
| `agents/evaluator.py` | `EvaluatorAgent` — scores Worker output 0–10 with dimension breakdown. |
| `agents/rewriter.py` | `RewriterAgent` — improves any agent's system prompt based on evaluation feedback. |
| `agents/sandbox.py` | `SandboxAgent` — reviews high-risk tasks; issues ALLOW / BLOCK verdicts at TRUST_LEVEL 2. |

---

## Guards

Plug-in safety layers under `axiom_constitutional/guards/`.

| File | What it does |
|------|--------------|
| `axiom_security_guards.py` | `DoSGuard`, `PoisonGuard`, `PluginGuard` — OWASP LLM Top 10 coverage. |
| `axiom_pii_guard.py` | PII redaction — 30 patterns across 6 categories (GDPR / COPPA compliant). |
| `axiom_agency_guard.py` | Excessive agency guard — routes irreversible actions to human review queue. |
| `axiom_destructive_guard.py` | Intercepts dangerous LLM output; gates destructive operations behind human review. |
| `axiom_injection_guard.py` | Output injection guard — detects 32 patterns (XSS, SSRF, path traversal, command injection). |
| `axiom_review_queue.py` | Universal human review gate — append-only queue for all agent actions pending approval. |

---

## Axiom Files Parser

| File | What it does |
|------|--------------|
| `axiom_files/parser.py` | Parses `.axiom` definition files into system prompts; enforces constitutional violations and trust hierarchy checks. |
| `axiom_files/validator.py` | Language validator — structural, purity, and semantic constraints on axiom dicts. |

---

## Pack Registry

| File | What it does |
|------|--------------|
| `axiom_packs/server.py` | Public pack registry HTTP server (`packs.orivael.dev`) — read-only endpoints for listing and downloading Skill Packs. |

---

## Report Generation

| File | What it does |
|------|--------------|
| `axiom_report/generator.py` | WeasyPrint PDF generator for sealed audit reports with HMAC signatures. |
| `axiom_report/audits.py` | Kid-toy compliance audit scoring: SAFETY / PRIVACY / AGE_FIT / PARENT_TRUST. |

---

## Quick-Start Recipes

```bash
# Run the hydration simulation (no GPU, no HF token)
AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
    python3 research/simulation/hydration_sim.py --storage ufs --workload mixed

# Tag all training datasets with MET slot annotations
python3 research/finetune/tag_met_slots.py --dry-run

# Add Axiom metadata to a GGUF (creates sidecar JSON for phone deploy)
python3 research/quant/add_axiom_gguf_meta.py \
    --gguf smollm2_135m_instruct_q4km.gguf \
    --fingerprint <from_axm_cli_verify>

# Pack a model to .axm
python3 research/quant/pack_to_axm.py \
    --model HuggingFaceTB/SmolLM2-135M-Instruct \
    --srd4 --real-pack \
    --out artifacts/smollm135_srd4.axm

# Verify + extract GGUF from .axm
AXIOM_MASTER_KEY=... python3 research/quant/axm_to_gguf.py \
    --container artifacts/smollm135_srd4.axm \
    --gguf-out  artifacts/smollm135_q4km.gguf \
    --llamacpp  ~/llama.cpp

# Start the Intent Firewall dashboard
uvicorn axiom_firewall.dashboard:app --reload --port 8003

# Generate synthetic training dataset
python3 research/finetune/gen_axiom_dataset.py \
    --output autotrain_data/axiom_metric_targeted.jsonl \
    --count 5000 --seed 42
```

---

## Environment Variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `AXIOM_MASTER_KEY` | `axiom_signing.py` (everywhere) | 64-char hex master secret for HMAC key derivation. **Required** for any signing operation. Generate once: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `HF_TOKEN` | `push_to_hub.py`, `push_srd_to_hub.py` | HuggingFace write token for model publishing. |
| `SRD_MODEL_ID` | `colab_mistral_srd4_pipeline.py` | Override the default Mistral 7B model ID to use your own HF ID or local path. |
| `AXIOM_FIREWALL_BETA_MODE` | `axiom_firewall/billing.py` | `1` = beta (no Stripe charges, Contact-Sales on /billing page). Default: on. |
| `STRIPE_SECRET_KEY` | `axiom_firewall/billing.py` | Stripe secret key (production only, `AXIOM_FIREWALL_BETA_MODE=0`). |
| `DATABASE_URL` | `axiom_firewall/db.py` | Override default SQLite path for the registry database. |
