# Axiom — Product Catalog

Live catalog of Axiom products and services. Each entry follows the
structure in `docs/PRODUCT_TEMPLATE.md` so new ideas slot in cleanly
without rebrand cycles.

## Brand architecture

Top-level brand: **Axiom**.

Product families and standalone products live underneath as siblings.
A family has multiple services that share a positioning; a standalone
product is its own SKU.

### Families

- **Axiom Certify** — third-party audit / attestation services
  *(point-in-time)*. Service form: `Axiom Certify · <Service>`.

### Standalone products

- **Axiom Flight Recorder** — continuous runtime observability and
  compliance logging for AI decisions

What unifies them: every product uses the same AXIOM backend
(constitutional engine, HMAC-signed manifests, latent reasoning,
QRF, VulnGuard) and delivers signed/verifiable outputs. The
positioning differs:

| Axis | Certify | Flight Recorder |
|---|---|---|
| Cadence | Point-in-time | Continuous |
| Deliverable | Pass/fail attestation + badge | Searchable audit trail + replay |
| Buyer | Compliance / risk officer | SecOps / SRE / compliance ops |
| Trigger | New agent goes live, quarterly re-audit | Always-on |
| Pricing shape | Per-audit one-time + optional sub | Subscription by volume |

## Catalog

| Product | Family | Status | First customer ETA |
|---|---|---|---|
| [Axiom Certify · Agent Audit](#axiom-certify--agent-audit) | Certify | partial-implementation | 1-2 weeks of build |
| [Axiom Flight Recorder](#axiom-flight-recorder) | standalone | partial-implementation | 2-3 weeks of build |

Update this table whenever status changes.

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
   **(Shared gap with Flight Recorder.)**
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

- **Brand stacking with openclaw.ai and Flight Recorder:** Axiom
  Certify is point-in-time B2B audit; Flight Recorder is continuous
  runtime observability; openclaw.ai is B2C personal assistant.
  All share the AXIOM stack but stay independently positioned.
- **OWASP LLM Top 10 alignment:** the existing
  `docs/AXIOM_OWASP_LLM_Compliance.pdf` template already maps the
  AXIOM stack to OWASP categories — natural framing for the
  prompt-injection deliverable.
- **Open question — re-audit cadence:** does "Axiom Certified"
  expire? Quarterly? On every model change? Continuous-verify tier
  suggests the latter, but one-time tier needs a clear expiration
  policy or the badge becomes meaningless after a prompt change.
- **Open question — multi-tenancy:** if we run audits in process,
  multiple concurrent customer audits could cross-leak via the
  singleton caches in `_vulnguard_state` and `_shield_daemon`. Need
  per-customer process or namespaced state before scaling past one
  audit at a time. **(Shared concern with Flight Recorder.)**

---

## Axiom Flight Recorder

**Tagline:** A black box recorder for AI decisions.

**Status:** partial-implementation
(backend mostly exists, replay UI and compliance export are the gaps)

**Last updated:** 2026-05-16

### What the customer submits

Configuration only — no artifact submission. The customer points their
existing AI traffic at the Flight Recorder by one of three integration
patterns:

- **Proxy mode** — wrap their LLM API calls through
  `axiom_guard_api.py /guard/proxy`, which already records every
  in/out pair
- **Sidecar mode** — emit decision events to a Flight Recorder
  webhook from their existing agent stack (LangChain, LlamaIndex,
  custom orchestrator)
- **OpenAI-compatible drop-in** — point their app at the AXIOM
  `/v1/chat/completions` proxy endpoint (already shipped, line 993
  of `examples/axiom_guard_api.py`)

### What the customer receives

A live web dashboard plus exportable compliance artifacts:

- **Time-series feed** of every AI action, with filters by user,
  intent class, constitutional distance band, and pass/block/throttle
  status
- **Per-decision detail view** showing: user prompt, AI response,
  intent classification, constitutional distance trajectory,
  pass/block/throttle/suspend status, the HMAC-signed manifest, and
  any concepts that fired
- **Replay button** — re-run any historical decision against the
  current agent config to detect regression (powered by the
  Constitutional Conversation Graph)
- **Exportable compliance report** — PDF/CSV/JSONL/SIEM for an
  arbitrary time window. PDF formatted for regulator submission
  (EU AI Act Article 12 record-keeping, HIPAA audit logs, FFIEC
  Section II)
- **Real-time alerts** — webhook + email + Slack on any
  L2_THROTTLE / L3_SUSPEND / L4_KILL escalation, with the offending
  trace pre-attached
- **API access** — programmatic search of the audit log for the
  customer's own SIEM / SOAR pipeline

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| Prompt + response logging | `POST /guard/proxy`, `POST /run_axiom`, `POST /guard/input`, `POST /guard/output` | shipped |
| OpenAI-compatible drop-in | `POST /v1/chat/completions` (`examples/axiom_guard_api.py:993`) | shipped |
| Intent classification | `axiom_intent_classifier.py` | shipped |
| Constitutional distance | `axiom_latent_v2.py ManifoldChecker`, `axiom_os_shield.py` | shipped |
| Pass/block/throttle/suspend status | `axiom_os_shield.py` L1-L4 levels, `/shield/*` endpoints | shipped |
| Signed manifest per decision | `axiom_signing.derive_key`, `GET /guard/manifest/{id}` | shipped |
| Manifest list + retrieval | `GET /guard/manifests`, `GET /guard/manifest/{id}` | shipped |
| Append-only audit log | `axiom_os_shield_log.jsonl`, `GET /gate/log` | shipped |
| Decision graph (replay foundation) | `POST /ccg/seed`, `GET /ccg/nodes`, `GET /ccg/edges` | shipped |
| Escalation events | `axiom_os_shield.py` escalate() + log_event() | shipped |
| Live shield status feed | `GET /os/shield/status` | shipped |

### Gaps to ship

What's missing for a customer-facing SaaS, in roughly priority order:

1. **Multi-tenant audit-log isolation** — today's
   `axiom_os_shield_log.jsonl` is a single file. Each customer
   needs an isolated stream (per-tenant subdirectory or per-tenant
   SQLite/Postgres database). Same singleton concern as Certify.
2. **Search/filter index** — the JSONL append-only log is fine for
   write but linear-scan for read. Production needs SQLite with
   indexes on (user, intent_class, distance_band, status, timestamp)
   or a real time-series store.
3. **Time-series dashboard UI** — `docs/axiom_dashboard.html`
   exists but is a snapshot dashboard, not a time-series feed. Need
   a new UI: scrollable timeline, decision-detail flyout, filter
   chips, replay action.
4. **Replay UI** — the CCG endpoints support graph traversal, but
   there's no UI button that takes a historical decision and reruns
   it. Needs a `POST /flight_recorder/replay/{decision_id}` that
   loads the CCG node, replays the prompt through the current agent
   config, and returns a delta report.
5. **Compliance PDF/CSV/SIEM export** — same generator gap as
   Certify (PDF). Plus a CSV adapter and SIEM webhook (Splunk HEC,
   Datadog Logs, Sumo Logic). The shape exists in JSONL; needs
   format adapters.
6. **External alert delivery** — escalation events are logged
   internally; need outbound webhooks + email (SES/Mailgun) + Slack
   incoming-webhook integration for the alerts deliverable.
7. **Tenant onboarding workflow** — sign-up flow, API key issuance,
   integration docs per pattern (proxy / sidecar / OAI drop-in),
   billing meter wiring.

Estimated effort: 2-3 weeks of focused build. Multi-tenant isolation
(item 1) is the biggest single piece; the rest are smaller.

### Target customer + pricing

- **Who buys this:** SecOps / SRE / compliance ops at any of:
  - Banks (FFIEC, OCC, FRB record-keeping)
  - Insurance carriers (NAIC market-conduct exams)
  - Healthcare admin (HIPAA audit logs, HITECH)
  - Government contractors (FedRAMP, FISMA)
  - Call centers (PCI if payment, GDPR, state consumer laws)
  - HR tech (NYC Local Law 144, GDPR Article 22)
  - Legal automation (state bar AI disclosure, privilege handling)
- **What pain it solves:** Every AI decision is now a discoverable
  artifact under regulation. Today most companies have NO immutable
  record of what their AI told a customer. Flight Recorder provides
  the discoverable artifact, the replay capability for adversarial
  proceedings, and the export format the regulator already expects.
- **Pricing model:** Subscription tiered by decision volume per month:
  - **Starter** (≤ 10K decisions/mo) — bottom-of-the-market
  - **Growth** (≤ 1M decisions/mo) — mid-tier
  - **Enterprise** (≥ 1M, dedicated tenant) — call-us
- **Ballpark:** Not committed — orientation only — Datadog Logs /
  LogRocket / Pangea sit in the $0.50-2.00 per thousand logs range
  for analogous volume. AI-decision audit logs probably command
  premium because of the regulatory framing.

### Cross-references

- ORVL alignment: ORVL-013 (Sovereign OS Shield) provides the
  pass/block/throttle/suspend taxonomy; ORVL-007 (Constitutional
  Conversation Graph) provides the replay substrate
- Related .axiom specs: `axiom_files/core/axiom_os_shield.axiom`,
  `axiom_files/core/axiom_ccg.axiom`,
  `axiom_files/core/axiom_latent_v2.axiom`
- Related modules: `axiom_os_shield.py`, `axiom_guard_api.py`,
  `axiom_intent_classifier.py`, `axiom_latent_v2.py`,
  `axiom_signing.py`
- Related consoles: `docs/axiom_dashboard.html` (snapshot view —
  needs time-series version), `docs/axiom_os_shield_console.html`
  (per-process live view — adjacent product)

### Notes / open questions

- **Brand cleanly distinct from Certify:** Certify says "your agent
  passed our audit"; Flight Recorder says "here's everything your
  agent did." Different buyer, different cadence, different SKU.
  Cross-sell is real (any Certify customer should also want Flight
  Recorder, and vice versa) but they should never be bundled into
  one offering — that muddles the buyer's mental model.
- **Storage cost is the operating risk:** at 1M decisions/mo per
  customer × 100 customers = 100M log lines/mo. JSONL won't scale;
  decide early whether the store is SQLite-per-tenant or a single
  Postgres / ClickHouse / OpenSearch backend with tenant_id
  partitioning. Both have real tradeoffs (isolation vs. ops cost).
- **Replay correctness:** when the customer replays a decision
  against their CURRENT agent config, the historical model may no
  longer be available (e.g. claude-3-sonnet retired). The replay
  needs an explicit "model resolution" step that either uses the
  current model and flags the diff, or refuses and explains why.
- **Regulator-format-of-record:** EU AI Act Article 12 says
  "automatic recording of events." Need to confirm with counsel that
  HMAC-SHA256 + append-only JSONL satisfies "tamper-evident" under
  their language. If yes, this is a strong differentiator vs.
  competitors using mutable database rows.
- **Adjacent product space — anti-cannibalization with Datadog /
  Pangea / LangSmith:** they log AI calls too, but they don't
  produce signed manifests, don't have a constitutional decision
  taxonomy (L1-L4), and don't have the replay-against-current
  capability. Positioning is "the only one regulators will accept"
  rather than "another AI observability tool."

---

## How to add a new product

1. Copy `docs/PRODUCT_TEMPLATE.md` content
2. Decide whether the product is part of an existing family
   (Axiom Certify · …) or standalone
3. Paste below the last product entry in this file as a `## Axiom
   <Family · Service | Standalone Name>` section
4. Fill in the placeholders
5. Add a row to the catalog table near the top of this file
6. Commit with message like
   `docs(products): add Axiom <Name> spec`

If the product gets large enough to warrant its own file, split it
out to `docs/products/<name>.md` and replace the section here with
a one-paragraph summary + link.
