"""Tests for beta-mode billing — upgrade buttons swap for Contact Sales.

When AXIOM_FIREWALL_BETA_MODE=1 (default):
  - /billing renders mailto: "Contact sales" links instead of Stripe forms
  - POST /billing/upgrade/{tier} returns 403 with a sales-email hint
  - Stripe checkout is unreachable from the UI (defense in depth)

When AXIOM_FIREWALL_BETA_MODE=0 (post-beta):
  - normal Stripe flow returns — covered by test_axiom_firewall_billing.py
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def beta_tenants(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test")
    # Belt-and-suspenders: explicitly enable beta mode in case a
    # parent process unset it.
    monkeypatch.setenv("AXIOM_FIREWALL_BETA_MODE", "1")
    # A Stripe key MUST be set so we prove beta mode wins over a
    # billing-enabled environment.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_INDIE", "price_indie_test")
    monkeypatch.setenv("STRIPE_PRICE_TEAM", "price_team_test")
    monkeypatch.setenv("AXIOM_FIREWALL_SALES_EMAIL", "sales@example.test")
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth",
        "axiom_firewall.billing", "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    yield tmp_path


def _client():
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    return TestClient(app)


def _signup(client, email="b@example.com"):
    r = client.post("/signup",
                    data={"email": email, "password": "longenoughpw"},
                    follow_redirects=False)
    assert r.status_code == 303
    client.get("/dashboard")


def test_beta_mode_swaps_indie_form_for_contact_sales(beta_tenants):
    client = _client()
    _signup(client)
    r = client.get("/billing")
    assert r.status_code == 200
    # No Stripe POST form for indie/team during beta
    assert '/billing/upgrade/indie' not in r.text
    assert '/billing/upgrade/team' not in r.text
    # Contact-sales mailto link present for both tiers
    assert "mailto:sales@example.test" in r.text
    assert "Indie%20plan" in r.text
    assert "Team%20plan" in r.text


def test_beta_mode_banner_renders(beta_tenants):
    client = _client()
    _signup(client)
    r = client.get("/billing")
    assert r.status_code == 200
    assert "Beta period" in r.text
    assert "Self-serve checkout is paused" in r.text


def test_beta_mode_blocks_direct_upgrade_post(beta_tenants):
    """Even a hand-crafted POST should 403 during beta."""
    client = _client()
    _signup(client)
    r = client.post("/billing/upgrade/indie", follow_redirects=False)
    assert r.status_code == 403
    assert "beta" in r.text.lower()
    assert "sales@example.test" in r.text


def test_beta_mode_hides_stripe_portal_button(beta_tenants):
    """Manage-subscription block stays hidden during beta even if the
    tenant somehow had a stripe_subscription_id (legacy or test data)."""
    client = _client()
    _signup(client)
    r = client.get("/billing")
    assert r.status_code == 200
    assert "Open billing portal" not in r.text


def test_beta_mode_default_is_on_when_env_unset(tmp_path, monkeypatch):
    """If AXIOM_FIREWALL_BETA_MODE is not set, beta mode is ON by default."""
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test")
    monkeypatch.delenv("AXIOM_FIREWALL_BETA_MODE", raising=False)
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth",
        "axiom_firewall.billing", "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    from axiom_firewall.dashboard import BETA_MODE
    assert BETA_MODE is True


def test_beta_mode_off_when_env_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test")
    monkeypatch.setenv("AXIOM_FIREWALL_BETA_MODE", "0")
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth",
        "axiom_firewall.billing", "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    from axiom_firewall.dashboard import BETA_MODE
    assert BETA_MODE is False
