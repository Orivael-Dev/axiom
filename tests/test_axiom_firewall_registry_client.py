"""Tests for the Firewall → registry HTTP client.

Spins up the real axiom_packs.server.app on an ephemeral port and
points the registry_client at it. This exercises the full HTTP path
including signature verification, end-to-end.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def isolated_packs(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    monkeypatch.setenv("AXIOM_PACKS_DIR", str(packs_dir))
    for mod in list(sys.modules):
        if mod.startswith(("axiom_packs", "axiom_firewall", "axiom_signing")):
            sys.modules.pop(mod, None)

    from axiom_firewall.skill_pack import sign_first_party

    def write(name: str, version: str = "0.1.0"):
        body = {
            "format_version": "1.0",
            "name":           name,
            "title":          f"{name} (test)",
            "description":    "test",
            "version":        version,
            "author":         "Test",
            "license":        "MIT",
            "tags":           ["test"],
            "tested_against": [],
            "policy": {
                "version": 1,
                "additional_block_patterns": [
                    {"class": "HARM", "regex": "registry-test"},
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

    yield packs_dir, write


@pytest.fixture
def running_registry(isolated_packs):
    """Spin up the real registry server on a random port using uvicorn-free TestClient."""
    from fastapi.testclient import TestClient
    from axiom_packs.server import app

    # TestClient routes HTTP via the ASGI app in-process — fast and
    # avoids real socket binding. For the registry client tests we
    # need a real socket because urllib won't go through ASGI. Use a
    # threading-based ASGI runner instead.
    import asyncio
    import socket
    import uvicorn

    # Pick a random free port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", lifespan="off",
    )
    server = uvicorn.Server(config)
    server.config.load()

    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()

    # Wait for the server to come up
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(40):
        try:
            from urllib.request import urlopen
            urlopen(f"{base_url}/healthz", timeout=0.2)
            break
        except Exception:
            time.sleep(0.05)
    else:
        raise RuntimeError("registry server did not start in time")

    yield base_url, isolated_packs[1]  # base_url + write fn

    server.should_exit = True
    thread.join(timeout=2)


# ─── list_packs ─────────────────────────────────────────────────────────


def test_list_packs_empty_registry(running_registry):
    base_url, _ = running_registry
    from axiom_firewall.registry_client import list_packs
    assert list_packs(base_url) == []


def test_list_packs_finds_two(running_registry):
    base_url, write = running_registry
    write("alpha")
    write("beta")
    from axiom_firewall.registry_client import list_packs
    out = list_packs(base_url)
    names = sorted(m.name for m in out)
    assert names == ["alpha", "beta"]
    # Each manifest should have its policy attached (we fetched full,
    # not just the index).
    assert out[0].policy["additional_block_patterns"][0]["class"] == "HARM"


# ─── get_pack ───────────────────────────────────────────────────────────


def test_get_pack_returns_manifest(running_registry):
    base_url, write = running_registry
    write("alpha")
    from axiom_firewall.registry_client import get_pack
    m = get_pack(base_url, "alpha")
    assert m is not None
    assert m.name == "alpha"
    assert m.version == "0.1.0"


def test_get_pack_with_explicit_version(running_registry):
    base_url, write = running_registry
    write("alpha", version="0.2.5")
    from axiom_firewall.registry_client import get_pack
    m = get_pack(base_url, "alpha", version="0.2.5")
    assert m is not None
    assert m.version == "0.2.5"


def test_get_pack_unknown_returns_none(running_registry):
    base_url, _ = running_registry
    from axiom_firewall.registry_client import get_pack
    assert get_pack(base_url, "does-not-exist") is None


def test_get_pack_wrong_version_returns_none(running_registry):
    base_url, write = running_registry
    write("alpha", version="0.1.0")
    from axiom_firewall.registry_client import get_pack
    assert get_pack(base_url, "alpha", version="9.9.9") is None


# ─── transport errors ───────────────────────────────────────────────────


def test_unreachable_registry_raises_registry_error(running_registry):
    from axiom_firewall.registry_client import RegistryError, list_packs
    with pytest.raises(RegistryError, match="failed to reach"):
        list_packs("http://127.0.0.1:1", timeout=0.5)


def test_timeout_raises_registry_error():
    # Run a tiny HTTP server that ACCEPTS connections but never replies.
    class _Hang(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            time.sleep(5)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a, **k):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Hang)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from axiom_firewall.registry_client import RegistryError, list_packs
        # Need master key for the module import chain
        import os
        os.environ.setdefault("AXIOM_MASTER_KEY", "test" + "0" * 60)
        for mod in list(sys.modules):
            if mod.startswith(("axiom_firewall", "axiom_signing")):
                sys.modules.pop(mod, None)
        from axiom_firewall.registry_client import RegistryError, list_packs
        with pytest.raises(RegistryError):
            list_packs(f"http://127.0.0.1:{port}", timeout=0.3)
    finally:
        server.shutdown()
        server.server_close()
