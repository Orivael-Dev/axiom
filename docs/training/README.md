# Axiom — training manuals

Internal training material for the products Orivael Dev ships. One
manual per product. These are NOT the public-facing developer docs —
those live at `docs/firewall/`. Use these to onboard new engineers,
brief support staff, and prep sales conversations.

Reading order for a new hire on the team:

1. **[Axiom Intent Firewall](firewall.md)** — the SaaS itself
2. **[Skill Pack Format](skill-pack-format.md)** — the unit of distribution
3. **[Skill Pack Registry](skill-pack-registry.md)** — how packs reach customers
4. **[First-party Skill Packs](first-party-packs.md)** — what each of the 9 packs covers
5. **[Python SDK](python-sdk.md)** — the customer integration path
6. **[TypeScript SDK](typescript-sdk.md)** — same, in JS-land

Each manual is self-contained — you can read just the one if you only
need that surface.

## What ships today

| Product | What it is | Status |
|---|---|---|
| **Intent Firewall** | Multi-tenant SaaS at `firewall.orivael.dev`. `/v1/guard/check` API + dashboard. | Phase 1 complete; ready for soft launch |
| **Python SDK** | `pip install axiom-firewall` | v0.1.0, 13 tests green |
| **TypeScript SDK** | `npm install @axiom/firewall` | v0.1.0, 13 tests green |
| **Skill Pack format** | Signed JSON manifest spec (`format_version 1.0`) | Committed for 2 years of backward-compat |
| **Skill Pack registry** | Read-only HTTP service at `packs.orivael.dev` | 9 first-party packs live |
| **First-party packs** | 9 curated policy bundles | Customer Support, Code Review, FDCPA, HIPAA Intake, GDPR Article 9, PCI-DSS, COPPA, SEC Rule 10b-5, Prompt-Injection Strict |

## What's coming

Per [`docs/GAME_PLAN.md`](../GAME_PLAN.md):

- **Phase 3** — Data Gate, Flight Recorder, Nightly Review (compliance-buyer wave)
- **Phase 4** — Certify · Agent Audit, Shield Lite, CallGuard (premium-enterprise wave)

## House rules for support + sales

- **Never claim a Skill Pack is "certified" or "audited."** They're
  curated baselines; legal review is the customer's responsibility.
  The packs are MIT-licensed and explicit about scope in each
  description field.
- **HMAC signature ≠ encryption.** When a customer asks "is my prompt
  encrypted?" the answer is "the verdict response is signed for
  audit replay; prompt content is not stored." Don't confuse the
  two surfaces.
- **Free-tier is hard-capped; paid tiers are metered.** A free-tier
  customer who hits 1,000 calls gets a 429 with `Retry-After`. A
  paid-tier customer who hits their bundle keeps serving — Stripe
  meters the overage.
- **The Firewall is not an LLM.** It's a classifier. Treat it as
  policy enforcement, not content generation. If a customer expects
  rewrites or completions, route them to a different product.
