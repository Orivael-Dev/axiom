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


# ── ORVL tool wrappers map to the right tool/args (no server needed) ─────────

def _record_calls(ax):
    calls = []
    ax.call_tool = lambda name, args: calls.append((name, args)) or {}
    return calls


def test_immune_scan_maps_to_axiom_immune():
    ax = AxiomBridge()
    calls = _record_calls(ax)
    ax.immune_scan("payload here", vector="probe")
    assert calls == [("axiom_immune", {"payload": "payload here", "vector": "probe"})]


def test_mkb_wrappers_map_to_axiom_mkb():
    ax = AxiomBridge()
    calls = _record_calls(ax)
    ax.mkb_register("AGENT x\n")
    ax.mkb_find("x", version="1.0")
    ax.mkb_list(block_type="GUARD")
    assert calls == [
        ("axiom_mkb", {"action": "register", "spec_content": "AGENT x\n"}),
        ("axiom_mkb", {"action": "find", "name": "x", "version": "1.0"}),
        ("axiom_mkb", {"action": "list", "block_type": "GUARD"}),
    ]


def test_crl_and_cas_wrappers():
    ax = AxiomBridge()
    calls = _record_calls(ax)
    ax.crl_compute({"constitutional_distance": 0.1})
    ax.cas_defend(["attack one", {"vector": "v", "payload": "p"}])
    ax.cas_report()
    assert calls[0] == ("axiom_crl", {"action": "compute", "scores": {"constitutional_distance": 0.1}})
    assert calls[1] == ("axiom_cas", {"action": "defend",
                                      "attacks": ["attack one", {"vector": "v", "payload": "p"}]})
    assert calls[2] == ("axiom_cas", {"action": "report"})
