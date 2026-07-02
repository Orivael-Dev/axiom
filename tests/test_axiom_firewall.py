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
    assert keys[0].key_id == k.key_id
    # Plaintext is intentionally NOT round-trippable from the DB —
    # only the peppered HMAC is persisted. list_api_keys blanks it.
    assert keys[0].secret == ""

    auth = authenticate(k.secret)
    assert auth is not None
    found_t, found_k = auth
    assert found_t.tenant_id == t.tenant_id
    assert found_k.key_id == k.key_id
    # Authenticated lookup also returns no plaintext.
    assert found_k.secret == ""


def test_invalid_keys_rejected(isolated_tenants):
    from axiom_firewall.auth import authenticate

    assert authenticate("") is None
    assert authenticate("not-an-axfw-key") is None
    assert authenticate("axfw_does_not_exist") is None


def test_revoke_api_key_drops_active_status_and_breaks_auth(isolated_tenants):
    """Revocation soft-deletes: row stays for usage_record joins, but
    list_api_keys hides it and the bearer-token auth fastpath stops
    matching. Clearing secret_hash is belt-and-braces — the
    revoked_at IS NULL filter would catch it anyway, but clearing
    the hash means an accidental future query without that filter
    still can't authenticate the key."""
    from axiom_firewall.auth import authenticate, hash_password
    from axiom_firewall.db import (
        insert_api_key, insert_tenant, list_api_keys, revoke_api_key,
    )
    from axiom_firewall.models import ApiKey, Tenant

    t = Tenant.new(email="rev@example.com",
                   pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    k = ApiKey.new(tenant_id=t.tenant_id, name="prod")
    insert_api_key(k)

    # Auth works before revoke.
    assert authenticate(k.secret) is not None
    assert len(list_api_keys(t.tenant_id)) == 1

    # Revoke returns True on a successful first revoke.
    assert revoke_api_key(t.tenant_id, k.key_id) is True

    # Auth now fails — bearer-hash cleared, revoked_at set.
    assert authenticate(k.secret) is None
    # list_api_keys filters out revoked rows.
    assert list_api_keys(t.tenant_id) == []

    # Idempotency: a second revoke returns False (no row matched
    # because revoked_at IS NULL is part of the WHERE clause).
    assert revoke_api_key(t.tenant_id, k.key_id) is False


def test_revoke_api_key_cross_tenant_isolation(isolated_tenants):
    """Tenant A must NOT be able to revoke Tenant B's key. The
    (tenant_id, key_id) WHERE clause is the isolation primitive —
    if a caller ever passes the wrong tenant_id, the UPDATE simply
    matches zero rows and returns False. No exception raised so
    the dashboard route can render a consistent 'not_found' result
    without leaking key-existence info across tenants."""
    from axiom_firewall.auth import authenticate, hash_password
    from axiom_firewall.db import (
        insert_api_key, insert_tenant, list_api_keys, revoke_api_key,
    )
    from axiom_firewall.models import ApiKey, Tenant

    a = Tenant.new(email="a@example.com", pw_hash=hash_password("longenoughpw"))
    b = Tenant.new(email="b@example.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(a); insert_tenant(b)
    kb = ApiKey.new(tenant_id=b.tenant_id, name="prod")
    insert_api_key(kb)

    # Tenant A tries to revoke Tenant B's key.
    assert revoke_api_key(a.tenant_id, kb.key_id) is False

    # B's key still authenticates and still lists active.
    assert authenticate(kb.secret) is not None
    assert len(list_api_keys(b.tenant_id)) == 1


def test_dashboard_revoke_endpoint_requires_login(isolated_tenants):
    """Unauthenticated POST to revoke must 303 to /login — no
    information leakage about which key_ids exist."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    client = TestClient(app)
    r = client.post(
        "/dashboard/keys/some-key-id/revoke",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_dashboard_revoke_endpoint_revokes_own_key(isolated_tenants):
    """Authenticated user can revoke a key they own; the dashboard
    re-renders without the revoked key visible and shows the
    'revoked' confirmation."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    client = TestClient(app)
    r = client.post(
        "/signup",
        data={"email": "carol@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Create a key so we have something to revoke. The HTML response
    # also gives us the key_id by reading the dashboard.
    r = client.post("/dashboard/keys", data={"name": "prod"},
                    follow_redirects=True)
    assert r.status_code == 200
    # Now fetch the dashboard and parse the key_id out of the row.
    from axiom_firewall.db import find_tenant_by_email, list_api_keys
    t = find_tenant_by_email("carol@example.com")
    keys = list_api_keys(t.tenant_id)
    assert len(keys) == 1
    kid = keys[0].key_id

    # Revoke it.
    r = client.post(f"/dashboard/keys/{kid}/revoke",
                    follow_redirects=True)
    assert r.status_code == 200
    assert "API key revoked" in r.text
    # No active keys remain.
    assert list_api_keys(t.tenant_id) == []
    # And the key row no longer appears in the table.
    assert kid[:8] not in r.text


def test_dashboard_revoke_endpoint_reports_not_found_on_alien_key(
    isolated_tenants,
):
    """When the key_id doesn't belong to the logged-in tenant, the
    revoke endpoint must NOT 500 or leak the key's existence — it
    reports the same 'not_found' result the user would see if the
    key were already revoked or never existed."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import insert_api_key, insert_tenant
    from axiom_firewall.auth import hash_password
    from axiom_firewall.models import ApiKey, Tenant
    # Pre-seed tenant B with a key, OUTSIDE the test client session.
    b = Tenant.new(email="b@example.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(b)
    kb = ApiKey.new(tenant_id=b.tenant_id, name="b-prod")
    insert_api_key(kb)

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "a@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    r = client.post(f"/dashboard/keys/{kb.key_id}/revoke",
                    follow_redirects=True)
    assert r.status_code == 200
    assert "already revoked or doesn&#39;t belong" in r.text or \
           "already revoked or doesn't belong" in r.text


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
    from axiom_firewall.db import insert_api_key, find_tenant_by_email, usage_summary
    from axiom_firewall.models import ApiKey

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "carol@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )

    # The dashboard flow shows the plaintext exactly once via a session
    # flash; we mint a key directly so the test can keep the plaintext.
    tenant = find_tenant_by_email("carol@example.com")
    k = ApiKey.new(tenant_id=tenant.tenant_id, name="prod")
    insert_api_key(k)
    secret = k.secret

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
    from axiom_firewall.db import find_tenant_by_email, insert_api_key, usage_summary
    from axiom_firewall.models import ApiKey

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "dave@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    tenant = find_tenant_by_email("dave@example.com")
    k = ApiKey.new(tenant_id=tenant.tenant_id, name="prod")
    insert_api_key(k)
    secret = k.secret

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
    from axiom_firewall.db import find_tenant_by_email, insert_api_key
    from axiom_firewall.models import ApiKey

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "eve@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    tenant = find_tenant_by_email("eve@example.com")
    k = ApiKey.new(tenant_id=tenant.tenant_id, name="prod")
    insert_api_key(k)
    secret = k.secret

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


# ─── Beta-tester touchpoints ─────────────────────────────────────────


def test_fresh_signup_shows_welcome_banner(isolated_tenants):
    """A brand-new free-tier user with no keys and no calls should see
    the 3-step welcome banner — that's the empty state that turns a
    blank dashboard into onboarding."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    r = client.post(
        "/signup",
        data={"email": "fresh@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Welcome to the Orivael Intent Firewall beta" in r.text
    # Three numbered steps
    assert "Create an API key" in r.text
    assert "axfw_" in r.text       # mentioned in the steps
    assert "first call" in r.text


def test_welcome_banner_hidden_after_key_created(isolated_tenants):
    """Once any key exists, the welcome banner stops nagging."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post("/signup",
                data={"email": "u@example.com", "password": "longenoughpw"},
                follow_redirects=False)
    client.post("/dashboard/keys", data={"name": "dev"},
                follow_redirects=True)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Welcome to the Orivael Intent Firewall beta" not in r.text


def test_beta_footer_link_renders_when_env_set(
    isolated_tenants, monkeypatch,
):
    """AXIOM_FIREWALL_BETA_FEEDBACK env var → footer beta badge + link."""
    monkeypatch.setenv(
        "AXIOM_FIREWALL_BETA_FEEDBACK",
        "mailto:beta@orivael.dev",
    )
    # Reload so the module-level constant picks up the new env.
    for mod in ("axiom_firewall.dashboard",):
        if mod in sys.modules:
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post("/signup",
                data={"email": "u@example.com", "password": "longenoughpw"},
                follow_redirects=False)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "mailto:beta@orivael.dev" in r.text
    assert "feedback welcome" in r.text


def test_beta_footer_hidden_when_env_unset(isolated_tenants):
    """Unset → no beta badge in footer (clean prod look post-beta)."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post("/signup",
                data={"email": "u@example.com", "password": "longenoughpw"},
                follow_redirects=False)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "feedback welcome" not in r.text
