# Axiom — Game Plan

How to take the nine products in `docs/PRODUCTS.md` from spec to
shipped revenue. Written 2026-05-16. Revisit monthly.

---

## TL;DR

**6-month shipping plan in four phases**, sequenced for cash early
and platform moat eventually:

- **Phase 1 (weeks 1–4)** — Ship **Intent Firewall** (smallest gap,
  fastest revenue). Validate the AI-safety-API buyer.
- **Phase 2 (weeks 5–8)** — Ship **Skill Pack Builder** + **MCP**.
  These are the foundation + distribution flywheel for everything
  after.
- **Phase 3 (weeks 9–14)** — Ship **Data Gate** + **Flight Recorder**
  + **Nightly Review**. The compliance-buyer wave.
- **Phase 4 (weeks 15–22)** — Ship **Certify · Agent Audit** +
  **Shield Lite (both shapes)** + **CallGuard**. The premium-
  enterprise wave.

Five foundational decisions need to be made **this week** before any
phase ships (see §1 below).

---

## 1. Foundational decisions (THIS WEEK, blocking everything)

These are technical and brand choices that cascade through every
product. Decide once; adopt everywhere. If we don't lock these now,
every product re-decides them inconsistently.

| # | Decision | Why blocking |
|---|---|---|
| 1 | **Skill Pack format stability commitment** — promise 2-year backward compat on `format_version` field before public registry opens | Once developers depend on `format_version: 0.1`, breaking it destroys trust. Several products depend on Skill Packs as config artifact. |
| 2 | **Intent taxonomy reconciliation** — `axiom_intent_classifier.py` uses {INFORM, CLARIFY, REFUSE, HARM, DECEIVE, UNCERTAIN}; `axiom_anf_emulator.py` uses {INFORM, REQUEST, EXPLORE, MANIPULATE, DECEIVE, HARM}. Pick one canonical, reconcile the other. | Intent Firewall and CallGuard both reference "intent class" — if they mean different things, customers will hit confusing edge cases. |
| 3 | **Multi-tenant isolation pattern** — SQLite-per-tenant vs single Postgres with `tenant_id` partitioning vs per-tenant process. | Same pattern reused by Flight Recorder, CallGuard, Data Gate, Intent Firewall, Skill Pack Builder. Pick once. |
| 4 | **PDF report generator architecture** — single shared generator (Jinja → WeasyPrint or equivalent), shared by Certify / CallGuard / Data Gate / Nightly Review / Shield Lite incident reports. | Five products depend on this. Building five separate PDF generators is waste. |
| 5 | **Brand domains + signing-key infrastructure** — register `packs.axiom.ai`, decide managed-KMS provider for publisher keys, lock down `axiom.ai` and product-name subdomains. | The marketplace can't ship without these. Domain squatting is real. |

These are <1 week of decisions, mostly meetings + writing.

---

## 2. Phase 1 — Intent Firewall (weeks 1–4)

**Goal:** First paying customer. Validate that someone will write a
check for AI-safety-as-a-service.

### Why Intent Firewall first

| Factor | Why this product wins |
|---|---|
| Build effort | ~1 week. The backend (`axiom_guard_api.py`) is 95% shipped. |
| Buyer | Solo developers and indie SaaS founders — easiest to reach, fastest to close, smallest contract values but volume. |
| Competitive landscape | Mature (Lakera, Pangea, Rebuff). Customers already understand the category. |
| Revenue model | Subscription + overage. Stripe billing meter is well-understood. |
| Validation value | Proves the AI-safety-API buyer exists. If nobody buys, we learn that fast and cheap. |
| Cross-sell setup | Every other Axiom product can later be sold to the same buyer. |

### Phase 1 work breakdown (weeks 1–4)

| Week | Deliverable |
|---|---|
| 1 | Foundational decisions (§1). Begin developer dashboard build. |
| 2 | API key auth + Stripe billing meter wired. Python + TypeScript SDK skeletons. |
| 3 | Free-tier abuse defense. Multi-tenant policy isolation. Quickstart docs. |
| 4 | Soft-launch to a 20-person developer waitlist. Iterate on the 5 sharpest pieces of feedback. |

### Phase 1 success metrics

- 50+ developer signups for the free tier
- 10+ engaged users (sent ≥100 API calls in week 4)
- 3+ paying customers at the $49 Indie tier
- One enterprise procurement conversation initiated
- Honest no-go signal if any of these miss by >50%

### Phase 1 risks + mitigations

- **Risk:** free-tier abuse. **Mitigation:** soft-throttle past tier rather than hard-cut; email-domain blocklist.
- **Risk:** Lakera or Pangea drops competitive pricing. **Mitigation:** lean on signed verdicts as the differentiator; this is the regulator-facing moat they don't have.
- **Risk:** developers don't understand 6-class taxonomy. **Mitigation:** default policy is binary (allow/block); 6-class taxonomy only surfaces in the verdict response for power users.

---

## 3. Phase 2 — Skill Pack Builder + MCP (weeks 5–8)

**Goal:** Build the foundation + distribution flywheel. Increase the
value of every product to come.

### Why these two together

Skill Pack Builder is the foundation: every other Axiom product becomes
configurable via Packs. MCP is the distribution surface: developers
discover Axiom through their existing Claude Code / Cursor / Codex
workflow. They reinforce each other — a developer installs the MCP
server, sees Skill Packs in the registry, adopts one, then realizes
the Firewall in their prod app can use the same pack.

### Phase 2 work breakdown (weeks 5–8)

| Week | Deliverable |
|---|---|
| 5 | Skill Pack Builder CLI polish + scaffolder. MCP installer (`pipx`, `npx`, Homebrew). |
| 6 | Public registry at `packs.axiom.ai` (v1: first-party packs only). MCP signed-distribution path. |
| 7 | 5–10 curated first-party Packs (Customer Support Base, Code Review Base, FDCPA, HIPAA Intake, GDPR Article 9). MCP dashboard at `localhost:8002/mcp`. |
| 8 | Smithery.ai listing. Publish Packs. Wire the Firewall (Phase 1) to optionally load a Skill Pack as its policy. |

### Phase 2 success metrics

- 200+ Skill Pack installs from the public registry
- MCP server installed by ≥30 developers via Smithery
- ≥2 Firewall customers (from Phase 1) opt into Pack-driven policy
- 1+ third-party developer expresses interest in publishing a public Pack
- Domain `packs.axiom.ai` live, with sub-second cold-start install

### Phase 2 risks + mitigations

- **Risk:** registry stays empty (chicken-and-egg). **Mitigation:** seed with 5–10 first-party Packs that solve real problems; ship the public registry only after they exist.
- **Risk:** LangChain Hub or HuggingFace Hub ships a competing format first. **Mitigation:** the moat is signed audit chain + constitutional safety tests as first-class format features — neither competitor has these natively. Ship fast.
- **Risk:** developer adoption is slow. **Mitigation:** Smithery.ai gives free distribution; we don't have to build our own discovery channel.

---

## 4. Phase 3 — Data Gate + Flight Recorder + Nightly Review (weeks 9–14)

**Goal:** Address the compliance buyer. Move from "developer tools"
to "enterprise data governance."

### Why these three together

- **Data Gate** is the best market gap of the nine — genuinely missing,
  high differentiation.
- **Flight Recorder** + **Nightly Review** are the upsell engine — every Phase 1
  customer is already producing manifests that Flight Recorder collects
  and Nightly Review mines.
- All three sell to overlapping buyers (CPO / DPO / compliance head),
  so the same sales motion converts across them.

### Phase 3 work breakdown (weeks 9–14)

| Week | Deliverable |
|---|---|
| 9–10 | Data Gate: GDPR Article 9 + PCI taxonomies (on top of shipped HIPAA). Per-agent policy engine. Memory write/read gate. |
| 11 | Data Gate: pgvector connector. Right-to-erasure workflow. Policy authoring UI. |
| 12 | Flight Recorder: time-series dashboard. Multi-tenant log isolation. Search/filter index. |
| 13 | Flight Recorder: replay UI. PDF/CSV/SIEM export adapters. Outbound webhook + email + Slack. |
| 14 | Nightly Review: rule-suggestion engine. Report templates. Scheduling + delivery fan-out. Soft-launch to existing customers + 10 enterprise prospects. |

### Phase 3 success metrics

- First Data Gate Enterprise contract signed (target: $30K-100K ARR)
- 10+ Flight Recorder paying customers (Growth tier)
- 5+ Nightly Review subscribers (Standard or Pro tier)
- One referenceable case study from a regulated-industry customer

### Phase 3 risks + mitigations

- **Risk:** enterprise sales cycles are 6 months, not 6 weeks. **Mitigation:** start prospecting in Phase 2 (week 5). Build pipeline 4 weeks ahead of build.
- **Risk:** right-to-erasure across embeddings is research-grade. **Mitigation:** explicit deletion-certificate limitation language reviewed by counsel before any GDPR contract is signed.
- **Risk:** Flight Recorder storage cost surprises. **Mitigation:** pick the storage architecture in Foundational Decision §1.3; size hardware for the first 10 enterprise customers; revisit at customer 25.

---

## 5. Phase 4 — Certify + Shield Lite + CallGuard (weeks 15–22)

**Goal:** Premium-enterprise revenue. The high-ticket deals.

### Why these three last

- **Certify · Agent Audit** is best sold *after* customers already use
  Skill Packs (Phase 2) — the Pack becomes the canonical artifact.
- **Shield Lite Shape B (tabletop)** is the highest-ticket per-deal
  product ($15K–$75K). Best sold after we have references from
  Phase 1–3.
- **CallGuard** has the largest build (3–4 weeks) and depends on
  PCI/HIPAA-compliant hosting (operational lift). Save for last.

### Phase 4 work breakdown (weeks 15–22)

| Week | Deliverable |
|---|---|
| 15–16 | Certify · Agent Audit: customer intake workflow, badge artifact + verification URL, scoring rubric, Tier 1 docs (engagement letter, SOW, data-handling). |
| 17–18 | Shield Lite Shape A: pipx / npm / `.msi` installers, fleet view, threshold-tuning UI, adaptive baseline learning. |
| 19–20 | Shield Lite Shape B: tabletop simulation harness (8–12 scripted patterns), AV gap-analysis tooling, written-report generator. **First paid tabletop with a mid-market customer.** |
| 21–22 | CallGuard: audio intake (Deepgram), 2–3 industry rule engines (FDCPA + banking UDAAP + telehealth HIPAA), agent scorecard system, regulator-format reports. PCI/HIPAA hosting deployed. |

### Phase 4 success metrics

- First Certify badge issued + 3 paying Certify customers
- First Shield Lite tabletop sold ($25K+)
- 1 CallGuard pilot signed (BPO mid-market)
- Total ARR run-rate ≥ $500K by week 22

### Phase 4 risks + mitigations

- **Risk:** PCI/HIPAA hosting takes longer than 8 weeks. **Mitigation:** restrict initial CallGuard pilots to non-PCI / non-HIPAA verticals (debt collection without on-call payment, telecom outside healthcare).
- **Risk:** Shield Lite tabletop content is technical and slow to build. **Mitigation:** seed the simulation harness from the existing scenarios in `docs/axiom_os_shield_console.html` (ransomware, insider exfiltration patterns are already scripted).
- **Risk:** Certify scoring rubric becomes a customer dispute. **Mitigation:** publish the rubric explicitly; transparent scoring beats "trust us."

---

## 6. Customer development track (runs PARALLEL to build)

Building without selling is dead air. This track runs continuously
from week 1.

### Cadence

| Cadence | Activity | Why |
|---|---|---|
| Weekly | 5 outbound conversations with prospects per week (cold or warm) | Builds pipeline for whichever product is shipping next phase |
| Weekly | 1 customer-discovery interview with someone who already manages AI safety/compliance in production | Catches false assumptions before they ship |
| Weekly | Pricing-validation conversation — ask current and prospective customers what they'd pay for each tier | Pricing assumptions in `PRODUCTS.md` are guesses until validated |
| Monthly | Sales-collateral refresh — case studies, demo flows, comparison tables | Each phase generates new material |

### Buyer personas to develop pipeline against

| Phase | Persona | Where to find them |
|---|---|---|
| 1 | Indie SaaS founder using Anthropic/OpenAI APIs | Twitter / r/SaaS / ProductHunt launches / IndieHackers |
| 1 | AI engineer at a startup (Series A–C) | YC / Pioneer / company-AI-eng job postings |
| 2 | Developer using Claude Code / Cursor on serious codebase | Cursor Discord, MCP early-adopter community |
| 3 | CPO / DPO at a regulated mid-market company | LinkedIn (search by title), IAPP membership lists |
| 3 | VP of AI engineering at a Fortune 1000 | Direct outreach, conference circuit (RSA, Black Hat, AI Engineer Summit) |
| 4 | CISO at mid-market doing tabletop exercises | Cyber-insurance broker networks; FedRAMP / CMMC compliance circles |
| 4 | Compliance head at a BPO / debt collection / banking CS | LinkedIn + cold outreach via industry trade pubs |

### Sales motions per product

- **Self-serve** (Firewall, MCP, Skill Pack Builder free tier) — landing page → free signup → activation email → Pro upgrade prompt
- **Sales-assisted** (Firewall Team, Data Gate, Flight Recorder) — landing page → demo request → 30-min call → procurement
- **Enterprise** (Certify, CallGuard, Shield Lite tabletop) — direct outreach → discovery call → custom proposal → SOC 2/legal review → signature

---

## 7. Cross-cutting decisions and tracks

These don't fit a single phase; they affect everything.

### Hiring / capacity

The plan above is **roughly one founder + one engineer for 6 months**.
If team is solo, multiply by 1.5–2×. If team expands to 3+ engineers
by Phase 3, Phases 3 and 4 can run partially in parallel and the
6-month plan compresses to ~4 months.

### Capital

- Phase 1 + 2 can be bootstrapped (small dev costs, no enterprise sales infrastructure)
- Phase 3 needs ~$50–100K runway for cloud + compliance attestation work (SOC 2 Type II readiness)
- Phase 4 needs ~$200–400K for sales / IR-grade hosting / counsel
- Total external capital realistically needed: **$300–500K to comfortably get to the Phase 4 milestones** unless revenue from Phase 1–3 funds the rest (likely if any Phase 3 enterprise deal lands)

### Brand + marketing

- Week 1: lock `axiom.ai` (or chosen brand domain) + product subdomains
- Week 2: simple landing page per product (template, not custom design per product)
- Week 4: publish first technical blog post (token-economics, reverse-QRF, or constitutional-distance — already-written content from `docs/ANF_TOKEN_ECONOMICS.md` is publishable)
- Week 8: launch Skill Pack Builder + MCP on ProductHunt / HackerNews / Smithery simultaneously — single coordinated event
- Week 14: Phase 3 launch event (Data Gate is the headline) — webinar with one named customer
- Ongoing: bi-weekly blog posts on real customer use cases

### Legal / compliance posture

- Phase 1 ship: standard SaaS Terms of Service + Privacy Policy
- Phase 2 ship: data-processing agreement (DPA) template ready for any EU customer
- Phase 3 ship: SOC 2 Type I attestation in progress; HIPAA BAA template; right-to-erasure certificate language reviewed by counsel
- Phase 4 ship: SOC 2 Type II complete; PCI DSS Level 1 if CallGuard sells to a card-on-call vertical; ITAR/CMMC partnership for federal contracts

---

## 8. Things we are DELIBERATELY not doing

- **Full AXM model-format replacement for GGUF.** Out of scope for v1. Skill Pack Builder is the digestible version; the GGUF replacement is a v3+ ambition once we have ecosystem.
- **Replacing antivirus.** Shield Lite is positioned as complementary, not competitive. Direct AV competition is a different company.
- **Generative AI model training.** The reverse-QRF synthetic-trajectory generator we shipped this branch is for *enabling* customer training pipelines, not for us training our own foundation model.
- **Hardware / Jetson / edge.** ANF / sparse activation / ASPA / mobile angles are real patents but not currently products. Could be Phase 5+ if a customer specifically demands edge deployment.
- **Consumer / openclaw.ai productization** beyond the TODO in `docs/OPENCLAW_TODO.md`. The nine-product B2B catalog is the focus. Consumer is a separate company-shape decision.
- **Audit firm partnerships** until Phase 4. Certify is a self-service tier first; partnerships with Big-4 audit firms are a different go-to-market that we shouldn't blend in early.

---

## 9. Monthly checkpoint questions

At the end of each month, answer these honestly:

1. How many paying customers do we have, across how many products?
2. What's the run-rate ARR, and what's the next-quarter forecast?
3. Which assumption from this game plan has been falsified by customer behavior?
4. What are we doing that the plan didn't predict, and why?
5. What's blocking the next phase from starting on time?
6. What would we do differently if we restarted today?

If any answer is "I don't know" or "still need to find out," that's
the work for the next month.

---

## 10. Cross-references

- Product specs: `docs/PRODUCTS.md`
- Token economics + reverse-QRF technical content (publishable): `docs/ANF_TOKEN_ECONOMICS.md`
- Reverse-QRF module + tests + demo (shipped this branch): `axiom_qrf_reverse.py`, `tests/test_axiom_qrf_reverse.py`, `examples/reverse_qrf_demo.py`
- Zero-day notifications (shipped this branch): `examples/axiom_guard_api.py` `/vulnguard/{scan,status}`, `docs/axiom_os_shield_console.html`
- HumanEval token-tracking (shipped this branch): `examples/axiom_humaneval_run.py`
- Consumer-side productization TODO (separate track): `docs/OPENCLAW_TODO.md`
- Branch with everything: `claude/test-axiom-security-KgUcQ`
