#!/usr/bin/env python3
"""One-shot Stripe setup for the Axiom Firewall.

Creates (idempotently):
  - One Product:   "Axiom Intent Firewall"
  - Two Meters:    axiom_firewall_indie, axiom_firewall_team
  - Four Prices:   Indie base + Indie metered overage,
                   Team  base + Team  metered overage

Outputs the env-var lines you need to set on the Firewall deployment.

Run once with your live or test secret key:

    STRIPE_SECRET_KEY=sk_test_... python scripts/stripe_setup.py

Safe to re-run — looks up existing objects by name/lookup_key before
creating. Tested against Stripe API version 2024-12-18.

Pricing (matches docs/firewall/internal/billing.md and Phase 1 Decisions §5):
  Indie    $49/mo + $0.001/call overage past 50,000
  Team     $199/mo + $0.0005/call overage past 500,000
"""
from __future__ import annotations

import os
import sys
from textwrap import dedent

try:
    import stripe  # type: ignore
except ImportError:
    sys.exit("stripe lib not installed. `pip install stripe`")


PRODUCT_NAME = "Axiom Intent Firewall"

TIERS = {
    "indie": {
        "label": "Indie",
        "base_amount_usd": 49,
        "included_calls": 50_000,
        "overage_per_call_usd": 0.001,
        "meter_event_name": "axiom_firewall_indie",
    },
    "team": {
        "label": "Team",
        "base_amount_usd": 199,
        "included_calls": 500_000,
        "overage_per_call_usd": 0.0005,
        "meter_event_name": "axiom_firewall_team",
    },
}


def _usd_to_cents(usd: float) -> int:
    return int(round(usd * 100))


def _decimal_cents_per_unit(usd: float) -> str:
    """Stripe metered prices use 'unit_amount_decimal' in cents.

    For $0.001 per call we set unit_amount_decimal = "0.1" (cents).
    """
    return f"{usd * 100:.6f}".rstrip("0").rstrip(".") or "0"


def _ensure_product() -> str:
    """Find-or-create the canonical Firewall product."""
    existing = stripe.Product.list(limit=100)
    for p in existing.auto_paging_iter():
        if p.name == PRODUCT_NAME:
            print(f"  product: reusing {p.id}")
            return p.id
    p = stripe.Product.create(
        name=PRODUCT_NAME,
        description="Constitutional intent classifier for LLM input.",
        url="https://firewall.orivael.dev",
    )
    print(f"  product: created {p.id}")
    return p.id


def _ensure_meter(event_name: str, display_name: str) -> str:
    """Find-or-create a Stripe Billing Meter for the per-call event."""
    existing = stripe.billing.Meter.list(limit=100)
    for m in existing.auto_paging_iter():
        if m.event_name == event_name:
            print(f"  meter {event_name}: reusing {m.id}")
            return m.id
    m = stripe.billing.Meter.create(
        display_name=display_name,
        event_name=event_name,
        default_aggregation={"formula": "sum"},
        customer_mapping={"event_payload_key": "stripe_customer_id", "type": "by_id"},
        value_settings={"event_payload_key": "value"},
    )
    print(f"  meter {event_name}: created {m.id}")
    return m.id


def _ensure_base_price(product_id: str, tier_key: str, tier: dict) -> str:
    """Find-or-create the recurring monthly base price for `tier`."""
    lookup_key = f"axiom_firewall_{tier_key}_base"
    found = stripe.Price.list(lookup_keys=[lookup_key], expand=["data.product"])
    for p in found.auto_paging_iter():
        print(f"  base price {tier_key}: reusing {p.id} (lookup_key={lookup_key})")
        return p.id
    p = stripe.Price.create(
        product=product_id,
        unit_amount=_usd_to_cents(tier["base_amount_usd"]),
        currency="usd",
        recurring={"interval": "month"},
        lookup_key=lookup_key,
        nickname=f"{tier['label']} base",
    )
    print(f"  base price {tier_key}: created {p.id}")
    return p.id


def _ensure_overage_price(product_id: str, tier_key: str, tier: dict, meter_id: str) -> str:
    """Find-or-create the metered overage price tied to the meter."""
    lookup_key = f"axiom_firewall_{tier_key}_overage"
    found = stripe.Price.list(lookup_keys=[lookup_key])
    for p in found.auto_paging_iter():
        print(f"  overage price {tier_key}: reusing {p.id} (lookup_key={lookup_key})")
        return p.id
    p = stripe.Price.create(
        product=product_id,
        currency="usd",
        recurring={"interval": "month", "usage_type": "metered", "meter": meter_id},
        unit_amount_decimal=_decimal_cents_per_unit(tier["overage_per_call_usd"]),
        lookup_key=lookup_key,
        nickname=f"{tier['label']} overage per call",
        billing_scheme="per_unit",
    )
    print(f"  overage price {tier_key}: created {p.id}")
    return p.id


def main() -> int:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        sys.exit(
            "STRIPE_SECRET_KEY environment variable is required. "
            "Use a test-mode key (sk_test_...) first to dry-run."
        )
    if not key.startswith(("sk_test_", "sk_live_")):
        sys.exit(f"STRIPE_SECRET_KEY has unexpected prefix: {key[:10]}...")
    stripe.api_key = key

    mode = "TEST" if key.startswith("sk_test_") else "LIVE"
    print(f"Stripe setup ({mode} mode)")
    print(f"Product: {PRODUCT_NAME}")
    print()

    print("Creating / verifying product")
    product_id = _ensure_product()

    output_env: dict[str, str] = {}

    for tier_key, tier in TIERS.items():
        print()
        print(f"Tier: {tier['label']}")
        meter_id = _ensure_meter(tier["meter_event_name"], f"{tier['label']} calls")
        base_id = _ensure_base_price(product_id, tier_key, tier)
        _ensure_overage_price(product_id, tier_key, tier, meter_id)

        output_env[f"STRIPE_PRICE_{tier_key.upper()}"] = base_id
        output_env[f"STRIPE_METER_{tier_key.upper()}"] = tier["meter_event_name"]

    print()
    print("Done. Set these on the Firewall deployment (Secrets Manager / .env):")
    print()
    print(dedent(f"""\
        export STRIPE_SECRET_KEY="{key}"
        export STRIPE_WEBHOOK_SECRET="whsec_..."   # create the webhook endpoint next
        export STRIPE_PRICE_INDIE="{output_env['STRIPE_PRICE_INDIE']}"
        export STRIPE_PRICE_TEAM="{output_env['STRIPE_PRICE_TEAM']}"
        export STRIPE_METER_INDIE="{output_env['STRIPE_METER_INDIE']}"
        export STRIPE_METER_TEAM="{output_env['STRIPE_METER_TEAM']}"
    """))

    print("Next step: create a webhook endpoint at")
    print("  https://dashboard.stripe.com/webhooks  →  Add endpoint")
    print("  URL:    https://firewall.orivael.dev/billing/webhook")
    print("  Events: customer.subscription.created,")
    print("          customer.subscription.updated,")
    print("          customer.subscription.deleted")
    print("Then copy the webhook signing secret into STRIPE_WEBHOOK_SECRET.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
