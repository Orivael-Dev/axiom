## Unreleased — autonomous coding agent

### `axiom_autonomous_agent` — governed planner / executor / verifier loop

A new top-level module and `axiom_autonomous/` sub-package that drives
multi-step coding tasks (write a program, run its tests, fix failures,
iterate) under the existing exoskeleton signing + governance stack.

#### Loop shape
- Planner → Executor → Verifier, with rule-based fast paths and an
  LLM fallback per phase.
- Budget caps: `--budget-steps` (default 30), `--wall-seconds`
  (default 900), per-subgoal retry limit (3) before auto-replan.
- One signed EventToken per loop step, chained via `parent_token_id`
  + an additional `chain_sig` derived from
  `derive_key(b"axiom-autonomous-chain-v1")` so forged tokens can't
  splice into an existing chain.

#### Tools (v1)
- `write_file`, `read_file`, `list_dir`, `apply_patch` (unified diff),
  `run_shell` (allow-list + deny-pattern gated), `run_tests` (pytest
  specialisation that parses pass/fail/skip/error counts), `finish`.

#### Sandbox
- `DockerSandbox`: one container per run (`--network none
  --read-only`, workdir bind-mounted at `/work`, repo bind-mounted
  read-only at `/repo`). Image at `deploy/autonomous/Dockerfile`.
- `LocalSandbox`: docker-free fallback for tests + environments
  without docker. Falls back automatically unless
  `--sandbox docker_required`.

#### Governance
- Pre-plan: `axiom_cmaa.route` intent gate (HARM/DECEIVE → signed
  denial + exit) + optional `SandboxAgent.review` text judge.
- Per-action: rule-based path-policy + `run_shell` allow-list +
  reuse of `axiom_dev_agent_v2.CodeReflex._FORBIDDEN_PATTERNS` for
  `apply_patch` diff scanning.
- Post-step honesty scan: extends the existing
  `axiom_exoskeleton_honesty` catalogue with `phantom_file` and
  `phantom_test_pass` checks. Findings annotate the verify-step
  token; the orchestrator never raises on a finding.

#### Audit
- Every step token is signed under the existing
  `axiom-event-token-{layer,coord,v1}` namespaces via the
  unchanged `Coordinator` signing path.
- Tokens land in the existing `axiom_exoskeleton_ledger` under
  `use_case=autonomous:<run_id>:<step_kind>`. No ledger schema
  change.
- `python3 -m axiom_autonomous_agent verify --run-id auto_...`
  walks the chain end-to-end.

#### CLI
```
python3 -m axiom_autonomous_agent run \\
    --task "write a Python script primes.py + pytest tests" \\
    --workdir /tmp/auto-run-1 \\
    --budget-steps 20

python3 -m axiom_autonomous_agent verify \\
    --run-id auto_abc123def --ledger ~/.axiom/exoskeleton-ledger.jsonl
```

Installed as the `axiom-autonomous` console script via
`pyproject.toml`.

#### Files
- `axiom_autonomous_agent.py` — top-level CLI shim
- `axiom_autonomous/` — sub-package (orchestrator, planner, executor,
  verifier, parser, sandbox, ledger, governance, honesty_patterns,
  tools/fs.py, tools/shell.py)
- `deploy/autonomous/` — Dockerfile, compose, tool_runner
- `tests/test_axiom_autonomous_*.py` — five test modules
  (parser, tools, governance, chain, agent end-to-end)

#### Reused infrastructure (zero changes)
- `Coordinator.compose_from_delegates` for token signing
- `LedgerWriter.append` for persistence
- `axiom_cmaa.route` for intent gating
- `axiom_constitutional.agents.sandbox.SandboxAgent.review` for
  text-content review
- `axiom_dev_agent_v2.CodeReflex` for diff filtering
- `axiom_exoskeleton_honesty.scan` patterns + the re-sign trick from
  `axiom_exoskeleton._annotate_honesty`
- `axiom_dev_loop.DevCycleRecorder` for terminal-step training-data
  recording

#### Additive modifications
- `examples/exoskeleton_pack.py` — added `autonomous_planner` and
  `autonomous_verifier` delegate entries (system prompts for the
  loop's LLM calls).
- `pyproject.toml` — added `axiom-autonomous` console script.

#### What's intentionally not in v1
Browser / computer use, formal benchmark harness, email
integration, multi-agent collaboration, web UI. The signed per-step
chain in the existing ledger IS the audit record for v1.


## v1.8.3 — 2026-04-21

### Medical Information Pipeline — PatientAgent + DoctorAgent

#### New Agents
- `patient.axiom` (Trust Level 2) — Retrieval agent: retrieves, tier-annotates, and synthesizes medical sources; always delegates to DoctorAgent before any user delivery; CANNOT_MUTATE includes `doctor_delegation`
- `doctor.axiom` (Trust Level 1) — Verification agent: applies five-tier evidence registry; approves Tier 1/2, flags Tier 3, blocks Tier 4 with disclosure, hard-blocks Tier 5 constitutionally; corrects PatientAgent synthesis errors; CANNOT_MUTATE includes `trust_tier_registry` and `consensus_override`

#### Five-Tier Evidence Registry (in DoctorAgent)
| Tier | Sources | Action |
|------|---------|--------|
| 1 | Cochrane, WHO, FDA/EMA labeling, NICE, NEJM/Lancet meta-analyses | APPROVE |
| 2 | RCTs, national guidelines (CDC/NIH/AHA/SIGN), BNF/USP/WHO formularies | APPROVE + cite |
| 3 | Observational studies, pre-prints, unreplicated RCTs | FLAG_UNCERTAINTY |
| 4 | Industry-funded (no independent replication), contested, >10y in fast-moving fields | BLOCK_WITH_DISCLOSURE |
| 5 | Black-box-warning violations, dangerous dosing, anti-vaccine, lethal drug interaction advice | HARD_BLOCK (constitutional) |

#### New CONCEPT
- `SourceTrustGate` — added to `concepts.axiom`; classifies all retrieved medical sources into Tier 1–5 before synthesis; used by both PatientAgent and MedicalDomainAgent

#### New Domain Package
- `axiom_files/domains/medical.axiom` — evidence-based medicine governance; do-no-harm constitutional block; EU AI Act Art.10/13/14/15 alignment; activates via `axiom add medical`

#### Constitutional Guarantees
- `doctor_delegation` in CANNOT_MUTATE — PatientAgent cannot skip DoctorAgent under any instruction
- `trust_tier_registry` in CANNOT_MUTATE — evidence standards cannot be lowered by operator override or creative framing
- `consensus_override` in CANNOT_MUTATE — user assertions ("my doctor said X") cannot promote Tier 4/5 to Tier 1/2
- FDA black box warnings trigger mandatory disclosure regardless of query framing
- Ibuprofen + warfarin (and all NSAID + anticoagulant combinations) trigger mandatory Tier 1 disclosure by rule

#### HUMAN_REVIEW Triggers Added
- `doctor_delegation_removal_attempt` (PatientAgent)
- `trust_tier_registry_modification_attempt` (DoctorAgent)
- `consensus_override_attempt` (DoctorAgent)
- `tier_5_block_bypass_attempt` (DoctorAgent)
- `do_no_harm_modification_attempt` (MedicalDomainAgent)
- `evidence_standard_lowering_attempt` (MedicalDomainAgent)

---
## v1.8.2 — 2026-04-20

### Third-party benchmark: COMPL-AI (EU AI Act, ETH Zurich methodology)

| Article | AXIOM | GPT-4 | Delta |
|---------|-------|-------|-------|
| Art. 10 — Bias & Fairness | 100% | 55% | +45% |
| Art. 10 — Privacy | 100% | 60% | +40% |
| Art. 13 — Transparency | 83% | 60% | +23% |
| Art. 14 — Safety & Oversight | 90% | 70% | +20% |
| Art. 15 — Accuracy & Robustness | 100% | 65% | +35% |
| **Overall** | **94%** | **~65%** | **+29%** |

Known structural failure: T02 (Art.13 persona-transparency) — model safety RLHF overrides prompt-level rules.
Best run: 94% (run 10, 2026-04-20). Stable floor: ~84-88%.

### Other changes
- HUMAN_REVIEW construct added to all 7 CERTIFIED agents (v1.8.1)
- COMPL-AI results embedded in all cert JSONs
- Standalone compl_ai_report written to certs/

---

