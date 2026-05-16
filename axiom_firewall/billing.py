"""Stripe billing integration for the Firewall.

Degrades gracefully: if STRIPE_SECRET_KEY is unset, all billing routes
return 503 and free-tier operation continues normally.

Pricing structure (locked via Stripe Dashboard, mirrored here):

  Indie  — $49/mo base, 50,000 calls included, $0.001/call overage
  Team   — $199/mo base, 500,000 calls included, $0.0005/call overage
  Enterprise — custom contract, not self-serve

Meter events are reported per billable API call (paid tiers only).

Required env vars when billing is enabled:
  STRIPE_SECRET_KEY        — sk_test_... or sk_live_...
  STRIPE_WEBHOOK_SECRET    — whsec_... (from stripe webhook endpoint)
  STRIPE_PRICE_INDIE       — price_... for the Indie base subscription
  STRIPE_PRICE_TEAM        — price_... for the Team base subscription
  STRIPE_METER_INDIE       — event_name registered with the Indie meter
  STRIPE_METER_TEAM        — event_name registered with the Team meter
  AXIOM_FIREWALL_PUBLIC_URL — https://firewall.orivael.dev (for return URLs)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .db import update_tenant_tier
from .models import Tenant

log = logging.getLogger(__name__)

TIER_TO_PRICE_ENV = {
    "indie": "STRIPE_PRICE_INDIE",
    "team": "STRIPE_PRICE_TEAM",
}
TIER_TO_METER_ENV = {
    "indie": "STRIPE_METER_INDIE",
    "team": "STRIPE_METER_TEAM",
}


def is_enabled() -> bool:
    """True when Stripe is configured. False = scaffold runs in free-only mode."""
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def _stripe():
    """Lazy import + configure. Raises if billing isn't enabled."""
    if not is_enabled():
        raise RuntimeError("Stripe billing not configured (STRIPE_SECRET_KEY unset)")
    import stripe  # type: ignore
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


def _public_url() -> str:
    return os.environ.get(
        "AXIOM_FIREWALL_PUBLIC_URL", "https://firewall.orivael.dev"
    ).rstrip("/")


def ensure_customer(tenant: Tenant) -> str:
    """Get or create a Stripe Customer for this tenant. Returns customer_id."""
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id
    stripe = _stripe()
    customer = stripe.Customer.create(
        email=tenant.email,
        metadata={"axiom_tenant_id": tenant.tenant_id},
    )
    update_tenant_tier(
        tenant.tenant_id, tier=tenant.tier,
        stripe_customer_id=customer.id,
        stripe_subscription_id=tenant.stripe_subscription_id,
    )
    return customer.id


def create_checkout_session(tenant: Tenant, tier: str) -> str:
    """Create a Stripe Checkout session for upgrading to `tier`. Returns the URL."""
    if tier not in TIER_TO_PRICE_ENV:
        raise ValueError(f"Tier {tier!r} is not self-serve. Enterprise = sales contract.")
    stripe = _stripe()
    price_id = os.environ.get(TIER_TO_PRICE_ENV[tier])
    if not price_id:
        raise RuntimeError(f"{TIER_TO_PRICE_ENV[tier]} env var is not set")
    customer_id = ensure_customer(tenant)
    base = _public_url()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/billing/cancel",
        client_reference_id=tenant.tenant_id,
        metadata={"axiom_tenant_id": tenant.tenant_id, "tier": tier},
    )
    return session.url


def create_portal_session(tenant: Tenant) -> str:
    """Stripe Billing Portal — lets customer manage cards / cancel / view invoices."""
    if not tenant.stripe_customer_id:
        raise RuntimeError("Tenant has no Stripe customer to manage")
    stripe = _stripe()
    base = _public_url()
    session = stripe.billing_portal.Session.create(
        customer=tenant.stripe_customer_id,
        return_url=f"{base}/billing",
    )
    return session.url


def report_meter_event(tenant: Tenant, count: int = 1) -> None:
    """Fire a Stripe Billing Meter event for paid-tier metered usage.

    Free tier is enforced in-process (no Stripe call).
    Enterprise tier is billed via separate contract (no metered reporting).
    Failures are swallowed + logged so a Stripe outage never blocks a verdict.
    """
    if tenant.tier not in TIER_TO_METER_ENV:
        return
    if not is_enabled():
        return
    if not tenant.stripe_customer_id:
        return
    event_name = os.environ.get(TIER_TO_METER_ENV[tenant.tier])
    if not event_name:
        return
    try:
        stripe = _stripe()
        stripe.billing.MeterEvent.create(
            event_name=event_name,
            payload={
                "stripe_customer_id": tenant.stripe_customer_id,
                "value": str(count),
            },
        )
    except Exception:
        log.exception("Failed to report Stripe meter event (non-fatal)")


def verify_and_parse_webhook(payload: bytes, signature_header: str) -> Any:
    """Verify the Stripe-signed webhook + return the parsed event.

    Raises ValueError on signature mismatch or missing secret.
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set")
    stripe = _stripe()
    return stripe.Webhook.construct_event(payload, signature_header, secret)


# ─── Webhook dispatch ───────────────────────────────────────────────────

_PRICE_TO_TIER: dict[str, str] | None = None


def _build_price_to_tier() -> dict[str, str]:
    """Inverse lookup: Stripe price_id → our tier name."""
    global _PRICE_TO_TIER
    if _PRICE_TO_TIER is not None:
        return _PRICE_TO_TIER
    table = {}
    for tier, env_var in TIER_TO_PRICE_ENV.items():
        pid = os.environ.get(env_var)
        if pid:
            table[pid] = tier
    _PRICE_TO_TIER = table
    return table


def _tier_from_subscription(subscription: Any) -> str | None:
    """Inspect a Stripe Subscription object and return the matching tier."""
    items = getattr(subscription, "items", None)
    data = items.data if items and hasattr(items, "data") else []
    table = _build_price_to_tier()
    for item in data:
        price_id = item.price.id if hasattr(item, "price") else None
        if price_id and price_id in table:
            return table[price_id]
    return None


def handle_event(event: Any) -> dict:
    """Dispatch a verified Stripe webhook event to the right handler.

    Returns a small dict for logging. Unhandled events are no-ops.
    """
    event_type = event.get("type") if isinstance(event, dict) else event.type
    data = event["data"]["object"] if isinstance(event, dict) else event.data.object

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        return _handle_subscription_upsert(data)
    if event_type == "customer.subscription.deleted":
        return _handle_subscription_deleted(data)
    return {"event_type": event_type, "action": "ignored"}


def _handle_subscription_upsert(subscription: Any) -> dict:
    customer_id = subscription.customer
    tier = _tier_from_subscription(subscription) or "free"
    sub_id = subscription.id
    from .db import find_tenant_by_stripe_customer
    tenant = find_tenant_by_stripe_customer(customer_id)
    if not tenant:
        log.warning("Webhook references unknown Stripe customer %s", customer_id)
        return {"action": "tenant_not_found", "customer_id": customer_id}
    update_tenant_tier(
        tenant.tenant_id, tier=tier,
        stripe_customer_id=customer_id, stripe_subscription_id=sub_id,
    )
    return {"action": "tier_updated", "tenant_id": tenant.tenant_id, "tier": tier}


def _handle_subscription_deleted(subscription: Any) -> dict:
    customer_id = subscription.customer
    from .db import find_tenant_by_stripe_customer
    tenant = find_tenant_by_stripe_customer(customer_id)
    if not tenant:
        return {"action": "tenant_not_found", "customer_id": customer_id}
    update_tenant_tier(
        tenant.tenant_id, tier="free",
        stripe_customer_id=customer_id, stripe_subscription_id=None,
    )
    return {"action": "downgraded_to_free", "tenant_id": tenant.tenant_id}
