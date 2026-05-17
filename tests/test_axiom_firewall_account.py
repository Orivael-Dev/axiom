"""Tests for password reset (recovery-code flow) + account deletion.

Covers:
  1. Signup issues a recovery code (shown ONCE via the session)
  2. Forgot-password requires the right code; wrong code is rejected
  3. Successful reset issues a fresh recovery code + auto-logs-in
  4. Password change from inside the dashboard
  5. Recovery-code rotation requires current password
  6. Account deletion: wrong password → rejected
  7. Account deletion: missing DELETE confirmation → rejected
  8. Account deletion: success cascades (tenant row + DB file gone, session cleared)
  9. After delete, the same email can sign up again (right-to-erasure)
"""
from __future__ import annotations

import sys
from pathlib import Path

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
        "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    yield tmp_path


def _signup(client, email="ann@example.com", password="longenoughpw"):
    """Sign up + return the plaintext recovery code (pulled from session)."""
    r = client.post(
        "/signup",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # The recovery code is stashed in the session for one-shot display.
    # The TestClient persists cookies, so we can read the dashboard,
    # which pops the code out of the session into the HTML.
    page = client.get("/dashboard").text
    # Extract the recovery code from the rendered <code> block.
    import re
    m = re.search(r"<pre><code>([A-Z0-9\-]+)</code></pre>", page)
    assert m, "recovery code not rendered on dashboard after signup"
    return m.group(1)


# ─── 1. Signup issues a recovery code ────────────────────────────────────


def test_signup_issues_recovery_code_shown_once(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        code = _signup(client)
        # Format: groups of 4 alphanumerics separated by dashes
        assert "-" in code and len(code.replace("-", "")) >= 20
        # Second dashboard load — code should NOT be shown again
        page2 = client.get("/dashboard").text
        assert code not in page2


# ─── 2 + 3. Forgot-password flow ────────────────────────────────────────


def test_forgot_password_wrong_code_rejected(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        _signup(client)
        client.post("/logout")
        r = client.post(
            "/forgot-password",
            data={
                "email": "ann@example.com",
                "recovery_code": "WRONG-CODE-1234-5678",
                "new_password": "newpassword123",
            },
            follow_redirects=False,
        )
        assert r.status_code == 401
        # Confirm the password did NOT change — old password still works
        login = client.post(
            "/login",
            data={"email": "ann@example.com", "password": "longenoughpw"},
            follow_redirects=False,
        )
        assert login.status_code == 303


def test_forgot_password_unknown_email_rejected(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        r = client.post(
            "/forgot-password",
            data={
                "email": "nobody@example.com",
                "recovery_code": "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF",
                "new_password": "newpassword123",
            },
            follow_redirects=False,
        )
        assert r.status_code == 401


def test_forgot_password_success_resets_and_issues_fresh_code(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        original_code = _signup(client)
        client.post("/logout")

        r = client.post(
            "/forgot-password",
            data={
                "email": "ann@example.com",
                "recovery_code": original_code,
                "new_password": "brand-new-pw",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"

        # Fresh code shown on the dashboard
        page = client.get("/dashboard").text
        import re
        m = re.search(r"<pre><code>([A-Z0-9\-]+)</code></pre>", page)
        assert m, "fresh recovery code not shown after reset"
        new_code = m.group(1)
        assert new_code != original_code

        # Old code is no longer valid
        client.post("/logout")
        r2 = client.post(
            "/forgot-password",
            data={
                "email": "ann@example.com",
                "recovery_code": original_code,
                "new_password": "another-pw",
            },
            follow_redirects=False,
        )
        assert r2.status_code == 401


def test_forgot_password_normalizes_user_input(isolated_tenants):
    """Lowercase, missing dashes, embedded whitespace — all should still match."""
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        code = _signup(client)
        client.post("/logout")
        mangled = code.lower().replace("-", " ")  # spaces + lowercase
        r = client.post(
            "/forgot-password",
            data={
                "email": "ann@example.com",
                "recovery_code": mangled,
                "new_password": "freshpassword",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303


def test_forgot_password_short_new_password_rejected(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        code = _signup(client)
        client.post("/logout")
        r = client.post(
            "/forgot-password",
            data={
                "email": "ann@example.com",
                "recovery_code": code,
                "new_password": "short",
            },
            follow_redirects=False,
        )
        assert r.status_code == 400


# ─── 4. Change password from dashboard ──────────────────────────────────


def test_account_change_password_wrong_current_rejected(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        _signup(client)
        r = client.post(
            "/dashboard/account/password",
            data={"current_password": "wrong", "new_password": "newpasswordok"},
        )
        assert r.status_code == 401


def test_account_change_password_success(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        _signup(client)
        r = client.post(
            "/dashboard/account/password",
            data={"current_password": "longenoughpw", "new_password": "newpasswordok"},
        )
        assert r.status_code == 200
        client.post("/logout")
        login = client.post(
            "/login",
            data={"email": "ann@example.com", "password": "newpasswordok"},
            follow_redirects=False,
        )
        assert login.status_code == 303


# ─── 5. Rotate recovery code ────────────────────────────────────────────


def test_rotate_recovery_code_invalidates_old_one(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        old_code = _signup(client)
        r = client.post(
            "/dashboard/account/recovery/rotate",
            data={"password": "longenoughpw"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        page = client.get("/dashboard/account").text
        import re
        m = re.search(r"<pre><code>([A-Z0-9\-]+)</code></pre>", page)
        assert m
        new_code = m.group(1)
        assert new_code != old_code

        # Old code no longer works for reset
        client.post("/logout")
        r2 = client.post(
            "/forgot-password",
            data={
                "email": "ann@example.com",
                "recovery_code": old_code,
                "new_password": "trying-old",
            },
            follow_redirects=False,
        )
        assert r2.status_code == 401


def test_rotate_recovery_code_wrong_password_rejected(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        _signup(client)
        r = client.post(
            "/dashboard/account/recovery/rotate",
            data={"password": "wrong"},
        )
        assert r.status_code == 401


# ─── 6 + 7. Account deletion guards ─────────────────────────────────────


def test_delete_account_wrong_password_rejected(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        _signup(client)
        r = client.post(
            "/dashboard/account/delete",
            data={"password": "wrong", "confirm": "DELETE"},
        )
        assert r.status_code == 401
        # Tenant still exists
        from axiom_firewall.db import find_tenant_by_email
        assert find_tenant_by_email("ann@example.com") is not None


def test_delete_account_missing_confirm_rejected(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        _signup(client)
        r = client.post(
            "/dashboard/account/delete",
            data={"password": "longenoughpw", "confirm": "delete"},  # lowercase
        )
        assert r.status_code == 400
        from axiom_firewall.db import find_tenant_by_email
        assert find_tenant_by_email("ann@example.com") is not None


# ─── 8 + 9. Successful deletion + email reusability ────────────────────


def test_delete_account_success_cascade(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app
    from axiom_firewall.db import (
        _tenant_path, find_tenant_by_email,
    )

    with TestClient(app) as client:
        _signup(client)
        # Create an API key first so we can check cascade
        client.post("/dashboard/keys", data={"name": "k"})
        t_before = find_tenant_by_email("ann@example.com")
        assert t_before is not None
        tenant_db_path = _tenant_path(t_before.tenant_id)
        assert tenant_db_path.exists()

        r = client.post(
            "/dashboard/account/delete",
            data={"password": "longenoughpw", "confirm": "DELETE"},
        )
        assert r.status_code == 200
        assert "deleted" in r.text.lower()

        # Tenant row gone
        assert find_tenant_by_email("ann@example.com") is None
        # Per-tenant SQLite file gone
        assert not tenant_db_path.exists()

        # Session cleared — dashboard redirects to login
        r2 = client.get("/dashboard", follow_redirects=False)
        assert r2.status_code == 303
        assert r2.headers["location"].endswith("/login")


def test_deleted_email_can_signup_again(isolated_tenants):
    from fastapi.testclient import TestClient
    from axiom_firewall.dashboard import app

    with TestClient(app) as client:
        _signup(client)
        client.post(
            "/dashboard/account/delete",
            data={"password": "longenoughpw", "confirm": "DELETE"},
        )
        # Fresh signup with the same email succeeds
        r = client.post(
            "/signup",
            data={"email": "ann@example.com", "password": "different-pw"},
            follow_redirects=False,
        )
        assert r.status_code == 303
