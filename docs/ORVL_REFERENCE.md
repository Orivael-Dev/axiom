# ORVL Patent Reference

A catalogue of every **ORVL-NNN** identifier used across the Axiom codebase,
what it covers, and where it lives. ORVL identifiers are Orivael Inc.'s
patent / patent-pending designators; they tag the constitutional-AI
inventions that the Axiom stack emulates in software.

> **Filing status.** Only **ORVL-001** and **ORVL-002** are formally filed
> provisionals (`ORVL-001-PROV` / `ORVL-002-PROV`, both filed April 22, 2026 —
> see `COPYRIGHT`, `LICENSE`, `ACB_SPEC_v1.md`). The remaining ORVL numbers are
> internal patent designators carried in source headers, `docs/mcp.json`, and
> the `.axiom` core specs; treat them as "patent pending / internal" rather than
> as issued or separately filed unless a `-PROV` suffix appears.

> **MCP exposure.** The IDs marked **MCP** below are surfaced as
> `patent_emulator` tools in `docs/mcp.json` and `axiom_mcp_server.py`. Per
> `CLAUDE.md`, these patent-emulator tools are the primary post-beta paywall
> candidates; the core five (`axiom_guard_check`, `axiom_lint`, `axiom_trace`,
> `axiom_qrf`, `axiom_status`) are expected to stay free.

---

## Quick index

| ORVL | Name | MCP tool | Primary file |
|------|------|----------|--------------|
| 001 | Runtime Authority Control for Agentic AI | `axiom_validate`, `axiom_ledger`, `axiom_marketplace` | `axiom_audit_ledger.py`, `axiom_marketplace.py` |
| 002 | Constitutional Intent / Sandboxed Bug-Fix Pipeline | — | `axiom_bug_sandbox.py`, `axiom_intent_classifier.py` |
| 003 | RL & Terminal Governance | — | `axiom_crl_reward.py`, `axiom_terminus.py` |
| 004 | Modular Constitutional Knowledge Blocks (MKB) | `axiom_mkb` | `axiom_mkb.py` |
| 005 | Constitutional Manifold Distance / MonotonicGate | — | `axiom_latent_v2.py` |
| 006 | Parallel-N Dynamic Branch Count | — | `axiom_latent_v2.py` (BRANCH_POOL) |
| 007 | Constitutional Conversation Graph (CCG) | — | `axiom_conversation_graph.py` |
| 008 | Constitutional Adversarial Sandbox (CAS) | `axiom_cas` | `axiom_cas_orchestrator.py` |
| 009 | Quantum Reasoning Forecast (QRF) | `axiom_qrf` *(core 5)* | `axiom_mcp_server.py` |
| 010 | Constitutional Boundary Validation (CBV) | — | `tests/test_axiom_cbv.py` |
| 011 | Constitutional Reinforcement Learning Reward (CRL) | `axiom_crl` | `axiom_crl_reward.py` |
| 012 | Constitutional Immune System (CIS) | `axiom_immune` | `axiom_immune.py`, `axiom_amputate.py` |
| 013 | Constitutional OS Shield (COS) | `axiom_shield` | `axiom_os_shield.py`, `axiom_os_shield_daemon.py` |
| 014 | Constitutional World Model | — | `axiom_world_model.py` |
| 015 | Constitutional Memory Engine | `axiom_memory` | `axiom_memory_engine.py` |
| 016 | Intent Classifier & Intent Gate | `axiom_intent_gate_check`, `axiom_workspace` | `axiom_intent_classifier.py`, `axiom_intent_gate.py` |
| 017 | Constitutional Multi-Agent Architecture (CMAA) | `axiom_cmaa_route`, `axiom_cmaa_fleet` | `axiom_cmaa.py` |
| 018 | Axiom Neural Fabric (ANF) | — | `axiom_anf_emulator.py` |
| 019 | Sovereign Phone (ASPA) | `axiom_phone_gate` | `axiom_sovereign_phone.py` |
| 020 | Constitutional Retrospective Learning | — | `axiom_retrospect.py` |
| 021 | VulnGuard — Constitutional Zero-Day Discovery | — | `axiom_vulnguard.py` |
| 022 | Constitutional Physical Intelligence (CPI) | `axiom_cpi` | `axiom_cpi.py`, `axiom_motion_examiner.py` |
| 023 | AXIOM eXchange Model (.AXM) | `axiom_axm` | `axiom_axm.py`, `axiom_training_to_axm.py` |
| 024 | Video Topology Detectors | — | `axiom_video/depth.py`, `axiom_video/surface.py` |
| 025 | Multimodal Intent Fusion (Axiom Fusion) | `axiom_fusion` | `axiom_fusion.py` |

---

## Full entries

### ORVL-001 — Runtime Authority Control for Agentic AI
**MCP:** `axiom_validate`, `axiom_ledger`, `axiom_marketplace` ·
**Files:** `axiom_audit_ledger.py`, `axiom_marketplace.py`, `axiom_cli.py`, `COPYRIGHT`, `LICENSE`

The foundational patent (filed `ORVL-001-PROV`, April 22, 2026). Covers the
bonded-pair authority model, the constitutional-geometry framework, the
HMAC-signed state register (runtime authority **without key rotation**), the
constitutional-language validator, and the append-only signed audit ledger.
The marketplace surface mints bonded pairs with live-revocable authority —
`revoke` cuts an agent's authority instantly and terminally without rotating
any keys.

### ORVL-002 — Constitutional Intent / Sandboxed Bug-Fix Pipeline
**Files:** `axiom_bug_sandbox.py`, `axiom_intent_classifier.py`, `ACB_SPEC_v1.md`

The second filed provisional (`ORVL-002-PROV`, April 22, 2026). Covers the
constitutional sandboxed bug-fix pipeline (BugHunter → SandboxAgent → SimRunner)
for isolated fix-proposal generation and testing, and the malign-intent
detection spine for language models.

### ORVL-003 — RL & Terminal Governance
**Files:** `axiom_crl_reward.py`, `axiom_terminus.py`

Constitutional reinforcement-learning governance for fine-tuning and terminal
command execution. Enforces `CANNOT_MUTATE` constitutional boundaries on
autonomous agents executing system commands.

### ORVL-004 — Modular Constitutional Knowledge Blocks (MKB)
**MCP:** `axiom_mkb` · **Files:** `axiom_mkb.py`

> *"Modular Constitutional Knowledge Blocks. `register` parses a .axiom spec into
> a typed HMAC-signed KnowledgeBlock and appends it to the registry; `find` looks
> one up by name (+ optional version); `list` returns blocks, optionally filtered
> by block_type (GUARD/AGENT/SPEC/REWARD/SOVEREIGN/VALIDATOR)."*

Registration, discovery, and composition of reusable, versioned, signed
constitutional modules.

### ORVL-005 — Constitutional Manifold Distance / MonotonicGate
**Files:** `axiom_latent_v2.py`, `axiom_latent.py`

Latent-space distance metric measuring constitutional deviation from safe
reasoning paths (Phase 2 of trajectory analysis). The MonotonicGate enforces
non-decreasing distance magnitude across reasoning stages
(`preflight → mid_chain → final_synthesis`).

### ORVL-006 — Parallel-N Dynamic Branch Count
**Files:** `axiom_latent_v2.py` (`BRANCH_POOL`)

Risk-adaptive branching for the LatentEngine. Scales parallel branches (N=2 → N=8)
based on Phase-1 risk clusters: high-risk domains (medical, legal, financial,
safety) spawn N=8; empty risk uses N=2. `BRANCH_POOL` is `CANNOT_MUTATE`.

### ORVL-007 — Constitutional Conversation Graph (CCG)
**Files:** `axiom_conversation_graph.py`, `docs/axiom_console.html`

Append-only graph of conversation nodes with cosine-similarity edge detection,
coordinate propagation via `seed_from()` (`DAMPEN_FACTOR=0.5`), and a web-console
visualization/inspection component.

### ORVL-008 — Constitutional Adversarial Sandbox (CAS)
**MCP:** `axiom_cas` · **Files:** `axiom_cas_orchestrator.py`

> *"`defend` runs the blue-team detectors over a corpus of attack payloads and
> reports detected vs missed (red_wins), weak regions and signed fix proposals;
> `report` summarises the signed CAS round log."*

The stateless evaluation + inspection surface for the red-vs-blue training loop.

### ORVL-009 — Quantum Reasoning Forecast (QRF)
**MCP:** `axiom_qrf` *(one of the free core-five)* · **Files:** `axiom_mcp_server.py`

> *"Quantum-style reasoning forecast: N parallel branches with constitutional
> probability per outcome."*

Each branch runs preflight/mid_chain/final_synthesis with independent intent
vectors, ranked by constitutional-distance score.

### ORVL-010 — Constitutional Boundary Validation (CBV)
**Files:** `tests/test_axiom_cbv.py`

The `CANNOT_MUTATE` primitive: immutable constitutional invariants that cannot be
reassigned at runtime. Freeze-time assertion of governance constants (trust
levels, limits, thresholds) across all modules.

### ORVL-011 — Constitutional Reinforcement Learning Reward (CRL)
**MCP:** `axiom_crl` · **Files:** `axiom_crl_reward.py`

> *"`compute` turns governance scores (constitutional_distance, monotonic_pass,
> cas_blue_win, cbv_validity) into a clipped, signed scalar reward with weighted
> components; `score` evaluates a prompt/response pair against the ACB modules
> (no LLM) ..."*

Weights: `W_DISTANCE=0.35`, `W_MONOTONIC=0.30`, `W_CAS=0.25`, `W_CBV=0.10`.

### ORVL-012 — Constitutional Immune System (CIS)
**MCP:** `axiom_immune` · **Files:** `axiom_immune.py`, `axiom_amputate.py`

> *"Runs the blue-team antibody detectors (guard-pattern, manifold-distance, HMAC
> violation, CANNOT_MUTATE, semantic-similarity) over a presented payload (the
> antigen) and returns a signed immune response: detected, which detector fired,
> confidence, weak-region cluster and a fix proposal."*

Includes the surgical `amputate` component for excising compromised regions.

### ORVL-013 — Constitutional OS Shield (COS)
**MCP:** `axiom_shield` · **Files:** `axiom_os_shield.py`, `axiom_os_shield_daemon.py`

> *"Constitutional ransomware defence that stops attackers at the enumeration
> stage, not after encryption."*

Process-trajectory monitoring daemon over the real-action surface (process
creation, file access); serializes incidents through the MKB registry (ORVL-004).

### ORVL-014 — Constitutional World Model
**Files:** `axiom_world_model.py`

Forward simulation of constitutional state evolution via causal-graph traversal
and branch-level monotonic enforcement. Extended into the physical domain by CPI
(ORVL-022) for contact prediction.

### ORVL-015 — Constitutional Memory Engine
**MCP:** `axiom_memory` · **Files:** `axiom_memory_engine.py`

> *"Local-first recall over signed, compressed memory packets (lossless for
> governance, lossy for language). `remember` stores a signed packet; `recall`
> returns the closest authentic packet above the similarity threshold."*

LSH-indexed recall with domain-scoped constraints and conversation history.

### ORVL-016 — Intent Classifier & Intent Gate
**MCP:** `axiom_intent_gate_check`, `axiom_workspace` · **Files:** `axiom_intent_classifier.py`, `axiom_intent_gate.py`

> *"Classify text + optional trajectory through the intent gate. Returns
> intent_class (INFORM / CLARIFY / REFUSE / HARM / DECEIVE / UNCERTAIN),
> confidence, signals, and HMAC signature."*

Rule-based 6-class intent-shape classifier that gates requests before they cross
container boundaries — Layer 0 of the CMAA orchestrator.

### ORVL-017 — Constitutional Multi-Agent Architecture (CMAA)
**MCP:** `axiom_cmaa_route`, `axiom_cmaa_fleet` · **Files:** `axiom_cmaa.py`

> *"Route a constitutional packet through the multi-agent orchestrator. HARM /
> DECEIVE intents are refused before reaching the orchestrator; bonded-pair
> revocations short-circuit authority without rotating keys."*

Routes packets through a bonded-pair-controlled fleet, enforcing a per-container
trust ACL. Wires the ORVL-016 default IntentGate so it deploys without external
dependencies.

### ORVL-018 — Axiom Neural Fabric (ANF)
**Files:** `axiom_anf_emulator.py`, `examples/anf_investor_demo.py`

Hardware substrate ("governance coprocessor") for sub-microsecond constitutional
gating. The software emulator provides a MonotonicGate (Layer-1 analog
comparator), sparse reasoning cores, 32-D latent buffers, and a `<1 µs` hardware
interrupt latency benchmark.

### ORVL-019 — Sovereign Phone (ASPA)
**MCP:** `axiom_phone_gate` · **Files:** `axiom_sovereign_phone.py`, `examples/hello_operator_demo.py`

> *"Run text through the Sovereign Phone constitutional coprocessor. `out` gates
> outbound queries; `in` gates inbound cloud responses."*

A mobile neural-compute block: outbound gate (pre-classification + PII redaction
+ ANF emulation) and inbound gate (manipulation + privacy-injection checks).
Lazy-loads delegates per intent class to keep VRAM lean. Includes the
"Hello Operator" IRS scam-call replay scenario.

### ORVL-020 — Constitutional Retrospective Learning
**Files:** `axiom_retrospect.py`

Scans signed latent manifests for borderline / missed / false-positive decisions
and replays them through the current stack, extracting improvement records and
generating morning regression reports (`TRUST_LEVEL=4`).

### ORVL-021 — VulnGuard (Constitutional Zero-Day Discovery)
**Files:** `axiom_vulnguard.py`, `examples/vulnguard_investor_demo.py`

Probes constitutional attack surfaces with intensity sweeps, detects non-linear
distance collapse ("cliffs"), and classifies vulnerabilities by severity. By
design it never generates exploits or crosses confirmed exploit boundaries.

### ORVL-022 — Constitutional Physical Intelligence (CPI)
**MCP:** `axiom_cpi` · **Files:** `axiom_cpi.py`, `axiom_motion_examiner.py`, `axiom_developmental_curriculum.py`

> *"Toddler reflex / supervisor / curriculum / examiner stack for robotics,
> prosthetics, vehicles."*

Maps constitutional governance to physical AI via five subsystems:
PhysicalMonotonicGate (sub-1 ms stability reflex), VertexClassifier
(geometry → skill class), MaterialSimulator (N-branch contact prediction),
PhysicalFixPlaybook (trajectory recovery), and HumanoidStabilityAgent.

### ORVL-023 — AXIOM eXchange Model (.AXM)
**MCP:** `axiom_axm` · **Files:** `axiom_axm.py`, `axiom_training_to_axm.py`

> *"Successor-to-GGUF format treating models as living execution graphs with
> signed skill delegates and proof ledgers."*

Hybrid trust model: container header + per-skill manifests + signed proof ledger.
Composes with ORVL-004 (MKB), ORVL-018 (ANF), and ORVL-019 (Phone).

### ORVL-024 — Video Topology Detectors
**Files:** `axiom_video/depth.py`, `axiom_video/surface.py`

Modular signed video-topology detectors (ObjectTracker, MotionClassifier,
ImpactDetector, TemporalChainExtractor) plus the Phase-C agents DepthClassifier
(near/mid/far + approach/recede + occlusion) and SurfaceClassifier
(upright/tilted/inverted + stability). Feeds the EventToken Coordinator.

### ORVL-025 — Multimodal Intent Fusion (Axiom Fusion)
**MCP:** `axiom_fusion` · **Files:** `axiom_fusion.py`, `research/finetune/MODEL_CARD_TEMPLATE.md`

> *"Multimodal intent fusion (axiom-fusion-v1) over an EventToken. Each present
> modality layer (text/audio/tempo/vad/voice/video/physics/governance) votes its
> intent signals weighted by confidence; the top-6 form the intent_vector ...
> Physical-event modalities (audio+video) dominate text ... fusion_confidence is
> the mean modal confidence capped at 0.85."*

The `0.85` confidence cap is `CANNOT_MUTATE`; an empty token yields
`['ask_general']`; every result is signed and self-verifies.

---

## Notes & caveats

- **Numbering is not strictly chronological by module maturity.** Several IDs
  (003, 005, 006, 010, 014, 020, 024) live primarily in implementation/test code
  and `.axiom` specs rather than as standalone documents.
- **ORVL-011 vs ORVL-003 / ORVL-020.** Both 003 and 011 touch reinforcement
  learning; 003 is the broader RL + terminal-governance designation, while 011 is
  specifically the *reward-signal* invention exposed as `axiom_crl`. ORVL-020
  ("Retrospective Learning") is also abbreviated "CRL" in some headers — distinct
  from the ORVL-011 reward engine.
- This document is generated from source headers, `docs/mcp.json`,
  `ACB_SPEC_v1.md`, `COPYRIGHT`/`LICENSE`, and the `axiom_files/core/*.axiom`
  specs as of 2026-06-20. If you add or renumber an ORVL identifier, update the
  Quick-index table and the matching full entry here.
