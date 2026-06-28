# EU AI Act — Alignment & Readiness Map

**Regulation (EU) 2024/1689 (the "AI Act").** This document maps Orivael Axiom
Infrastructure's controls to the Act's obligations, article by article, and states
honestly what Axiom provides versus what a deployer must complete.

> **This is an engineering-readiness artifact, not legal advice and not a
> certification.** Conformity under the AI Act attaches to a *deployed AI system at a
> given risk class*, determined by the provider/deployer's use case and — for high-risk
> systems — an Annex VI/VII conformity assessment. Whether any deployment is "compliant"
> is a legal determination requiring qualified counsel (e.g. an ISO/IEC 42001 lead
> auditor). Axiom reduces that work; it does not replace it. Do not represent Axiom as
> "EU AI Act compliant" in the abstract — claim only what this map substantiates:
> Axiom is a **compliance enabler** that supplies the technical controls the Act
> requires.

## 1. Axiom's role under the Act

The Act assigns obligations by **actor** and by **risk class**. Axiom's position:

- **Axiom is a governance/oversight tool used *inside* an AI system.** It is not itself
  a general-purpose AI model, so **GPAI provider duties (Art. 53–55) do not apply to
  Axiom** — they apply to the base-model provider (e.g. the NIM/OpenAI/Llama endpoint
  the deployer points at).
- **Whether the deployed system is "high-risk" (Annex III) is the deployer's
  determination.** Axiom does not decide risk class; it generates the *evidence* a
  high-risk classification then demands (logging, oversight, FRIA, technical docs).
- For most obligations, Axiom is the **provider of the controls** and the deployer is
  the **operator who configures and attests** them. The split is made explicit per
  article below and in §3.

## 2. Per-article alignment matrix

Status legend: **Supported** = control implemented in Axiom · **Partial** = control
exists but deployer must complete it · **Deployer** = Axiom supplies template/checklist,
execution is the deployer's · **Out of scope** = obligation falls on another actor.

| Art. | Obligation | Status | Axiom control (file) | Deployer must add |
|---|---|---|---|---|
| 5 | Prohibited practices | Deployer | `DEPLOYER_GUIDE.md` lists prohibited uses; intent gate blocks `HARM`/`DECEIVE` classes (`axiom_intent_classifier.py`) | Confirm the use case is not prohibited |
| 9 | Risk-management system | **Partial** | Constitutional constraints + 7 `HUMAN_REVIEW` triggers + honesty gate ≥0.85 (`axiom_certify.py` steps 2–6) | Formal risk taxonomy + residual-risk identification (in FRIA) |
| 10 | Data & data governance | **Supported** | `AXIOM_DATA_GOVERNANCE.md`; teacher–student fairness + demographic-variant testing (`axiom/integrity_check.py`); `fairness_ledger.jsonl`; hash-only logging (no raw PII) | Data-subject rights, retention, cross-border assessment (doc §§7–9) |
| 11 + Annex IV | Technical documentation | **Supported** | 6-step certification (`axiom_certify.py`) + **Annex IV generator** (`axiom_annex_iv.py`) — assembles all 9 Annex IV sections, pre-fills every Axiom-substantiated item, signs the pack | Complete the `[DEPLOYER]` items (I/O specs, declaration of conformity, post-market plan) |
| 12 | Record-keeping / logging | **Supported** | Four append-only HMAC-signed ledgers: `axiom_audit_ledger.py`, `axiom_exoskeleton_ledger.py`, `axiom_autonomous/ledger.py`, `axiom_medical_ledger.py`; per-interaction compliance manifest | Retention policy (≥ the Act's minimum; GDPR Art. 5(1)(e)) |
| 13 | Transparency to deployers | **Supported** | `GET /disclosure` (`axiom_server.py`); OWASP coverage manifest (`axiom_agentic_compliance.py`, 89%); FRIA `system_description` | Deployment-specific capabilities/limitations |
| 14 | Human oversight | **Supported** | 7 `HUMAN_REVIEW` triggers, 24h block-on-timeout, review queue (`axiom_files/parser.py`, `axiom_review.py`); drift escalation (`sovereign/drift_detector.py`) | Name operator/team; define response SLA |
| 15 | Accuracy, robustness, cybersecurity | **Supported** | Benchmark gate ≥75%, honesty ≥0.85, fairness ≥0.75; 4-layer injection defence; DoS limiter; HMAC signing; supply-chain SHA-256 | Periodic re-certification; deployment-env hardening |
| 25 | Responsibilities along the value chain | **Supported** | Supply-chain hash registry + tamper detection (`axiom_certify.py` step 1) | Verify hashes; retain signed cert PDFs |
| 26 | Deployer obligations | **Deployer** | Pre-deployment checklist + templates (`DEPLOYER_GUIDE.md`) | Execute all deployer steps |
| 27 | Fundamental Rights Impact Assessment | **Supported** | **Auto-generated FRIA** on every cert run — 6 EU-Charter rights, Annex III mapping, technical mitigations, monitoring paths (`axiom_certify.py:generate_fria`) | Complete residual-risk, escalation, SLA, signed attestation fields |
| 50 | Transparency to natural persons | **Supported** | `GET /disclosure` (notice, capabilities, limits, rights) + acknowledgement tracking (`axiom_server.py`); **synthetic-content marking** — human-readable footer + signed machine-readable provenance tag, content- and tag-tamper-evident (`axiom_content_provenance.py`) | Show disclosure before interaction; apply the marker to generated output |
| 53–55 | GPAI provider obligations | **Out of scope** | n/a — Axiom is not a model provider | Ensure the base-model provider complies |

## 3. The provider / deployer split (do not blur this)

**Axiom provides out of the box:** signed, append-only audit logs (Art. 12); human-
oversight gates with audit trail (Art. 14); auto-generated FRIA template (Art. 27); an
AI-disclosure endpoint **and a signed synthetic-content marker** (Art. 50/13); a
data-governance statement and bias testing (Art. 10); robustness/cybersecurity controls
(Art. 15); a supply-chain integrity registry (Art. 25).

**The deployer must still:** confirm the use case is permitted (Art. 5) and its risk
class (Annex III); complete the FRIA placeholders and sign it (Art. 27); set a log-
retention policy (Art. 12); implement data-subject rights (Art. 10 / GDPR); name the
human overseer and SLA (Art. 14); show the disclosure to users and **apply** the
synthetic-content marker to outputs (Art. 50 — the marker ships in
`axiom_content_provenance.py`); and, for high-risk systems, obtain the Annex VI/VII conformity
assessment. None of these can be satisfied by Axiom alone.

## 4. Known gaps (honest remediation backlog)

These are obligations the Act raises that Axiom does **not** yet fully implement.
Listing them is itself part of being "in line" — silent gaps are the liability.

| Gap | Article | Current state | Proposed close |
|---|---|---|---|
| ~~Synthetic-content marking~~ ✅ closed | 50(2) | **Done** — `axiom_content_provenance.py`: human-readable AI-disclosure footer + signed, machine-readable provenance tag; `verify()` detects content tampering and tag forgery | Wire `mark()` into the server response path (one call after `OutputShaper`) |
| Formal risk taxonomy | 9 | Controls exist; no structured risk register | Ship a risk-register template + a `risk` section the certifier fills |
| ~~Annex IV doc generator~~ ✅ closed | 11 | **Done** — `axiom_annex_iv.py` assembles the 9-section Annex IV pack from system metadata + cert/FRIA, pre-fills Axiom-substantiated items, marks `[DEPLOYER]` placeholders, signs the output | Complete the deployer items + conformity assessment |
| Data-subject-rights hooks | 10 / GDPR | Deployer-layer only | Reference adapters for access/erasure/portability against the ledgers |
| Semantic fairness scoring | 10(3) | Length + disparagement signals only | Cosine-similarity fairness scoring (was tracked for a later release) |

## 5. How to produce evidence today

```bash
# 1. Certify an agent → emits cert + FRIA + signed PDF (Art. 9/11/15/27)
python axiom_certify.py --agent <name> --domain <healthcare|finance|government|general>
#    → certs/<name>_cert_<ts>.json   (technical documentation)
#    → certs/<name>_fria_<ts>.json   (FRIA template to complete + sign)
#    → certs/<name>_cert_<ts>.pdf    (auditor-facing summary)

# 2. Serve the Art. 50 / Art. 13 disclosure
export AXIOM_DEPLOYER_NAME=... AXIOM_DEPLOYER_CONTACT=... AXIOM_DEPLOYER_JURISDICTION=...
#    GET /disclosure  → must be acknowledged before /run_axiom

# 3. Art. 12 record-keeping is automatic — every decision is HMAC-signed and appended:
#    axiom_files/.history/  .reviews/  .honesty/  .dos/   + the *_ledger.py ledgers

# 4. Art. 50 synthetic-content marking — mark generated text, verify it later:
echo "<ai output>" | python axiom_content_provenance.py mark --deployer "<you>" --model <id>
python axiom_content_provenance.py verify --file marked.txt   # VALID / CONTENT_ALTERED / SIG_INVALID

# 5. Art. 11 / Annex IV — assemble the technical-documentation pack (signed):
python axiom_annex_iv.py generate --provider "<you>" --purpose "<purpose>" \
    --cert certs/<agent>_cert.json --fria certs/<agent>_fria.json --out annex_iv.md
```

The Annex IV generator stitches the rest together: it pre-fills from the signed cert +
FRIA, `AXIOM_DATA_GOVERNANCE.md`, and the runtime controls, leaving the deployer only the
`[DEPLOYER]` items (declaration of conformity, harmonised standards, post-market plan) and
the audit-ledger export for the review period.

## 6. References

- Regulation (EU) 2024/1689 (AI Act); Charter of Fundamental Rights (2000/C 364/01); GDPR (EU) 2016/679.
- In-repo: `DEPLOYER_GUIDE.md`, `AXIOM_DATA_GOVERNANCE.md`, `axiom_certify.py`,
  `axiom_agentic_compliance_manifest.json`, `packs/gdpr-article-9/`.

*Maintainers: keep this map honest. If a control is removed or a claim weakens, update
the status here in the same change — an alignment map that drifts from the code is worse
than none.*
