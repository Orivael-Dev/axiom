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
- **Axiom Intent Firewall** — developer-facing API that blocks/flags
  prompt injection, harmful instructions, PII leakage, and deceptive
  outputs in-flight on any LLM call
- **Axiom MCP** — local MCP server giving AI coding tools (Claude
  Code, Claude Desktop, Cursor, Codex) a governance copilot that
  checks plans, actions, and code diffs before execution
- **Axiom CallGuard** — call-center agent compliance auditing for
  regulated industries (debt collection, banking, insurance,
  healthcare, telecom, loan servicing, BPOs)
- **Axiom Data Gate** — permissions layer for AI memory: classify,
  redact, and gate every piece of data before an AI agent reads,
  remembers, or exports it
- **Axiom Skill Pack Builder** — Docker containers for AI behavior:
  package agent instructions, allowed/forbidden actions, domain rules,
  safety tests, and signed metadata into a portable `.axm` pack.
  *Potentially foundational — the other Axiom products can consume
  Skill Packs as their config artifact.*
- **Axiom Nightly Review** — service-style recurring report that
  mines the previous day's signed audit trail and produces a ranked
  list of risky prompts, near misses, bias patterns, leakage risks,
  and suggested new rules
- **Axiom Shield Lite** — behavioral early-warning layer for
  suspicious AI / script / ransomware-like process activity. Local
  monitor + dashboard + tabletop ransomware simulation service.
  *Not* trying to replace antivirus.

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
| [Axiom Flight Recorder](#axiom-flight-recorder) | standalone | **shippable** | Core shipped *(multi-tenant decisions table, search/filter, replay endpoint, CSV/JSON/Splunk/Datadog export, webhook/email/Slack alerts shipped; remaining gap: time-series dashboard UI, tenant onboarding flow)* |
| [Axiom Intent Firewall](#axiom-intent-firewall) | standalone | near-shippable | 1 week of build *(smallest gap of the SaaS three)* |
| [Axiom MCP](#axiom-mcp) | standalone | near-shippable | 1-2 weeks of build *(13 MCP tools shipped; code-pattern refusal needs build)* |
| [Axiom CallGuard](#axiom-callguard) | standalone | partial-implementation | 3-4 weeks of build *(intent patterns + signing exist; audio intake + per-industry rule engines need build)* |
| [Axiom Data Gate](#axiom-data-gate) | standalone | **shippable** | Core shipped *(GDPR Art. 9 + PCI DSS patterns, per-agent policy engine, memory gate, right-to-erasure cert, pgvector connector all shipped; remaining gaps: FCRA/GLBA/CUI taxonomies, UI, compliance PDF, vector sweep)* |
| [Axiom Skill Pack Builder](#axiom-skill-pack-builder) | standalone *(foundational — see notes)* | near-shippable | 2-3 weeks of build *(AXMContainer + pack/inspect/verify/route shipped; need CLI polish, registry, marketplace)* |
| [Axiom Nightly Review](#axiom-nightly-review) | standalone | near-shippable | 1-2 weeks of build *(ConstitutionalRetrospect + ImprovementRecord shipped; need report templates, scheduling, delivery)* |
| [Axiom Shield Lite](#axiom-shield-lite) | standalone | near-shippable | 1-2 weeks for monitor product; 4-6 weeks for tabletop-simulation service *(ProcessManifold + file_access_rate + sovereign thresholds shipped)* |

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

**Status:** shippable
(per-tenant decisions table, search/filter API, replay endpoint,
export in 4 formats, webhook/email/Slack alert dispatch all shipped.
Remaining gaps: time-series dashboard UI, tenant onboarding workflow.)

**Last updated:** 2026-06-04

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
| **Multi-tenant decisions table** | `axiom_firewall/db.py decisions` table in per-tenant SQLite with composite indexes on `(intent_class, timestamp)` and `(verdict, timestamp)` | **shipped** |
| **Search/filter API** | `axiom_firewall/flight_recorder.search_decisions()` + `POST /flight_recorder/search` | **shipped** |
| **Replay endpoint** | `axiom_firewall/flight_recorder.replay_decision()` + `POST /flight_recorder/replay/{id}` — returns original vs current policy delta | **shipped** |
| **Export (4 formats)** | `export_decisions(fmt)` → JSON lines, CSV, Splunk HEC, Datadog Logs + `GET /flight_recorder/export` | **shipped** |
| **External alerts** | `AlertConfig` with webhook, Slack, SMTP; `PUT/GET /flight_recorder/alerts` | **shipped** |

### Gaps remaining (v2 scope)

1. **Time-series dashboard UI** — `docs/axiom_dashboard.html` is a snapshot; needs scrollable timeline, decision-detail flyout, filter chips, replay button. The API surface is complete; this is purely a UI build.
2. **Tenant onboarding workflow** — sign-up flow, API key issuance, integration docs per pattern (proxy / sidecar / OAI drop-in), billing meter wiring.
3. **Compliance PDF export** — CSV/SIEM already ship; PDF formatted for regulator submission (EU AI Act Article 12, HIPAA audit packet, FFIEC) is the shared gap with Certify/CallGuard.

Estimated remaining effort: **~1 week** (the backend is complete; remaining work is UI and PDF).

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

## Axiom Intent Firewall

**Tagline:** Stripe for AI safety. One API call sits between any user
and any LLM and blocks the categories regulators care about.

**Status:** near-shippable
(backend is `axiom_guard_api.py` essentially as-is; the gap is the
developer-facing layer — auth, SDK, billing, dashboard)

**Last updated:** 2026-05-16

### What the customer submits

Each API call carries the prompt + the customer's policy preferences;
no upfront artifact submission. Two main shapes:

- **Filter-only mode** — single endpoint takes `{prompt, mode:
  "input" | "output", policy?}` and returns
  `{verdict: allow | block | flag, intent_class, reasons[],
  signature}`. Customer integrates as a pre-flight check before
  their existing LLM call (and optionally a post-flight check on
  the response).
- **Full-proxy mode** — single endpoint takes
  `{messages[], model, policy?}` and proxies to the LLM (or the
  customer's own provider), running input + output filters in one
  round-trip. OpenAI-compatible request/response shape so existing
  code drops in without changes.

### What the customer receives

Per-call response:

- **Verdict** — one of `allow`, `block`, `flag` (with severity)
- **Intent class** — one of the six Constitutional Intent Typing
  classes (INFORM / CLARIFY / REFUSE / HARM / DECEIVE / UNCERTAIN
  per `axiom_intent_classifier.py:58`) — HARM and DECEIVE are
  hard-blocks (`BLOCK_CLASSES` at line 60)
- **Reason codes** — machine-readable enum of what triggered the
  verdict (e.g. `pii_email`, `prompt_injection_template`,
  `policy_bypass_jailbreak`, `pii_ssn`, `harm_self`,
  `deception_impersonation`)
- **Redacted prompt/response** (if in redact mode) — PII fields
  replaced with `<REDACTED:TYPE>` markers
- **HMAC-SHA256 signature** — every verdict is cryptographically
  signed so the customer (and downstream auditors) can prove the
  verdict wasn't tampered with after the fact
- **Manifest ID** — pointer to the full signed manifest retrievable
  by `GET /guard/manifest/{id}` if the customer needs the audit
  detail

What the customer's product receives (developer dashboard):

- **Live call volume + verdict breakdown** — calls/sec, allow/block/
  flag mix, top reason codes
- **Per-policy diff view** — show what a config change to the policy
  would have done over the last N calls (so changes ship safely)
- **Reason-code trend lines** — surfaces new attack patterns
- **Replay** — fetch any blocked call by manifest ID and re-run with
  a different policy, for tuning
- **API key management** — issue, rotate, revoke
- **Usage / billing** — current-period count, plan limit, overage rate

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| Input filter | `POST /guard/input` (`examples/axiom_guard_api.py`) | shipped |
| Output filter | `POST /guard/output` | shipped |
| Combined check | `POST /guard/check` | shipped |
| Full LLM proxy | `POST /guard/proxy` | shipped |
| OpenAI-compatible drop-in | `POST /v1/chat/completions` | shipped |
| Intent classification | `axiom_intent_classifier.py` (6-class typing: INFORM / CLARIFY / REFUSE / HARM / DECEIVE / UNCERTAIN) | shipped |
| PII redaction | `POST /guard/redact`, `GET /guard/redact/patterns`, `axiom_redact.py` | shipped |
| Signed verdicts | `axiom_signing.derive_key` + HMAC-SHA256 on every response | shipped |
| Manifest store + retrieval | `GET /guard/manifest/{id}`, `GET /guard/manifests` | shipped |
| Policy configuration | `POST /guard/configure`, `GET /guard/agents` | shipped |
| Per-agent .axiom policy specs | `axiom_files/core/*.axiom` (constitutional rules per agent) | shipped |

### Gaps to ship

This product has the **smallest gap of the three** in the catalog —
the backend is the existing `axiom_guard_api.py` essentially unchanged.
What's missing is purely the developer-facing wrapper:

1. **API key auth + rate limiting** — today the guard_api is open. Add
   `X-Axiom-Key` header, per-key quotas, per-key rate limits. FastAPI
   middleware + a SQLite or Postgres key store.
2. **Stripe billing meter** — record per-key usage; bill at the four
   proposed tiers; cut off (or auto-upgrade) past the threshold.
3. **Developer dashboard** — sign-up, key management, usage, plan,
   policy editor, replay button. The closest existing UI is
   `docs/axiom_dashboard.html` — needs a tenant-aware rewrite.
4. **SDK packages** — Python (`pip install axiom-firewall`),
   TypeScript (`npm install @axiom/firewall`), curl quickstart. The
   API surface is small enough that all three are <300 LOC each.
5. **Docs site** — quickstart, API reference, policy guide, prompt-
   injection threat model, comparison vs Lakera / Pangea / Rebuff.
6. **Multi-tenant policy isolation** — today `POST /guard/configure`
   mutates a singleton agent state. Per-tenant policy state is
   required before scaling past one customer.
7. **Free-tier abuse defense** — IP-based + email-domain heuristics
   to prevent free-tier farming (industry standard for developer
   APIs).

Estimated effort: **1 week of focused build** (the smallest of the
three catalog entries). Items 1-5 are the launch-blockers; 6-7 are
soft-launch items.

### Target customer + pricing

- **Who buys this:** Solo developers, indie SaaS founders, AI
  engineering teams at startups — anyone embedding an LLM into a
  product who needs prompt-injection / PII / policy-bypass defense
  *and* doesn't want to roll it themselves
- **What pain it solves:** Avoiding the "we shipped a chatbot and
  the news caught it saying [terrible thing]" outcome, without
  hiring a security team or building an in-house guardrail layer
- **Pricing model** (customer's proposal):
  - **Free** — `0` cost, capped at e.g. 1,000 calls/mo, single API
    key, no policy customization (default policy only)
  - **Indie — $49/mo** — capped at ~100K calls/mo, custom policy,
    3 API keys
  - **Team — $299/mo** — capped at ~1M calls/mo, unlimited keys,
    dashboard, replay
  - **Enterprise — custom** — dedicated tenant, SLA, custom
    .axiom specs, support
  - **Overage** — per-check usage pricing kicks in past threshold
    (suggest $0.30 per 1,000 calls — undercuts Lakera/Pangea
    competitively while staying margin-positive)
- **Ballpark comparables:**
  - **Lakera Guard** — ~$0.50 / 1K calls, prompt-injection focused
  - **Pangea AI Guard** — similar pricing band, broader feature set
  - **Rebuff** — open-source + commercial, less-developed pricing
  - The differentiator here is constitutional intent typing
    (6-class taxonomy vs. simple "is this a prompt injection?"),
    HMAC-signed verdicts (others don't sign), and pairing with
    Axiom Certify / Flight Recorder for full-stack adoption

### Cross-references

- **Pairs cleanly with Flight Recorder:** Intent Firewall does the
  in-flight blocking, Flight Recorder records what was blocked.
  Cross-sell motion is natural — "you're using Firewall, now log it
  for compliance."
- **Pairs cleanly with Certify:** Certify-audited agents that fail
  the audit can be retrofitted behind Intent Firewall as a stopgap
  while remediation happens.
- Related .axiom specs: `axiom_files/core/axiom_intent_classifier.axiom`,
  `axiom_files/core/axiom_redact.axiom`, every `axiom_files/core/*.axiom`
  agent (those ARE the per-tenant policies)
- Related modules: `axiom_guard_api.py`, `axiom_intent_classifier.py`,
  `axiom_redact.py`, `axiom_signing.py`
- ORVL alignment: ORVL-002 (Intent Classifier) is the spine of the
  verdict deliverable
- Related external prior art: OWASP LLM Top 10 (the threat model);
  Lakera, Pangea, Rebuff (the competitive landscape)

### Notes / open questions

- **This is probably the first revenue product.** Smallest gap,
  clearest competitive landscape, simplest pricing model. If the
  goal is "ship something paying customers can use this quarter,"
  this is the natural first build, not Certify or Flight Recorder.
- **Free-tier farming risk:** developer APIs with generous free
  tiers are routinely abused (one user with 50 emails). Plan
  defense before launch: email-domain blocklist (Gmail allowed,
  10-minute-mail blocked), IP heuristics, soft-throttle past free
  tier rather than hard-cut.
- **Open question — policy authoring UX:** the policy is a `.axiom`
  spec under the hood. Free tier gets the default policy. Indie
  and above can customize. The question is whether customization
  is YAML-style, web-form-style, or actual `.axiom` syntax. Vote
  here matters for the first dashboard build.
- **Open question — model-agnostic vs. opinionated:** today the
  guard_api proxies to Anthropic Claude. The Firewall product
  needs to support OpenAI, Anthropic, Gemini, Mistral, and local
  models (Ollama / vLLM) for the OpenAI-compatible drop-in. The
  routing layer needs a clean abstraction before launch.
- **Cannibalization with Certify:** none, because the buyers are
  different. A compliance officer doesn't write code; a developer
  doesn't run audits. Both can sell into the same enterprise
  through different doors.

---

## Axiom MCP

**Tagline:** Safety rails for AI coding agents. A local MCP server
that gives Claude Code / Cursor / Codex a governance copilot — checks
plans, actions, and code diffs before execution.

**Status:** near-shippable
(MCP server + 13 tools already ship; code-pattern refusal layer and
distribution polish are the gaps)

**Last updated:** 2026-05-16

### What the customer submits

Nothing to submit — they install the MCP server locally and point
their AI coding tool at it. Three install paths:

- **Claude Desktop** — add an entry to `claude_desktop_config.json`
  pointing at the Axiom MCP binary or `npx` runner
- **Claude Code** — `claude mcp add axiom <command>` from the CLI
- **Cursor** — add to the Cursor MCP config (same shape as Claude
  Desktop)
- **Codex / other MCP-aware agents** — standard MCP server protocol
  over stdio

The customer's coding agent then has access to 13 tools that check
its proposed plans, actions, and code diffs against the AXIOM
constitutional framework.

### What the customer receives

A locally-running MCP server that exposes the following 13 tools
(all already wired in `axiom_mcp_server.py`):

| Tool | What it does |
|---|---|
| `axiom_guard_check` | Run a prompt or plan through the constitutional guard; returns verdict + reasons |
| `axiom_lint` | Validate an `.axiom` spec for strict-mode compliance |
| `axiom_trace` | Run latent reasoning trace on a question (intent + confidence + risk clusters) |
| `axiom_qrf` | Quantum Reasoning Forecast — domain-specific weighted branch analysis |
| `axiom_status` | System health + loaded agents + manifest count |
| `axiom_intent_gate_check` | Classify intent (INFORM/CLARIFY/REFUSE/HARM/DECEIVE/UNCERTAIN); HARM and DECEIVE auto-refuse |
| `axiom_validate` | Validate a full `.axiom` spec against the validator |
| `axiom_cmaa_route` | Route a task to the right agent via CMAA orchestrator |
| `axiom_cmaa_fleet` | View the live agent fleet state |
| `axiom_cpi` | Constitutional Physical Intelligence — humanoid stability check |
| `axiom_axm` | AXM container operations (inspect / verify / route) |
| `axiom_shield` | OS Shield daemon status (process ancestry + level) |
| `axiom_phone_gate` | Voice-call gate (Hello Operator scam-call detection) |

Plus, as part of the productized layer:

- **Local dashboard** — `http://localhost:8002/mcp` showing the most
  recent 100 tool calls, verdict distribution, and a "ban list" of
  blocked patterns the customer's agent has tried
- **Configuration UI** — edit the local `.axiom` policy without
  hand-writing the spec (toggle which tools are active, set
  blocking thresholds)
- **Telemetry opt-in** — anonymous usage data to upgrade tier pricing
  decisions; PII-stripped before sending

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| MCP server core | `axiom_mcp_server.py` | shipped (`tools/list`, `tools/call`, 13 tools) |
| MCP tool: guard_check | `_handle_guard_check` (`axiom_mcp_server.py:222`) | shipped |
| MCP tool: lint | `_handle_lint` (`axiom_mcp_server.py:242`) | shipped |
| MCP tool: trace | `_handle_trace` (`axiom_mcp_server.py:260`) | shipped |
| MCP tool: qrf | `_handle_qrf` (`axiom_mcp_server.py:283`) | shipped |
| MCP tool: status | `_handle_status` (`axiom_mcp_server.py:301`) | shipped |
| MCP tools: intent_gate, cmaa, axm, shield, phone_gate, validate, cpi | declared in `_TOOL_SCHEMAS` (`axiom_mcp_server.py:53-219`) | shipped |
| Code intent classification (HARM/DECEIVE refusal on plans) | `axiom_intent_classifier.py` natural-language patterns (`_HARM_PATTERNS`, `_DECEIVE_PATTERNS`) | shipped — but **does not currently include code-specific patterns** (see Gaps §2) |
| Constitutional dev review | `axiom_dev_agent.py` (v1; reviews files for AXIOM bug patterns BUG-001..BUG-008) | shipped |
| Dev capture-and-train loop | `axiom_dev_loop.py` | shipped (training-data side, not enforcement) |

### Gaps to ship

This product is **near-shippable** — the MCP server + 13 tools work
today — but four pieces are missing for the customer-facing product:

1. **Distribution polish** — today the MCP server runs via
   `python -m axiom_mcp_server`. Needs a polished install path:
   - PyPI: `pip install axiom-mcp` or `pipx install axiom-mcp`
   - npm: `npx @axiom/mcp` for the Claude Desktop config snippet
   - Homebrew tap: `brew install axiom-mcp`
   - Docker image for enterprise self-host
2. **Code-pattern refusal layer** (new tool: `axiom_diff_check`) —
   the README claims Dev Agent v2 refuses `eval()`, `exec()`,
   `os.system()`, shell subprocess, and credential-shaped strings.
   The current `axiom_intent_classifier.py` patterns target
   natural-language attacks (weapons synthesis, jailbreaks, scam
   calls, authority impersonation) — **not** code patterns. The
   code-pattern refusal needs to be built. Suggested approach: a
   new `axiom_diff_check` MCP tool that scans a unified diff for
   the explicit code-pattern blocklist and returns ALLOW/BLOCK with
   line-level reasons.
3. **Plan-check tool** (new: `axiom_plan_check`) — review a plan
   *before* the agent executes any of it. Different from
   `axiom_guard_check`, which checks a single prompt; this checks
   a multi-step plan (e.g. Claude Code's plan-mode output) for
   HARM/DECEIVE intent across steps, side-effect surface analysis
   (file writes, network calls, secret access), and the
   cumulative constitutional distance trajectory.
4. **Local dashboard + config UI** — today there's no
   `localhost:8002/mcp` dashboard; this is a small Vue/React app
   that calls the MCP server's `tools/list` and shows recent
   calls. ~2-3 days of UI work.

Estimated effort: **1-2 weeks of focused build.** Item 2 (code-pattern
refusal) is the largest single piece; the others are smaller.

### Target customer + pricing

- **Who buys this:**
  - Solo developers using Claude Code or Cursor on production
    codebases who want safety rails
  - AI engineering teams at startups whose agents touch production
    systems (CI, deploy, prod database)
  - Enterprise dev teams nervous about agentic coding tools making
    unsafe changes (the cited "Devin / Cursor / Claude Code touched
    production" anxiety)
- **What pain it solves:** Agentic coding tools can take destructive
  actions (`rm -rf`, push to main, leak credentials, run unsafe
  shell commands). Today most companies have no governance layer.
  Axiom MCP sits between the agent and its tools, refusing
  constitutional violations with a signed manifest of the refusal.
- **Pricing model** (developer-tool shape, different from hosted
  API):
  - **Free (local install)** — single dev, default policy, no team
    features. Drives adoption. No telemetry by default.
  - **Pro $19/mo per dev** — custom local policy, history sync
    across the dev's machines, priority issue support
  - **Team $99/mo per team** — centralized policy, fleet view of
    all team devs' refusals, Slack/PagerDuty integration on
    blocked actions
  - **Enterprise — custom** — on-prem deployment, custom `.axiom`
    specs, SSO, audit log integration with the customer's SIEM,
    optional cross-sell into Flight Recorder for centralized
    decision logging
- **Ballpark comparables:**
  - **Composio** — MCP toolkit aggregator (different product —
    they expand tool access, we constrain it)
  - **Smithery.ai** — MCP server marketplace (distribution channel,
    not a competitor — we should publish there)
  - **Snyk Code** — code security ($25-79/dev/mo) — closer
    analog but not AI-aware
  - **GitHub Copilot Enterprise** ($39/user/mo) — has some safety
    features built in but customer can't customize

### Cross-references

- **Pairs cleanly with Flight Recorder:** Pro+ tier could ship
  refusal events upstream to a hosted Flight Recorder tenant for
  centralized compliance logging — natural Team→Enterprise upgrade
  path
- **Pairs cleanly with Intent Firewall:** same constitutional
  engine, different distribution. MCP = embedded in your IDE;
  Firewall = embedded in your production app. A customer might use
  both: Firewall for their shipping product, MCP for their dev
  workflow
- **Pairs cleanly with Certify:** Certify says "your shipped agent
  passed our audit"; MCP says "your dev agents can't ship unsafe
  code in the first place"
- Related modules: `axiom_mcp_server.py`, `axiom_intent_classifier.py`,
  `axiom_dev_agent.py`, `axiom_dev_loop.py`, `axiom_signing.py`,
  every `axiom_files/core/*.axiom` spec (those become the per-tenant
  policies)
- Related .axiom specs: `axiom_files/core/axiom_dev.axiom`,
  `axiom_files/core/axiom_intent_classifier.axiom`,
  `axiom_files/core/axiom_vulnguard.axiom`,
  `axiom_files/core/axiom_qrf_reverse.axiom`
- ORVL alignment: ORVL-001 (Dev Agent) is the spine of the
  code-review deliverable; ORVL-002 (Intent Classifier) provides the
  HARM/DECEIVE refusal substrate

### Notes / open questions

- **Honesty about Dev Agent v2 claims:** the README mentions Dev
  Agent v2 refusing `eval()` / `exec()` / `os.system()` / shell /
  credentials, but the current `axiom_intent_classifier.py` only
  has natural-language attack patterns, NOT code-specific
  patterns. Before shipping the marketing, either build the
  code-pattern blocklist (Gap §2) or adjust the marketing to
  describe what's actually shipped. Don't sell what doesn't run.
- **Open question — taxonomy inconsistency:** there are TWO
  6-class intent taxonomies in the repo. `axiom_intent_classifier`
  uses (INFORM, CLARIFY, REFUSE, HARM, DECEIVE, UNCERTAIN);
  `axiom_anf_emulator.CORE_ACTIVATION` uses (INFORM, REQUEST,
  EXPLORE, MANIPULATE, DECEIVE, HARM). Both are 6 classes, but
  different sets. Pick one canonical taxonomy and reconcile the
  other module before any product ships, or both products will
  reference different "intent class" meanings.
- **MCP marketplace strategy:** Smithery.ai is the de-facto MCP
  registry. Free tier should auto-publish there with a "verified"
  badge from us. Drives discoverability without paid acquisition.
- **Open question — telemetry default:** anonymous usage telemetry
  in free tier is a contentious default. Better: ship telemetry
  OFF by default with a clear opt-in dialog the first time the
  customer's agent runs an axiom_* tool. Costs us data; protects
  the trust narrative.
- **Open question — VS Code / JetBrains support:** MCP is currently
  Claude Code/Cursor/Codex-flavored. VS Code's AI features and
  JetBrains AI Assistant don't natively speak MCP yet but
  probably will within 12 months. Plan for the second-wave editor
  integrations rather than ignoring them.
- **No cannibalization with Intent Firewall:** same engine,
  different distribution. Selling both to the same enterprise is
  natural (Firewall for prod, MCP for dev). Marketing should
  treat them as a bundle for enterprise pricing.

---

## Axiom CallGuard

**Tagline:** Compliance and manipulation detection for AI-powered call
center agents. Watches every conversation, scores every response,
flags every violation, before the regulator does.

**Status:** partial-implementation
(intent-classifier patterns and signing framework apply directly; audio
intake pipeline and per-industry rule engines are the main gaps)

**Last updated:** 2026-05-16

**Important scope note:** Axiom CallGuard is the *outbound* product —
auditing call-center agents for regulatory compliance. The existing
`axiom_files/core/callguard.axiom` spec is *inbound* (defending users
from scam callers, the Hello Operator framing in ORVL-019). Both
directions share constitutional plumbing but are marketed and sold
separately. This spec covers the outbound B2B product only; the
inbound consumer-defense capability stays as backend infrastructure
that Intent Firewall and Flight Recorder can borrow from for their
phone channels.

### What the customer submits

The call center pipes their existing agent traffic into Axiom
CallGuard via one of three integration patterns:

- **Post-call batch** — upload audio recordings (or pre-existing
  transcripts) at the end of each call; receive verdicts within an
  SLA (default: 15 minutes for batch, 60 seconds for priority)
- **Near-real-time** — stream call audio chunks (or live agent
  utterances via WebRTC sidecar) and receive verdicts within a few
  seconds — useful for live supervisor alerts
- **Script pre-flight** — submit the agent's script / prompts (for
  AI agents) or call playbook (for human agents) for advance
  certification before any live calls

Customer also submits their **regulatory profile** — which industry
rule sets apply (FDCPA, TCPA, UDAAP, FCRA, HIPAA, NAIC, etc.) — so
the rule engine only flags violations that matter to their compliance
posture.

### What the customer receives

Per-call deliverables:

- **Compliance verdict** — PASS / WARN / FAIL with severity tier
  (mirrors the 5-tier framework from `callguard.axiom` adapted for
  agent-side verdicts)
- **Manipulative-language flags** — sentence-level detections with
  the offending phrase and rule citation (e.g. "Gift card payment
  demand — FTC Act §5 deceptive practice, FTC Consumer Sentinel
  Top 10")
- **Prohibited-claims detection** — industry-specific (debt
  collection FDCPA §807 false representations; insurance NAIC
  Unfair Trade Practices Act; lending TILA misrepresentation; etc.)
- **Customer coercion detection** — sales-pressure tactics
  (artificial urgency, fake authority, threat language, false
  scarcity, foot-in-the-door escalation patterns)
- **Agent scorecard** — per-agent / per-team / per-script rolling
  metrics: compliance rate, top violation categories, trend lines,
  comparative percentile vs peers
- **Audit reports** — exportable PDF/CSV/SIEM for regulator
  submission (FDCPA monthly summary, CFPB call-review packet,
  state AG complaint response packet, NAIC market conduct exam
  packet)
- **Real-time supervisor alerts** — webhook + email + Slack on any
  FAIL verdict during live calls (sub-5-second from utterance to
  alert in the near-real-time tier)
- **Signed manifests** — every verdict HMAC-SHA256 signed; the
  audit report is itself signed and tamper-evident

Account-level dashboard:

- Live call volume + verdict distribution
- Heat-map of violations by hour / agent / script
- Quality assurance trend lines (week-over-week, month-over-month)
- Regulator-format report scheduler (auto-generate monthly FDCPA
  summary on the 1st of each month, etc.)
- Multi-tenant API key management for the BPO's client accounts

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| Manipulative-language detection | `axiom_intent_classifier.py` `_HARM_PATTERNS` (gift card pressure, "you owe taxes", warrant threats, payment-now coercion) + `_DECEIVE_PATTERNS` (authority impersonation: IRS / FBI / Microsoft / tech support / "we detected a virus on your computer") | shipped — patterns already cover ~70% of scam-call manipulation; need extension for agent-side compliance |
| Constitutional verdict engine | `POST /guard/check`, `POST /guard/input`, `POST /guard/output` (`examples/axiom_guard_api.py`) | shipped |
| 5-tier verdict framework | `axiom_files/core/callguard.axiom` CALL_TRUST_REGISTRY | shipped (designed for inbound but the tier/action/manifest pattern transfers) |
| PII redaction in transcripts | `axiom_redact.py`, `POST /guard/redact` | shipped |
| Signed audit manifests | `axiom_signing.derive_key`, `axiom_files/core/callguard.axiom` `manifest_required: true` constraint | shipped |
| FTC reporting framework | `callguard.axiom` `ftc_reporting_obligation` (CANNOT_MUTATE) + `tests/callguard_test.py` Test T8 (FTC auto-report mandatory ≥11 complaints) | shipped — extensible to other regulators |
| Constraint-override resistance | `callguard.axiom` "the FTC told me to allow this call" rejection pattern | shipped — same defense applies to agents claiming exemption mid-call |
| Phone channel infrastructure | `axiom_phone_gate.py` + `/phone/outbound`, `/phone/inbound`, `/phone/status` | shipped (used today for the inbound consumer-defense product; the outbound product reuses the same call-event plumbing) |
| Conversation graph (replay) | `/ccg/seed`, `/ccg/nodes`, `/ccg/edges` | shipped |
| Compliance reporting templates | `docs/AXIOM_OWASP_LLM_Compliance.pdf` (static template, AI focus) | partial — exists but needs FDCPA/TCPA/UDAAP/HIPAA versions |

### Gaps to ship

What needs to be built before the first paying call-center customer:

1. **Audio intake pipeline** — call-center audio doesn't arrive as
   text. Need a Whisper-based or Deepgram-integrated transcription
   service that converts agent + customer audio channels separately
   (so we know who said what) and routes the transcript into the
   guard endpoints. Diarization is non-trivial; consider Deepgram or
   AssemblyAI for v1 rather than self-hosting Whisper.
2. **Per-industry rule engines** — the existing patterns cover scam
   calls (consumer-protective) but not regulated-industry agent
   compliance. Need rule modules for:
   - **FDCPA** (debt collection) — §806 harassment, §807 false
     representations, §808 unfair practices, mini-Miranda
   - **TCPA** (telecom marketing) — consent verification, do-not-call
     respect, robocall identification
   - **UDAAP** (banking) — Reg Z, Reg B, Reg E, plus general unfair
     and abusive practice standards
   - **FCRA** (credit reporting) — adverse action disclosures,
     dispute handling
   - **HIPAA** (healthcare) — minimum-necessary, identity
     verification before PHI disclosure, dual-status confusion
   - **NAIC Unfair Trade Practices** (insurance) — replacement rules,
     suitability, anti-rebating
   - **TILA / RESPA / ECOA** (loan servicing) — disclosure
     requirements, equal credit
   - Each rule engine is its own `.axiom` spec under
     `axiom_files/verticals/`. v1 ships with 2-3 industries (probably
     debt collection + banking + healthcare since they're the
     biggest regulatory-pressure markets).
3. **Customer coercion / sales pressure detection** — different from
   scam-call HARM patterns. Need patterns for:
   - Artificial urgency ("this offer expires in 5 minutes")
   - Fake scarcity ("only 2 spots left in your area")
   - Authority pressure ("my supervisor said you have to do this today")
   - False reciprocity ("I just did you a favor, now you need to…")
   - Foot-in-the-door escalation (small ask → big ask within same call)
   - Hidden anchoring (mentioning a high price first to make the real
     price seem reasonable)
4. **Agent scorecard system** — rolling time-series aggregation per
   agent / team / script. Time-series store (probably TimescaleDB or
   the same backend Flight Recorder uses) with computed columns for
   compliance rate, top violations, percentile ranking. Backend
   service + dashboard UI.
5. **Regulator-format report generator** — PDF + CSV adapters for:
   FDCPA monthly summary, CFPB call-review packet, state AG
   complaint response packet, NAIC market conduct exam packet, CFPB
   complaint-narrative export. Each format is regulator-prescribed
   and must match exactly. Shared gap with Certify (PDF generator)
   but the templates are different.
6. **Near-real-time inference path** — the existing `/guard/check`
   endpoint is sub-second for short prompts. For live-call alerts,
   need to keep that latency under 5 seconds end-to-end including
   transcription. Architecture: streaming Deepgram → utterance-level
   `/guard/check` → debounced supervisor alert via webhook. The
   batch path is much easier; the live-alert path is the harder
   build.
7. **Multi-tenant + PCI/HIPAA hosting** — call center audio frequently
   contains payment data (card-on-file requests) and PHI (healthcare
   scheduling). The hosting model is constrained: PCI DSS Level 1 or
   HIPAA Business Associate Agreement (BAA), not just any cloud
   tenant. This is the biggest operational blocker — needs deployment
   on a HIPAA-eligible AWS account with BAA, segregated tenant
   storage, and a data-handling policy the customer's compliance team
   can sign off on. Roll-your-own at first; SOC 2 Type II audit later.
8. **Recording-consent compliance** — two-party-consent states
   (California, etc.) require disclosure that the call is being
   AI-analyzed. EU AI Act Article 50 also requires disclosure.
   Customer needs a consent-injection mechanism for their call
   openers, plus we need a written notice template.

Estimated effort: **3-4 weeks of focused build.** Heaviest items
(audio intake + first three industry rule engines + PCI/HIPAA hosting)
account for ~2 of those weeks; the rest assemble around them.

### Target customer + pricing

- **Who buys this:** Compliance officer / VP of customer experience
  at:
  - Debt collection agencies (FDCPA exposure is existential)
  - Banking customer service / credit unions (UDAAP / Reg Z exposure)
  - Insurance sales floors (NAIC market conduct exams)
  - Healthcare scheduling / telehealth intake (HIPAA + ACA enrollment)
  - Telecom / cable customer service (TCPA + state PUC rules)
  - Loan servicing (TILA / RESPA / ECOA / CARES Act)
  - BPOs serving any of the above (Teleperformance, Concentrix,
    Sutherland, Alorica, TTEC)
- **What pain it solves:** Today most call centers do manual QA on
  ~2% of calls (pulled randomly by a supervisor). CallGuard scores
  100% of calls automatically, produces the regulator-format report
  on demand, and flags the violations a supervisor would have caught
  on the 98% of calls they never reviewed. The CFPB collected $3.7B
  in penalties in 2023; call-center violations are a major source.
- **Pricing model** (vertical SaaS shape, per-agent or per-call):
  - **Starter** — small BPO (≤50 agents), one industry rule set:
    $50/agent/month
  - **Growth** — mid-size (50-500 agents), 2-3 industry rule sets,
    dashboard, scorecard analytics: $100/agent/month
  - **Enterprise** — large BPO (≥500 agents), all industry rule
    sets, dedicated tenant, SLA, custom `.axiom` specs, regulator
    audit support: $150-200/agent/month + implementation fee
  - **Alternative volume tier** — $0.10-0.30/call for spiky usage
    patterns
- **Ballpark comparables:**
  - **NICE Nexidia** — $50-100/agent/month, mature analytics, weak
    AI safety angle
  - **Verint** — similar pricing band, broader contact-center suite
  - **Observe.AI** — $50-150/agent/month, modern AI-first, less
    regulatory-rule-engine depth
  - **CallMiner** — enterprise-only, $200K-1M ARR contracts
  - Differentiator: AXIOM's constitutional engine + HMAC-signed
    manifests + the existing scam-pattern library + the FTC auto-
    report obligation framework already in `callguard.axiom`. No
    competitor signs their verdicts; that's the regulator-facing
    differentiator.

### Cross-references

- **Pairs cleanly with Flight Recorder:** every CallGuard verdict
  also lands in Flight Recorder as a tenant decision event; one
  contract covers both.
- **Pairs cleanly with Certify:** Certify · Call Audit could be a
  point-in-time pre-launch attestation (does your script pass?),
  with CallGuard providing the continuous post-launch monitoring.
- **No overlap with MCP / Intent Firewall:** different distribution,
  different buyer.
- ORVL alignment: ORVL-019 (Hello Operator — phone-gate / scam-call
  framing) provides the inbound twin; ORVL-002 (Intent Classifier)
  provides the manipulation-pattern substrate; new ORVL number may
  be needed for the outbound agent-compliance framing.
- Related .axiom specs: `axiom_files/core/callguard.axiom` (inbound,
  partially transferable patterns), `axiom_files/core/axiom_intent_classifier.axiom`,
  `axiom_files/core/axiom_phone_gate.axiom` (phone channel
  infrastructure)
- Related modules: `axiom_intent_classifier.py`, `axiom_phone_gate.py`,
  `axiom_redact.py`, `axiom_signing.py`, `examples/call_manifest.py`
  (already produces three signed manifest examples)
- Related tests: `tests/callguard_test.py` (5-tier classification +
  FTC auto-report + override-resistance — extensive)

### Notes / open questions

- **The brand reuse is the load-bearing decision** — we kept the
  "Axiom CallGuard" name even though the existing `callguard.axiom`
  is technically the inbound consumer-defense product. The risk is
  customer confusion if both ship under the same name. Mitigation:
  the outbound product is the only one with public marketing; the
  inbound capability lives quietly as backend that Intent Firewall
  and Flight Recorder can plug into for their phone channels (e.g.
  Intent Firewall can offer a "phone tier" using the existing
  callguard.axiom scam-call protection).
- **PCI/HIPAA hosting is the operating constraint that may set
  pricing floor:** standard cloud tenancy isn't enough. Either we
  invest in a HIPAA-eligible AWS account with BAA + PCI DSS Level 1
  certification (months of compliance work), or we restrict the
  first launch to non-PCI / non-HIPAA verticals only (debt
  collection without payment-on-call, telecom outside healthcare).
  This is the single biggest go-to-market decision.
- **Open question — first vertical:** which industry rule engine
  ships first? Debt collection has the largest single-vertical TAM
  and the most-existential regulatory risk (CFPB / state AG); banking
  has the deepest pockets; healthcare has the highest data-sensitivity
  cost. Recommend debt collection for v1 (clearest pain, smallest
  rule surface), then banking, then healthcare once BAA is in place.
- **Open question — human vs AI agent monitoring:** the spec works
  for both AI-agent call centers (cleaner integration, the agent's
  prompt is already structured) and human-agent call centers (need
  audio transcription). Ideally v1 supports both; if narrower scope
  is needed, human-agent BPOs are the much larger market.
- **Open question — supervisor-in-the-loop tier:** for live FAIL
  verdicts, do we just alert the supervisor, or do we have authority
  to interrupt the call (mute the agent, force-route to a senior)?
  The latter is a more powerful product but requires deep telephony
  integration (Genesys / Five9 / NICE inContact). v1 should be alert-
  only; live interrupt is a v2 enterprise feature.
- **Open question — agent privacy vs employer surveillance:** the
  agents being monitored are employees who didn't choose to use an
  AI system. State labor laws and unions (in some markets) restrict
  workplace surveillance. The customer's contract with their workers
  must cover this; we provide the technical capability, the customer
  owns the employment-law compliance. Need a clear data-use
  agreement template.

---

## Axiom Data Gate

**Tagline:** A permissions layer for AI memory. Classify, redact, and
gate every piece of data before an AI agent reads, remembers, or
exports it.

**Status:** shippable
(HIPAA + GDPR Art. 9 + PCI DSS patterns, per-agent policy engine,
memory read/write gates, right-to-erasure with signed cert, pgvector
connector, and 6 new REST endpoints all shipped. Remaining gaps:
FCRA/GLBA/CUI taxonomies, policy authoring UI, compliance PDF export,
vector classification sweep.)

**Last updated:** 2026-06-04

### What the customer submits

The enterprise points their existing data flows at Data Gate via one
of three integration patterns:

- **Pre-flight check** — single endpoint takes
  `{data: <raw_text|json|file>, agent_id, action: "read" | "write" |
  "export" | "remember"}` and returns
  `{verdict: allow | block | redact, redacted_data, classifications[],
  policy_violations[], signature}`. The customer's agent code calls
  this before passing data to or storing it from the model.
- **Memory proxy mode** — Data Gate sits between the agent and the
  vector store (Pinecone / Weaviate / Qdrant / pgvector / ChromaDB).
  Every write is classified and policy-checked; every read is filtered
  according to the requesting agent's policy. Drop-in proxy at the
  vector DB layer.
- **Batch classification** — upload a corpus / database snapshot
  before connecting an AI agent to it; receive a classification map
  (per-field PII / regulated / public) plus a recommended `.axiom`
  policy spec the customer can review and apply.

Customer also submits their **regulatory profile** (HIPAA / GDPR /
CCPA / FCRA / GLBA / PCI / CUI) plus **per-agent role definitions**
(which agent is allowed to see what class of data).

### What the customer receives

Per-request response:

- **Classification** — array of detected classes: HIPAA PHI (which
  of the 18 Safe Harbor identifiers), GDPR special-category data
  (race, religion, health, biometric, sex life, criminal record),
  PCI cardholder data (PAN, CVV, expiration), FCRA covered data
  (credit reports, employment verification), GLBA NPI (non-public
  personal information), CUI / ITAR / EAR, custom enterprise
  taxonomies
- **Verdict** — `allow` / `block` / `redact` with rule citations
- **Redacted data** (if `redact`) — the original payload with
  classified fields replaced by typed placeholders (`[NAME REDACTED]`,
  `[SSN REDACTED]`, etc.) following the existing HIPAA Safe Harbor
  pattern
- **Policy violations** — if `block`, the specific rule(s) the
  request would have violated (per-agent role, data class, action)
- **Memory write decision** — for `action: "remember"`, whether the
  agent is permitted to write this to long-term memory (vector
  store / cache / log) or must process it transiently only
- **HMAC-SHA256 signature** — every verdict cryptographically signed
- **Audit-log entry** — appended to the tenant's tamper-evident
  data-access log; queryable via `GET /data_gate/log`

Tenant dashboard:

- **Data classification map** — heat-map of what classes of data
  flowed through which agents over a time window
- **Right-to-erasure workflow** — accept GDPR Article 17 / CCPA
  Section 1798.105 / state-equivalent deletion requests, find every
  log entry and memory block containing the subject's data, and
  produce a signed deletion certificate (or an explanation of which
  artifacts can't be deleted, with the lawful-basis citation)
- **Policy editor** — author per-agent access rules in a structured
  UI (which agent can see which class for which action); compiles to
  a `.axiom` spec under the hood
- **Vector-store sweep** — periodically re-classify existing
  embeddings (data classifications evolve; what was acceptable
  yesterday may be regulated tomorrow when a new state law passes)
- **Compliance export** — Article 30 records of processing, HIPAA
  audit log packet, CCPA personal-information inventory

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| HIPAA Safe Harbor de-identification (18 identifiers) | `axiom_redact.py` implementing 45 CFR 164.514(b)(2) | shipped |
| PII redaction endpoint | `POST /guard/redact`, `GET /guard/redact/patterns` (`examples/axiom_guard_api.py`) | shipped |
| Memory block primitive | `axiom_mkb.py` `KnowledgeBlock`, `ComposedBlock` | shipped |
| Memory block certification | `KnowledgeBlock.certify()`, `_sign_block` (HMAC-signed at registration) | shipped |
| Memory block registry | `BlockRegistry.register()`, `BlockRegistry.find()` | shipped |
| Load from `.axiom` spec | `load_from_axiom(filepath, hmac_key)` | shipped |
| Signed audit manifests | `axiom_signing.derive_key`, every guard verdict already HMAC-signed | shipped |
| Per-agent intent classification | `axiom_intent_classifier.py` (used to decide which agents can do what action class) | shipped |
| Per-agent policy via SkillDelegate | `axiom_axm.py SkillDelegate.intent_classes` (existing pattern for "this agent only handles these intents" — generalizes to "this agent only sees these data classes") | shipped |
| Append-only data access log | reuse `axiom_os_shield_log.jsonl` pattern + new `axiom_data_gate_log.jsonl` | partial (pattern shipped) |
| **GDPR Article 9 patterns** | `axiom_redact.py GDPR_PATTERNS` (9 patterns: race, religion, trade union, health conditions, genetic data, biometric, sexual orientation, criminal record, political opinions) | **shipped** |
| **PCI DSS patterns** | `axiom_redact.py PCI_PATTERNS` (6 patterns: PAN, CVV, card expiry, track data, PIN, cardholder name) | **shipped** |
| **Per-agent policy engine** | `axiom_firewall/data_policy.py` — `is_allowed(tenant_id, agent_id, action, data_class) → PolicyVerdict`; `AgentAccessRule` stored per-tenant SQLite; prefix matching; safe defaults for sensitive classes | **shipped** |
| **Memory write/read gate** | `axiom_mkb.py BlockRegistry(gate_fn=...)` — optional gate hook on `register()` and `find()`; denied writes raise `PermissionError`, denied reads return `None` | **shipped** |
| **Right-to-erasure workflow** | `axiom_firewall/db.erase_subject_data()` — substring scan of decisions table, delete, return HMAC-signed cert with scope limitation note; `DELETE /data_gate/erasure` | **shipped** |
| **pgvector connector** | `axiom_firewall/pgvector_connector.py` — `store_embedding`, `search_similar` (cosine via IVFFlat), `delete_by_subject`, `delete_by_tenant` | **shipped** |
| REST endpoints | `PUT/GET/DELETE /data_policy/rule`, `GET /data_policy/rules`, `POST /data_policy/check`, `DELETE /data_gate/erasure` | **shipped** |

### Gaps remaining (v2 scope)

1. **FCRA / GLBA NPI / CUI-ITAR-EAR taxonomies** — additional pattern libraries for credit-report data, bank NPI, and government contractor CUI markings. Each is a `.axiom` spec under `axiom_files/taxonomies/`. V1 shipped HIPAA + GDPR Art. 9 + PCI.
2. **Policy authoring UI** — web form for per-agent access rules that compiles to the `AgentAccessRule` JSON schema. Raw API works today; UI removes the config overhead for non-technical buyers.
3. **Compliance PDF export** — GDPR Article 30 records of processing, HIPAA audit log packet, CCPA personal-information inventory. PDF generator is a shared gap with Certify and CallGuard — one build, three products benefit.
4. **Vector-store classification sweep** — background cron that re-scans existing embeddings against current classifiers as regulations evolve. Reports new findings to the dashboard.
5. **Additional vector-DB connectors** — Pinecone, Weaviate, Qdrant, ChromaDB. pgvector shipped; rest follow the same pattern (~200 lines each).

Estimated remaining effort: **~1 week** (the hard parts are now shipped).

### Target customer + pricing

- **Who buys this:** Chief Privacy Officer / Data Protection Officer
  / VP of Engineering at:
  - Banks + credit unions (GLBA, CCPA, state privacy laws, FCRA for
    underwriting AI)
  - Healthcare systems / payors / telehealth (HIPAA, state medical
    privacy, ACA enrollment)
  - Government contractors (CUI, ITAR, EAR, FedRAMP boundary)
  - Enterprise AI teams in any sector (GDPR Article 22 automated
    decision-making, EU AI Act data governance requirements)
  - Legal services (attorney-client privilege preservation when AI
    summarizes docs)
  - HR tech (ADA, FCRA for background checks, NYC Local Law 144)
  - Any EU-presence company (GDPR Articles 5, 9, 17, 22, 32)
- **What pain it solves:** The "we connected our AI agent to our
  Snowflake / Confluence / Salesforce and now what?" problem. Right
  now most enterprises either give the agent full access (and pray)
  or wire up an ad-hoc patchwork of row-level security + column
  masking + custom prompt filters. Data Gate replaces all of that
  with a single policy layer that's auditable, signed, and produces
  the regulator-format records by default.
- **Pricing model** (per-call + per-tenant):
  - **Free** — ~1K classification calls/mo, default PII patterns
    only (HIPAA + basic PII), single agent, no memory proxy
  - **Indie $99/mo** — ~50K calls/mo, custom policies, 3 agents,
    one vector-store connector
  - **Team $499/mo** — ~500K calls/mo, unlimited agents, all
    vector-store connectors, dashboard, audit log 90-day retention
  - **Enterprise** — custom, dedicated tenant, GDPR DPA + SOC 2
    Type II + HIPAA BAA, audit log multi-year retention, custom
    `.axiom` taxonomies, on-prem deployment option
  - **Overage** — $0.50 per 1,000 classification calls past tier
    threshold
- **Ballpark comparables:**
  - **Immuta** — $50-200K/yr data access governance, not AI-native
  - **Skyflow** — per-record pricing, privacy vault (different
    architecture; they store the data, we gate access to it where it
    already lives)
  - **Microsoft Purview** — bundled with M365 E5 ($57/user/mo);
    broad data governance but weak on AI-agent permissions
  - **Pangea Vault / Pangea Redact** — closer comparable, per-API-
    call pricing
  - **Lakera Guard** — prompt-injection focus, not data-permissions
  - Differentiator: tight coupling of `(agent_id, action,
    data_class)` policy + memory-write gating + right-to-erasure
    across embeddings + HMAC-signed audit log. No competitor signs
    their audit log; that's the regulator-facing differentiator.

### Cross-references

- **Pairs cleanly with Intent Firewall:** Firewall checks the
  prompt; Data Gate checks the data the prompt is going to access.
  Natural bundle.
- **Pairs cleanly with Flight Recorder:** every Data Gate verdict
  also lands in Flight Recorder as a decision event.
- **Pairs cleanly with MCP:** MCP gates *code* the agent writes;
  Data Gate gates *data* the agent reads. A dev agent that touches
  prod data wants both.
- **Pairs cleanly with CallGuard:** call-center transcripts contain
  PHI / PCI / GLBA data; Data Gate classifies + redacts before the
  AI agent sees the transcript.
- **Pairs cleanly with Certify:** data-handling compliance is part
  of any agent audit; Certify uses Data Gate's classification engine
  to score the data-permissions surface of the audited agent.
- ORVL alignment: ORVL-004 (Modular Knowledge Block / MKB) provides
  the memory primitive; new ORVL number may be needed for the
  Data Gate framing.
- Related .axiom specs: `axiom_files/core/axiom_mkb.axiom`,
  `axiom_files/core/axiom_redact.axiom`,
  `axiom_files/core/axiom_axm.axiom`
- Related modules: `axiom_redact.py` (HIPAA Safe Harbor), `axiom_mkb.py`
  (memory blocks + registry), `axiom_axm.py` (delegate pattern),
  `axiom_intent_classifier.py`, `axiom_signing.py`

### Notes / open questions

- **This is the most "missing in the market" of the six products.**
  Everyone talks about agent tool-use; few talk about agent
  data-permissions. Immuta + Skyflow are close but neither tightly
  couples agent identity + action + memory write gating + signed
  audit log in one product. The competitive moat is narrow but
  defensible.
- **Right-to-erasure across embeddings is a research-grade problem.**
  Deleting a vector is easy; proving the subject's information
  isn't latently encoded elsewhere in the embedding space is hard
  (model-stealing literature, membership inference attacks). The
  deletion certificate needs an explicit limitation: "we have
  deleted all explicit records and known embeddings; latent encoding
  in trained model weights is outside our deletion scope and would
  require model retraining." Get this language in writing before
  shipping.
- **Open question — first taxonomy beyond HIPAA:** GDPR special
  categories has the broadest TAM (any EU presence forces it).
  PCI has the most-existential per-violation fine ($5K-100K per
  card record exposed). FCRA is largest credit/HR market.
  Recommend GDPR special categories for v1 (most universal) + PCI
  (highest per-incident risk).
- **Open question — bring-your-own-classifier:** enterprises often
  have custom data classifications (internal taxonomies, trade
  secrets, contract-sensitive info). v1 should support customer-
  defined classification patterns alongside the regulated-data
  defaults. Same `.axiom` spec mechanism as the per-tenant policy.
- **Open question — vector-store consistency model:** if a customer
  deletes data via right-to-erasure, but their vector store has
  3 replicas + a snapshot in S3 + cached query results, the
  deletion has to propagate everywhere. Need explicit guarantees:
  "deletion request accepted within 100ms; full propagation
  guaranteed within 24h; replication-lag risk acknowledged in
  the certificate."
- **Open question — DPIA as a paid deliverable:** a Data Protection
  Impact Assessment (GDPR Article 35) is something every enterprise
  needs before deploying high-risk AI. Data Gate's classification
  output is 80% of a DPIA. Could be a $5-15K one-time deliverable
  bolted onto the Enterprise tier — natural Certify · Data Audit
  cross-sell.
- **Open question — synthetic data generation:** the reverse-QRF
  module shipped on this branch could generate synthetic training
  examples that match a customer's data shape without including
  the real PII. Future product hook: "Data Gate · Synthetic" — pay
  more, get model-training-safe synthetic data drawn from your real
  corpus.

---

## Axiom Skill Pack Builder

**Tagline:** Docker containers for AI behavior. Build, sign, share,
and install portable agent skill packs.

**Status:** near-shippable
(`AXMContainer` + pack/inspect/verify/route already ship; gaps are
CLI polish, public registry, and a signing-trust chain for the
marketplace)

**Last updated:** 2026-05-16

### Important positioning note

The full ORVL-023 AXM patent describes `.AXM` as a "living execution
graph" successor to GGUF — model weights + skill delegates + trajectory
blocks + vector-vertex DB + proof ledger + hardware map. **That vision
is too ambitious for v1 marketing.** Replacing GGUF is a multi-year
ecosystem-positioning fight against HuggingFace, Ollama, llama.cpp,
and the entire model-distribution incumbent stack.

**Axiom Skill Pack Builder is the digestible v1.** It uses the same
underlying `.axm` container format, but the product pitch is "package
your agent's *behavior*" — not "package the model itself." Developers
already understand Docker containers for code, npm packages for
libraries, Helm charts for infrastructure. A "Skill Pack" is the same
mental model applied to AI agent behavior: instructions + allowed/
forbidden actions + domain rules + safety tests + signed metadata.

Once Skill Packs are a developer-adopted format, the full AXM model-
container vision is a v2 upgrade ("now we also ship the weights with
the behavior"). Don't sell v2 before v1 ships.

### What the customer submits / does

The developer interacts with three surfaces:

- **CLI** — `axiom skillpack` commands:
  - `axiom skillpack init` — scaffold a new pack directory
  - `axiom skillpack build` — pack the directory into a signed `.axm` artifact
  - `axiom skillpack verify` — run the pack's own safety tests + signature checks
  - `axiom skillpack publish` — push to the public or private registry
  - `axiom skillpack install <name>` — install a pack from the registry
  - `axiom skillpack list` — list installed packs
  - `axiom skillpack inspect <name>` — show pack header + delegates + proof ledger
  - `axiom skillpack route <name> "<task>"` — test-route a task through the pack
- **Pack directory** — a developer authors a directory like:
  ```
  my_pack/
  ├── header.json          # name, version, description, license
  ├── core/core.json       # core logic + quantization map
  ├── delegates/           # SkillDelegate entries (one per skill)
  │   ├── pii_redactor/skill.json
  │   └── tone_check/skill.json
  ├── trajectories/*.jsonl # action sequences for each pack capability
  ├── tests/               # BLOCKED/PASSED safety tests
  ├── vertices.json        # semantic-class vector entries (optional)
  └── proofs/ledger.jsonl  # Proof Ledger entries (one per sub-module)
  ```
- **Registry web app** — browse, search, install, publish, and review
  packs at `packs.axiom.ai` (free tier) or
  `<your-team>.packs.axiom.ai` (paid tier with private packs).

### What the customer receives

- **Portable signed `.axm` file** — fully-signed container the
  developer can attach to a GitHub release, send via email, or
  publish to the registry. Anyone who installs it can verify the
  signature chain back to the publisher's key.
- **Verification report** — `verify` output: signature chain status,
  proof-ledger HMAC integrity, safety-test pass/fail summary, list
  of declared capabilities
- **Installable runtime** — installing a pack registers all its skill
  delegates into the local `BlockRegistry`, making them available to
  any Axiom product on the same machine
- **Registry presence** — public packs get a permalink, version
  history, download stats, dependency graph, security advisories
- **Marketplace economics** (deferred to v2) — paid packs, revenue
  share, sponsorship surfaces

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| `.axm` container dataclasses (AXMHeader, SkillDelegate, TrajectoryBlock, VectorVertexEntry, ProofLedgerEntry) | `axiom_axm.py` (all 6 dataclasses, all frozen, all HMAC-signed) | shipped |
| `AXMContainer.pack(spec, output_path)` | `axiom_axm.py` | shipped |
| `AXMContainer.from_path(path)` | `axiom_axm.py` | shipped |
| `AXMContainer.inspect()` | `axiom_axm.py` | shipped |
| `AXMContainer.verify_proofs()` | `axiom_axm.py` (drives the ANF coprocessor once per proof) | shipped |
| `AXMContainer.route(task, classifier)` | `axiom_axm.py` (routes a task to matched delegates via MKB) | shipped |
| Starter pack scaffold generator | `examples/axm_pack_starter.py` | shipped |
| End-to-end demo | `examples/axm_demo.py` (pack → inspect → verify → route) | shipped |
| Constitutional spec | `axiom_files/core/axiom_axm.axiom` (TRUST_LEVEL 1, STRICT MODE, HUMAN_REVIEW on container_header_change / trust_level_change / proof_ledger_rotation) | shipped |
| MCP tool | `axiom_mcp_server.py` `axiom_axm` (action: inspect / verify / route) | shipped |
| REST endpoints | `POST /axm/inspect`, `POST /axm/verify`, `POST /axm/route` (`axiom_server.py`) | shipped |
| HMAC signing chain | `axiom_signing.derive_key` with 3 derived keys (container / delegate / proof) | shipped |
| MKB registration on `route()` | `axiom_axm.py` creates `KnowledgeBlock` per matched delegate, calls `BlockRegistry.find()` first (idempotent), registers if absent | shipped |
| Unit tests | `tests/test_axiom_axm.py` (3 BLOCKED + 4 PASSED + 2 INVARIANTS) | shipped |
| Integration tests | `tests/test_axiom_axm_integration.py` (REST + MCP, BLOCKED + PASSED + INVARIANT per surface) | shipped |

### Gaps to ship

The container itself is done. What's missing is the developer-product
wrapper around it:

1. **CLI polish** — `axiom_axm.py __main__` exists with `pack /
   inspect / verify / route` subcommands but is bare-bones. Need:
   - A `skillpack init` scaffolder that produces a fully-working
     starter (so the first 5 minutes are positive)
   - Better error messages (current ones are stack traces)
   - Coloured output, progress bars for `build`, `--dry-run` flags
   - `axiom skillpack` aliased from the `axiom-cli` package
   - Cross-platform Windows / macOS / Linux builds (the test path
     uses `subprocess.run(sys.executable, tmp)` which needs Windows
     adjustments)
2. **Public registry** — `packs.axiom.ai`. Backed by:
   - S3 + CloudFront for pack downloads
   - Postgres for pack metadata + version history
   - Search via SQLite FTS5 or Postgres full-text for v1; Algolia
     for v2
   - GitHub OAuth for publisher identity (avoids running our own
     password store v1)
3. **Signing-trust chain for marketplace** — today every pack is
   signed with the developer's local `AXIOM_MASTER_KEY` (deployer-
   key-specific, so committed packs don't verify across users). For
   a public marketplace, we need:
   - Per-publisher signing keys (held in a managed KMS — AWS KMS or
     similar)
   - A signing service that signs packs on publish, never exposing
     the private key to the developer's machine
   - A verification flow that checks the pack's signature against
     the publisher's published-public-key fingerprint
4. **Pack composition / dependencies** — Skill Pack A depends on
   Skill Pack B (e.g. a "Banking · Mortgage" pack depends on
   "Banking · UDAAP Base"). Today there's no dependency resolution.
   Add a `dependencies: [{name, version}]` field to the header and
   a resolver that fetches transitively.
5. **Pack quality scoring** — automated assessment on publish:
   - Did `verify_proofs()` pass?
   - How many safety tests are declared? How many pass?
   - Are there delegates without tests? (warn)
   - Are constitutional distance bounds explicit? (recommended)
   - Is there a CONSTRAINT line for every CANNOT_MUTATE field?
   - Output: 0-100 "Pack Quality Score" badge on the registry page
6. **Marketplace web UI** — register / publish / browse / search /
   install. Same web stack as Intent Firewall / Data Gate dashboards
   to keep ops simple.
7. **Documentation site** — Skill Pack format spec, authoring guide,
   examples gallery, troubleshooting, comparison with adjacent
   formats (LangChain Hub, GGUF, OCI artifacts).
8. **Curation / featured packs** — at launch, ship 5-10 high-quality
   first-party packs (e.g. "Customer Support Base," "Code Review
   Base," "FDCPA Debt Collection," "HIPAA Healthcare Intake," "GDPR
   Article 9 Data Handler") so the registry doesn't look empty.

Estimated effort: **2-3 weeks of focused build.** Items 1, 2, 3, 6,
and 8 are the launch-blockers; 4, 5, 7 are soft-launch and can ship
post-MVP.

### Target customer + pricing

- **Who buys this:**
  - **Free tier:** Solo developers + AI engineers building agents
    in Cursor, Claude Code, internal tools. Drives ecosystem
    adoption.
  - **Pro tier:** Indie SaaS founders + small AI teams who want
    private packs (to avoid leaking their proprietary agent
    instructions) and team-shared pack libraries.
  - **Team / Enterprise tier:** AI platform teams at larger
    companies who want a private corporate registry, SSO, audit
    trail on who-published-what, integration with their existing
    code-review process.
- **What pain it solves:**
  - "How do I version-control my agent instructions across 30
    deployments?"
  - "How do I share constitutional rules across my team without
    pasting Markdown files?"
  - "How do I install someone else's curated agent behavior
    without reading 800 lines of YAML?"
  - "How do I prove to my compliance team that the agent in prod
    is the same one we audited?" (signed packs → audit trail)
- **Pricing model:**
  - **Free** — unlimited public packs, unlimited installs, basic
    CLI, build + verify + publish + install
  - **Pro $19/mo per developer** — private packs (unlimited),
    quality scoring on private packs, dependency resolution,
    extended pack history, priority support
  - **Team $99/mo per team (5 seats)** — private team registry,
    role-based access (publisher / reviewer / installer),
    audit log of installs and updates, Slack/PagerDuty alerts
    on critical-CVE packs
  - **Enterprise — custom** — fully-private registry hosted on
    customer's cloud, SSO, custom signing-key infrastructure
    (HSM or KMS), audit log integration with customer SIEM,
    dedicated support contact, contracts with paying-pack
    marketplace economics if customer wants to sell packs
- **Ballpark comparables:**
  - **Docker Hub** — free public repos + paid private repos
    ($5-25/user/mo) — closest direct analog
  - **npm Registry + npm Pro** ($7/user/mo for private packages)
    — the dev-tool playbook this product follows
  - **HuggingFace Hub** — free, with paid hosted inference + spaces
    add-ons — relevant ecosystem competitor for AI artifacts
  - **LangChain Hub / LangSmith Prompts** — free, integrated with
    paid LangSmith — closer feature comparable than HuggingFace
    Hub since it's about prompts not weights
  - **OCI Artifacts** — non-Docker artifact distribution; could
    pitch Skill Packs as "AI behavior as OCI artifacts" for
    enterprise customers who already have private registries
  - Differentiator: HMAC-signed proofs at every layer (header /
    delegate / proof ledger), constitutional safety-test
    enforcement on publish, native integration with the rest of
    the Axiom product family

### Cross-references

- **POTENTIALLY FOUNDATIONAL.** Every other Axiom product can consume
  Skill Packs as its config artifact:
  - **Intent Firewall** loads a Skill Pack as the per-tenant policy
    spec instead of hand-authored `.axiom` files
  - **MCP** installs Skill Packs as additional local governance rules
  - **CallGuard** ships per-industry rule engines as Skill Packs
    (FDCPA pack, TCPA pack, HIPAA-intake pack)
  - **Data Gate** ships per-jurisdiction classification packs (HIPAA
    pack, GDPR-special-categories pack, PCI pack)
  - **Certify** uses Skill Packs as the canonical artifact that gets
    audited (you submit your pack, we run our audit against it,
    badge the pack on success)
  - **Flight Recorder** logs which Skill Pack version was active for
    every decision (so audit trails carry the exact behavioral
    config used)
- This means Skill Pack Builder is the lever for the **product flywheel:**
  developers adopt the free pack format → pack ecosystem grows → other
  Axiom products become more valuable because they consume packs →
  more developers adopt → ...
- ORVL alignment: ORVL-023 (Axiom eXchange Model / AXM). The full
  ORVL-023 vision is the model-format successor; Skill Pack Builder
  is its developer-facing slice that ships first.
- Related .axiom specs: `axiom_files/core/axiom_axm.axiom` (the
  constitutional spec for the container format itself; TRUST_LEVEL 1,
  STRICT MODE, HUMAN_REVIEW gates on header / trust_level / proof
  ledger changes)
- Related modules: `axiom_axm.py`, `axiom_mkb.py` (the `KnowledgeBlock`
  primitive that delegates become), `axiom_intent_classifier.py` (the
  WHEN-condition matcher in `route()`), `axiom_signing.py`,
  `bundle_v1_8.py` (content-hash for sub-module integrity in the proof
  ledger)
- Related examples: `examples/axm_pack_starter.py`,
  `examples/axm_demo.py`
- Related tests: `tests/test_axiom_axm.py` (9 unit tests),
  `tests/test_axiom_axm_integration.py` (6 integration tests)

### Notes / open questions

- **The 5-product foundation argument:** if Skill Pack Builder ships
  and adoption follows, the other five Axiom products effectively
  become *configurable*. A customer who buys CallGuard isn't paying
  for the rule engines themselves — they're paying for the
  enterprise tier of the platform that runs Skill Packs +
  multi-tenant + signed audit. This is a much more defensible
  business than per-vertical SaaS, because the SwitchingCost on the
  Skill Pack format is high once a customer has their own internal
  packs depending on it. This is the strongest moat in the catalog.
- **Open question — public registry vs private-only at launch:**
  Public registry brings adoption but also brings governance overhead
  (malicious packs, copyright issues, registry-as-a-service ops
  costs from day one). Private-only at launch (only Pro/Team
  customers get a registry) is easier ops but lower adoption. The
  hybrid: public registry of *first-party* packs only at v1, plus
  private registries for Pro/Team — third-party public publishing in
  v2 after governance process is in place.
- **Open question — signing-key custody for the marketplace:** managed
  KMS (we hold the publisher's key in AWS KMS, never send it to
  their machine) is operationally safer but creates a single-point-
  of-trust. Bring-your-own-key (publisher generates their own key,
  sends us the public-key fingerprint, signs locally) is more
  decentralized but harder for non-crypto-native developers. Suggest
  KMS-by-default with BYOK opt-in for advanced users.
- **Open question — pack-format stability commitment:** the
  developer ecosystem will not adopt a format that breaks every
  release. Need to commit to backward compat for at least 2 years
  before v1 ships. Versioning scheme in `AXMHeader.format_version`
  is already in place; just need a written promise + a migration
  story for any future breaking change.
- **Open question — marketplace economics:** paid packs are
  attractive (developers sell expertise), but they require revenue-
  share infrastructure, payouts, dispute resolution, tax compliance
  per jurisdiction. Defer to v2 unless a specific high-value
  use case emerges in v1.
- **Open question — IDE integrations:** beyond the CLI, developers
  expect VS Code / Cursor / JetBrains extensions for browse-install-
  inspect-edit-validate flow. The MCP integration (already shipped
  via the `axiom_axm` MCP tool) gives Cursor / Claude Code partial
  coverage today; full IDE plugins are v1.5 work.
- **Open question — comparison with OCI artifacts:** OCI's
  artifact spec lets anyone push non-Docker stuff to an OCI
  registry (Helm uses this, Bicep uses this). Could ship Skill
  Packs as OCI artifacts as well as `.axm` files, giving enterprise
  customers compatibility with their existing Harbor / Artifactory
  / ECR private registries. Investigate as a fast-follow.
- **Sustained competitive risk:** LangChain Hub and HuggingFace
  Hub are well-positioned to add "agent behavior packages" to their
  existing offerings. The way to win this race is **signed
  audit chain + constitutional safety tests as first-class
  format features** — neither competitor has these natively, and
  enterprise customers will care about them once one regulator
  starts asking.

---

## Axiom Nightly Review

**Tagline:** Every morning, a signed report on what your AI did
yesterday, what almost went wrong, and what rules should change.

**Status:** near-shippable
(`ConstitutionalRetrospect` + signed audit-trail framework already
ship; gaps are report templates, scheduling, and delivery)

**Last updated:** 2026-05-16

### What the customer submits

Either:

- **They already have Flight Recorder** — Nightly Review automatically
  ingests the previous day's signed manifest log from their Flight
  Recorder tenant. Zero additional integration.
- **They don't have Flight Recorder** — upload signed manifests via
  `POST /nightly_review/ingest`, or point at any of the other Axiom
  products' audit logs (Intent Firewall, CallGuard, Data Gate, MCP,
  Certify). All seven sibling products produce HMAC-signed manifests
  that `ConstitutionalRetrospect` can consume.

Customer configures **scope** (what to review: today, last 7 days,
last month), **delivery target** (email PDF, Slack summary, S3 bucket,
SIEM webhook, JIRA ticket), and **rule-recommendation aggressiveness**
(conservative: only flag patterns observed ≥10 times; aggressive: flag
anything unusual).

### What the customer receives

A daily/weekly signed report covering:

- **Top risky prompts** — ranked by `constitutional_distance` proximity
  to the L1/L2/L3/L4 thresholds, deduplicated by intent class
- **Blocked attempts** — every HARM/DECEIVE refusal with the offending
  prompt (redacted per the customer's PII policy) and the rule that
  triggered the block
- **Near misses** — prompts that *passed* but cleared the L1 warning
  threshold by less than `0.02`; these are the precursors to future
  blocks and the most-actionable category
- **Bias patterns** — disparate-impact signals across the protected
  categories declared in the customer's policy (e.g. "refusal rate
  for prompts containing demographic-X is 3.2× baseline")
- **Data leakage risks** — Data Gate verdicts that allowed but
  redacted, plus any allow-without-redact where the classifier
  flagged ambiguity. These are the "should we have blocked this?"
  candidates.
- **Suggested new rules** — the meat of the report. The retrospect
  engine identifies repeating patterns of near-misses and proposes
  new constraints (in `.axiom` syntax) that would have caught them.
  Suggestions include the proposed rule, the prompts it would have
  affected, and the false-positive risk if adopted.
- **Updated governance pack recommendations** — if the customer uses
  Skill Packs, recommends version bumps or new dependencies (e.g.
  "switch from `axiom/financial-base@1.2` to `1.3` to pick up the
  TILA-disclosure rule that would have caught 4 near-misses
  yesterday")
- **Trend lines** — week-over-week: risk score, block rate, near-miss
  rate, top intent classes
- **Signed manifest** — the entire report HMAC-signed; tamper-evident
  for downstream regulator submission

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| Retrospect engine | `axiom_retrospect.py` `ConstitutionalRetrospect` class | shipped |
| Review candidate selection | `ReviewCandidate` dataclass + manifest replay loop | shipped |
| Replay-against-current-policy | `ReplayResult` dataclass (re-runs manifest under current rules) | shipped |
| Improvement record output | `ImprovementRecord` dataclass | shipped |
| Manifest entry ingestion | `ManifestEntry` dataclass + parser | shipped |
| Signed retrospect output | `_sign(data)` (HMAC-SHA256 over canonical JSON) | shipped |
| Review categorization | `ReviewCategory` enum | shipped |
| Audit-trail source | Flight Recorder (`axiom_os_shield_log.jsonl` + `/guard/manifests`) | shipped |
| Manifest retrieval | `GET /guard/manifest/{id}` (`examples/axiom_guard_api.py`) | shipped |
| HMAC signing chain | `axiom_signing.derive_key` | shipped |

### Gaps to ship

1. **Report templates** — PDF + email + Slack summary versions. PDF
   shares the gap with Certify / Flight Recorder / CallGuard /
   Data Gate (single PDF generator, multiple templates).
2. **Scheduling** — cron-driven trigger that runs the retrospect at
   the customer's configured time (default: 02:00 in tenant tz) and
   delivers to their endpoint of choice. Use a managed scheduler
   (AWS EventBridge or similar) rather than running our own cron.
3. **Bias pattern detection** — `axiom_acb` (Adversarial Constitutional
   Benchmark) has the substrate for disparate-impact scoring, but the
   retrospect engine doesn't currently call it on the previous day's
   manifest set. Wire `ConstitutionalRetrospect` → `axiom_acb`
   batch evaluation. ~150 LOC.
4. **Rule-suggestion engine** — today `ConstitutionalRetrospect`
   identifies review candidates but doesn't propose new `.axiom`
   rules. Build a pattern miner that takes near-miss clusters and
   outputs a draft `CONSTRAINT` line or `_HARM_PATTERN` regex. This
   is the most novel piece of the product and worth getting right —
   suggested rules need to be *legible* and *minimal* (no
   over-fitting to a single prompt).
5. **Skill Pack version recommendations** — when Skill Pack Builder
   ships, this engine should also recommend pack version bumps. Wire
   to the registry API: "this near-miss would have been caught by
   `axiom/financial-base@1.3` — recommend upgrade."
6. **Multi-tenant delivery** — each customer's reports go to their
   declared destination (S3, email, Slack, JIRA, SIEM webhook).
   Standard fan-out pattern, but needs build.
7. **Self-improvement loop guardrails** — the spec says "self-
   improvement without human annotation" but **rule changes must
   never auto-apply** in this product. The report SUGGESTS;
   the human REVIEWS; the human APPLIES. Hard requirement; otherwise
   we're shipping autonomous policy mutation which is a different
   product with a much higher trust bar.

Estimated effort: **1-2 weeks of focused build.** Items 4 and 7 are
the load-bearing ones; the rest is plumbing.

### Target customer + pricing

- **Who buys this:** Risk officer / compliance head / VP of AI
  engineering at any company already running an Axiom product (so
  there's an audit trail to mine). Also: cross-sells well as a
  *service tier* — instead of paying for a SaaS product, the
  customer pays for the report.
- **What pain it solves:** "We have a Flight Recorder log of 50,000
  decisions per day — what should I do with it?" Nightly Review
  turns the log into actionable intelligence without requiring the
  customer to hire a dedicated AI safety analyst.
- **Pricing model** (recurring service, not SaaS-by-volume):
  - **Standard** — $499/mo flat, daily PDF + Slack summary, 30-day
    retention, conservative rule-suggestion mode
  - **Pro** — $1,499/mo, daily + weekly + monthly reports, 1-year
    retention, aggressive rule-suggestion mode, dedicated analyst
    review of the weekly report
  - **Enterprise** — $5K-15K/mo, all of Pro plus: custom report
    templates, regulator-format export, on-call Axiom analyst, SLA
    on rule-suggestion accuracy, custom integration into customer
    SIEM / GRC platform
  - **One-time deliverable** — $5K-25K for a single
    "retrospective audit" of an existing customer audit log (good
    sales-development motion: prospects pay for one report, see the
    value, then convert to recurring)
- **Ballpark comparables:**
  - **Vanta / Drata** ($30K-100K/yr) — compliance automation, not
    AI-specific
  - **Datadog Compliance Monitoring** (bundled with Datadog
    enterprise, $15-25/host/mo) — closer in shape but cloud-infra
    focused
  - **AuditBoard** ($30K-200K/yr) — GRC platform, not AI-specific
  - Differentiator: this is the only product on the market that
    mines an AI agent's *constitutional* decision log specifically.
    The output (suggested `.axiom` rules) is unique to the AXIOM
    ecosystem.

### Cross-references

- **Pairs with EVERY other product:** Nightly Review consumes
  manifests from any of the seven sibling products. The pitch is
  natural — "you're already producing signed audit logs; we mine
  them and tell you what's actually happening."
- **Flight Recorder is the most natural pairing:** Flight Recorder
  collects, Nightly Review reports. Could be bundled at the
  Enterprise tier ("Flight Recorder Pro + Nightly Review Standard
  = $X/mo bundle").
- **Skill Pack Builder is the second-most-natural pairing:** the
  rule-suggestion engine outputs new Skill Pack versions, which
  the customer can adopt.
- **Certify cross-sell:** customers who run Nightly Review for 90
  days have already produced the audit trail Certify needs.
  Conversion path is natural.
- ORVL alignment: ORVL-011 (Constitutional Retrospect) is the
  spine.
- Related .axiom specs: `axiom_files/core/axiom_retrospect.axiom`
- Related modules: `axiom_retrospect.py`, `axiom_signing.py`,
  `axiom_acb` (for bias scoring), every other Axiom product (as
  data source)

### Notes / open questions

- **The "service vs software" pricing is intentional.** Most B2B
  SaaS products price by volume; Nightly Review prices by report
  cadence + analyst touch. This is more like an audit firm's pricing
  than a software company's. The reason: the differentiating value
  is the *judgment* in the rule-suggestion engine, not the volume of
  data processed.
- **Open question — autonomy boundary:** the README phrase
  "self-improvement without human annotation" is true (the engine
  needs no human labels to identify near-misses), but it should
  NEVER auto-apply rule changes. The product surface is
  recommend-only. If a customer asks for auto-apply at the
  Enterprise tier, that's a separate product with a much higher
  trust bar — and probably a different liability model.
- **Open question — false-positive control on rule suggestions:**
  the rule-suggestion engine has to be honest about over-fitting.
  Each suggested rule should ship with a "would-have-affected" list
  showing every prompt it would have touched if applied to
  yesterday's traffic — so the customer can spot rules that catch
  one true positive and 50 false positives.
- **Open question — delivery format for regulator submission:** PDF
  is universal, but some regulators prefer specific shapes (HHS OCR
  uses CSV templates; CFPB uses their portal). The Enterprise tier
  needs custom delivery formats; standard tier ships PDF + JSON.
- **Open question — how much to summarize:** customers will hit
  fatigue if the report is 200 pages of every near-miss. Need an
  executive-summary layer (top 5 risks, top 3 suggested rules) plus
  a deep-dive link for analysts who want the full data. Same
  pattern as security operations dashboards.

---

## Axiom Shield Lite

**Tagline:** A behavioral early-warning layer for suspicious AI,
script, and ransomware-like activity. Not trying to replace
antivirus. Just trying to spot the things antivirus misses because
they look like normal binaries doing unusual amounts.

**Status:** near-shippable
(`ConstitutionalOSShield` + `ProcessManifold` + `file_access_rate`
detection + sovereign escalation thresholds already ship; gaps are
local dashboard, tabletop-simulation service, and the
opinionated "Lite" packaging)

**Last updated:** 2026-05-16

### Important positioning note

Replacing antivirus is a $20B+ market with deep incumbents
(CrowdStrike, SentinelOne, Microsoft Defender for Endpoint). That is
not the product. **Axiom Shield Lite is explicitly a complementary
layer**: it watches *behavior* (file enumeration rate, child-process
spawn patterns, network burst signatures, constitutional distance
trajectory) rather than *signatures*. The pitch is:

> "Your AV catches known-bad binaries. We catch a normal binary
> doing 200 file reads per second."

This positioning has three benefits: (1) it doesn't trigger
incumbent competitive response, (2) it gives the customer a reason
to keep their AV contract, (3) it sells into the "what about
zero-days?" anxiety that AV vendors don't own.

### What the customer does

Two distinct product shapes share this entry:

#### Shape A — Local monitor (developer / SMB)

The customer installs Shield Lite on their laptop or a small fleet.
The monitor runs as a system service, polls `psutil` at the
`POLL_INTERVAL_MS` cadence, and tracks every running process's
constitutional distance trajectory (rooted in `ProcessManifold`).

#### Shape B — Tabletop simulation service (enterprise)

We deploy Shield Lite into the customer's test environment,
run a scripted ransomware simulation (no real encryption — synthetic
file-touch storms, fake exfiltration patterns, ancestry-manipulation
sequences) plus benign-baseline workloads, and deliver a written
report on:

- Which simulation steps triggered L1/L2/L3/L4 escalation
- Time-to-detection at each level
- False-positive rate against benign workloads
- Recommended threshold tuning for the customer's actual environment
- Comparison against what the customer's existing AV would have
  detected (gap analysis, not displacement)

### What the customer receives

#### Shape A deliverables

- **Local dashboard at `localhost:8003/shield`** — process tree with
  constitutional distance for each, color-coded by level (NORMAL /
  WATCH / L1_FLAG / L2_THROTTLE / L3_SUSPEND / L4_KILL)
- **Suspicious process log** — append-only JSONL of every escalation
  with signed manifest (HMAC-SHA256). Already shipping at
  `axiom_os_shield_log.jsonl`.
- **File enumeration spike detection** — alerts when
  `file_access_rate` exceeds the learned baseline by a configurable
  multiplier (default: 5×). Already wired in `ProcessSnapshot`.
- **Signed incident report** — when an L2+ event fires, an auto-
  generated incident packet: timeline, ancestry, what changed, what
  was blocked, what would have happened next if not blocked. PDF +
  JSON.
- **"What happened?" timeline** — replay view that shows
  second-by-second what the suspect process was doing before, during,
  and after escalation. Built on the conversation-graph primitive
  (`/ccg/seed`).
- **Restore button** — for `L3_SUSPEND` events, the operator can
  review and unsuspend if the trigger was a false positive
  (`POST /shield/restore`).

#### Shape B deliverables

- **Engagement scoping** — 2-week pre-engagement to inventory the
  customer's test environment and baseline their existing AV
- **Live tabletop exercise** — half-day to full-day on-site (or
  remote) running the ransomware-simulation script. The script
  includes 8-12 distinct attack patterns:
  - Synthetic mass file enumeration (mimics encryption preamble)
  - Anomalous child-process spawn (`cmd.exe → powershell -enc → ...`)
  - Network burst to never-seen destinations
  - Constitutional distance regression on a known-benign binary
  - Cross-process credential harvesting pattern
  - Backup tampering signatures
  - Shadow copy deletion sequence
  - Process-injection markers
- **Written incident report** — signed PDF covering each pattern's
  detection result, time-to-alert, false-positive rate, threshold
  tuning recommendations, gap analysis vs. existing AV
- **30-day post-engagement** — review of any real incidents seen
  during the trial, threshold-tuning support, optional conversion
  to ongoing Shape A licenses

### Backend modules used

| Deliverable | Module / endpoint | Status |
|---|---|---|
| Process monitoring daemon | `axiom_os_shield.py` `ConstitutionalOSShield` | shipped |
| Process snapshot data | `axiom_os_shield.py` `ProcessSnapshot` (with `file_access_rate`, `child_procs`, `network_conns` fields) | shipped |
| Process manifold (baseline establishment) | `axiom_os_shield.py` `ProcessManifold` | shipped |
| File enumeration rate | `ProcessSnapshot.file_access_rate` (files/sec observed) | shipped |
| Sovereign escalation thresholds (L1/L2/L3/L4) | `axiom_os_shield.py:44` | shipped |
| Real psutil actions (suspend / kill / restore) | `axiom_os_shield.py:268+` | shipped |
| Daemon control endpoints | `POST /shield/start`, `POST /shield/stop`, `GET /shield/status`, `POST /shield/tick`, `POST /shield/restore` (`axiom_server.py`) | shipped |
| Live status view | `GET /os/shield/status` (`examples/axiom_guard_api.py`) | shipped |
| Shield console (live dashboard) | `docs/axiom_os_shield_console.html` (with zero-day notification panel from commit `2a95540`) | shipped |
| Signed incident log | `axiom_os_shield_log.jsonl` (append-only, HMAC-signed) | shipped |
| Constitutional distance computation | `axiom_latent_v2.py ManifoldChecker` | shipped |
| FixPlaybook for known-ransomware patterns | `axiom_fix_playbook.py` (referenced in shield demo scenarios) | shipped |

### Gaps to ship

1. **Cross-platform install path (Shape A)** — today the monitor
   runs from a checked-out repo. Needs:
   - `pipx install axiom-shield-lite` (Linux/macOS)
   - `.msi` installer for Windows
   - System-service registration (launchd / systemd / Windows
     Service)
   - Auto-update mechanism
2. **Local-dashboard packaging** — the shield console exists but
   assumes the operator manually edits the endpoint field. For
   product Shape A, the installer should drop a desktop shortcut
   that opens the dashboard pre-pointed at `localhost:8003/shield`.
3. **Incident-report PDF generator** — shared gap with all other
   products (single PDF generator, multiple templates).
4. **Tabletop simulation harness (Shape B)** — write the 8-12
   ransomware-pattern simulation scripts as deterministic,
   reproducible workloads with no actual destructive effects.
   Approximate effort: 1 week for the scripts + 1 week for the
   automated report generator. **This is the largest single piece
   of work in this product.**
5. **AV gap-analysis tooling (Shape B)** — automated comparison
   harness that runs each simulated pattern, observes what the
   customer's existing AV detected, and produces the comparison
   matrix. Requires API integrations with CrowdStrike Falcon,
   SentinelOne, Microsoft Defender XDR, Carbon Black. v1 supports
   the top 2; others added by request.
6. **Threshold-tuning UI** — today thresholds are constants in
   `axiom_os_shield.py:44`. Operators need a UI to view their
   current thresholds, see the false-positive rate at each, and
   tune.
7. **Multi-machine fleet view (Shape A Pro)** — SMB customers will
   run Shield Lite on 5-50 machines. Need a central aggregator
   that pulls each machine's status into one dashboard.
8. **Constitutional baseline learning** — the LEARNING_SECONDS=60
   default is too short for production environments. Need an
   adaptive learning mode that runs for 1-7 days before enabling
   enforcement, with confidence intervals on each process's
   baseline.

Estimated effort:
- Shape A (monitor product): **1-2 weeks**
- Shape B (tabletop service): **4-6 weeks** (mostly the simulation
  harness and the AV-comparison tooling)

### Target customer + pricing

#### Shape A — Local monitor

- **Who buys this:**
  - Solo developers / power users worried about supply-chain
    attacks on their dev machine
  - SMB IT admins running 5-50 machines who can't afford
    CrowdStrike but want better-than-Defender behavioral coverage
  - Security-conscious creators / consultants handling client data
- **Pricing model:**
  - **Free** — single-machine local install, basic dashboard,
    7-day log retention, no fleet view
  - **Pro $9/mo per machine** — fleet view, 90-day log retention,
    Slack/email alerts, custom thresholds, priority support
  - **Team $49/mo per team (up to 10 machines)** — shared fleet
    view, role-based access, audit log of all responses,
    integration with Slack / PagerDuty / generic webhook

#### Shape B — Tabletop simulation service

- **Who buys this:**
  - CISO / VP Security at mid-market companies (200-2,000
    employees) doing tabletop exercises for cyber-insurance audits
  - Cyber-insurance providers themselves, who want to commission
    tabletop reports for high-risk clients
  - Federal contractors meeting FedRAMP / CMMC / FISMA tabletop
    exercise requirements
- **Pricing model:**
  - **Standard Tabletop** $15K-30K — half-day exercise, 8-pattern
    simulation, written report within 2 weeks
  - **Comprehensive Tabletop** $40K-75K — full-day on-site,
    12-pattern simulation, AV gap-analysis matrix, threshold-
    tuning recommendations, 30-day post-engagement support
  - **Annual Engagement** $100K-250K/yr — quarterly tabletops,
    ongoing threshold tuning, on-call analyst for real incidents,
    optional conversion to managed Shape A deployment
- **Ballpark comparables:**
  - **CrowdStrike tabletop services** — $25K-100K per exercise
  - **Mandiant red-team** — $50K-500K per engagement
  - **CISA tabletop exercises** — free but constrained scope
  - The differentiator: AXIOM tabletops are signed, reproducible,
    and produce an `.axiom` Skill Pack of new detection rules at
    the end. No competitor outputs a portable artifact like that.

### Cross-references

- **Pairs with Skill Pack Builder:** every tabletop output is a new
  Skill Pack of detection rules (`axiom/shield-ransomware-2026q2`
  style). Customer keeps the pack, can adopt it, or share it.
- **Pairs with Flight Recorder:** Shield Lite events stream into
  Flight Recorder as process-level decision events; one customer
  contract covers both.
- **Pairs with Nightly Review:** the previous day's shield events
  feed into the nightly retrospective, producing weekly
  threshold-tuning recommendations.
- **No overlap with the other six products** — Shield Lite is the
  only one operating at the process / OS layer instead of the
  LLM-call / API layer.
- ORVL alignment: ORVL-013 (Sovereign OS Shield) is the spine.
- Related .axiom specs: `axiom_files/core/axiom_os_shield.axiom`
- Related modules: `axiom_os_shield.py`, `axiom_latent_v2.py`
  (ManifoldChecker for distance), `axiom_fix_playbook.py` (known-
  pattern matching)
- Related consoles: `docs/axiom_os_shield_console.html` (already
  ships with the zero-day notification panel from commit `2a95540`)
- Related tests: there are already shield scenario simulations
  embedded in `docs/axiom_os_shield_console.html` (ransomware,
  insider exfiltration) — usable as the seed for the tabletop
  simulation harness

### Notes / open questions

- **Two-shape product is intentional and supports a sales motion.**
  Shape B (tabletop service) is the higher-ticket but easier-to-sell
  enterprise entry point. After a tabletop, the customer has seen
  the product in action and is qualified to buy Shape A licenses
  for their production fleet. Don't sell Shape A to enterprises
  cold; sell the tabletop first.
- **Open question — Windows priority:** Shape A needs Windows
  support to address most of the SMB market, but `psutil`-based
  detection is most mature on Linux. Recommend Linux + macOS at
  Free / Pro launch, Windows for Team tier in v1.5.
- **Open question — false-positive cost:** ransomware tabletops
  produce a lot of false positives at first (every cron job that
  enumerates files looks suspicious). The threshold-tuning workflow
  needs to be **fast** (the customer can dismiss-and-tune within 30
  seconds), otherwise the product produces alert fatigue and gets
  uninstalled.
- **Open question — does Shape B require ITAR/CMMC compliance for
  federal customers?** Federal contractors are the largest tabletop-
  buying segment. If Shape B is sold to them, our consultants need
  ITAR-compliant equipment and clearances. Either invest in this
  certification path or restrict federal sales to a partner.
- **Open question — incident-response cross-sell:** customers who
  experience a real incident during a Shape A deployment will want
  IR consulting. We don't currently offer IR. Either build that
  capability (much bigger lift) or partner with an existing IR firm
  (Mandiant, Coveware, etc.) and refer.
- **Open question — public marketing of the constitutional-
  distance framing:** "constitutional distance for processes" is
  novel positioning, but it requires explanation. The marketing
  language should probably stay closer to "behavioral early-
  warning" and leave the constitutional framing for the technical
  documentation. Sell what the customer understands.

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
