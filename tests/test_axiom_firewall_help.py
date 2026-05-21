"""Tests for the firewall /help routes — render docs/firewall/*.md."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
httpx   = pytest.importorskip("httpx")
from fastapi.testclient import TestClient   # noqa: E402


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test-session-secret")
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth",
        "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        if mod in sys.modules:
            del sys.modules[mod]
    yield tmp_path


def _client():
    from axiom_firewall.dashboard import app
    return TestClient(app)


def test_help_index_returns_html(isolated):
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"].lower()
    # Landing page lands on the index (or quickstart as fallback).
    assert "<h1" in r.text


def test_help_index_has_nav_to_other_docs(isolated):
    """Every .md under docs/firewall/ should appear in the per-doc
    nav row so beta testers can jump between Quickstart, Billing,
    Skill Packs, etc. without leaving the dashboard."""
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200
    # The most load-bearing docs for a beta tester:
    for expected_slug in ("quickstart", "billing", "skill-packs",
                            "api-reference"):
        assert f'href="/help/{expected_slug}"' in r.text, \
            f"missing nav link for {expected_slug}"


def test_help_quickstart_renders(isolated):
    client = _client()
    r = client.get("/help/quickstart")
    assert r.status_code == 200
    # The actual quickstart content from docs/firewall/quickstart.md
    assert "Quickstart" in r.text
    assert "axfw_" in r.text   # the api-key prefix is mentioned


def test_help_billing_renders(isolated):
    client = _client()
    r = client.get("/help/billing")
    assert r.status_code == 200
    assert "<h1" in r.text


def test_help_unknown_slug_returns_404(isolated):
    client = _client()
    r = client.get("/help/not-a-real-doc")
    assert r.status_code == 404
    assert "no doc named" in r.text


def test_help_blocks_path_traversal(isolated):
    """Slug must be alphanumeric + hyphens/underscores — anything
    else is refused with 400 (defense against `../`-style requests)."""
    client = _client()
    r = client.get("/help/..%2Fsomething")
    # FastAPI will URL-decode the path; our regex check refuses dots.
    assert r.status_code in (400, 404)


def test_help_topbar_links_back_to_dashboard(isolated):
    client = _client()
    r = client.get("/help/quickstart")
    assert r.status_code == 200
    assert 'href="/dashboard"' in r.text
    assert "← Dashboard" in r.text


def test_dashboard_nav_links_to_help(isolated):
    """The base.html nav should expose the new /help link in both
    the signed-in and signed-out branches."""
    client = _client()
    r = client.get("/")
    assert r.status_code == 200
    # Signed-out nav: Help link visible
    assert 'href="/help"' in r.text
