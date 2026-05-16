"""Tests for the Phase 2 Skill Pack registry server (packs.orivael.dev).

Covers: index, latest-version lookup, exact-version lookup, healthz/
readyz, rejection of unsigned packs, path-traversal safety.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def signed_packs_dir(tmp_path, monkeypatch):
    """Build a signed pack dir from a fresh first-party manifest.

    The signature is derived from the test AXIOM_MASTER_KEY; we need to
    SIGN the pack in the test environment, not reuse the repo's signed
    packs (those use the production key).
    """
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    monkeypatch.setenv("AXIOM_PACKS_DIR", str(packs_dir))
    for mod in list(sys.modules):
        if mod.startswith("axiom_packs") or mod.startswith("axiom_firewall") or mod == "axiom_signing":
            sys.modules.pop(mod, None)

    from axiom_firewall.skill_pack import sign_first_party

    def write(name: str, version: str, *, title: str | None = None):
        body = {
            "format_version": "1.0",
            "name":           name,
            "title":          title or f"{name} (test)",
            "description":    "test pack",
            "version":        version,
            "author":         "Test",
            "license":        "MIT",
            "tags":           ["test"],
            "tested_against": ["axiom-firewall>=0.1.0"],
            "policy": {
                "version": 1,
                "additional_block_patterns": [
                    {"class": "HARM", "regex": "evil pattern"},
                ],
                "disabled_default_classes": [],
                "allow_only_classes": None,
            },
        }
        body["signature"] = sign_first_party(body)
        (packs_dir / name).mkdir(parents=True, exist_ok=True)
        (packs_dir / name / "pack.json").write_text(
            json.dumps(body, indent=2), encoding="utf-8"
        )
        return body

    yield packs_dir, write


def _client(signed_packs_dir):
    from fastapi.testclient import TestClient
    from axiom_packs.server import app
    return TestClient(app)


# ─── Health endpoints ───────────────────────────────────────────────────


def test_healthz(signed_packs_dir):
    r = _client(signed_packs_dir).get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_ready_when_packs_dir_exists(signed_packs_dir):
    packs_dir, write = signed_packs_dir
    write("alpha", "0.1.0")
    r = _client(signed_packs_dir).get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["pack_count"] == 1


def test_readyz_unready_when_packs_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_PACKS_DIR", str(tmp_path / "no-such-dir"))
    for mod in list(sys.modules):
        if mod.startswith("axiom_packs"):
            sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    from axiom_packs.server import app
    r = TestClient(app).get("/readyz")
    assert r.status_code == 503
    assert "not found" in r.json()["error"]


# ─── Index + lookup ─────────────────────────────────────────────────────


def test_packs_index_empty(signed_packs_dir):
    r = _client(signed_packs_dir).get("/v1/packs")
    assert r.status_code == 200
    body = r.json()
    assert body["format_version"] == "1.0"
    assert body["packs"] == []


def test_packs_index_lists_signed_packs(signed_packs_dir):
    packs_dir, write = signed_packs_dir
    write("alpha", "0.1.0", title="Alpha")
    write("beta",  "0.2.0", title="Beta")
    r = _client(signed_packs_dir).get("/v1/packs")
    assert r.status_code == 200
    names = sorted(p["name"] for p in r.json()["packs"])
    assert names == ["alpha", "beta"]


def test_pack_latest_returns_full_manifest(signed_packs_dir):
    _, write = signed_packs_dir
    write("alpha", "0.1.0")
    r = _client(signed_packs_dir).get("/v1/packs/alpha")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "alpha"
    assert body["version"] == "0.1.0"
    # Full manifest INCLUDES the policy section (index doesn't)
    assert "policy" in body
    assert body["policy"]["additional_block_patterns"][0]["class"] == "HARM"


def test_pack_versioned_lookup_via_directory(signed_packs_dir):
    """packs/<name>/<version>/pack.json should resolve."""
    packs_dir, write = signed_packs_dir
    # Manually build a versioned tree
    from axiom_firewall.skill_pack import sign_first_party
    body = {
        "format_version": "1.0",
        "name":           "history",
        "title":          "Old version",
        "description":    "x",
        "version":        "0.0.9",
        "author":         "Test",
        "license":        "MIT",
        "tags":           [],
        "tested_against": [],
        "policy": {
            "version": 1,
            "additional_block_patterns": [],
            "disabled_default_classes": [],
            "allow_only_classes": None,
        },
    }
    body["signature"] = sign_first_party(body)
    (packs_dir / "history" / "0.0.9").mkdir(parents=True, exist_ok=True)
    (packs_dir / "history" / "0.0.9" / "pack.json").write_text(
        json.dumps(body, indent=2)
    )

    r = _client(signed_packs_dir).get("/v1/packs/history/0.0.9")
    assert r.status_code == 200
    assert r.json()["version"] == "0.0.9"


def test_pack_versioned_falls_back_to_latest_match(signed_packs_dir):
    """If only packs/<name>/pack.json exists and its version matches, return it."""
    _, write = signed_packs_dir
    write("alpha", "0.1.0")
    r = _client(signed_packs_dir).get("/v1/packs/alpha/0.1.0")
    assert r.status_code == 200
    assert r.json()["version"] == "0.1.0"


def test_pack_versioned_returns_404_on_wrong_version(signed_packs_dir):
    _, write = signed_packs_dir
    write("alpha", "0.1.0")
    r = _client(signed_packs_dir).get("/v1/packs/alpha/9.9.9")
    assert r.status_code == 404


def test_unknown_pack_returns_404(signed_packs_dir):
    r = _client(signed_packs_dir).get("/v1/packs/nope")
    assert r.status_code == 404


# ─── Security ───────────────────────────────────────────────────────────


def test_unsigned_pack_not_listed(signed_packs_dir):
    """A pack with no signature must NOT show up in the index."""
    packs_dir, _ = signed_packs_dir
    body = {
        "format_version": "1.0",
        "name":           "rogue",
        "title":          "Rogue",
        "description":    "no signature",
        "version":        "0.1.0",
        "author":         "Anon",
        "license":        "MIT",
        "tags":           [],
        "tested_against": [],
        "policy": {
            "version": 1,
            "additional_block_patterns": [],
            "disabled_default_classes": [],
            "allow_only_classes": None,
        },
        # signature absent
    }
    (packs_dir / "rogue").mkdir()
    (packs_dir / "rogue" / "pack.json").write_text(json.dumps(body))

    r = _client(signed_packs_dir).get("/v1/packs")
    assert r.status_code == 200
    assert r.json()["packs"] == []  # rejected

    r = _client(signed_packs_dir).get("/v1/packs/rogue")
    assert r.status_code == 404


def test_pack_with_wrong_key_signature_rejected(signed_packs_dir):
    """A pack signed with a non-master key should NOT be served."""
    packs_dir, _ = signed_packs_dir
    from axiom_firewall.skill_pack import sign_payload
    body = {
        "format_version": "1.0",
        "name":           "wrong-key",
        "title":          "Wrong key",
        "description":    "signed with the wrong key",
        "version":        "0.1.0",
        "author":         "Anon",
        "license":        "MIT",
        "tags":           [],
        "tested_against": [],
        "policy": {
            "version": 1,
            "additional_block_patterns": [],
            "disabled_default_classes": [],
            "allow_only_classes": None,
        },
    }
    body["signature"] = sign_payload(body, b"wrong-key-32-bytes-xxxxxxxxxxxx")
    (packs_dir / "wrong-key").mkdir()
    (packs_dir / "wrong-key" / "pack.json").write_text(json.dumps(body))

    r = _client(signed_packs_dir).get("/v1/packs/wrong-key")
    assert r.status_code == 404


def test_path_traversal_rejected(signed_packs_dir):
    r = _client(signed_packs_dir).get("/v1/packs/..%2Fetc")
    # FastAPI's path validator likely won't even hit our handler with
    # this, but if it does, _safe_name() catches it.
    assert r.status_code in (404, 400)


def test_request_id_echoed(signed_packs_dir):
    r = _client(signed_packs_dir).get(
        "/v1/packs", headers={"x-request-id": "trace-abc-123"}
    )
    assert r.headers["x-request-id"] == "trace-abc-123"


def test_cors_default_allows_all(signed_packs_dir, monkeypatch):
    """Registry is meant to be globally browseable — default CORS = *."""
    r = _client(signed_packs_dir).options(
        "/v1/packs",
        headers={
            "Origin": "https://random.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "*"
