"""Tiny example CLI — the autonomous-agent scenario will extend it.

Today it accepts --verbose and prints a greeting. The scenario asks
the agent to add a --json flag that emits {"greeting": "..."} on
stdout instead of plain text, preserving the existing text behaviour
when --json is absent.
"""
import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="greet")
    p.add_argument("--name", default="world")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.verbose:
        print(f"greeting {args.name}", file=sys.stderr)
    print(f"hello, {args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
