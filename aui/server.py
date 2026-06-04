"""
AX OS local service — the on-device API the AUI front-end talks to.
==================================================================
A thin FastAPI app that holds one long-lived ``AxiomBridge`` and exposes
the workspace + marketplace surfaces as JSON over localhost. Local-first
by default: the front-end (Streamlit / desktop) calls these endpoints; the
bridge speaks MCP to the Axiom trust layer underneath.

    uvicorn-style:  python -m aui.server         # binds 127.0.0.1:8800

``create_app(bridge)`` is dependency-injected so it can be tested with a
fake bridge and no network.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from workspace.assembler import open_workspace
from marketplace import AgentStore, AgentRunner
from aui.plan import build_plan
from aui.planner_claude import get_planner
from aui.panels import fill_plan


class GoalReq(BaseModel):
    goal: str
    domain: Optional[str] = None


class ManifestReq(BaseModel):
    manifest: dict


class PairReq(BaseModel):
    pair_id: str
    actor: str = "human"


class RunReq(BaseModel):
    pair_id: str
    action: str
    agent: str = ""


def create_app(bridge: Any, *, repo: Optional[str] = None):
    """Build the FastAPI app over an already-started bridge.

    ``repo`` is the workspace the file/branch/tests/docs panels read from
    (defaults to AX_OS_REPO, then the current directory)."""
    repo = repo or os.environ.get("AX_OS_REPO", ".")
    app = FastAPI(title="AX OS", version="0.1.0")
    store = AgentStore(bridge)
    runner = AgentRunner(bridge)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "tools": bridge.list_tools()}

    @app.post("/assemble")
    def assemble(req: GoalReq) -> dict:
        ws = open_workspace(bridge, req.goal, domain=req.domain)
        bridge.log_event("workspace_opened" if ws.allowed else "workspace_refused",
                         actor="ax-os.aui", subject=req.goal,
                         outcome="allowed" if ws.allowed else (ws.refusal or "blocked"))
        plan = build_plan(ws, domain=req.domain, suggest=get_planner())
        fill_plan(plan, repo=repo, bridge=bridge)
        return plan.to_dict()

    @app.post("/marketplace/install")
    def install(req: ManifestReq) -> dict:
        return store.install_for_review(req.manifest).__dict__

    @app.post("/marketplace/approve")
    def approve(req: PairReq) -> dict:
        return store.approve(req.pair_id, actor=req.actor)

    @app.post("/marketplace/revoke")
    def revoke(req: PairReq) -> dict:
        return store.revoke(req.pair_id, actor=req.actor)

    @app.post("/marketplace/run")
    def run(req: RunReq) -> dict:
        return runner.run_action(req.pair_id, req.action, agent=req.agent).to_dict()

    @app.get("/marketplace/agents")
    def agents() -> dict:
        # Reconstruct installed agents (pair_id + name) from the signed audit
        # ledger, then ask the marketplace for each one's live authority state.
        seen: dict = {}
        for e in bridge.audit_list(limit=200).get("events", []):
            if not str(e.get("event_type", "")).startswith("agent_"):
                continue
            pid = (e.get("attributes") or {}).get("pair_id")
            if pid and pid not in seen:
                seen[pid] = e.get("subject", "")
        out = []
        for pid, name in seen.items():
            a = bridge.mkt_authority(pid)
            out.append({"agent": name, "pair_id": pid,
                        "authorized": bool(a.get("authorized")), "state": a.get("state", "")})
        return {"agents": out}

    @app.get("/audit")
    def audit(limit: int = 20) -> dict:
        return bridge.audit_list(limit=limit)

    return app


def _bridge_from_env():
    from bridge import AxiomBridge
    env = {
        "AXIOM_MEMORY_STORE": os.environ.get("AXIOM_MEMORY_STORE", "ax_os_memory.jsonl"),
        "AXIOM_AUDIT_LEDGER": os.environ.get("AXIOM_AUDIT_LEDGER", "ax_os_audit.jsonl"),
        "AXIOM_MARKETPLACE_LEDGER": os.environ.get("AXIOM_MARKETPLACE_LEDGER",
                                                   "ax_os_marketplace.jsonl"),
    }
    repo = os.environ.get("AXIOM_REPO")
    if repo:
        return AxiomBridge(command=["python", "axiom_mcp_server.py"], cwd=repo, env=env)
    return AxiomBridge(env=env)


def main() -> None:
    import uvicorn
    bridge = _bridge_from_env()
    bridge.start()
    try:
        uvicorn.run(create_app(bridge),
                    host=os.environ.get("AX_OS_HOST", "127.0.0.1"),
                    port=int(os.environ.get("AX_OS_PORT", "8800")))
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
