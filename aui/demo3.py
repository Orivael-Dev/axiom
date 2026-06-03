"""
AX OS AUI — Demo 3 + 4: signed agent install, review, approve, revoke.
=====================================================================
Walks the AX Store lifecycle for a signed agent manifest:

  verify → sandbox-install → human review → approve → (agent may act)
  → revoke → (agent blocked; cannot be re-approved)

Every step is written to Axiom's signed audit ledger.

    python -m aui.demo3 --manifest agent.json --approve --then-revoke

``--manifest`` is a publisher-signed SkillPackManifest JSON. Signing is a
publisher/registry concern (Axiom-side); AX OS only verifies + installs.
For a local dev run against an Axiom checkout, ``--dev-sign`` is provided
by the test harness rather than this CLI to keep the product boundary
clean.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Optional

from bridge import AxiomBridge
from marketplace import AgentStore
from aui.render import render_install_review, render_authority


def _make_bridge(axiom_repo, audit_ledger, mkt_ledger):
    env = {"AXIOM_AUDIT_LEDGER": audit_ledger,
           "AXIOM_MARKETPLACE_LEDGER": mkt_ledger}
    env.setdefault("AXIOM_MASTER_KEY", os.environ.get("AXIOM_MASTER_KEY", "ax-os-demo-key"))
    if axiom_repo:
        return AxiomBridge(command=["python", "axiom_mcp_server.py"],
                           cwd=axiom_repo, env=env)
    return AxiomBridge(env=env)


def run(manifest: dict, *, approve: bool, then_revoke: bool,
        axiom_repo: Optional[str], audit_ledger: str, mkt_ledger: str) -> int:
    with _make_bridge(axiom_repo, audit_ledger, mkt_ledger) as ax:
        store = AgentStore(ax)
        review = store.install_for_review(manifest)
        print(render_install_review(review))
        if not review.installed:
            return 2

        pid = review.pair_id
        if approve:
            store.approve(pid, agent=review.agent, actor="human")
            print(render_authority("after approve", ax.mkt_authority(pid)))
            print(f"agent may act: {store.can_act(pid)}")

        if then_revoke:
            store.revoke(pid, agent=review.agent, actor="human")
            print(render_authority("after revoke", ax.mkt_authority(pid)))
            print(f"agent may act: {store.can_act(pid)}")
    return 0


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="aui.demo3",
                                description="AX OS Demo 3/4 — signed agent install + bonded authority.")
    p.add_argument("--manifest", required=True, help="path to a signed manifest JSON")
    p.add_argument("--approve", action="store_true", help="approve after review")
    p.add_argument("--then-revoke", action="store_true", help="revoke after approving")
    p.add_argument("--axiom-repo", default=os.environ.get("AXIOM_REPO"))
    p.add_argument("--audit-ledger", default=os.environ.get("AXIOM_AUDIT_LEDGER",
                                                            "ax_os_audit.jsonl"))
    p.add_argument("--mkt-ledger", default=os.environ.get("AXIOM_MARKETPLACE_LEDGER",
                                                          "ax_os_marketplace.jsonl"))
    args = p.parse_args(argv)
    with open(args.manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return run(manifest, approve=args.approve, then_revoke=args.then_revoke,
               axiom_repo=args.axiom_repo, audit_ledger=args.audit_ledger,
               mkt_ledger=args.mkt_ledger)


if __name__ == "__main__":
    raise SystemExit(main())
