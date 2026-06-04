# -*- coding: utf-8 -*-
"""
Bridge command resolution — pure (no server launch).
=====================================================
The packaged build points the bridge at a frozen Axiom sidecar binary via
AX_OS_MCP_BIN; these assert the precedence without starting anything.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import AxiomBridge  # noqa: E402


def test_explicit_command_wins(monkeypatch):
    monkeypatch.setenv("AX_OS_MCP_BIN", "/opt/axiom-mcp")
    ax = AxiomBridge(command=["python", "axiom_mcp_server.py"], cwd="/repo")
    assert ax._command == ["python", "axiom_mcp_server.py"]


def test_sidecar_binary_from_env(monkeypatch):
    monkeypatch.setenv("AX_OS_MCP_BIN", "/opt/ax-os/axiom-mcp")
    ax = AxiomBridge()
    assert ax._command == ["/opt/ax-os/axiom-mcp"]


def test_default_is_installed_package(monkeypatch):
    monkeypatch.delenv("AX_OS_MCP_BIN", raising=False)
    ax = AxiomBridge()
    assert ax._command == ["python", "-m", "axiom_mcp_server"]
