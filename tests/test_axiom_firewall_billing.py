"""Tests for Firewall billing — stripe module mocked.

Covers: billing disabled (no STRIPE_SECRET_KEY), billing enabled (mocked
stripe), upgrade checkout, webhook subscription upsert + cancel, meter
event fired on record_call for paid tiers, billing failure never blocks
verdict path.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def isolated_tenants(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test")
    # These tests exercise the Stripe checkout/portal path (post-beta).
    # The dashboard's default BETA_MODE=1 disables self-serve upgrade,
    # so opt out here. Beta-mode behaviour is covered by
    # test_axiom_firewall_billing_beta.py.
    monkeypatch.setenv("AXIOM_FIREWALL_BETA_MODE", "0")
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth",
        "axiom_firewall.billing", "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    yield tmp_path


@pytest.fixture
def mock_stripe(monkeypatch):
    """Inject a fake `stripe` module with the shape billing.py expects."""
    fake = types.ModuleType("stripe")
    fake.api_key = None
    fake.Customer = MagicMock()
    fake.Customer.create.return_value = types.SimpleNamespace(id="cus_test_123")
    fake.checkout = types.SimpleNamespace(
        Session=MagicMock(create=MagicMock(
            return_value=types.SimpleNamespace(url="https://stripe.com/checkout/test")
        )),
    )
    fake.billing_portal = types.SimpleNamespace(
        Session=MagicMock(create=MagicMock(
            return_value=types.SimpleNamespace(url="https://stripe.com/portal/test")
        )),
    )
    # billing.MeterEvent is the modern metered-billing API.
    fake.billing = types.SimpleNamespace(MeterEvent=MagicMock(create=MagicMock()))
    fake.Webhook = MagicMock()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return fake


# ─── billing.is_enabled() ────────────────────────────────────────────────


def test_billing_disabled_by_default(isolated_tenants, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from axiom_firewall import billing
    assert billing.is_enabled() is False


def test_billing_enabled_when_key_set(isolated_tenants, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    from axiom_firewall import billing
    assert billing.is_enabled() is True


# ─── checkout session ────────────────────────────────────────────────────


def test_create_checkout_session_creates_customer_and_returns_url(
    isolated_tenants, mock_stripe, monkeypatch
):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_INDIE", "price_indie_test")
    from axiom_firewall import billing
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant, find_tenant_by_id
    from axiom_firewall.models import Tenant

    t = Tenant.new(email="a@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    url = billing.create_checkout_session(t, "indie")
    assert url == "https://stripe.com/checkout/test"

    # Customer was created with our metadata
    mock_stripe.Customer.create.assert_called_once_with(
        email="a@b.com",
        metadata={"axiom_tenant_id": t.tenant_id},
    )
    # Customer ID was persisted
    after = find_tenant_by_id(t.tenant_id)
    assert after.stripe_customer_id == "cus_test_123"

    # Checkout Session was created with the right price
    call_kwargs = mock_stripe.checkout.Session.create.call_args.kwargs
    assert call_kwargs["mode"] == "subscription"
    assert call_kwargs["customer"] == "cus_test_123"
    assert call_kwargs["line_items"] == [
        {"price": "price_indie_test", "quantity": 1}
    ]
    assert call_kwargs["client_reference_id"] == t.tenant_id


def test_checkout_rejects_unknown_tier(isolated_tenants, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    from axiom_firewall import billing
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant

    t = Tenant.new(email="a@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    with pytest.raises(ValueError, match="not self-serve"):
        billing.create_checkout_session(t, "enterprise")


def test_checkout_requires_price_env(isolated_tenants, mock_stripe, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.delenv("STRIPE_PRICE_INDIE", raising=False)
    from axiom_firewall import billing
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant

    t = Tenant.new(email="a@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    with pytest.raises(RuntimeError, match="STRIPE_PRICE_INDIE"):
        billing.create_checkout_session(t, "indie")


# ─── webhook handling ───────────────────────────────────────────────────


def test_webhook_subscription_updated_upgrades_tier(
    isolated_tenants, mock_stripe, monkeypatch
):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_INDIE", "price_indie_test")
    from axiom_firewall import billing
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant, find_tenant_by_id, update_tenant_tier
    from axiom_firewall.models import Tenant

    t = Tenant.new(email="up@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    # Pretend an earlier upgrade attempt already created the Stripe customer
    update_tenant_tier(
        t.tenant_id, tier="free",
        stripe_customer_id="cus_xyz", stripe_subscription_id=None,
    )

    # Build a fake subscription object the way Stripe shapes it
    sub = types.SimpleNamespace(
        id="sub_abc",
        customer="cus_xyz",
        items=types.SimpleNamespace(data=[
            types.SimpleNamespace(price=types.SimpleNamespace(id="price_indie_test")),
        ]),
    )
    event = types.SimpleNamespace(
        type="customer.subscription.updated",
        data=types.SimpleNamespace(object=sub),
    )
    result = billing.handle_event(event)
    assert result["action"] == "tier_updated"
    assert result["tier"] == "indie"

    after = find_tenant_by_id(t.tenant_id)
    assert after.tier == "indie"
    assert after.stripe_subscription_id == "sub_abc"


def test_webhook_subscription_deleted_downgrades_to_free(
    isolated_tenants, mock_stripe, monkeypatch
):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_INDIE", "price_indie_test")
    from axiom_firewall import billing
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant, find_tenant_by_id, update_tenant_tier
    from axiom_firewall.models import Tenant

    t = Tenant.new(email="dn@b.com", pw_hash=hash_password("longenoughpw"), tier="indie")
    insert_tenant(t)
    update_tenant_tier(
        t.tenant_id, tier="indie",
        stripe_customer_id="cus_dn", stripe_subscription_id="sub_dn",
    )

    sub = types.SimpleNamespace(id="sub_dn", customer="cus_dn")
    event = types.SimpleNamespace(
        type="customer.subscription.deleted",
        data=types.SimpleNamespace(object=sub),
    )
    result = billing.handle_event(event)
    assert result["action"] == "downgraded_to_free"

    after = find_tenant_by_id(t.tenant_id)
    assert after.tier == "free"
    assert after.stripe_subscription_id is None


def test_webhook_unknown_customer_does_not_crash(
    isolated_tenants, mock_stripe, monkeypatch
):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    from axiom_firewall import billing
    sub = types.SimpleNamespace(id="sub_x", customer="cus_does_not_exist")
    event = types.SimpleNamespace(
        type="customer.subscription.deleted",
        data=types.SimpleNamespace(object=sub),
    )
    result = billing.handle_event(event)
    assert result["action"] == "tenant_not_found"


# ─── meter events fired on record_call ──────────────────────────────────


def test_meter_event_fired_for_paid_tier(
    isolated_tenants, mock_stripe, monkeypatch
):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_INDIE", "price_indie_test")
    monkeypatch.setenv("STRIPE_METER_INDIE", "axiom_firewall_call")
    from axiom_firewall import billing
    from axiom_firewall.auth import hash_password, record_call
    from axiom_firewall.db import insert_tenant, update_tenant_tier
    from axiom_firewall.models import Tenant
    from time import perf_counter

    t = Tenant.new(email="paid@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    update_tenant_tier(
        t.tenant_id, tier="indie",
        stripe_customer_id="cus_paid", stripe_subscription_id="sub_paid",
    )

    record_call(
        tenant_id=t.tenant_id, key_id="k1", endpoint="/v1/guard/check",
        verdict="allow", intent_class="INFORM", confidence=0.55,
        started_at=perf_counter() - 0.001,
    )

    mock_stripe.billing.MeterEvent.create.assert_called_once()
    call_kwargs = mock_stripe.billing.MeterEvent.create.call_args.kwargs
    assert call_kwargs["event_name"] == "axiom_firewall_call"
    assert call_kwargs["payload"]["stripe_customer_id"] == "cus_paid"
    assert call_kwargs["payload"]["value"] == "1"


def test_meter_event_skipped_for_free_tier(
    isolated_tenants, mock_stripe, monkeypatch
):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_METER_INDIE", "axiom_firewall_call")
    from axiom_firewall.auth import hash_password, record_call
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from time import perf_counter

    t = Tenant.new(email="free@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    record_call(
        tenant_id=t.tenant_id, key_id="k1", endpoint="/v1/guard/check",
        verdict="allow", intent_class="INFORM", confidence=0.55,
        started_at=perf_counter() - 0.001,
    )

    mock_stripe.billing.MeterEvent.create.assert_not_called()


def test_meter_event_failure_does_not_break_verdict(
    isolated_tenants, mock_stripe, monkeypatch
):
    """If Stripe throws during meter reporting, the call should still be recorded."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_METER_INDIE", "axiom_firewall_call")
    mock_stripe.billing.MeterEvent.create.side_effect = RuntimeError("Stripe down")

    from axiom_firewall.auth import hash_password, record_call
    from axiom_firewall.db import insert_tenant, update_tenant_tier, usage_summary
    from axiom_firewall.models import Tenant
    from time import perf_counter

    t = Tenant.new(email="resil@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    update_tenant_tier(
        t.tenant_id, tier="indie",
        stripe_customer_id="cus_x", stripe_subscription_id="sub_x",
    )

    # Should not raise even though Stripe is throwing
    record_call(
        tenant_id=t.tenant_id, key_id="k1", endpoint="/v1/guard/check",
        verdict="allow", intent_class="INFORM", confidence=0.55,
        started_at=perf_counter() - 0.001,
    )

    # Usage still got persisted
    summary = usage_summary(t.tenant_id)
    assert summary["total_calls"] == 1


# ─── dashboard routes ───────────────────────────────────────────────────


def test_billing_page_renders_disabled_when_no_stripe(isolated_tenants, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post("/signup",
                data={"email": "x@b.com", "password": "longenoughpw"},
                follow_redirects=False)

    r = client.get("/billing")
    assert r.status_code == 200
    assert "Billing is not configured" in r.text


def test_billing_upgrade_503_when_disabled(isolated_tenants, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post("/signup",
                data={"email": "x@b.com", "password": "longenoughpw"},
                follow_redirects=False)
    r = client.post("/billing/upgrade/indie", follow_redirects=False)
    assert r.status_code == 503


def test_billing_upgrade_redirects_to_stripe_when_enabled(
    isolated_tenants, mock_stripe, monkeypatch
):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_INDIE", "price_indie_test")
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post("/signup",
                data={"email": "go@b.com", "password": "longenoughpw"},
                follow_redirects=False)
    r = client.post("/billing/upgrade/indie", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "https://stripe.com/checkout/test"


def test_billing_webhook_503_when_disabled(isolated_tenants, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    r = client.post("/billing/webhook", content=b"{}",
                    headers={"stripe-signature": "anything"})
    assert r.status_code == 503
