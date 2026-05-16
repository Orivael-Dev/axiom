# Billing

## Pricing

| Tier | Base | Included | Overage | What you get |
|---|---|---|---|---|
| **Free** | $0 | 1,000 calls/mo | hard cap (429) | Default policy, community support |
| **Indie** | $49/mo | 50,000 calls/mo | $0.001/call | Custom policies, email support |
| **Team** | $199/mo | 500,000 calls/mo | $0.0005/call | Multi-tenant policies, dashboard analytics |
| **Enterprise** | custom | unlimited | — | SOC 2, DPA, BAA, dedicated tenant, SLA |

## Free tier — hard cap behavior

Free tier blocks at exactly **1,000 calls per calendar month**. Past
that, `/v1/guard/check` returns:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 1234567
Content-Type: application/json

{
  "detail": "Monthly quota exhausted for the free tier (1000/1000 calls). Upgrade at /billing or wait N day(s).",
  "used": 1000,
  "limit": 1000,
  "retry_after_seconds": 1234567
}
```

The `Retry-After` header is the number of seconds until the start of
next month UTC. Counter resets at `00:00:00` on the 1st.

The dashboard shows a usage bar with a warning at 80% and a hard stop
at 100%.

## Paid tiers — metered, no hard cap

Indie / Team have a **soft cap** (the "included" amount). Past it, each
additional call is metered to Stripe at the overage rate. There's no
hard block — your traffic keeps flowing. You see the meter in real-time
on your Stripe Billing Portal.

You can switch to a hard cap at the metered price if you prefer
predictable billing. Contact `support@orivael.dev`.

## Upgrade flow

1. **Dashboard** → **Billing** → **Upgrade to Indie / Team**
2. You're redirected to Stripe Checkout (PCI-compliant card collection)
3. After successful checkout, Stripe webhook updates your tier
4. You're back on the dashboard with the new tier active

A successful upgrade typically takes 5–10 seconds end-to-end.

## Downgrade / cancel

**Dashboard** → **Billing** → **Open billing portal**.

The Stripe Billing Portal handles:

- Update card on file
- Switch tier (Indie ↔ Team)
- Cancel subscription (downgrades to Free at end of current period)
- View / download invoices

## What's billed

The metered event is **one per authenticated `/v1/guard/check` call**.

- Free tier: no Stripe event (counter is in-process).
- Indie / Team: one `axiom_firewall_call` meter event per call.
- Enterprise: handled by your contract — typically a single annual
  invoice with usage reported on the side.

A meter event records:

- `event_name` (Indie or Team)
- `stripe_customer_id`
- `value: "1"`

Verdict content (intent class, signals, signature) is **not** sent to
Stripe. Stripe only sees the count.

## Resilience

If Stripe is temporarily unreachable during meter reporting, the
Firewall keeps serving verdicts and **logs the missed event**. Stripe
allows backfilling meter events within a 24-hour window, which we
exercise via a hourly retry job (Phase 2).

A Stripe outage **never blocks a verdict**.

## Self-serve scope

Free → Indie → Team upgrades are self-serve via Stripe Checkout.

**Enterprise** requires a sales conversation. Email
`sales@orivael.dev` for:

- Volume commitments above 500K calls/mo
- Dedicated tenant infrastructure
- Data Processing Agreement (DPA)
- Business Associate Agreement (BAA) for HIPAA workloads
- SLA + dedicated support

## Tax + invoicing

Stripe handles tax calculation automatically based on your billing
address. Invoices are emailed on the 1st of each month and downloadable
from the Billing Portal indefinitely.

EU customers: VAT is collected via Stripe Tax.
