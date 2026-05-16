"""Tests for Week 4 production hardening.

Covers: /healthz + /readyz endpoints, X-Request-ID middleware, CORS
config (off by default, configurable via env), startup config logging.
"""
from __future__ import annotations

import sys

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


def test_healthz_returns_ok(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_returns_ready(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    r = TestClient(app).get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_request_id_middleware_tags_response(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    r = TestClient(app).get("/healthz")
    assert "x-request-id" in r.headers
    assert len(r.headers["x-request-id"]) >= 8


def test_request_id_middleware_echoes_incoming(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    r = TestClient(app).get(
        "/healthz", headers={"x-request-id": "abc12345deadbeef"}
    )
    assert r.headers["x-request-id"] == "abc12345deadbeef"


def test_cors_disabled_by_default(isolated_tenants, monkeypatch):
    monkeypatch.delenv("AXIOM_FIREWALL_CORS_ORIGINS", raising=False)
    for mod in list(sys.modules):
        if mod.startswith("axiom_firewall."):
            sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import CORS_ORIGINS, app
    assert CORS_ORIGINS == []
    # OPTIONS preflight without configured CORS should not get
    # Access-Control-Allow-Origin (FastAPI default = 405 for unknown OPTIONS).
    r = TestClient(app).options(
        "/v1/guard/check",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in r.headers


def test_cors_enabled_when_env_set(isolated_tenants, monkeypatch):
    monkeypatch.setenv(
        "AXIOM_FIREWALL_CORS_ORIGINS",
        "https://app.example.com,https://staging.example.com",
    )
    for mod in list(sys.modules):
        if mod.startswith("axiom_firewall."):
            sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import CORS_ORIGINS, app

    assert "https://app.example.com" in CORS_ORIGINS
    assert "https://staging.example.com" in CORS_ORIGINS

    r = TestClient(app).options(
        "/v1/guard/check",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com"

    # Disallowed origin should NOT get the allow header
    r2 = TestClient(app).options(
        "/v1/guard/check",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r2.headers.get("access-control-allow-origin") != "https://evil.example.com"
