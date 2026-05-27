"""Top-level shim + CLI for the AXIOM autonomous coding agent.

The real implementation lives in the `axiom_autonomous/` sub-package;
this module is the public import surface (`from axiom_autonomous_agent
import AutonomousAgent`) and the `python3 -m axiom_autonomous_agent`
CLI entrypoint.

CLI:
    python3 -m axiom_autonomous_agent run \\
        --task "write primes.py + tests" \\
        --workdir /tmp/auto-run-1 \\
        --budget-steps 20

    python3 -m axiom_autonomous_agent verify \\
        --ledger ~/.axiom/exoskeleton-ledger.jsonl --run-id auto_abc123
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

from axiom_autonomous import (
    AutonomousAgent, AutonomousRunResult,
)
from axiom_autonomous.ledger import verify_chain


def _cmd_run(args: argparse.Namespace) -> int:
    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY must be set (32 bytes hex).",
              file=sys.stderr)
        return 2

    task = args.task
    if not task and args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8").strip()
    if not task:
        print("error: --task or --task-file is required.", file=sys.stderr)
        return 2

    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    ledger = None
    if not args.no_ledger:
        from axiom_exoskeleton_ledger import LedgerWriter, default_ledger_path
        ledger = LedgerWriter(
            Path(args.ledger) if args.ledger else default_ledger_path()
        )

    backend = None  # default_backend() will run inside AutonomousAgent

    agent = AutonomousAgent(
        backend=backend,
        ledger=ledger,
        sandbox_prefer=args.sandbox,
        record_dev_cycle=not args.no_dev_cycle,
    )
    result = agent.run(
        task=task,
        workdir=workdir,
        budget_steps=args.budget_steps,
        wall_seconds=args.wall_seconds,
    )

    print(_render_result(result), file=sys.stderr)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else (
        2 if result.aborted_reason.startswith(("intent_gate", "sandbox_review"))
          else 1
    )


def _cmd_verify(args: argparse.Namespace) -> int:
    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY must be set (32 bytes hex).",
              file=sys.stderr)
        return 2
    from axiom_exoskeleton_ledger import default_ledger_path, read_ledger
    path = Path(args.ledger) if args.ledger else default_ledger_path()
    entries = read_ledger(path)
    prefix = f"autonomous:{args.run_id}:"
    matching = [e for e in entries if e.use_case.startswith(prefix)]
    if not matching:
        print(f"no ledger entries for run_id {args.run_id!r}", file=sys.stderr)
        return 2
    all_verified = all(e.verified for e in matching)
    print(f"run_id={args.run_id}", file=sys.stderr)
    print(f"  steps in ledger: {len(matching)}", file=sys.stderr)
    print(f"  all verified:    {all_verified}", file=sys.stderr)
    return 0 if all_verified else 1


def _render_result(result: AutonomousRunResult) -> str:
    lines = [
        f"# autonomous run {result.run_id}",
        f"  success={result.success}  steps={result.steps}",
        f"  head_token={result.chain_head_token_id}",
    ]
    if result.aborted_reason:
        lines.append(f"  aborted: {result.aborted_reason}")
    if result.plan.subgoals:
        lines.append(f"  subgoals: {len(result.plan.subgoals)}")
        for sg in result.plan.subgoals:
            marker = "✓" if sg.done else "•"
            lines.append(f"    {marker} {sg.id}: {sg.description[:80]}")
    if result.plan.last_pass or result.plan.last_fail:
        lines.append(
            f"  last test counts: pass={result.plan.last_pass} "
            f"fail={result.plan.last_fail}"
        )
    if result.plan.changed_files:
        lines.append(
            f"  files written: {len(result.plan.changed_files)} "
            f"({', '.join(result.plan.changed_files[:5])}"
            f"{'…' if len(result.plan.changed_files) > 5 else ''})"
        )
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="axiom-autonomous",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="execute one autonomous task")
    p_run.add_argument("--task", "-t",
                       help="inline task description")
    p_run.add_argument("--task-file", "-f",
                       help="read task from a file")
    p_run.add_argument("--workdir", "-w", required=True,
                       help="sandbox workdir (created if missing)")
    p_run.add_argument("--budget-steps", type=int, default=30,
                       help="max plan/execute/verify cycles (default 30)")
    p_run.add_argument("--wall-seconds", type=int, default=900,
                       help="max wall-clock seconds (default 900)")
    p_run.add_argument("--sandbox",
                       choices=("docker", "local", "docker_required"),
                       default="docker",
                       help="sandbox preference (default 'docker' falls back "
                            "to 'local' when docker is unavailable; use "
                            "'docker_required' for production)")
    p_run.add_argument("--ledger",
                       help="JSONL audit-ledger path "
                            "(default: ~/.axiom/exoskeleton-ledger.jsonl)")
    p_run.add_argument("--no-ledger", action="store_true",
                       help="skip ledger append for this run")
    p_run.add_argument("--no-dev-cycle", action="store_true",
                       help="skip DevCycleRecord append at terminal step")
    p_run.add_argument("--json", action="store_true",
                       help="emit the run result as JSON on stdout")
    p_run.set_defaults(func=_cmd_run)

    p_ver = sub.add_parser("verify",
                           help="verify a run's signed chain from the ledger")
    p_ver.add_argument("--run-id", required=True,
                       help="run_id to verify (e.g. auto_abc123def)")
    p_ver.add_argument("--ledger",
                       help="JSONL audit-ledger path "
                            "(default: ~/.axiom/exoskeleton-ledger.jsonl)")
    p_ver.set_defaults(func=_cmd_verify)

    args = ap.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
