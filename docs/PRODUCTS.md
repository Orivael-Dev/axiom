# Axiom Certify — Product Catalog

Live catalog of Axiom Certify products and services. Every product
follows the structure in `docs/PRODUCT_TEMPLATE.md` so new ideas slot
in cleanly without rebrand cycles.

## Naming convention

Single umbrella brand: **Axiom Certify**.

Individual services hang off it with the `Axiom Certify · <Service>`
form:

- Axiom Certify · Agent Audit
- Axiom Certify · Model Audit          *(future)*
- Axiom Certify · Workflow Audit       *(future)*
- Axiom Certify · Continuous Verify    *(future, subscription tier)*

The brand promise across all of them: **a customer submits an AI
artifact, we run it through the AXIOM stack, and they get a
constitutional audit report plus a signed badge if it passes.** The
badge is the deliverable they put on their site.

## Catalog

| Product | Status | First customer ETA |
|---|---|---|
| [Axiom Certify · Agent Audit](#axiom-certify--agent-audit) | partial-implementation | ~1-2 weeks of build |

Add new products below the existing entries. Update this table when
status changes.

---

## Axiom Certify · Agent Audit

**Tagline:** Submit your AI agent. Get a signed constitutional audit
and a certification badge.

**Status:** partial-implementation
(backend exists, customer-facing layer is the gap)

**Last updated:** 2026-05-16

### What the customer submits

Any one of:

- A live agent endpoint (chatbot URL, internal assistant API,
  OpenAI/Anthropic-compatible endpoint)
- A system prompt + the model name it runs on
- An `.axiom` agent spec
- A prompt workflow (sequence of prompts + tool calls)
- A chat transcript log to audit retrospectively

### What the customer receives

A signed PDF report plus a JSON audit log, both delivered within the
SLA (one-time: 48h; continuous tier: real-time webhook).

Report contents:

- **Risk score** — 0-100 composite, with severity breakdown
- **Prompt injection test results** — pass/fail across the OWASP LLM
  Top 10 injection vectors
- **Bias/fairness checks** — disparate-impact scoring across the
  protected categories listed in the system prompt's CANNOT_MUTATE
  block (or our default set if none provided)
- **Hallucination pressure tests** — TruthfulQA-style adversarial
  probing, scored against the agent's stated truthfulness commitments
- **Signed decision manifests** — every test produces an HMAC-SHA256
  manifest the customer can independently verify
- **Before/after remediation report** — for each failed test, a
  proposed fix from `axiom_fix_playbook.py` plus a re-run showing
  the delta
- **"Axiom Certified" badge** — SVG + HTML snippet + verification URL
  if the composite score clears the threshold (currently 80/100,
  configurable per engagement)

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| Risk score (constitutional) | `axiom_vulnguard.py` severity classification, `POST /vulnguard/scan` | shipped (commit `2a95540`) |
| Prompt injection tests | `axiom_guard_api.py` `POST /guard/check`, `POST /guard/input` | shipped |
| Hallucination pressure | `axiom_red_agent.py` + `examples/truthfulqa_run.py` | shipped |
| Bias/fairness checks | `axiom_acb` + `tests/acb_runner.py` (Adversarial Constitutional Benchmark) | shipped; needs scoring rubric |
| Signed decision manifests | `axiom_signing.derive_key` + `GET /guard/manifest/{id}` | shipped |
| Fix proposals | `axiom_fix_playbook.py` (called from VulnGuard `classify_vulnerability`) | shipped |
| Agent spec verification | `axiom_axm.py` `POST /axm/verify` | shipped |
| Quality benchmarks | `examples/axiom_humaneval_run.py` (with token tracking, commit `29d2b7f`), `examples/axiom_arc_run.py`, `examples/axiom_agi_eval.py` | shipped |
| Audit dashboard UI | `docs/axiom_console.html`, `docs/axiom_os_shield_console.html` (zero-day panel, commit `2a95540`) | shipped |

### Gaps to ship

What does NOT exist today and blocks delivery to a paying customer:

1. **Customer intake workflow** — no form / API / dashboard to receive
   the artifact, no engagement-tracking state, no SLA timer
2. **Scoring rubric for the badge** — the composite-score formula
   that combines VulnGuard severity, /guard/check verdicts,
   hallucination rate, bias disparities into a single 0-100 number
   (and the threshold for passing). Needs a written rubric before
   the first audit.
3. **PDF report generator** — closest existing artifact is the static
   `docs/AXIOM_OWASP_LLM_Compliance.pdf` template. Need a generator
   that takes JSON audit output and produces a branded PDF.
4. **"Axiom Certified" badge artifact** — SVG + verification URL +
   the route that resolves the URL back to the signed manifest
5. **Tier 1 documentation** — engagement letter, SOW template,
   data-handling policy (customers will ask before submitting their
   production prompts)

Estimated effort: 1-2 weeks of focused build, mostly on the
customer-facing layer rather than new backend.

### Target customer + pricing

- **Who buys this:** Compliance / risk officer at a regulated
  enterprise (financial services, healthcare, legal) deploying an
  internal AI assistant; or a vendor selling an AI agent into one
  of those industries who needs third-party attestation
- **What pain it solves:** EU AI Act Article 50 disclosure, NYC
  Local Law 144 (bias audits), insurer / procurement requirements
  for third-party AI attestation
- **Pricing model:** One-time per-audit ($X) for first delivery,
  optional continuous-verify subscription ($Y/mo) for re-audit on
  every prompt or model change
- **Ballpark:** Not committed — orientation only — but the closest
  external comparables (HackerOne pentests, SOC 2 audits for AI
  agents) sit in the $15-50K range for one-time, $2-10K/mo for
  continuous

### Cross-references

- ORVL alignment: ORVL-021 (VulnGuard, Constitutional Zero-Day
  Discovery) is the spine of the risk-score deliverable
- Related .axiom specs: `axiom_files/core/axiom_vulnguard.axiom`,
  `axiom_files/core/axiom_qrf_reverse.axiom`,
  `axiom_files/core/axiom_axm.axiom`
- Related modules: `axiom_vulnguard.py`, `axiom_guard_api.py`,
  `axiom_signing.py`, `axiom_fix_playbook.py`, `axiom_axm.py`
- Related docs: `docs/ANF_TOKEN_ECONOMICS.md` (token-cost story
  for the continuous-verify tier), `docs/OPENCLAW_TODO.md`
  (consumer assistant productization — different product line)

### Notes / open questions

- **Brand stacking with openclaw.ai:** Axiom Certify is the B2B
  audit service; openclaw.ai is the B2C personal assistant. Both
  share the AXIOM stack but are independently positioned. Don't
  cross-brand the marketing.
- **OWASP LLM Top 10 alignment:** the existing
  `docs/AXIOM_OWASP_LLM_Compliance.pdf` template already maps the
  AXIOM stack to OWASP categories — this is the natural framing
  for the prompt-injection deliverable.
- **Open question — re-audit cadence:** does "Axiom Certified"
  expire? Quarterly? On every model change? Continuous-verify
  tier suggests the latter, but one-time tier needs a clear
  expiration policy or the badge becomes meaningless after a
  prompt change.
- **Open question — multi-tenancy:** if we run audits in
  process, multiple concurrent customer audits could cross-leak
  via the singleton caches in `_vulnguard_state` and `_shield_daemon`.
  Need per-customer process or namespaced state before scaling
  past one audit at a time.

---

## How to add a new product

1. Copy `docs/PRODUCT_TEMPLATE.md` content
2. Paste below the last product entry in this file (as a `## Axiom
   Certify · <Service>` section)
3. Fill in the placeholders
4. Add a row to the catalog table near the top of this file
5. Commit with message like
   `docs(products): add Axiom Certify · <Service> spec`

If the product gets large enough to warrant its own file, split it
out to `docs/products/<service>.md` and replace the section here with
a one-paragraph summary + link.
