# AI Risk Assessment & Treatment Procedure

**ISO/IEC 42001:2023 Clauses 6.1.2–6.1.3, Annex A.5.** How Orivael identifies, analyzes,
evaluates, and treats risks related to AI systems. Draft procedure — `[ORG]` ratifies
thresholds, owners, and cadence.

- **Owner:** `[ORG] — AI risk owner`
- **Inputs:** AI system spec (`.axiom`), intended purpose, deployment context, FRIA.
- **Cadence:** before deployment, on material change, and at least `[ORG]` (e.g. annually).

## 1. Scope of risk

Assess risks to: individuals' rights and safety; groups/society; the organization; and the
AI system itself (security, robustness, drift). Risk sources to consider include those in
ISO/IEC 42001 Annex C (e.g. lack of transparency, automation bias, data quality, misuse,
security, unintended outcomes).

## 2. Process

1. **Identify** — for each in-scope AI system, enumerate risk scenarios across the lifecycle
   (design, data, deployment, operation, decommission). Seed from the FRIA fundamental-rights
   list and the constitutional spec's prohibited behaviors.
2. **Analyze** — rate each risk by **likelihood × severity** on the `[ORG]` scale. Use
   operational evidence where available: drift metrics (`constitutional_distance`), honesty/
   fairness rates, refusal/denial logs, adversarial-sandbox (CAS) findings.
3. **Evaluate** — compare against `[ORG]` risk-acceptance criteria. Anything above tolerance
   requires treatment before deployment.
4. **Treat** — select treatment and map to a control. Axiom control surface includes:
   - Policy enforcement / refusal (intent gate, `CANNOT_MUTATE`)
   - Human oversight gates (`HUMAN_REVIEW`, 7 triggers, block-on-timeout)
   - Monitoring + drift detection + signed event logs
   - Scoped, expiring, revocable authority (guest-key delegation)
   - Adversarial regression (CAS) for residual-risk reduction
   Treatments map to the **Statement of Applicability** Annex A controls.
5. **Record & accept** — document the residual risk; `[ORG]` risk owner formally accepts or
   rejects. Record in the risk register (`[ORG]` location).
6. **Monitor** — operational ledgers + drift detection continuously surface risk signals;
   threshold breaches reopen the risk (Clause 9 / 10).

## 3. Relationship to impact assessment

Where a risk concerns impacts on people or society, the **AI System Impact Assessment
Procedure** (FRIA) is the deeper instrument; this procedure references its output rather
than duplicating it.

## 4. Outputs

- AI risk register (per system, versioned)
- Updated Statement of Applicability (controls selected as treatments)
- Residual-risk acceptance records, signed by the risk owner

## 5. Records & retention

Risk registers and acceptance records are retained per `[ORG]` retention policy and are
auditable evidence for Clauses 6, 8, and 9.
