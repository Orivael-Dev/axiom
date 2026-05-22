"""Tests for the `axiom-mcp` pipx console-script entry point.

Verifies:
  1. axiom_mcp_server exposes a `main()` callable
  2. pyproject.toml declares the entry point
  3. pyproject.toml ships axiom_mcp_server as a top-level module
  4. python -m axiom_mcp_server handles a stdin initialize round-trip
     (sanity check that the boot path still works post-refactor)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_main_callable_exists():
    """axiom_mcp_server.main is the pipx entry point — must be importable."""
    import axiom_mcp_server
    assert hasattr(axiom_mcp_server, "main"), \
        "axiom_mcp_server.main missing — pipx entry point will not resolve"
    assert callable(axiom_mcp_server.main)


def test_pyproject_declares_axiom_mcp_script():
    """The console script must point at axiom_mcp_server:main."""
    pyproject = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    assert "axiom-mcp" in scripts, \
        f"axiom-mcp script missing from [project.scripts]; got {list(scripts)}"
    assert scripts["axiom-mcp"] == "axiom_mcp_server:main", \
        f"axiom-mcp must point at axiom_mcp_server:main; got {scripts['axiom-mcp']!r}"


def test_pyproject_ships_top_level_modules():
    """axiom_mcp_server.py must be in py-modules so the wheel includes it."""
    pyproject = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    py_modules = data.get("tool", {}).get("setuptools", {}).get("py-modules", [])
    assert "axiom_mcp_server" in py_modules, \
        f"axiom_mcp_server missing from [tool.setuptools] py-modules: {py_modules}"
    assert "axiom_signing" in py_modules, \
        f"axiom_signing missing from py-modules (hard dep of axiom_mcp_server): {py_modules}"


def test_module_run_handles_initialize(tmp_path, monkeypatch):
    """`python -m axiom_mcp_server` round-trips a JSON-RPC initialize.

    Catches regressions in the module-run boot path that a pipx install
    would also fail on (since both go through the same `main()` →
    AxiomMCPServer().run() chain).
    """
    env = {
        "AXIOM_MASTER_KEY": "test" + "0" * 60,
        "PATH":            "/usr/bin:/bin:/usr/local/bin",
        "PYTHONPATH":      str(REPO_ROOT),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "axiom_mcp_server"],
        input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
        capture_output=True,
        timeout=10,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    # Server should respond on stdout with a JSON-RPC result. (It exits
    # cleanly when stdin closes — return code may be 0 or non-zero
    # depending on Python's stdin EOF handling; we only assert the
    # response shape.)
    assert proc.stdout.strip(), \
        f"no response on stdout. stderr={proc.stderr!r}"
    resp = json.loads(proc.stdout.strip().splitlines()[0])
    assert resp.get("jsonrpc") == "2.0"
    assert resp.get("id") == 1
    result = resp.get("result", {})
    assert result.get("serverInfo", {}).get("name") == "axiom"
    assert result.get("protocolVersion") == "2024-11-05"
