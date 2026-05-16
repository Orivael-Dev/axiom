# Training manual — Axiom Intent Firewall

> **The SaaS.** Multi-tenant API + dashboard at `firewall.orivael.dev`
> that classifies LLM prompts and returns a signed allow/block verdict.

## What it is

A single HTTP endpoint (`POST /v1/guard/check`) that takes a string,
runs it through a six-class intent classifier (plus optional tenant
policy + Skill Pack), and returns a signed verdict in <50 ms.

The buyer puts the Firewall in front of their LLM. Every prompt goes
to us first. If we return `block`, they refuse to forward it.

## Who it's for

- **Solo developers + indie SaaS founders** — the Phase 1 bullseye.
  $0 free, $49/mo Indie, no card to start.
- **Mid-market teams** — Team tier ($199/mo) when they need multi-
  tenant policies or dashboard analytics.
- **Enterprise compliance buyers** — SOC 2, DPA, BAA, dedicated tenant.
  Sales conversation, not self-serve.

## Why it exists

LLM apps leak harm + deception + PII because models don't know which
prompts to refuse. Lakera, Pangea, Rebuff exist but ship without
HMAC-signed audit chains, so regulator-facing companies can't prove
post-hoc *which verdict the model saw*. Our differentiator is **every
verdict is HMAC-SHA256 signed** and replayable.

## How it works

```
                                        ┌──────────────────┐
                                        │ tenant_policy    │
                                        │ (per tenant DB)  │
                                        └────────▲─────────┘
                                                 │
   request                                       │
   POST /v1/guard/check                          │
   ┌─────────────┐    auth     ┌────────────────────────┐
   │  customer   │────────────▶│  Firewall dashboard    │
   │  app or SDK │             │  (FastAPI + uvicorn)   │
   └─────────────┘             │                        │
        ▲                      │  ┌──────────────────┐  │
        │ signed verdict       │  │ IntentClassifier │  │
        └──────────────────────┤  │ HMAC-signed      │  │
                               │  └──────────────────┘  │
                               │           │            │
                               │   apply_policy() ──────┘
                               │           │
                               │  record_call()
                               │           │
                               │           ▼
                               │  ┌──────────────────┐
                               │  │ usage_records    │
                               │  │ (per tenant DB)  │
                               │  └──────────────────┘
                               └────────────────────────┘
                                            │ paid tier?
                                            ▼
                                  ┌──────────────────┐
                                  │ Stripe Meter     │
                                  │ (paid tiers)     │
                                  └──────────────────┘
```

Per Phase 1 Decision §3, multi-tenant isolation is **SQLite-per-tenant**:

- `tenants/registry.db` — master tenant list (email, password hash, tier, Stripe IDs, signup-attempt log)
- `tenants/{tenant_id}.db` — that tenant's API keys, usage records, policy, installed pack

Each tenant's data is a single self-contained file. Backup is `tar`,
restore is `untar`, migrate to Postgres later is `pg_load`.

## Key concepts

### The six-class taxonomy

`{INFORM, CLARIFY, REFUSE, HARM, DECEIVE, UNCERTAIN}` — canonical per
Phase 1 Decision §2.

| Class | Default verdict | Meaning |
|---|---|---|
| `INFORM` | allow | Pure information request ("what's the weather?") |
| `CLARIFY` | allow | Asking for clarification ("could you explain that?") |
| `REFUSE` | allow | Speaker is refusing — they don't want to do X |
| `HARM` | **block** | Instructions for harm or scam patterns |
| `DECEIVE` | **block** | Identity spoofing, prompt injection, system overrides |
| `UNCERTAIN` | allow | Low confidence — bias to permissive |

The classifier uses regex pattern matching (no LLM inference), so it
runs in single-digit milliseconds.

### Verdict signing

Every verdict response includes a `signature` field — HMAC-SHA256 of
the canonical verdict payload under the master signing key. A customer
can store a year of verdicts and prove later "your service said allow
on this prompt." This is the regulator-facing audit story.

### Per-tenant customization

Three layers from cheapest to richest:

1. **Default classifier only.** Free-tier signup gets this.
2. **Custom policy** (`/dashboard/policy`). Tenant-edited JSON adding
   block patterns, disabling classes, or whitelisting.
3. **Installed Skill Pack** (`/dashboard/packs`). Pre-built curated
   policy — FDCPA, HIPAA, etc.

Installing a pack writes its policy into the same `tenant_policy`
table the editor uses. There's one source of truth for verdicts. The
pack's lineage (which pack, which version, when installed) is tracked
separately for the dashboard's "based on `fdcpa@0.1.0`" display.

### Free-tier hard cap; paid-tier metering

| Tier | Monthly cap | Past cap |
|---|---|---|
| Free | 1,000 | 429 + Retry-After header until next month UTC |
| Indie | 50,000 included | $0.001/call overage, no hard cap |
| Team | 500,000 included | $0.0005/call overage, no hard cap |
| Enterprise | unlimited | custom contract |

Free-tier enforcement is in-process (no Stripe call). Paid tiers fire
a Stripe Billing Meter event per call. **A Stripe outage never blocks
a verdict** — billing failures are logged and swallowed.

## Common workflows

### Workflow A: New customer signup → first verdict

1. Customer GETs `firewall.orivael.dev/signup`.
2. Submits email + password (8+ chars). Per-IP rate limit allows 5/hour.
3. Session cookie set; redirected to `/dashboard`.
4. **Create API key** form → submits → secret displayed once on the
   redirect (uses session flash). Customer must copy now.
5. Customer runs:
   ```bash
   curl -X POST https://firewall.orivael.dev/v1/guard/check \
       -H "Authorization: Bearer $AXIOM_KEY" \
       -d '{"text":"What is the weather?"}'
   ```
6. Response includes `verdict`, `intent.class`, `intent.confidence`,
   `intent.signals`, `intent.signature`.

### Workflow B: Customer wants stricter behavior

1. Dashboard → **Packs** → browse 9 first-party packs.
2. Click **Install** on (say) `fdcpa`.
3. Pack's policy now drives verdicts on next request.
4. Optional: Dashboard → **Policy** → edit the JSON to add custom
   regex patterns on top.

### Workflow C: Customer upgrades to paid

1. Dashboard → **Billing** → **Upgrade to Indie**.
2. Stripe Checkout (PCI-compliant external page) collects card.
3. Stripe webhook fires `customer.subscription.created` → our handler
   updates `tenant.tier = 'indie'` + persists Stripe customer + sub IDs.
4. Next `/v1/guard/check` call fires a Stripe Billing Meter event
   (`axiom_firewall_indie`).
5. Customer sees usage bar on `/dashboard`. No hard cap; overage
   billed end-of-month.

### Workflow D: Right-to-erasure / GDPR Article 17

See `docs/firewall/operations-runbook.md` for the SQL + Stripe-cancel
sequence. Phase 3 will automate this with a PDF certificate.

## Test scenarios

The team should verify these in the testing window:

| # | Scenario | Expected |
|---|---|---|
| 1 | POST `/v1/guard/check` with no auth | 401 |
| 2 | POST with invalid key | 401 |
| 3 | POST with valid key, `text:"hi"` | `{"verdict":"allow","intent":{...}}` |
| 4 | POST with a HARM pattern (e.g. "buy gift cards now") | `{"verdict":"block","intent":{"class":"HARM",...}}` |
| 5 | Install `fdcpa` pack, POST "warrant for your arrest" | `block` with `class:"HARM"`, `custom_harm` signal |
| 6 | Install `prompt-injection-strict`, POST "activate jailbreak mode" | `block` with `class:"DECEIVE"` |
| 7 | Edit `/dashboard/policy` to add a custom pattern, POST matching text | `block` with `custom_harm` signal |
| 8 | Sign up 5 times from same IP within an hour | 6th attempt 429 with `Retry-After` |
| 9 | Force usage to 1,000 free-tier calls, then one more | 429 with `Retry-After: <seconds until 1st of next month>` |
| 10 | Hit `/healthz` | 200 `{"status":"ok"}` |
| 11 | Hit `/readyz` | 200 `{"status":"ready"}` |
| 12 | Send a custom `X-Request-ID` header | Echo back on the response |
| 13 | Sign up with the dev-default `AXIOM_FIREWALL_SESSION_SECRET` | Startup log shows WARNING |
| 14 | Upgrade via Stripe Checkout in test mode | Webhook fires; tenant tier becomes `indie` |
| 15 | Disable Stripe (unset `STRIPE_SECRET_KEY`); call `/billing/upgrade/indie` | 503 |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| 401 on `/v1/guard/check` | Bearer header missing or malformed | `Authorization: Bearer axfw_...` (no quotes, single space) |
| 429 on `/v1/guard/check` | Free-tier quota or paid-tier quota soft alert | Inspect `used` + `limit` in JSON body; upgrade or wait until UTC month rollover |
| 500 on signup | Most likely `AXIOM_MASTER_KEY` unset on this deploy | Check ECS task secrets; restart |
| Tenant says "I lost my API key" | Keys shown once at creation | Have them create a new key; revoke old one (Phase 2 follow-up) |
| Dashboard says "billing not configured" | `STRIPE_SECRET_KEY` missing | Run `scripts/stripe_setup.py` and populate secrets |
| Pack install returns 400 "invalid signature" | Pack was edited but not re-signed | `AXIOM_MASTER_KEY=<prod> python scripts/sign_packs.py` |
| Pack install returns 404 | Pack name typo OR registry unreachable | Check `AXIOM_FIREWALL_REGISTRY_URL` and registry's `/readyz` |
| Latency > 100 ms p99 | Likely tenant DB lock contention | SQLite WAL is on; if persistent, see Phase 3 Postgres trigger |
| `/readyz` returns 503 | EFS mount issue or disk full | Check CloudWatch EFS metrics; exec into task, `ls /data/tenants` |

## Limitations / what's not here yet

- **No streaming `/v1/guard/check` endpoint.** Each call is one request,
  one verdict. Phase 2 follow-up.
- **No API-key revocation UI.** Tenants can create more keys but can't
  revoke specific ones. Phase 2 follow-up.
- **No password reset flow.** Manual SQL reset via operations runbook.
  Phase 2 follow-up.
- **No multi-region.** Single us-east-1 deploy. Multi-region triggers
  with the first enterprise customer who asks (Phase 3+).
- **No GraphQL or gRPC.** REST only. Out of scope.
- **No prompt rewriting / redaction.** This is a verdict surface, not
  a content rewriter. If a customer wants redaction, that's Data Gate
  (Phase 3).

## Further reading

- Public-facing quickstart: `docs/firewall/quickstart.md`
- API reference: `docs/firewall/api-reference.md`
- Operations runbook: `docs/firewall/operations-runbook.md`
- Phase 1 foundational decisions: `docs/PHASE_1_DECISIONS.md`
- Game plan: `docs/GAME_PLAN.md`
