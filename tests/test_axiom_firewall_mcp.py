"""Tests for the /dashboard/mcp integration page."""
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
        "axiom_firewall.skill_pack", "axiom_firewall.dashboard",
        "axiom_mcp_server", "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    yield tmp_path


def _client():
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    return TestClient(app)


def _signup(client, email="m@example.com", password="longenoughpw"):
    r = client.post(
        "/signup",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert r.status_code == 303
    client.get("/dashboard")   # pops the recovery code from the session


def test_mcp_page_requires_login(isolated_tenants):
    client = _client()
    r = client.get("/dashboard/mcp", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_mcp_page_renders_version_and_tools(isolated_tenants):
    client = _client()
    _signup(client)
    r = client.get("/dashboard/mcp")
    assert r.status_code == 200
    html = r.text
    # Version + trust level surfaced
    assert "Axiom MCP Server v" in html
    assert "trust level 3" in html
    # All 14 tools rendered as <code> names
    expected_tools = [
        "axiom_guard_check", "axiom_lint", "axiom_trace", "axiom_qrf",
        "axiom_status", "axiom_intent_gate_check", "axiom_cmaa_route",
        "axiom_cmaa_fleet", "axiom_cpi", "axiom_axm", "axiom_shield",
        "axiom_phone_gate", "axiom_validate",
    ]
    for name in expected_tools:
        assert f"<code>{name}</code>" in html, f"tool {name} missing from /dashboard/mcp"


def test_mcp_page_shows_claude_desktop_config(isolated_tenants):
    client = _client()
    _signup(client)
    r = client.get("/dashboard/mcp")
    assert r.status_code == 200
    # Config snippet keys must be present so users can copy-paste
    assert '"mcpServers"' in r.text
    assert '"axiom"' in r.text
    assert "axiom_mcp_server.py" in r.text
    assert "AXIOM_MASTER_KEY" in r.text


def test_mcp_link_in_nav(isolated_tenants):
    """Nav surfaces the MCP page next to Packs/Policy."""
    client = _client()
    _signup(client)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert 'href="/dashboard/mcp"' in r.text
