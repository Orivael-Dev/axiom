"""
AX OS AUI — Demo 2: work on a branch, with signed event logging.
================================================================
"Work on the AX OS branch." Assembles a workspace for the goal (intent
gate + local recall, Demo 1), gathers the git **branch context**, and
logs every step to Axiom's **signed audit ledger** — a tamper-evident
trail of what the workspace did.

    python -m aui.demo2 "work on the launch demo branch" --repo . --seed

Like Demo 1, point at an Axiom checkout with --axiom-repo for development.
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

from bridge import AxiomBridge
from workspace.assembler import open_workspace
from workspace.branch import gather_branch_context
from aui.render import render, render_branch

ACTOR = "ax-os.aui"

DEMO_MEMORIES = [
    {"text": "work on the launch demo branch: adaptive workspace, recall, signed events",
     "domain": "general", "resolution": "in_progress",
     "constraints": ["local_first", "signed_audit"]},
]


def _make_bridge(axiom_repo: Optional[str], memory_store: str,
                 audit_ledger: str) -> AxiomBridge:
    env = {"AXIOM_MEMORY_STORE": memory_store, "AXIOM_AUDIT_LEDGER": audit_ledger}
    env.setdefault("AXIOM_MASTER_KEY", os.environ.get("AXIOM_MASTER_KEY", "ax-os-demo-key"))
    if axiom_repo:
        return AxiomBridge(command=["python", "axiom_mcp_server.py"],
                           cwd=axiom_repo, env=env)
    return AxiomBridge(env=env)


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="aui.demo2",
                                description="AX OS Demo 2 — branch workspace + signed event logging.")
    p.add_argument("goal", help="what you want to work on")
    p.add_argument("--repo", default=".", help="repo to read branch context from")
    p.add_argument("--domain", default=None)
    p.add_argument("--seed", action="store_true",
                   help="remember a demo memory first so recall can hit")
    p.add_argument("--axiom-repo", default=os.environ.get("AXIOM_REPO"))
    p.add_argument("--memory-store", default=os.environ.get("AXIOM_MEMORY_STORE",
                                                            "ax_os_memory.jsonl"))
    p.add_argument("--audit-ledger", default=os.environ.get("AXIOM_AUDIT_LEDGER",
                                                            "ax_os_audit.jsonl"))
    args = p.parse_args(argv)

    with _make_bridge(args.axiom_repo, args.memory_store, args.audit_ledger) as ax:
        if args.seed:
            for m in DEMO_MEMORIES:
                ax.remember(m["text"], domain=m["domain"],
                            resolution=m["resolution"], constraints=m["constraints"])

        ax.log_event("workspace_requested", actor=ACTOR, subject=args.goal)
        ws = open_workspace(ax, args.goal, domain=args.domain)

        if not ws.allowed:
            ax.log_event("workspace_refused", actor=ACTOR, subject=args.goal,
                         outcome=ws.refusal or "blocked",
                         attributes={"intent_class": ws.intent_class})
            print(render(ws))
            return 2

        ax.log_event("workspace_opened", actor=ACTOR, subject=args.goal,
                     outcome="allowed", attributes={"recall_hit": ws.has_context})

        branch = gather_branch_context(args.repo)
        if branch.available:
            ax.log_event("branch_loaded", actor=ACTOR, subject=branch.branch,
                         outcome="loaded",
                         attributes={"docs": branch.docs_count,
                                     "tests": branch.tests_count,
                                     "readme": branch.has_readme})

        trail = ax.audit_list(limit=10)
        print(render_branch(ws, branch, trail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
