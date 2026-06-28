# Statement of Applicability (SoA) — ISO/IEC 42001:2023 Annex A

The SoA is the central certification artifact (Clause 6.1.3 d): for **every** Annex A
reference control it records whether the control is *applicable*, its *justification*, and
its *implementation status*. This draft maps each control to the Axiom evidence that
supports it and marks honest gaps.

> Draft for `[ORG]` ratification — not an audit result. Status legend:
> **✅ Implemented** (Axiom control + evidence exists) · **🟡 Partial** (partly built; org
> completes) · **⬜ Org** (organizational/process control the org must establish) ·
> **N/A** (not applicable, with justification). Control titles follow ISO/IEC 42001:2023
> Annex A; confirm exact wording against the standard text.

## A.2 — Policies related to AI

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.2.2 AI policy | Yes | 🟡 | `AI_POLICY.md` drafted; leadership adopts + publishes |
| A.2.3 Alignment with other org policies | Yes | ⬜ | `[ORG]` reconcile with security/privacy/HR policies |
| A.2.4 Review of the AI policy | Yes | ⬜ | `[ORG]` set review cadence + owner |

## A.3 — Internal organization

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.3.2 AI roles & responsibilities | Yes | ⬜ | `[ORG]` define AIMS roles (owner, risk, oversight) |
| A.3.3 Reporting of concerns | Yes | 🟡 | Mechanism exists in runtime (refusal/escalation, `HUMAN_REVIEW`); `[ORG]` add a people-facing reporting channel |

## A.4 — Resources for AI systems

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.4.2 Resource documentation | Yes | 🟡 | Annex IV generator + cert document system resources; `[ORG]` formalize |
| A.4.3 Data resources | Yes | ✅ | `AXIOM_DATA_GOVERNANCE.md`; hash-only logging; provenance |
| A.4.4 Tooling resources | Yes | 🟡 | `.axm` signed model/skill packs; supply-chain SHA-256 registry; `[ORG]` inventory tooling |
| A.4.5 System & computing resources | Yes | ⬜ | `[ORG]` document compute/runtime environment |
| A.4.6 Human resources | Yes | ⬜ | `[ORG]` competence + training records |

## A.5 — Assessing impacts of AI systems

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.5.2 Impact assessment process | Yes | ✅ | `AI_IMPACT_ASSESSMENT_PROCEDURE.md` + FRIA generation (`axiom_certify.py`) |
| A.5.3 Documentation of impact assessments | Yes | ✅ | FRIA artifacts persisted per agent/version (`certs/*_fria_*.json`) |
| A.5.4 Impact on individuals/groups | Yes | ✅ | FRIA enumerates EU-Charter rights; bias/fairness testing (`integrity_check.py`) |
| A.5.5 Societal impacts | Yes | 🟡 | FRIA covers rights impacts; `[ORG]` extend to broader societal scope |

## A.6 — AI system life cycle

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.6.1.2 Objectives for responsible development | Yes | 🟡 | Constitutional objectives (CANNOT_MUTATE, refusal-correctness); `[ORG]` ratify |
| A.6.1.3 Processes for responsible design & development | Yes | ✅ | 6-step certification (`axiom_certify.py`); constitutional spec workflow |
| A.6.2.2 Requirements & specification | Yes | ✅ | `.axiom` constitutional specs define behavior + limits |
| A.6.2.3 Documentation of design & development | Yes | ✅ | Annex IV generator (`axiom_annex_iv.py`); cert manifest |
| A.6.2.4 Verification & validation | Yes | ✅ | Benchmark ≥75%, honesty ≥0.85, fairness ≥0.75 gates; CAS adversarial sandbox |
| A.6.2.5 Deployment | Yes | ✅ | Signed `.axm` containers + attestation; Ed25519 guest-key delegation |
| A.6.2.6 Operation & monitoring | Yes | ✅ | Runtime guard + drift detection (`sovereign/drift_detector.py`) |
| A.6.2.7 Technical documentation | Yes | ✅ | Annex IV generator; `EU_AI_ACT_ALIGNMENT.md` |
| A.6.2.8 Recording of event logs | Yes | ✅ | Hash-chained, HMAC-signed append-only ledgers (`axiom_*_ledger.py`) |

## A.7 — Data for AI systems

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.7.2 Data for development & enhancement | Yes | 🟡 | Internally-authored benchmark/eval data; `[ORG]` document base-model data reliance |
| A.7.3 Acquisition of data | Yes | ⬜ | `[ORG]` document data acquisition + lawful basis |
| A.7.4 Quality of data | Yes | 🟡 | Fairness/bias testing; `[ORG]` add data-quality criteria |
| A.7.5 Data provenance | Yes | ✅ | Signed memory packets + content-provenance marking (`axiom_content_provenance.py`); hash-chained logs |
| A.7.6 Data preparation | Yes | ⬜ | `[ORG]` document preparation/labelling where applicable |

## A.8 — Information for interested parties

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.8.2 System documentation & info for users | Yes | ✅ | `/disclosure` endpoint (Art. 50); DEPLOYER_GUIDE.md |
| A.8.3 External reporting | Yes | ⬜ | `[ORG]` define external reporting (regulators, registries) |
| A.8.4 Communication of incidents | Yes | 🟡 | Drift/incident detection + signed ledger; `[ORG]` define notification process |
| A.8.5 Information for interested parties | Yes | 🟡 | Disclosure + provenance marking; `[ORG]` map all interested parties |

## A.9 — Use of AI systems

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.9.2 Processes for responsible use | Yes | ✅ | Intent gate, policy enforcement, HUMAN_REVIEW gates, monotonic gate |
| A.9.3 Objectives for responsible use | Yes | 🟡 | Constitutional objectives encoded; `[ORG]` ratify use objectives |
| A.9.4 Intended use | Yes | 🟡 | Per-agent intended purpose in spec + FRIA; `[ORG]` confirm per deployment |

## A.10 — Third-party & customer relationships

| Control | Applicable | Status | Axiom evidence / `[ORG]` action |
|---|---|---|---|
| A.10.2 Allocating responsibilities | Yes | ✅ | Provider/deployer split documented (`EU_AI_ACT_ALIGNMENT.md` §3); guest-key delegation binds authority |
| A.10.3 Suppliers | Yes | 🟡 | Supply-chain integrity registry for model/skill packs; `[ORG]` add supplier due-diligence |
| A.10.4 Customers | Yes | 🟡 | Disclosure + deployer guide; `[ORG]` formalize customer governance terms |

## Summary

- **Implemented (✅):** the AI-system lifecycle, impact assessment, event logging, V&V,
  deployment, and responsible-use controls — the technical core — are backed by working,
  signed Axiom evidence.
- **Partial (🟡) / Org (⬜):** the *management-system wrapper* — policy adoption, roles,
  scope, supplier/customer process, training, audit cadence — is organizational work the
  `[ORG]` completes. No control is "not applicable" by default; justify any exclusion here.

*Maintainers: when a control's backing module changes, update its row in the same change.
An SoA that drifts from the system it describes fails its first audit.*
