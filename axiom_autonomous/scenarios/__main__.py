"""CLI: `python3 -m axiom_autonomous.scenarios ...`

Subcommands:
  run    — execute scenarios, write signed JSON report
  list   — show registered scenarios
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .library import filter_scenarios, load_library
from .runner import run_scenarios, write_report


def _parse_ids(arg: str) -> list:
    return [s.strip() for s in arg.split(",") if s.strip()]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m axiom_autonomous.scenarios",
        description=(
            "Run the AXIOM autonomous agent against the curated "
            "real-world scenarios library."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("run", help="execute scenarios; write JSON report")
    rp.add_argument("--only", type=_parse_ids, default=None,
                    help="comma-separated scenario ids "
                         "(default: all in library)")
    rp.add_argument("--sandbox",
                    choices=("docker_required", "docker", "local"),
                    default="docker_required",
                    help="sandbox preference (default: docker_required "
                         "— refuses to fall back to LocalSandbox; pass "
                         "'local' explicitly for fast smoke runs without "
                         "real isolation)")
    rp.add_argument("--output", type=Path, default=Path("scenarios_run.json"),
                    help="path to write the signed JSON report")
    rp.add_argument("--workdir-root", type=Path, default=None,
                    help="parent dir for per-scenario workdirs "
                         "(default: fresh mkdtemp)")
    rp.add_argument("--verbose", "-v", action="store_true")

    lp = sub.add_parser("list", help="show registered scenarios")
    lp.add_argument("--format", choices=("text", "json"), default="text")

    return p


def cmd_run(args: argparse.Namespace) -> int:
    try:
        library = load_library()
        scenarios = filter_scenarios(library, args.only)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.verbose:
        print(
            f"  running {len(scenarios)} scenario(s) "
            f"in sandbox={args.sandbox}",
            file=sys.stderr,
        )

    report = run_scenarios(
        scenarios, sandbox_prefer=args.sandbox,
        workdir_root=args.workdir_root, verbose=args.verbose,
    )
    out_path = write_report(report, args.output)

    summary = report.summary()
    print(
        f"  wrote {out_path} — total={summary['total']} "
        f"agent_succeeded={summary['agent_succeeded']} "
        f"criteria_passed={summary['criteria_passed']} "
        f"wall={summary['total_wall_ms']}ms"
    )
    # Non-zero exit when any scenario failed criteria — lets CI
    # treat the runner as a gate.
    return 0 if summary["criteria_passed"] == summary["total"] else 1


def cmd_list(args: argparse.Namespace) -> int:
    try:
        library = load_library()
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps([s.to_dict() for s in library], indent=2))
    else:
        for s in library:
            tag = f" [{','.join(s.tags)}]" if s.tags else ""
            print(f"  {s.id}{tag}  {s.title}")
            print(f"      seed={s.seed} budget_steps={s.budget_steps} "
                  f"wall={s.wall_seconds}s")
    return 0


_DISPATCH = {"run": cmd_run, "list": cmd_list}


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    return _DISPATCH[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
