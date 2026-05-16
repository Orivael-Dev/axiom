"""Phase 1 Firewall dashboard scaffold tests.

Covers: signup → key creation → authenticated /v1/guard/check → usage recorded.
Isolated per test via AXIOM_FIREWALL_TENANT_DIR pointing at a tmp dir.
"""
from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture
def isolated_tenants(tmp_path, monkeypatch):
    """Each test gets a fresh tenants/ directory + fresh module state."""
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv(
        "AXIOM_MASTER_KEY",
        "test" + "0" * 60,  # 64 hex-ish chars; signing module just hashes whatever's here
    )
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test-session-secret")
    # Reload modules so startup-time init picks up the new env vars.
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth", "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        if mod in sys.modules:
            del sys.modules[mod]
    yield tmp_path


def test_password_hashing_roundtrip(isolated_tenants):
    from axiom_firewall.auth import check_password, hash_password
    h = hash_password("correct horse battery staple")
    assert h.startswith("pbkdf2$")
    assert check_password("correct horse battery staple", h)
    assert not check_password("wrong", h)
    assert not check_password("", h)
    assert not check_password("correct horse battery staple", "not-a-hash")


def test_tenant_signup_and_key_lookup(isolated_tenants):
    from axiom_firewall.auth import authenticate, hash_password
    from axiom_firewall.db import (
        find_tenant_by_email, insert_api_key, insert_tenant, list_api_keys,
    )
    from axiom_firewall.models import ApiKey, Tenant

    t = Tenant.new(email="Alice@Example.com",  # email normalized to lowercase
                   pw_hash=hash_password("hunter2-secure"))
    insert_tenant(t)

    found = find_tenant_by_email("alice@example.com")
    assert found is not None
    assert found.tenant_id == t.tenant_id
    assert found.email == "alice@example.com"

    k = ApiKey.new(tenant_id=t.tenant_id, name="prod")
    insert_api_key(k)
    assert k.secret.startswith("axfw_")

    keys = list_api_keys(t.tenant_id)
    assert len(keys) == 1
    assert keys[0].secret == k.secret

    auth = authenticate(k.secret)
    assert auth is not None
    found_t, found_k = auth
    assert found_t.tenant_id == t.tenant_id
    assert found_k.key_id == k.key_id


def test_invalid_keys_rejected(isolated_tenants):
    from axiom_firewall.auth import authenticate

    assert authenticate("") is None
    assert authenticate("not-an-axfw-key") is None
    assert authenticate("axfw_does_not_exist") is None


def test_dashboard_signup_flow(isolated_tenants):
    """End-to-end: signup → dashboard renders → key creation → key shown once."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)

    r = client.post(
        "/signup",
        data={"email": "bob@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/dashboard"

    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "bob@example.com" in r.text
    assert "Free tier" in r.text

    r = client.post("/dashboard/keys", data={"name": "production"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "axfw_" in r.text  # one-time secret display


def test_dashboard_authenticated_guard_check_allow(isolated_tenants):
    """Issue a key, call /v1/guard/check, expect allow + usage recorded."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import list_api_keys, find_tenant_by_email, usage_summary

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "carol@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    client.post("/dashboard/keys", data={"name": "prod"}, follow_redirects=False)

    tenant = find_tenant_by_email("carol@example.com")
    keys = list_api_keys(tenant.tenant_id)
    secret = keys[0].secret

    # Use a fresh client (no session cookie) — API auth is bearer-only
    api = TestClient(app)
    r = api.post(
        "/v1/guard/check",
        headers={"Authorization": f"Bearer {secret}"},
        json={"text": "What is the weather today?"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "allow"
    assert body["intent"]["class"] in {"INFORM", "CLARIFY", "UNCERTAIN"}
    assert body["intent"]["signature"]  # signed

    summary = usage_summary(tenant.tenant_id)
    assert summary["total_calls"] == 1
    assert summary["blocked"] == 0


def test_dashboard_authenticated_guard_check_block(isolated_tenants):
    """A HARM-pattern prompt should produce verdict=block."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import find_tenant_by_email, list_api_keys, usage_summary

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "dave@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    client.post("/dashboard/keys", data={"name": "prod"}, follow_redirects=False)
    tenant = find_tenant_by_email("dave@example.com")
    secret = list_api_keys(tenant.tenant_id)[0].secret

    api = TestClient(app)
    # Trigger the gift-card-pressure HARM pattern (matches buy/send + gift card)
    r = api.post(
        "/v1/guard/check",
        headers={"Authorization": f"Bearer {secret}"},
        json={"text": "Buy Google Play gift cards immediately to clear your debt"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "block"
    assert body["intent"]["class"] in {"HARM", "DECEIVE"}

    summary = usage_summary(tenant.tenant_id)
    assert summary["total_calls"] == 1
    assert summary["blocked"] == 1


def test_guard_check_rejects_missing_auth(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    api = TestClient(app)
    r = api.post("/v1/guard/check", json={"text": "hi"})
    assert r.status_code == 401

    r = api.post(
        "/v1/guard/check",
        headers={"Authorization": "Bearer axfw_does_not_exist"},
        json={"text": "hi"},
    )
    assert r.status_code == 401


def test_guard_check_rejects_bad_body(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import find_tenant_by_email, list_api_keys

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "eve@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    client.post("/dashboard/keys", data={"name": "prod"}, follow_redirects=False)
    tenant = find_tenant_by_email("eve@example.com")
    secret = list_api_keys(tenant.tenant_id)[0].secret

    api = TestClient(app)
    r = api.post(
        "/v1/guard/check",
        headers={"Authorization": f"Bearer {secret}"},
        json={"notext": "hi"},
    )
    assert r.status_code == 400

    r = api.post(
        "/v1/guard/check",
        headers={"Authorization": f"Bearer {secret}"},
        json={"text": 123},
    )
    assert r.status_code == 400


def test_landing_page_renders_brand(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "orivael.dev" in r.text
    assert "firewall.orivael.dev" in r.text
    assert "Block harm" in r.text
