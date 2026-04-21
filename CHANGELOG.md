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

