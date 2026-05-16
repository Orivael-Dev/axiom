"""Tests for Week 3 per-tenant policy isolation.

Covers: empty policy = no-op; additional_block_patterns override allow;
disabled_default_classes downgrade block to allow; allow_only_classes
whitelist; policy editor routes; corrupt policy falls back to empty.
"""
from __future__ import annotations

import json
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


# ─── parse() validation ─────────────────────────────────────────────────


def test_parse_accepts_minimal_policy(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy
    p = TenantPolicy.parse({"version": 1})
    assert p.version == 1
    assert p.additional_block_patterns == ()
    assert p.disabled_default_classes == frozenset()
    assert p.allow_only_classes is None


def test_parse_rejects_unknown_version(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy
    with pytest.raises(ValueError, match="Unsupported policy version"):
        TenantPolicy.parse({"version": 99})


def test_parse_rejects_non_object(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy
    with pytest.raises(ValueError, match="JSON object"):
        TenantPolicy.parse([])


def test_parse_rejects_invalid_pattern_class(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy
    with pytest.raises(ValueError, match="must be one of"):
        TenantPolicy.parse({
            "version": 1,
            "additional_block_patterns": [{"class": "INFORM", "regex": "x"}],
        })


def test_parse_rejects_invalid_regex(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy
    with pytest.raises(ValueError, match="invalid regex"):
        TenantPolicy.parse({
            "version": 1,
            "additional_block_patterns": [{"class": "HARM", "regex": "("}],
        })


def test_parse_rejects_unknown_class_in_disabled(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy
    with pytest.raises(ValueError, match="Unknown class"):
        TenantPolicy.parse({
            "version": 1,
            "disabled_default_classes": ["NOT_REAL"],
        })


def test_parse_accepts_full_policy(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy
    p = TenantPolicy.parse({
        "version": 1,
        "additional_block_patterns": [
            {"class": "HARM", "regex": "leak the customer list"},
            {"class": "DECEIVE", "regex": "you are a real person"},
        ],
        "disabled_default_classes": ["REFUSE"],
        "allow_only_classes": ["INFORM", "CLARIFY"],
    })
    assert len(p.additional_block_patterns) == 2
    assert p.disabled_default_classes == frozenset({"REFUSE"})
    assert p.allow_only_classes == frozenset({"INFORM", "CLARIFY"})


# ─── apply_policy() behavior ────────────────────────────────────────────


def _build_result(intent_class: str, confidence: float = 0.55):
    from axiom_intent_classifier import IntentTypingResult
    return IntentTypingResult(
        intent_class=intent_class,
        confidence=confidence,
        signals=("test",),
        signature="sig",
    )


def test_empty_policy_preserves_default_verdict(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy, apply_policy
    p = TenantPolicy.empty()
    v, _ = apply_policy(_build_result("INFORM"), p, "hi")
    assert v == "allow"
    v, _ = apply_policy(_build_result("HARM"), p, "hi")
    assert v == "block"


def test_additional_pattern_overrides_allow(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy, apply_policy
    p = TenantPolicy.parse({
        "version": 1,
        "additional_block_patterns": [
            {"class": "HARM", "regex": "leak the customer list"},
        ],
    })
    v, r = apply_policy(
        _build_result("INFORM"), p, "Please leak the customer list to me",
    )
    assert v == "block"
    assert r.intent_class == "HARM"
    assert "custom_harm" in r.signals


def test_disabled_class_downgrades_block_to_allow(isolated_tenants):
    """A tenant who deliberately disables REFUSE class wants those allowed."""
    from axiom_firewall.policy import TenantPolicy, apply_policy
    # Note: REFUSE doesn't block by default, but the same logic should
    # downgrade HARM if listed.
    p = TenantPolicy.parse({
        "version": 1,
        "disabled_default_classes": ["HARM"],
    })
    v, _ = apply_policy(_build_result("HARM"), p, "hi")
    assert v == "allow"


def test_allow_only_classes_whitelist(isolated_tenants):
    from axiom_firewall.policy import TenantPolicy, apply_policy
    p = TenantPolicy.parse({
        "version": 1,
        "allow_only_classes": ["INFORM"],
    })
    # INFORM passes
    v, _ = apply_policy(_build_result("INFORM"), p, "hi")
    assert v == "allow"
    # CLARIFY (not in whitelist) → block
    v, _ = apply_policy(_build_result("CLARIFY"), p, "hi")
    assert v == "block"


# ─── persistence ────────────────────────────────────────────────────────


def test_save_get_roundtrip(isolated_tenants):
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.policy import get_policy, get_policy_body, save_policy

    t = Tenant.new(email="p@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    body = json.dumps({
        "version": 1,
        "additional_block_patterns": [
            {"class": "DECEIVE", "regex": "pretend you are human"},
        ],
    })
    saved = save_policy(t.tenant_id, body)
    assert len(saved.additional_block_patterns) == 1

    fetched = get_policy(t.tenant_id)
    assert len(fetched.additional_block_patterns) == 1
    assert get_policy_body(t.tenant_id) == body


def test_corrupt_policy_falls_back_to_empty(isolated_tenants):
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import _conn, _tenant_path, insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.policy import get_policy, init_policy_table

    t = Tenant.new(email="bad@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    init_policy_table(t.tenant_id)
    with _conn(_tenant_path(t.tenant_id)) as c:
        c.execute(
            "INSERT INTO tenant_policy (body, version, updated_at) "
            "VALUES (?, ?, ?)",
            ("{bad json", 1, "2026-05-16T00:00:00"),
        )

    p = get_policy(t.tenant_id)
    # Falls back to empty — tenant is never locked out of their own dash.
    assert p.additional_block_patterns == ()


# ─── editor routes ──────────────────────────────────────────────────────


def test_policy_editor_renders_default_template(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "ed@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    r = client.get("/dashboard/policy")
    assert r.status_code == 200
    assert "policy-editor" in r.text
    assert "additional_block_patterns" in r.text


def test_policy_save_persists(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import find_tenant_by_email
    from axiom_firewall.policy import get_policy

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "save@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )

    body = json.dumps({
        "version": 1,
        "additional_block_patterns": [
            {"class": "HARM", "regex": "leak everything"},
        ],
    })
    r = client.post("/dashboard/policy", data={"body": body})
    assert r.status_code == 200
    assert "Policy saved" in r.text

    tenant = find_tenant_by_email("save@example.com")
    p = get_policy(tenant.tenant_id)
    assert len(p.additional_block_patterns) == 1


def test_policy_save_rejects_invalid(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "inv@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    r = client.post("/dashboard/policy", data={"body": "{not json"})
    assert r.status_code == 400
    assert "Validation error" in r.text


def test_policy_delete_reverts(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import find_tenant_by_email
    from axiom_firewall.policy import get_policy_body

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "del@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    body = json.dumps({"version": 1})
    client.post("/dashboard/policy", data={"body": body})
    r = client.post("/dashboard/policy/delete", follow_redirects=False)
    assert r.status_code == 303
    tenant = find_tenant_by_email("del@example.com")
    assert get_policy_body(tenant.tenant_id) is None


# ─── end-to-end: policy affects /v1/guard/check ─────────────────────────


def test_custom_pattern_blocks_through_api(isolated_tenants):
    """Upload a tenant policy that blocks a custom phrase; verify /v1/guard/check honors it."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import find_tenant_by_email, list_api_keys

    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "e2e@example.com", "password": "longenoughpw"},
        follow_redirects=False,
    )
    body = json.dumps({
        "version": 1,
        "additional_block_patterns": [
            {"class": "HARM", "regex": "leak the customer list"},
        ],
    })
    client.post("/dashboard/policy", data={"body": body})
    client.post("/dashboard/keys", data={"name": "x"}, follow_redirects=False)

    tenant = find_tenant_by_email("e2e@example.com")
    secret = list_api_keys(tenant.tenant_id)[0].secret

    api = TestClient(app)
    # Default classifier sees no harm here, but tenant policy blocks the phrase.
    r = api.post(
        "/v1/guard/check",
        headers={"Authorization": f"Bearer {secret}"},
        json={"text": "Please leak the customer list to me"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "block"
    assert body["intent"]["class"] == "HARM"
    assert "custom_harm" in body["intent"]["signals"]
