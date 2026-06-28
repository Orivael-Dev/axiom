# AI System Impact Assessment Procedure

**ISO/IEC 42001:2023 Clause 6.1.4, Annex A.5.** How Orivael assesses the potential impacts
of AI systems on individuals, groups, and society. Draft procedure — `[ORG]` ratifies the
trigger criteria and sign-off authority.

- **Owner:** `[ORG] — AI governance owner`
- **Instrument:** the FRIA generator (`axiom_certify.py:generate_fria`) produces the
  assessment skeleton; this procedure governs *when* and *how* it is completed and approved.

## 1. When an impact assessment is required

Conduct (or refresh) an assessment when an AI system is:
- newly developed or materially changed in purpose, model, or data;
- deployed into a new context or population;
- classified as, or near, high-risk (EU AI Act Annex III) — see `EU_AI_ACT_ALIGNMENT.md`;
- flagged by the risk procedure as potentially affecting rights, safety, or fairness.

## 2. Process

1. **Generate the assessment** — run the certifier to emit a FRIA pre-filled from the
   system spec: intended purpose, risk classification (Annex III lookup), affected
   fundamental rights (EU Charter), technical mitigations, and monitoring paths.
2. **Assess impacts on individuals/groups** (A.5.4) — for each right (dignity, privacy,
   non-discrimination, expression, remedy, presumption of innocence), record inherent
   impact, mitigations in place, and **residual risk**. Use fairness/bias test results
   (`integrity_check.py`) as evidence for non-discrimination.
3. **Assess societal impacts** (A.5.5) — `[ORG]` extend beyond individual rights to
   broader societal effects relevant to the use case (labor, access, information integrity).
4. **Determine acceptability** — `[ORG]` sign-off authority accepts the residual impact or
   requires further mitigation (loops back to the risk procedure).
5. **Document & retain** — the completed, signed FRIA is the record (A.5.3), persisted with
   the system version (`certs/*_fria_*.json`) and retained per `[ORG]` policy.
6. **Review** — re-assess on the triggers in §1, and at least `[ORG]` (e.g. annually).

## 3. What Axiom pre-fills vs what `[ORG]` completes

| Pre-filled by Axiom | `[ORG]` completes |
|---|---|
| System description, intended purpose | Confirm accuracy for the deployment context |
| Risk classification (Annex III lookup) | Confirm the classification |
| Fundamental-rights list + mitigations | Residual-risk rating per right |
| Technical mitigations (gates, logging) | Operator, escalation contact, response SLA |
| Monitoring paths (ledgers) | Retention policy; deployment-specific residual risks |
| — | Societal-impact assessment; signed attestation |

## 4. Outputs

- Completed, signed FRIA per system/version (A.5.2, A.5.3)
- Inputs to the risk register and the Statement of Applicability
- Evidence for EU AI Act Art. 27 (the FRIA doubles as the Art. 27 instrument)

## 5. Integration

This procedure is the impact-assessment half of planning (Clause 6.1); the **AI Risk
Assessment & Treatment Procedure** is the risk half. Together they drive control selection
recorded in the **Statement of Applicability**.
