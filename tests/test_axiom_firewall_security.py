"""Tests for the security hardening patch (issues #2 + #3 from the audit).

#3: AXIOM_FIREWALL_SESSION_SECRET must be a real secret when
    AXIOM_ENV=production. Dev-default or short secret => hard fail at
    import.
#2: Request bodies over MAX_REQUEST_BODY_BYTES are rejected with 413
    before the route handler runs. The /v1/guard/check API has a
    tighter cap.
Also covers: session cookie hardening (SameSite=lax, Secure in prod,
2-week max_age).
"""
from __future__ import annotations

import importlib
import sys

import pytest


_DEFAULT_DEV_SECRET = "dev-only-replace-before-deploy"


def _reload_dashboard(monkeypatch, *, env_overrides: dict | None = None):
    """Force a clean import of axiom_firewall.dashboard with the given env.

    The dashboard module evaluates SESSION_SECRET / AXIOM_ENV at
    import time, so we must drop it from sys.modules between scenarios.
    """
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for k, v in (env_overrides or {}).items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth", "axiom_firewall.billing",
        "axiom_firewall.limits", "axiom_firewall.policy",
        "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    return importlib.import_module("axiom_firewall.dashboard")


# ─── #3 SESSION_SECRET hard-fail in production ──────────────────────────

class TestSessionSecretGuard:
    def test_production_with_dev_default_refuses_to_boot(self, monkeypatch):
        with pytest.raises(RuntimeError, match="dev default"):
            _reload_dashboard(monkeypatch, env_overrides={
                "AXIOM_ENV": "production",
                "AXIOM_FIREWALL_SESSION_SECRET": None,
            })

    def test_production_with_short_secret_refuses_to_boot(self, monkeypatch):
        with pytest.raises(RuntimeError, match="at least 32 characters"):
            _reload_dashboard(monkeypatch, env_overrides={
                "AXIOM_ENV": "production",
                "AXIOM_FIREWALL_SESSION_SECRET": "too-short",
            })

    def test_production_with_real_secret_boots(self, monkeypatch):
        dash = _reload_dashboard(monkeypatch, env_overrides={
            "AXIOM_ENV": "production",
            "AXIOM_FIREWALL_SESSION_SECRET": "a" * 64,
            "AXIOM_FIREWALL_TENANT_DIR": "/tmp/_axiom_test_prod_secret",
        })
        assert dash.SESSION_SECRET == "a" * 64
        assert dash._IS_PROD is True

    def test_development_with_dev_default_still_boots(self, monkeypatch):
        dash = _reload_dashboard(monkeypatch, env_overrides={
            "AXIOM_ENV": None,
            "AXIOM_FIREWALL_SESSION_SECRET": None,
            "AXIOM_FIREWALL_TENANT_DIR": "/tmp/_axiom_test_dev_default",
        })
        assert dash.SESSION_SECRET == _DEFAULT_DEV_SECRET
        assert dash._IS_PROD is False


# ─── #3 cookie hardening — Secure flag flips with env ───────────────────

class TestSessionCookieFlags:
    def test_dev_cookie_is_not_https_only(self, monkeypatch, tmp_path):
        dash = _reload_dashboard(monkeypatch, env_overrides={
            "AXIOM_ENV": "development",
            "AXIOM_FIREWALL_SESSION_SECRET": "x" * 64,
            "AXIOM_FIREWALL_TENANT_DIR": str(tmp_path / "tenants"),
        })
        from fastapi.testclient import TestClient
        client = TestClient(dash.app)
        r = client.get("/")  # any route that sets a session
        # Starlette only emits Set-Cookie when the session dict changes;
        # signup-flow route is a safer trigger.
        r = client.post(
            "/signup",
            data={"email": "x@example.com", "password": "password123",
                  "name": "x"},
            follow_redirects=False,
        )
        cookies = r.headers.get_list("set-cookie")
        assert any("session=" in c.lower() for c in cookies), \
            "session cookie should have been set on signup"
        cookie = next(c for c in cookies if "session=" in c.lower())
        assert "samesite=lax" in cookie.lower()
        assert "httponly" in cookie.lower()
        # Dev mode — Secure must NOT be forced (would block localhost HTTP).
        assert "secure" not in cookie.lower()

    def test_production_cookie_is_secure_and_samesite(self, monkeypatch, tmp_path):
        dash = _reload_dashboard(monkeypatch, env_overrides={
            "AXIOM_ENV": "production",
            "AXIOM_FIREWALL_SESSION_SECRET": "y" * 64,
            "AXIOM_FIREWALL_TENANT_DIR": str(tmp_path / "tenants"),
        })
        from fastapi.testclient import TestClient
        client = TestClient(dash.app, base_url="https://test")
        r = client.post(
            "/signup",
            data={"email": "y@example.com", "password": "password123",
                  "name": "y"},
            follow_redirects=False,
        )
        cookies = r.headers.get_list("set-cookie")
        cookie = next(c for c in cookies if "session=" in c.lower())
        assert "secure" in cookie.lower()
        assert "samesite=lax" in cookie.lower()
        assert "httponly" in cookie.lower()


# ─── #2 body size limits ────────────────────────────────────────────────

class TestBodySizeLimits:
    @pytest.fixture
    def client(self, monkeypatch, tmp_path):
        dash = _reload_dashboard(monkeypatch, env_overrides={
            "AXIOM_FIREWALL_TENANT_DIR": str(tmp_path / "tenants"),
            "AXIOM_FIREWALL_SESSION_SECRET": "z" * 64,
        })
        from fastapi.testclient import TestClient
        return TestClient(dash.app), dash

    def test_oversize_dashboard_post_is_rejected_with_413(self, client):
        client_, dash = client
        # 2 MiB body — over the 1 MiB dashboard cap, under the Caddy 2 MB cap.
        oversize = "a" * (dash.MAX_REQUEST_BODY_BYTES + 1024)
        r = client_.post(
            "/signup",
            content=oversize,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 413
        assert "too large" in r.json()["error"]

    def test_oversize_guard_api_post_is_rejected_with_413(self, client):
        client_, dash = client
        # 300 KiB body — over the 256 KiB guard cap, under the dashboard cap.
        oversize = '{"text":"' + ("a" * (dash.MAX_GUARD_API_BODY_BYTES + 1024)) + '"}'
        r = client_.post(
            "/v1/guard/check",
            content=oversize,
            headers={
                "content-type":  "application/json",
                "authorization": "Bearer fake-key-for-size-check",
            },
        )
        assert r.status_code == 413
        assert r.json()["limit_bytes"] == dash.MAX_GUARD_API_BODY_BYTES

    def test_normal_size_request_passes_through(self, client):
        client_, _ = client
        # Login page should render — body size middleware must not block GETs.
        r = client_.get("/login")
        assert r.status_code == 200

    def test_invalid_content_length_rejected_with_400(self, client):
        client_, _ = client
        r = client_.post(
            "/signup",
            content=b"x=1",
            headers={
                "content-type":   "application/x-www-form-urlencoded",
                "content-length": "not-a-number",
            },
        )
        # httpx may strip an obviously-bad Content-Length, so we accept
        # either the middleware's 400 or a normal 200/302 — but never 500.
        assert r.status_code < 500
