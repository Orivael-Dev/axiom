# AXIOM Deployer Guide
**v1.8 — EU AI Act & OWASP LLM Top-10 Compliance Reference**

This guide is for organisations deploying AXIOM in production. It covers what the
framework provides out of the box, what you must configure, and what you must
complete yourself before deploying in a regulated context.

---

## Contents

1. [What AXIOM ships with](#1-what-axiom-ships-with)
2. [Quick start](#2-quick-start)
3. [Configuration reference](#3-configuration-reference)
4. [EU AI Act obligations](#4-eu-ai-act-obligations)
5. [Article 50 — User disclosure](#5-article-50--user-disclosure)
6. [Article 27 — FRIA template](#6-article-27--fria-template)
7. [HUMAN_REVIEW gate — ops guide](#7-human_review-gate--ops-guide)
8. [Certification — what the output means](#8-certification--what-the-output-means)
9. [Monitoring and audit logs](#9-monitoring-and-audit-logs)
10. [Domain governance packages](#10-domain-governance-packages)
11. [OWASP LLM Top-10 coverage](#11-owasp-llm-top-10-coverage)
12. [Prohibited uses](#12-prohibited-uses)
13. [Pre-deployment checklist](#13-pre-deployment-checklist)

---

## 1. What AXIOM ships with

AXIOM is a constitutional AI agent framework. Every agent is defined by a `.axiom`
spec file. The runtime enforces the spec — it does not interpret it loosely.

**Out of the box (no configuration required):**

| Capability | Implementation |
|---|---|
| 4-layer injection defence | Constitutional suffix, output validation, SandboxContent, SandboxAgent |
| Constitutional immutability | `CANNOT_MUTATE` enforced at file-save layer — `AxiomConstitutionalViolation` on violation |
| Human review gate | `HUMAN_REVIEW` block — 7 trigger conditions, append-only review queue |
| Rate limiting + circuit breaker | `DosWatcher` — per-minute/hour/session limits, burst protection, replay detection |
| Supply chain integrity | SHA-256 hash per agent, verified on every cert run |
| Honesty evaluation | `TeacherAgent` — independent expected-response generation, ledger-based integrity rate |
| Audit trail | Append-only `.history/`, `.dos/`, `.reviews/`, `.honesty/` logs |
| EU AI Act Article 50 disclosure | `GET /disclosure` endpoint — machine + human readable |
| FRIA template | Auto-generated on every `axiom_certify.py` run |
| 7/7 agents CERTIFIED | `worker`, `evaluator`, `rewriter`, `sandbox`, + 3 domain agents |

**What you must add:**

- Your API key and model endpoint (`AXIOM_API_KEY`, `AXIOM_BASE_URL`)
- Your deployer identity (`AXIOM_DEPLOYER_NAME`, `AXIOM_DEPLOYER_CONTACT`)
- FRIA completion — fill in PLACEHOLDER fields and obtain sign-off
- Residual risk assessment for your specific deployment context
- Data retention policy and GDPR Article 5(1)(e) compliance statement
- Operator escalation contact for HUMAN_REVIEW notifications

---

## AXIOM EqualDepthGuarantee — Design Commitment

Wealth, professional knowledge, and institutional guidance have historically been distributed unequally across demographic groups. AI agents trained on that historical data reproduce those inequalities by default — giving shorter, less complete advice when names, pronouns, or locations pattern-match to underrepresented groups in the training corpus.

AXIOM's EqualDepthGuarantee treats the highest quality guidance the model can provide as the baseline for everyone — not a privilege for some.

**The information ceiling for any group becomes the information floor for all groups.**

This is not a technical adjustment. It is a design commitment — that AI agents governed by AXIOM will actively bridge information access gaps rather than replicate them.

Implementation:
- `EqualDepthGuarantee` CONCEPT — activated on all professional advice tasks
- RULES block — explicit zero-weight instruction for demographic markers on response calibration
- CONSTRAINT — 15% variance threshold enforced
- Fairness evaluation — teacher-student system detects violations and logs them to the fairness ledger

This principle is constitutionally enforced. It cannot be removed by the evolution loop without triggering HUMAN_REVIEW.

---

## 2. Quick start

### Install

```bash
pip install -r requirements.txt
```

### Configure `.env`

```ini
# Required
AXIOM_API_KEY=your_api_key_here
AXIOM_BASE_URL=https://integrate.api.nvidia.com/v1   # or Ollama, OpenAI, vLLM

# Strongly recommended for compliance
AXIOM_DEPLOYER_NAME=Your Organisation Name
AXIOM_DEPLOYER_CONTACT=compliance@yourorg.example.com
AXIOM_DEPLOYER_JURISDICTION=EU

# Optional
AXIOM_MODEL=meta/llama-3.3-70b-instruct
AXIOM_HOST=0.0.0.0
AXIOM_PORT=8000
AXIOM_LAN_ONLY=1        # restrict to LAN clients only
AXIOM_LAN_PREFIX=192.168.
```

### Start the server

```bash
python axiom_server.py
```

Server prints:

```
  AXIOM Server v1.8.0
  Listening on http://0.0.0.0:8000
  Project root: /path/to/axiom
  Docs:         http://localhost:8000/docs
  Disclosure:   http://localhost:8000/disclosure  (EU AI Act Article 50)
```

### Run certification

```bash
python axiom_certify.py --agent worker
python axiom_certify.py --agent domains/healthcare --domain healthcare
python axiom_certify.py --all
```

Outputs per run (in `certs/`):
- `worker_cert_*.json` — machine-readable certification report
- `worker_fria_*.json` — FRIA template (you must complete PLACEHOLDER fields)
- `worker_cert_*.pdf` — two-page PDF: certification + FRIA summary

---

## 3. Configuration reference

### Required

| Variable | Description |
|---|---|
| `AXIOM_API_KEY` | API key for the model endpoint. Also accepts `NVIDIA_API_KEY` or `OPENAI_API_KEY`. |
| `AXIOM_BASE_URL` | Model endpoint base URL. Defaults to `https://api.openai.com/v1`. |

### EU AI Act (strongly recommended)

| Variable | Description | Default |
|---|---|---|
| `AXIOM_DEPLOYER_NAME` | Your organisation name. Appears in `/disclosure` and FRIA. | `AXIOM Operator` |
| `AXIOM_DEPLOYER_CONTACT` | Compliance contact email. | `operator@example.com` |
| `AXIOM_DEPLOYER_JURISDICTION` | Jurisdiction (e.g. `EU`, `UK`, `US`). | `EU` |

### Runtime

| Variable | Description | Default |
|---|---|---|
| `AXIOM_MODEL` | Model identifier. | `meta/llama-3.3-70b-instruct` |
| `AXIOM_HOST` | Server bind address. | `0.0.0.0` |
| `AXIOM_PORT` | Server port. | `8000` |
| `AXIOM_LAN_ONLY` | `1` to reject requests from outside `AXIOM_LAN_PREFIX`. | `0` |
| `AXIOM_LAN_PREFIX` | IP prefix for LAN-only mode. | `192.168.` |
| `AXIOM_CALL_DELAY` | Seconds to wait between model calls. | `3` |
| `AXIOM_FILES_DIR` | Path to `axiom_files/` directory. | `axiom_files` |
| `AXIOM_LAB_RESULTS` | Path to benchmark results directory. | `~/Desktop/ax/axiom_lab/results/domains` |

---

## 4. EU AI Act obligations

AXIOM implements the technical controls required by the EU AI Act. The table below
maps Act obligations to framework features and identifies what remains for you.

| Obligation | Article | AXIOM provides | Deployer must add |
|---|---|---|---|
| User disclosure | Art. 50 | `GET /disclosure` endpoint, `disclosure_acknowledged` field in API | Show disclosure before first user interaction; configure deployer env vars |
| FRIA | Art. 27 | Auto-generated template on every cert run | Complete PLACEHOLDER fields; obtain sign-off; store signed copy |
| Human oversight | Art. 14 | HUMAN_REVIEW gate — 7 triggers, block-on-timeout | Name the operator/team; define SLA for PENDING reviews |
| Transparency | Art. 13 | `/disclosure` capabilities + limitations; FRIA system_description | Add deployment-specific context to FRIA system_description |
| Accuracy & robustness | Art. 15 | 4-layer injection defence; 99% benchmark score; honesty rate 1.0 | Run integrity_check.py before deployment; schedule periodic re-runs |
| Audit logging | Art. 12 | 4 append-only logs (history, DoS, reviews, honesty) | Define retention policy; comply with GDPR Art. 5(1)(e) |
| Supply chain | Art. 25 | SHA-256 per agent; TAMPERED status on cert | Verify hashes before deployment; maintain signed cert PDFs |
| Prohibited uses | Art. 5 | See Section 12 | Do not deploy for prohibited use cases |

**Risk classification:** AXIOM does not classify its own deployment risk — that depends
on how you use it. Run `axiom_certify.py --domain healthcare` (or `government`,
`finance`) to get the correct Annex III classification pre-filled in the FRIA.

---

## 5. Article 50 — User disclosure

### What the endpoint returns

```http
GET /disclosure
```

```json
{
  "eu_ai_act_article": "50",
  "disclosure_version": "1.0",
  "system_name": "AXIOM",
  "deployer": {
    "name": "Your Organisation",
    "contact": "compliance@yourorg.example.com",
    "jurisdiction": "EU"
  },
  "notice": "You are interacting with AXIOM, an AI system...",
  "capabilities": [...],
  "limitations": [...],
  "user_rights": [...],
  "compliance": { "eu_ai_act_article_50": "compliant", "axiom_certified": true }
}
```

### Android / mobile integration

1. On session start: `GET /disclosure`
2. Display the `notice` field to the user with an "I understand" button
3. On acknowledgement: set `disclosure_acknowledged: true` in all subsequent `/run_axiom` calls
4. If `disclosure_acknowledged` is `false`, the response includes `disclosure_warning`
   (the request is not blocked — that is a deployer UX decision)

### Customising the notice

Set the deployer env vars (see Section 3). The `notice`, `capabilities`,
`limitations`, and `user_rights` fields are framework-defined — edit
`axiom_server.py::_build_disclosure()` if your deployment requires different text,
and document the change in your FRIA.

---

## 6. Article 27 — FRIA template

Every `axiom_certify.py` run writes `{agent}_fria_{ts}.json`. This is a
pre-filled template. You must complete it before deploying in a regulated context.

### What is pre-filled

- System description (purpose, domain, trust_level, decision_type)
- Risk classification (EU AI Act Annex III item and category)
- 6 EU Charter rights with inherent impact and AXIOM mitigations
- Technical mitigations (CANNOT_MUTATE, security layers, HUMAN_REVIEW, DoS, supply chain)
- Monitoring and logging paths
- Regulatory references (Articles 9, 10, 13, 27, 50; EU Charter; GDPR)

### What you must complete

All fields marked `PLACEHOLDER`:

| Field | Where | What to write |
|---|---|---|
| `residual_risk` | Each rights assessment entry | Your assessment of residual risk after AXIOM mitigations |
| `residual_risks[]` | Top-level array | Deployment-specific risks not covered by the framework |
| `human_oversight.operator_escalation` | `human_oversight` | Name and contact of responsible person/team |
| `human_oversight.response_sla` | `human_oversight` | How quickly PENDING reviews will be actioned |
| `monitoring_and_logging.deployer_note` | `monitoring_and_logging` | Your retention policy and GDPR compliance statement |
| `deployer_attestation.*` | All five fields | Organisation, name+role, date, review period, signature |

### Filing the signed copy

Store the signed FRIA alongside the cert PDF. Both should be version-controlled and
linked to the agent version and manifest hash they were generated against. On material
change to the agent, regenerate and re-sign.

---

## 7. HUMAN_REVIEW gate — ops guide

The HUMAN_REVIEW gate fires before any `.axiom` file save that triggers a risk
condition. When it fires, `save_axiom()` raises `HumanReviewRequired` and writes
an entry to `axiom_files/.reviews/review_queue.jsonl` — the save does not proceed.

### Trigger conditions

| Trigger | Risk | What causes it |
|---|---|---|
| `security_modification` | HIGH | Any change to the SECURITY block |
| `trust_level_change` | HIGH | TRUST_LEVEL changed up or down |
| `semantic_drift` | MEDIUM | Vocabulary overlap with original < 80% |
| `bulk_constraint_change` | MEDIUM | More than 3 constraints changed in one save |
| `external_agent_import` | HIGH | Agent file not in the supply chain registry |
| `score_below_snapshot` | MEDIUM | Benchmark score below best recorded, with pending rewrite |
| `cannot_mutate_expansion` | LOW | New field added to CANNOT_MUTATE |

### Review queue operations

```bash
# List pending reviews
python axiom_review.py list

# Inspect a review (full diff + hashes + recommendation)
python axiom_review.py show RVW-A4F2B1

# Approve (unblocks the save)
python axiom_review.py approve RVW-A4F2B1 --reason "Reviewed and approved by CISO"

# Reject
python axiom_review.py reject RVW-A4F2B1 --reason "Security regression — reverting"
```

### Timeout behaviour

The `timeout: 24h` and `block_on_timeout: true` settings in the agent spec mean:

- A review that has been PENDING for more than 24 hours **cannot be approved**.
- The initiating save must be retried (the FRIA must be re-evaluated after 24h have passed).
- This prevents stale approvals — a CISO reviewing from their phone approves the diff
  they see at that moment, not a diff that may have accumulated further changes.

### What you must configure

- Set `escalate_to: operator_email` in your agent `.axiom` files to a real address.
  Currently set to the literal string `operator_email` — replace with your contact.
- Define an SLA for responding to PENDING reviews. The `block_on_timeout: true`
  setting means unreviewed changes are permanently blocked after 24h.

---

## 8. Certification — what the output means

```bash
python axiom_certify.py --agent worker --output certs/
```

### Conformance levels

| Level | Requirements |
|---|---|
| `NON-CONFORMANT` | Structural validation failed |
| `BASIC` | Step 1 (structural) passes |
| `STANDARD` | BASIC + security stack declared + CANNOT_MUTATE present + manifest |
| `CERTIFIED` | STANDARD + benchmark evidence + audit trail + honesty_rate ≥ 0.85 + manifest signed |

### The 7 steps

| Step | Name | What it checks |
|---|---|---|
| 1 | Structural Validation | Phases 1–5 validator + SHA-256 supply chain |
| 2 | Security Stack Audit | Injection detection, HighRiskInput WHEN, Sandbox routing, CANNOT_MUTATE, trust level, runtime layers |
| 3 | Benchmark Evidence | Most recent benchmark results ≥ 75% |
| 4 | Constitutional Integrity | CANNOT_MUTATE covers critical fields; file hash |
| 5 | Audit Trail | History log present and non-empty |
| 6 | Honesty Integrity | Latest run honesty_rate ≥ 0.85 (from TeacherAgent) |
| 7 | Manifest Signature | SHA-256 of all step results + content hash |

### Re-certifying after change

Any material change to a `.axiom` file changes its SHA-256 and invalidates the
previous cert. Re-run `axiom_certify.py` before deploying the updated agent.
The FRIA should also be regenerated and re-signed if the change affects:
- Purpose, capabilities, or limitations
- CANNOT_MUTATE fields
- SECURITY rules
- HUMAN_REVIEW triggers

---

## 9. Monitoring and audit logs

All logs are append-only. Never delete or truncate them — this would constitute a
compliance gap under EU AI Act Article 12 and GDPR Article 5(1)(f).

| Log | Path | Content |
|---|---|---|
| Agent history | `axiom_files/.history/{agent}_history.jsonl` | Every `.axiom` field change with before/after diff |
| DoS / rate limit | `axiom_files/.dos/dos_log.jsonl` | Every BLOCK decision + CIRCUIT_TRIPPED event |
| Review queue | `axiom_files/.reviews/review_queue.jsonl` | Every HUMAN_REVIEW trigger, approval, and rejection |
| Honesty ledger | `axiom_files/.honesty/honesty_ledger.jsonl` | Every TeacherAgent integrity evaluation |
| Supply chain | `axiom_files/.chain/supply_chain.json` | SHA-256 registry for all agents |

### Retention

You must define a retention policy that complies with GDPR Article 5(1)(e)
(data not kept longer than necessary). Log lines contain no personal data by default
— they record agent state changes, rate limit decisions, and integrity scores.
If your deployment logs user inputs, personal data obligations apply.

### Log tampering detection

The `DosWatcher` and `TeacherAgent` use append-only writes with `mode="a"`.
If you detect a log file has been truncated or rewritten, treat this as a security
incident and report it via your incident response process.

---

## 10. Domain governance packages

Three domain agents ship with AXIOM. Each is CERTIFIED and scores 100% on its
benchmark suite.

| Agent | File | Frameworks | Tests |
|---|---|---|---|
| Government | `axiom_files/domains/government.axiom` | FedRAMP, NIST 800-53, FISMA, Privacy Act | 29/29 (100%) |
| Finance | `axiom_files/domains/finance.axiom` | FINRA, SOX, Dodd-Frank, AML/BSA, Basel III | 14/14 (100%) |
| Healthcare | `axiom_files/domains/healthcare.axiom` | HIPAA, HITECH, 45 CFR 164 | 21/21 (100%) |

### Using a domain agent

```python
from axiom_files.parser import get_prompt_with_when
system_prompt = get_prompt_with_when("domains/healthcare", task)
```

Or via the server:

```http
POST /run_axiom
{ "prompt": "...", "agent": "domains/healthcare", "disclosure_acknowledged": true }
```

### Certifying with a domain

```bash
python axiom_certify.py --agent domains/healthcare --domain healthcare
```

This pre-fills the FRIA with Annex III item 5(a) (HIGH risk) automatically.

---

## 11. OWASP LLM Top-10 coverage

| OWASP ID | Risk | AXIOM mitigation |
|---|---|---|
| LLM01 | Prompt Injection | Layer 1 constitutional suffix; Layer 2 output validation; Layer 2b SandboxContent; Layer 3 SandboxAgent; WHEN/DELEGATES routing |
| LLM02 | Insecure Output Handling | `validate_output()` checks 24 compliance signals before return |
| LLM04 | Model Denial of Service | `DosWatcher` — per-minute/hour/session limits, burst protection, replay detection, circuit breaker |
| LLM05 | Supply Chain Vulnerabilities | SHA-256 per agent; TAMPERED status fails Step 1; UNREGISTERED triggers HUMAN_REVIEW |
| LLM06 | Sensitive Information Disclosure | SECURITY rules + constitutional suffix block system prompt disclosure; CANNOT_MUTATE protects config |
| LLM07 | Insecure Plugin Design | TOOLS block — strict permission declarations; sandbox enforcement; CANNOT_MUTATE on tools |
| LLM08 | Excessive Agency | CANNOT_MUTATE; DELEGATES boundary enforcement; trust level hierarchy |
| LLM09 | Overreliance | Honesty integrity system — TeacherAgent evaluates for hallucination patterns |

LLM03 (Training Data Poisoning) and LLM10 (Model Theft) are infrastructure-layer
concerns outside the scope of this framework.

---

## 12. Prohibited uses

Do not deploy AXIOM for uses prohibited under EU AI Act Article 5, including:

- Real-time remote biometric identification in public spaces
- Social scoring by public authorities
- Subliminal manipulation of human behaviour
- Exploitation of vulnerabilities of specific groups
- Predictive policing based solely on profiling

AXIOM agents are advisory — they do not issue binding decisions. Ensure your
deployment does not convert advisory outputs into automated binding decisions
without human oversight.

---

## 13. Pre-deployment checklist

Complete this checklist before going live in any regulated context.

### Framework setup
- [ ] `AXIOM_API_KEY` and `AXIOM_BASE_URL` set in `.env`
- [ ] `AXIOM_DEPLOYER_NAME`, `AXIOM_DEPLOYER_CONTACT`, `AXIOM_DEPLOYER_JURISDICTION` set
- [ ] `axiom_certify.py --all` run — all agents return `CERTIFIED`
- [ ] Supply chain hashes registered (`axiom_files/.chain/supply_chain.json` populated)
- [ ] `GET /disclosure` returns your organisation name and correct contact

### EU AI Act
- [ ] FRIA template generated (`axiom_certify.py` run)
- [ ] All PLACEHOLDER fields completed in `{agent}_fria_{ts}.json`
- [ ] Risk classification confirmed for your deployment context
- [ ] Deployer attestation signed and stored with cert PDF
- [ ] FRIA review period defined (e.g. annual or on material change)
- [ ] Article 50 disclosure shown to users before first interaction

### HUMAN_REVIEW gate
- [ ] `escalate_to: operator_email` updated to a real address in all agent `.axiom` files
- [ ] Operator/team named in FRIA `human_oversight.operator_escalation`
- [ ] Response SLA defined and documented in FRIA
- [ ] Team briefed on `axiom_review.py list / show / approve / reject` workflow

### Monitoring
- [ ] Log retention policy defined and documented in FRIA `monitoring_and_logging.deployer_note`
- [ ] GDPR Article 5(1)(e) compliance statement written
- [ ] Alerting configured for `axiom_files/.reviews/review_queue.jsonl` new PENDING entries
- [ ] Incident response process defined for log tampering detection

### Integrity
- [ ] `python integrity_check.py` run — honesty_rate ≥ 0.85
- [ ] Periodic re-certification schedule defined (recommended: on every agent change + quarterly)
- [ ] Cert PDFs stored in version control alongside agent `.axiom` files

---

*AXIOM v1.8 | axiom-lang | Last updated: 2026-04-18*
