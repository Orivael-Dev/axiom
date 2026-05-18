"""Tests for axiom_packs_cli — the CLI client for the pack registry.

Hermetic: replaces urllib-based _fetch_json with a TestClient call so
every test exercises the real registry server logic but never hits
the network.

Covers:

  - list         renders a table that includes every available pack
  - show         pretty-prints + verifies signature (exit 0 on PASS,
                                                     exit 2 on FAIL)
  - install      writes pack.json, verifies first, refuses on bad sig,
                 respects --force, exits non-zero on existing without
                 --force
  - verify       local-only check; exits 0 on PASS, 2 on FAIL,
                 1 on missing/malformed file
  - sources      prints active registry + source (default / env)
  - registry-url resolution priority: flag > env > default
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def cli_against_test_server(tmp_path, monkeypatch):
    """Build a signed pack dir, spin up the FastAPI app via TestClient,
    and rewire axiom_packs_cli._fetch_json to call that client instead
    of the real network."""
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.delenv("AXIOM_PACKS_REGISTRY", raising=False)

    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    monkeypatch.setenv("AXIOM_PACKS_DIR", str(packs_dir))

    # Force fresh imports so the new env vars take effect
    for mod in list(sys.modules):
        if (mod.startswith("axiom_packs") or mod.startswith("axiom_firewall")
                or mod == "axiom_signing" or mod == "axiom_packs_cli"):
            sys.modules.pop(mod, None)

    from fastapi.testclient import TestClient

    from axiom_firewall.skill_pack import sign_first_party
    from axiom_packs.server import app
    import axiom_packs_cli as cli

    client = TestClient(app)

    def write_pack(name: str, version: str = "0.1.0",
                   title: str | None = None) -> dict:
        body = {
            "format_version": "1.0",
            "name":           name,
            "title":          title or f"{name} (test pack)",
            "description":    "test pack",
            "version":        version,
            "author":         "Test",
            "license":        "MIT",
            "tags":           ["test", "compliance"],
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

    def fake_fetch_json(url, timeout=30):
        # url looks like "http://localhost:8002/v1/packs/foo" — extract path
        path = "/" + url.split("/", 3)[-1] if url.startswith("http") else url
        r = client.get(path)
        if r.status_code != 200:
            raise cli.RegistryError(f"HTTP {r.status_code} from {url}")
        return r.json()

    monkeypatch.setattr(cli, "_fetch_json", fake_fetch_json)
    yield cli, write_pack, packs_dir


# ─── list ───────────────────────────────────────────────────────────────


def test_list_shows_all_packs(cli_against_test_server, capsys):
    cli, write_pack, _ = cli_against_test_server
    write_pack("alpha", "0.1.0", title="Alpha pack")
    write_pack("beta", "0.2.0", title="Beta pack")
    rc = cli.main(["axiom-packs", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out
    assert "0.1.0" in out
    assert "0.2.0" in out
    assert "2 pack(s) available" in out


def test_list_empty_registry(cli_against_test_server, capsys):
    cli, _, _ = cli_against_test_server
    rc = cli.main(["axiom-packs", "list"])
    assert rc == 0
    assert "no packs" in capsys.readouterr().out


# ─── show ───────────────────────────────────────────────────────────────


def test_show_pretty_prints_and_verifies(cli_against_test_server, capsys):
    cli, write_pack, _ = cli_against_test_server
    write_pack("alpha", "0.1.0")
    rc = cli.main(["axiom-packs", "show", "alpha"])
    assert rc == 0
    cap = capsys.readouterr()
    # Manifest body to stdout, verification banner to stderr
    body = json.loads(cap.out)
    assert body["name"] == "alpha"
    assert "VERIFIED" in cap.err


def test_show_specific_version(cli_against_test_server, capsys):
    cli, write_pack, _ = cli_against_test_server
    write_pack("alpha", "0.1.0")
    rc = cli.main(["axiom-packs", "show", "alpha", "--version", "0.1.0"])
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["version"] == "0.1.0"


def test_show_returns_1_when_server_filters_corrupt_pack(
        cli_against_test_server, capsys):
    """The server's _load_manifest rejects any pack whose signature
    doesn't verify under the first-party namespace. Once we corrupt
    the on-disk file, the server treats the pack as nonexistent —
    the CLI sees HTTP 404 → RegistryError → exit 1. This is
    server-side defense in depth: bad packs never reach clients."""
    cli, write_pack, packs_dir = cli_against_test_server
    write_pack("alpha", "0.1.0")
    pack_path = packs_dir / "alpha" / "pack.json"
    body = json.loads(pack_path.read_text())
    body["signature"] = "0" * 64
    pack_path.write_text(json.dumps(body))
    rc = cli.main(["axiom-packs", "show", "alpha"])
    assert rc == 1
    assert "registry error" in capsys.readouterr().err


# ─── install ────────────────────────────────────────────────────────────


def test_install_writes_pack_json(cli_against_test_server, tmp_path, capsys):
    cli, write_pack, _ = cli_against_test_server
    write_pack("alpha", "0.1.0")
    dest = tmp_path / "install_target"
    rc = cli.main(["axiom-packs", "install", "alpha", "--dest", str(dest)])
    assert rc == 0
    pack_path = dest / "alpha" / "pack.json"
    assert pack_path.is_file()
    body = json.loads(pack_path.read_text())
    assert body["name"] == "alpha"
    assert body["signature"]
    assert "VERIFIED" in capsys.readouterr().out


def test_install_refuses_existing_without_force(cli_against_test_server,
                                                  tmp_path, capsys):
    cli, write_pack, _ = cli_against_test_server
    write_pack("alpha", "0.1.0")
    dest = tmp_path / "install_target"
    rc1 = cli.main(["axiom-packs", "install", "alpha", "--dest", str(dest)])
    assert rc1 == 0
    rc2 = cli.main(["axiom-packs", "install", "alpha", "--dest", str(dest)])
    assert rc2 == 1
    assert "--force" in capsys.readouterr().err


def test_install_force_overwrites(cli_against_test_server, tmp_path):
    cli, write_pack, _ = cli_against_test_server
    write_pack("alpha", "0.1.0")
    dest = tmp_path / "install_target"
    cli.main(["axiom-packs", "install", "alpha", "--dest", str(dest)])
    rc = cli.main(["axiom-packs", "install", "alpha",
                   "--dest", str(dest), "--force"])
    assert rc == 0


# ─── verify ─────────────────────────────────────────────────────────────


def test_verify_passes_on_signed_pack(cli_against_test_server, tmp_path,
                                        capsys):
    cli, write_pack, _ = cli_against_test_server
    write_pack("alpha", "0.1.0")
    dest = tmp_path / "install_target"
    cli.main(["axiom-packs", "install", "alpha", "--dest", str(dest)])
    rc = cli.main(["axiom-packs", "verify",
                   str(dest / "alpha" / "pack.json")])
    assert rc == 0
    assert "VERIFIED" in capsys.readouterr().out


def test_verify_fails_on_tampered_pack(cli_against_test_server, tmp_path,
                                        capsys):
    cli, write_pack, _ = cli_against_test_server
    write_pack("alpha", "0.1.0")
    dest = tmp_path / "install_target"
    cli.main(["axiom-packs", "install", "alpha", "--dest", str(dest)])
    p = dest / "alpha" / "pack.json"
    body = json.loads(p.read_text())
    body["description"] = "tampered"
    p.write_text(json.dumps(body))
    rc = cli.main(["axiom-packs", "verify", str(p)])
    assert rc == 2
    assert "FAILED" in capsys.readouterr().out


def test_verify_returns_1_on_missing_file(cli_against_test_server, tmp_path,
                                            capsys):
    cli, _, _ = cli_against_test_server
    rc = cli.main(["axiom-packs", "verify", str(tmp_path / "nope.json")])
    assert rc == 1


# ─── registry URL resolution ────────────────────────────────────────────


def test_registry_resolution_priority(cli_against_test_server, monkeypatch):
    cli, _, _ = cli_against_test_server
    # Default
    assert cli._resolve_registry(None) == "http://localhost:8002"
    # Env var
    monkeypatch.setenv("AXIOM_PACKS_REGISTRY", "https://env.example/")
    assert cli._resolve_registry(None) == "https://env.example"
    # Flag wins over env
    assert cli._resolve_registry("https://flag.example/") == "https://flag.example"


def test_sources_command(cli_against_test_server, capsys):
    cli, _, _ = cli_against_test_server
    rc = cli.main(["axiom-packs", "sources"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "localhost:8002" in out
    assert "AXIOM_PACKS_REGISTRY" in out
