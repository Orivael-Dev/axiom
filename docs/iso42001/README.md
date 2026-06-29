# ISO/IEC 42001 — AI Management System (AIMS) Readiness Pack

This directory is the starting AIMS document set for **ISO/IEC 42001:2023** certification
of Orivael as an organization. It turns "how do we get certified" into a checklist a lead
implementer or Stage-1 auditor can pick up.

> **What ISO 42001 certifies:** the *organization's AI management system* — not the Axiom
> product. Axiom is *evidence inside* that system. A certificate only comes from an
> **accredited certification body** auditing a real, operating AIMS. These documents are a
> readiness scaffold, not a certificate, and not audit/legal advice. `[ORG]` placeholders
> mark what Orivael must decide or fill in.

## Contents

| File | Purpose | ISO 42001 reference |
|---|---|---|
| `STATEMENT_OF_APPLICABILITY.md` | Every Annex A control → applicability → Axiom evidence → gap | Clause 6.1.3, Annex A |
| `AI_POLICY.md` | The AI policy (top-level commitment) | Clause 5.2, A.2 |
| `AI_RISK_ASSESSMENT_PROCEDURE.md` | How AI risks are assessed and treated | Clause 6.1, A.5 |
| `AI_IMPACT_ASSESSMENT_PROCEDURE.md` | How AI system impacts on people/society are assessed | Clause 6.1.4, A.5 |

## The certification path (where these fit)

1. **Scope** the AIMS — `[ORG]` decides which entities/AI systems are in scope (Clause 4.3).
2. **Gap analysis** — the SoA below is the gap analysis against Annex A.
3. **Build the AIMS** — these documents + the management-system clauses (4–10).
4. **Operate & generate evidence** — run it ~2–3 months so records exist (logs, risk
   reviews, impact assessments). Axiom produces most of this automatically.
5. **Internal audit + management review** (Clause 9).
6. **Accredited certification body** — Stage 1 (docs) → Stage 2 (effectiveness).
7. **Maintain** — annual surveillance, 3-yearly recertification.

## Management-system clauses (4–10) — readiness

The SoA covers Annex A controls; an auditor *also* checks Clauses 4–10. Status of each:

| Clause | Requirement | Status | Note |
|---|---|---|---|
| 4 Context | Org context, interested parties, AIMS scope | ⬜ `[ORG]` | Decide scope + interested parties |
| 5 Leadership | Leadership commitment, **AI policy**, roles | 🟡 Partial | Policy drafted (`AI_POLICY.md`); leadership must adopt |
| 6 Planning | **Risk assessment**, **impact assessment**, objectives, SoA | 🟡 Partial | Procedures + SoA drafted; `[ORG]` ratifies |
| 7 Support | Resources, competence, awareness, documented info | ⬜ `[ORG]` | Assign roles, training, doc control |
| 8 Operation | Operational planning, risk treatment, impact assessments | ✅ Strong | Axiom runtime *is* the operational control surface |
| 9 Performance | Monitoring, internal audit, management review | 🟡 Partial | Audit ledgers give monitoring data; audit/review cadence is `[ORG]` |
| 10 Improvement | Nonconformity, corrective action, continual improvement | 🟡 Partial | Drift/incident detection feeds this; process is `[ORG]` |

## Why Orivael starts far ahead

Because Axiom is itself a governance runtime, much of the operational evidence ISO 42001
asks for already exists and is signed: AI impact assessments (FRIA), risk controls
(constitutional gates, human review), event logging (hash-chained ledgers), technical
documentation (Annex IV generator), data governance, transparency. The remaining work is
the *management-system wrapper* — policy, scope, roles, internal audit, and operating
records over time — which is organizational, not technical.

## Strategic note

ISO 42001 and the EU AI Act reinforce each other: 42001 is increasingly how organizations
*demonstrate* the governance the AI Act requires. See `../../EU_AI_ACT_ALIGNMENT.md`. Once
Orivael is certified, "we run a certified AIMS, and here's the tooling that produces the
evidence" becomes both a trust signal and a product the same controls sell to customers.
