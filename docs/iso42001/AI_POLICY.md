# AI Policy

**ISO/IEC 42001:2023 Clause 5.2 / Annex A.2.2.** Top-level statement of Orivael's
commitment to responsible AI. Draft for leadership adoption — `[ORG]` fields require a
decision and a signature.

- **Owner:** `[ORG] — e.g. Head of AI Governance`
- **Approved by:** `[ORG] — top management`
- **Effective date / version:** `[ORG]`
- **Review cycle:** `[ORG] — at least annually (A.2.4)`

## 1. Purpose & scope

This policy governs how Orivael develops, deploys, and operates AI systems within the AIMS
scope (`[ORG]` define scope, Clause 4.3). It applies to all staff, contractors, and AI
systems in that scope.

## 2. Commitments

Orivael commits to:

1. **Governed-by-default operation.** AI actions are subject to policy enforcement *before*
   they execute, not reviewed after — implemented via the constitutional runtime (intent
   classification, policy gates, immutable `CANNOT_MUTATE` fields).
2. **Human oversight.** High-impact actions require human review and can be halted; oversight
   is auditable. (Annex A.9, A.6.2.6.)
3. **Transparency.** Users are informed when they interact with an AI system, and AI-generated
   content is marked. (Disclosure + content-provenance controls.)
4. **Accountability & traceability.** Every governed decision is signed and recorded in a
   tamper-evident log; authority is explicit, scoped, and revocable. (Annex A.6.2.8.)
5. **Risk- and impact-driven development.** AI risks and impacts on individuals and society
   are assessed before deployment and re-assessed on change. (Annex A.5; the FRIA process.)
6. **Fairness & data governance.** Bias is tested for; data provenance and quality are
   managed; no raw personal data in core logs. (Annex A.7; `AXIOM_DATA_GOVERNANCE.md`.)
7. **Security & integrity.** AI systems, models, and supply chain are integrity-protected
   (signing, hash-chained logs, supply-chain registry).
8. **Legal & regulatory alignment.** Orivael aligns with applicable law including the EU AI
   Act and GDPR. (`EU_AI_ACT_ALIGNMENT.md`.)
9. **Continual improvement.** The AIMS is monitored, audited, and improved. (Clauses 9–10.)

## 3. Roles & responsibilities

`[ORG]` assign: AIMS owner; AI risk owner; human-oversight operators; data governance
owner; incident handler. (Annex A.3.2.)

## 4. Governance of this policy

This policy aligns with Orivael's security, privacy, and HR policies (A.2.3), is reviewed on
the cycle above (A.2.4), and is communicated to all interested parties (A.8.5). Concerns may
be raised via `[ORG]` reporting channel without retaliation (A.3.3).

## 5. Enforcement

Non-adherence is handled per `[ORG]` disciplinary/contractual process. Material AI incidents
are managed under the incident process (A.8.4) and feed corrective action (Clause 10).

---
*Signed:* `[ORG] — name, role, date`
