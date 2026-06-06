"""
AX OS AUI — Demo 1 entry point.
===============================
"State a goal, get the right workspace." Reads a goal, assembles a
workspace through Axiom (intent-gate safety check + local recall), and
renders an adaptive layout.

    # against an installed Axiom package:
    python -m aui.demo "help me work on the launch demo" --seed

    # against an Axiom checkout (dev):
    python -m aui.demo "help me work on the launch demo" --seed \
        --axiom-repo /path/to/axiom

``--seed`` first remembers a few demo packets so recall has something to
return; drop it once real memory exists.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from bridge import AxiomBridge
from workspace.assembler import open_workspace
from aui.render import render

# A couple of demo memories so --seed makes recall demonstrably hit.
DEMO_MEMORIES = [
    {"text": "help me work on the launch demo: adaptive workspace and recall",
     "domain": "general", "resolution": "approved_for_demo",
     "constraints": ["local_first", "human_review_before_approve"]},
    {"text": "open my mixing session: session notes, reference tracks, plugin chain",
     "domain": "general", "resolution": "loaded",
     "constraints": ["local_first"]},
]


def _make_bridge(axiom_repo: Optional[str], memory_store: str) -> AxiomBridge:
    env = {"AXIOM_MEMORY_STORE": memory_store}
    env.setdefault("AXIOM_MASTER_KEY", os.environ.get("AXIOM_MASTER_KEY", "ax-os-demo-key"))
    if axiom_repo:
        return AxiomBridge(command=["python", "axiom_mcp_server.py"],
                           cwd=axiom_repo, env=env)
    return AxiomBridge(env=env)  # installed package: python -m axiom_mcp_server


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="aui.demo",
                                description="AX OS Demo 1 — state a goal, get a workspace.")
    p.add_argument("goal", help="what you want to work on")
    p.add_argument("--domain", default=None, help="optional domain filter for recall")
    p.add_argument("--seed", action="store_true",
                   help="remember a few demo memories first so recall can hit")
    p.add_argument("--axiom-repo", default=os.environ.get("AXIOM_REPO"),
                   help="path to an Axiom checkout (defaults to the installed package)")
    p.add_argument("--memory-store", default=os.environ.get("AXIOM_MEMORY_STORE",
                                                            "ax_os_memory.jsonl"),
                   help="path to the local-first memory store")
    args = p.parse_args(argv)

    with _make_bridge(args.axiom_repo, args.memory_store) as ax:
        if args.seed:
            for m in DEMO_MEMORIES:
                ax.remember(m["text"], domain=m["domain"],
                            resolution=m["resolution"], constraints=m["constraints"])
        ws = open_workspace(ax, args.goal, domain=args.domain)
        print(render(ws))
    return 0 if ws.allowed else 2


if __name__ == "__main__":
    raise SystemExit(main())
