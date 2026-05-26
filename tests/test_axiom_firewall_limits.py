"""Tests for Week 3 free-tier abuse defense.

Covers: per-IP signup rate limit (allow within window, deny + Retry-After
beyond), monthly quota counting, free-tier block at cap, paid tier no-cap.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def isolated_tenants(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test")
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth", "axiom_firewall.billing",
        "axiom_firewall.limits", "axiom_firewall.policy",
        "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    yield tmp_path


# ─── per-IP signup rate limiting ─────────────────────────────────────────


def test_signup_rate_allows_first_five_then_blocks(isolated_tenants):
    from axiom_firewall.limits import (
        SIGNUP_MAX_PER_WINDOW, check_signup_rate,
    )
    assert SIGNUP_MAX_PER_WINDOW == 5
    for i in range(5):
        allowed, retry = check_signup_rate("1.2.3.4")
        assert allowed, f"attempt {i+1} should have been allowed"
        assert retry == 0
    allowed, retry = check_signup_rate("1.2.3.4")
    assert not allowed
    assert retry >= 60


def test_signup_rate_separates_by_ip(isolated_tenants):
    from axiom_firewall.limits import check_signup_rate
    for _ in range(5):
        assert check_signup_rate("10.0.0.1")[0]
    # Different IP is fresh
    assert check_signup_rate("10.0.0.2")[0]


def test_dashboard_signup_429_after_burst(isolated_tenants):
    """Six signups from the same source → sixth returns 429 + Retry-After."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    for i in range(5):
        r = client.post(
            "/signup",
            data={"email": f"user{i}@example.com", "password": "longenoughpw"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"attempt {i}: {r.text[:200]}"
        client.cookies.clear()

    r = client.post(
        "/signup",
        data={"email": "blocked@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 60


# ─── monthly quota ───────────────────────────────────────────────────────


def test_monthly_usage_counts_only_current_month(isolated_tenants):
    """Insert usage rows across months; count returns only this month's."""
    import sqlite3
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import _conn, _tenant_path, insert_tenant
    from axiom_firewall.limits import monthly_usage_count
    from axiom_firewall.models import Tenant

    t = Tenant.new(email="m@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    now = datetime.utcnow()
    this_month = now.replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    last_month = (this_month.replace(day=1) - timedelta(days=1)).replace(day=15)

    with _conn(_tenant_path(t.tenant_id)) as c:
        for i, ts in enumerate((this_month, this_month, last_month)):
            c.execute(
                "INSERT INTO usage_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"r{i}", t.tenant_id, "k", "/v1/guard/check",
                 "allow", "INFORM", 0.5, 0.0, ts.isoformat()),
            )

    assert monthly_usage_count(t.tenant_id) == 2


def test_check_monthly_quota_free_tier_blocks_at_cap(isolated_tenants):
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import _conn, _tenant_path, insert_tenant
    from axiom_firewall.limits import (
        TIER_MONTHLY_HARD_CAP, check_monthly_quota,
    )
    from axiom_firewall.models import Tenant

    t = Tenant.new(email="cap@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    cap = TIER_MONTHLY_HARD_CAP["free"]
    # Insert one row short of the cap.
    now = datetime.utcnow().replace(day=15, hour=12)
    with _conn(_tenant_path(t.tenant_id)) as c:
        for i in range(cap - 1):
            c.execute(
                "INSERT INTO usage_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"r{i}", t.tenant_id, "k", "/v1/guard/check",
                 "allow", "INFORM", 0.5, 0.0, now.isoformat()),
            )

    allowed, used, lim = check_monthly_quota(t)
    assert allowed
    assert used == cap - 1
    assert lim == cap

    # One more row puts us AT the cap.
    with _conn(_tenant_path(t.tenant_id)) as c:
        c.execute(
            "INSERT INTO usage_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("r_at_cap", t.tenant_id, "k", "/v1/guard/check",
             "allow", "INFORM", 0.5, 0.0, now.isoformat()),
        )
    allowed, used, lim = check_monthly_quota(t)
    assert not allowed
    assert used == cap


def test_paid_tier_has_no_hard_cap(isolated_tenants):
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant, update_tenant_tier
    from axiom_firewall.limits import check_monthly_quota
    from axiom_firewall.models import Tenant

    t = Tenant.new(email="paid@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    update_tenant_tier(
        t.tenant_id, tier="indie",
        stripe_customer_id="cus_x", stripe_subscription_id="sub_x",
    )
    from axiom_firewall.db import find_tenant_by_id
    paid_tenant = find_tenant_by_id(t.tenant_id)

    allowed, used, lim = check_monthly_quota(paid_tenant)
    assert allowed
    assert lim is None  # no cap


def test_guard_check_returns_429_at_free_quota(isolated_tenants):
    """End-to-end: stuff usage_records to the cap, expect a 429 from /v1/guard/check."""
    from fastapi.testclient import TestClient
    from axiom_firewall.auth import hash_password
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import (
        _conn, _tenant_path, find_tenant_by_email, insert_api_key,
    )
    from axiom_firewall.limits import TIER_MONTHLY_HARD_CAP
    from axiom_firewall.models import ApiKey

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "fullup@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    tenant = find_tenant_by_email("fullup@example.com")
    k = ApiKey.new(tenant_id=tenant.tenant_id, name="x")
    insert_api_key(k)
    secret = k.secret

    # Backfill the cap directly into usage_records.
    cap = TIER_MONTHLY_HARD_CAP["free"]
    now = datetime.utcnow().replace(day=15, hour=12)
    with _conn(_tenant_path(tenant.tenant_id)) as c:
        for i in range(cap):
            c.execute(
                "INSERT INTO usage_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"r{i}", tenant.tenant_id, "k", "/v1/guard/check",
                 "allow", "INFORM", 0.5, 0.0, now.isoformat()),
            )

    api = TestClient(app)
    r = api.post(
        "/v1/guard/check",
        headers={"Authorization": f"Bearer {secret}"},
        json={"text": "hi"},
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    body = r.json()
    assert body["used"] == cap
    assert body["limit"] == cap
    assert body["retry_after_seconds"] > 0


def test_dashboard_renders_usage_bar(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "bar@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "quota-bar" in r.text
    assert "calls / month" in r.text
