"""
AX OS → Axiom bridge — the single seam to the Axiom trust layer.
================================================================
AX OS consumes Axiom only through its public MCP tool surface (and the
published package). This module launches the Axiom MCP server as a
subprocess and speaks JSON-RPC 2.0 over stdio. **No Axiom source is
vendored into AX OS** — every Axiom call in the product goes through
``AxiomBridge`` so there is one reviewable integration point.

See ``BOUNDARY.md`` at the repo root for the contract this enforces.

Usage:
    from bridge import AxiomBridge

    with AxiomBridge() as ax:
        ctx = ax.assemble_workspace("help me work on the launch demo")
        if ctx["allowed"]:
            ...   # ctx["recalled"] holds the signed local context
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Any, Optional, Sequence


class AxiomError(RuntimeError):
    """An Axiom MCP call failed or the server returned an error."""


class AxiomBridge:
    """Thin MCP stdio client for the Axiom trust layer.

    Parameters
    ----------
    command:
        How to launch the Axiom MCP server. Defaults to the installed
        package entrypoint (``python -m axiom_mcp_server``). Point this at
        a checkout (e.g. ``["python", "axiom_mcp_server.py"]`` with
        ``cwd=...``) for local development.
    cwd, env:
        Working directory / extra environment for the server process.
        ``AXIOM_MASTER_KEY`` (signing) and ``AXIOM_MEMORY_STORE`` (the
        local-first memory path) are typically supplied via ``env``.
    timeout:
        Per-call read timeout, in seconds.
    """

    def __init__(self, command: Optional[Sequence[str]] = None, *,
                 cwd: Optional[str] = None,
                 env: Optional[dict] = None,
                 timeout: float = 30.0):
        # How to launch the Axiom MCP server. Explicit `command` wins; else a
        # frozen Axiom sidecar binary via AX_OS_MCP_BIN (for packaged builds);
        # else the installed package.
        if command:
            self._command = list(command)
        elif os.environ.get("AX_OS_MCP_BIN"):
            self._command = [os.environ["AX_OS_MCP_BIN"]]
        else:
            self._command = ["python", "-m", "axiom_mcp_server"]
        self._cwd = cwd
        self._env = {**os.environ, **(env or {})}
        self._timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────
    def __enter__(self) -> "AxiomBridge":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            self._command, cwd=self._cwd, env=self._env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        # MCP handshake.
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ax-os-bridge", "version": "0.1.0"},
        })
        self._notify("notifications/initialized")

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        finally:
            self._proc = None

    # ── JSON-RPC plumbing ────────────────────────────────────────
    def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _rpc(self, method: str, params: Optional[dict] = None) -> Any:
        if self._proc is None:
            self.start()
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})
            assert self._proc and self._proc.stdout
            line = self._proc.stdout.readline()
        if not line:
            raise AxiomError("axiom server closed the connection")
        resp = json.loads(line)
        if resp.get("error"):
            raise AxiomError(resp["error"].get("message", "unknown error"))
        return resp.get("result")

    # ── generic tool access ──────────────────────────────────────
    def list_tools(self) -> list[str]:
        result = self._rpc("tools/list") or {}
        return [t["name"] for t in result.get("tools", [])]

    def call_tool(self, name: str, arguments: dict) -> Any:
        """Call an Axiom tool and unwrap the JSON result payload."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments}) or {}
        content = result.get("content") or []
        if content and content[0].get("type") == "text":
            return json.loads(content[0]["text"])
        return result

    # ── typed AX OS surface (the seam product code uses) ─────────
    def assemble_workspace(self, goal: str, domain: Optional[str] = None) -> dict:
        """Intent-gated workspace assembly for a goal (ORVL-015/016)."""
        args: dict = {"goal": goal}
        if domain:
            args["domain"] = domain
        return self.call_tool("axiom_workspace", args)

    def remember(self, text: str, *, domain: str = "general",
                 constraints: Optional[Sequence[str]] = None,
                 resolution: str = "",
                 history: Optional[Sequence[str]] = None) -> dict:
        """Compress + store a signed memory packet (ORVL-015)."""
        return self.call_tool("axiom_memory", {
            "action": "remember", "text": text, "domain": domain,
            "constraints": list(constraints or ()),
            "resolution": resolution, "history": list(history or ()),
        })

    def recall(self, query: str, domain: Optional[str] = None) -> dict:
        """Recall the closest authentic memory packet for a query."""
        args: dict = {"action": "recall", "query": query}
        if domain:
            args["domain"] = domain
        return self.call_tool("axiom_memory", args)

    def guard_check(self, text: str) -> dict:
        """Two-layer constitutional safety check on text (ORVL-001/016)."""
        return self.call_tool("axiom_guard_check", {"input": text})

    def log_event(self, event_type: str, *, actor: str = "", subject: str = "",
                  outcome: str = "", attributes: Optional[dict] = None) -> dict:
        """Append a signed audit event to the Axiom ledger (ORVL-001)."""
        return self.call_tool("axiom_ledger", {
            "action": "log", "event_type": event_type, "actor": actor,
            "subject": subject, "outcome": outcome, "attributes": attributes or {},
        })

    def audit_list(self, *, event_type: Optional[str] = None,
                   since: Optional[str] = None, limit: Optional[int] = None) -> dict:
        """List signed audit events (optionally filtered)."""
        args: dict = {"action": "list"}
        if event_type:
            args["event_type"] = event_type
        if since:
            args["since"] = since
        if limit:
            args["limit"] = limit
        return self.call_tool("axiom_ledger", args)

    def audit_verify(self) -> dict:
        """Re-verify every audit row; reports any tampered entries."""
        return self.call_tool("axiom_ledger", {"action": "verify"})

    # ── signed-agent marketplace (bonded authority) ──────────────
    def mkt_verify(self, manifest: dict) -> dict:
        """Check a signed agent manifest (no install)."""
        return self.call_tool("axiom_marketplace", {"action": "verify", "manifest": manifest})

    def mkt_install(self, manifest: dict) -> dict:
        """Sandbox-install a signed agent (installed, not yet authorized)."""
        return self.call_tool("axiom_marketplace",
                              {"action": "sandbox_install", "manifest": manifest})

    def mkt_review(self, manifest: dict, pair_id: str) -> dict:
        """Human-readable access report for an installed agent."""
        return self.call_tool("axiom_marketplace",
                              {"action": "review", "manifest": manifest, "pair_id": pair_id})

    def mkt_approve(self, pair_id: str, actor: str = "human") -> dict:
        """Grant scoped authority to a sandboxed agent."""
        return self.call_tool("axiom_marketplace",
                              {"action": "approve", "pair_id": pair_id, "actor": actor})

    def mkt_revoke(self, pair_id: str, actor: str = "human") -> dict:
        """Cut an agent's authority instantly (terminal)."""
        return self.call_tool("axiom_marketplace",
                              {"action": "revoke", "pair_id": pair_id, "actor": actor})

    def mkt_authority(self, pair_id: str) -> dict:
        """The gate: is this agent currently authorized to act?"""
        return self.call_tool("axiom_marketplace",
                              {"action": "authority", "pair_id": pair_id})

    # ── ORVL-012 — Constitutional Immune System ──────────────────
    def immune_scan(self, payload: str, vector: Optional[str] = None) -> dict:
        """Screen a payload through the blue-team antibody detectors."""
        args: dict = {"payload": payload}
        if vector:
            args["vector"] = vector
        return self.call_tool("axiom_immune", args)

    # ── ORVL-004 — Modular Constitutional Knowledge Blocks ───────
    def mkb_register(self, spec_content: str) -> dict:
        """Parse a .axiom spec into a signed KnowledgeBlock and register it."""
        return self.call_tool("axiom_mkb",
                              {"action": "register", "spec_content": spec_content})

    def mkb_find(self, name: str, version: Optional[str] = None) -> dict:
        """Look a registered block up by name (+ optional version)."""
        args: dict = {"action": "find", "name": name}
        if version:
            args["version"] = version
        return self.call_tool("axiom_mkb", args)

    def mkb_list(self, block_type: Optional[str] = None) -> dict:
        """List registered blocks, optionally filtered by block_type."""
        args: dict = {"action": "list"}
        if block_type:
            args["block_type"] = block_type
        return self.call_tool("axiom_mkb", args)

    # ── ORVL-011 — Constitutional Reinforcement Learning ─────────
    def crl_compute(self, scores: dict) -> dict:
        """Reward from governance scores (distance/monotonic/cas/cbv)."""
        return self.call_tool("axiom_crl", {"action": "compute", "scores": scores})

    def crl_score(self, prompt: str, response: str, *,
                  module: Optional[str] = None, context: Optional[str] = None) -> dict:
        """Score a prompt/response pair against the ACB modules (no LLM)."""
        args: dict = {"action": "score", "prompt": prompt, "response": response}
        if module:
            args["module"] = module
        if context:
            args["context"] = context
        return self.call_tool("axiom_crl", args)

    # ── ORVL-008 — Constitutional Adversarial Sandbox ────────────
    def cas_defend(self, attacks: Sequence[Any]) -> dict:
        """Run the blue team over an attack corpus; report weak regions."""
        return self.call_tool("axiom_cas", {"action": "defend", "attacks": list(attacks)})

    def cas_report(self) -> dict:
        """Summarise the signed CAS round log."""
        return self.call_tool("axiom_cas", {"action": "report"})

    # ── axiom-fusion-v1 — multimodal intent fusion ───────────────
    def fuse(self, token: dict) -> dict:
        """Fuse an EventToken dict into a signed FusedIntent (axiom-fusion-v1).
        Returns intent_vector / risk_clusters / fusion_confidence / signature."""
        return self.call_tool("axiom_fusion", {"token": token})
