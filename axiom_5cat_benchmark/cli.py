"""CLI for the 5-category benchmark.

Subcommands:
  run             — execute trials and write results.json
  verify          — re-check signatures on a previously-written results.json
  report          — render results.json as markdown or html
  list-categories — show registered category ids
  list-adapters   — show supported adapter providers
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from axiom_5cat_benchmark import __version__
from axiom_5cat_benchmark.adapters import build_adapter
from axiom_5cat_benchmark.categories import available as available_categories
from axiom_5cat_benchmark.runner import run_benchmark
from axiom_5cat_benchmark.signing import verify_attached, verify_result


_KNOWN_PROVIDERS = ("stub", "anthropic", "openai", "local")


def _parse_categories(arg: str) -> list[int]:
    out: list[int] = []
    for tok in arg.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError as e:
            raise argparse.ArgumentTypeError(
                f"--categories: expected comma-separated ints, got {tok!r}"
            ) from e
    if not out:
        raise argparse.ArgumentTypeError("--categories cannot be empty")
    return out


def _parse_models(arg: str) -> list[str]:
    specs = [s.strip() for s in arg.split(",") if s.strip()]
    if not specs:
        raise argparse.ArgumentTypeError("--models cannot be empty")
    return specs


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m axiom_5cat_benchmark",
        description=(
            "AXIOM 5-category AI benchmark "
            f"(v{__version__}). Run trials across Epistemic Humility, "
            "Efficiency, Adaptation, Multi-Agent, and Self-Evolution."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # ── run ────────────────────────────────────────────────────────
    rp = sub.add_parser("run", help="execute trials, write results.json")
    rp.add_argument("--models", type=_parse_models, required=True,
                    help="comma-separated provider:model_id specs "
                         "(e.g. 'anthropic:claude-opus-4-7,openai:gpt-4o')")
    rp.add_argument("--categories", type=_parse_categories,
                    default=[1, 2, 3, 4, 5],
                    help="comma-separated category ids (default: 1,2,3,4,5)")
    rp.add_argument("--trials", type=int, default=5,
                    help="trials per category per adapter (default: 5)")
    rp.add_argument("--temperature", type=float, default=0.0)
    rp.add_argument("--seed", type=int, default=1729)
    rp.add_argument("--output", type=Path, default=Path("results.json"))
    rp.add_argument("--stub", action="store_true",
                    help="force every --models spec to use the stub "
                         "adapter (CI-safe; no API spend)")
    rp.add_argument("--allow-spend", action="store_true",
                    help="required if any non-stub adapter is used with "
                         "--trials > 1 (defense against accidental API spend)")
    rp.add_argument("--no-sign", action="store_true",
                    help="dev only; meta.signature := 'UNSIGNED'")
    rp.add_argument("--verbose", "-v", action="store_true")

    # ── verify ─────────────────────────────────────────────────────
    vp = sub.add_parser("verify",
                        help="re-check HMAC signatures on a results.json")
    vp.add_argument("--input", type=Path, required=True)

    # ── report ─────────────────────────────────────────────────────
    pp = sub.add_parser("report", help="render results.json as md/html")
    pp.add_argument("--input", type=Path, required=True)
    pp.add_argument("--format", choices=("md", "html"), default="md")

    # ── list ───────────────────────────────────────────────────────
    sub.add_parser("list-categories",
                   help="show registered category ids")
    sub.add_parser("list-adapters",
                   help="show supported adapter providers")

    return p


def _check_spend_guard(specs: list[str], trials: int, allow: bool) -> None:
    has_real = any(
        not s.startswith("stub:") for s in specs
    )
    if has_real and trials > 1 and not allow:
        print(
            "ERROR: refusing to run multi-trial benchmark with a non-stub "
            "adapter without --allow-spend.\n"
            "  Re-run with --stub for CI, or pass --allow-spend if you "
            "really want to bill the provider.",
            file=sys.stderr,
        )
        sys.exit(2)


def cmd_run(args: argparse.Namespace) -> int:
    _check_spend_guard(args.models, args.trials, args.allow_spend)
    # Build adapters.  --stub forces every spec into a stub.
    adapters = []
    for spec in args.models:
        if args.stub and not spec.startswith("stub:"):
            spec = f"stub:{spec}"
        adapters.append(build_adapter(spec))
        if args.verbose:
            print(f"  adapter ready: {spec}", file=sys.stderr)

    results = run_benchmark(
        adapters=adapters,
        category_ids=args.categories,
        n_trials=args.trials,
        seed=args.seed,
        temperature=args.temperature,
    )

    payload = results.to_dict()
    if args.no_sign:
        payload["meta"]["signature"] = "UNSIGNED"
        print("WARNING: --no-sign in use; meta.signature := 'UNSIGNED'.",
              file=sys.stderr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    print(
        f"  wrote {args.output} — total={results.total_tests} "
        f"axiom_wins={results.axiom_wins} "
        f"improvement_pct={results.improvement_pct}% "
        f"criteria_met={results.criteria_met}"
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    with args.input.open(encoding="utf-8") as f:
        data = json.load(f)
    meta_ok = verify_attached(data.get("meta", {}))
    trials = data.get("tests", [])
    bad_trials = []
    for t in trials:
        d = dict(t)
        sig = d.pop("trial_signature", "")
        if not verify_result(d, sig):
            bad_trials.append(t.get("id", "?"))
    if not meta_ok:
        print("FAIL: meta block signature did not verify", file=sys.stderr)
        return 1
    if bad_trials:
        print(
            f"FAIL: {len(bad_trials)} trial signature(s) bad: "
            f"{bad_trials[:5]}{'…' if len(bad_trials) > 5 else ''}",
            file=sys.stderr,
        )
        return 1
    print(
        f"OK: meta + {len(trials)} trial signatures verify under "
        f"axiom-5cat-bench-v1"
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    with args.input.open(encoding="utf-8") as f:
        data = json.load(f)
    if args.format == "md":
        print(_render_md(data))
    else:
        print(_render_html(data))
    return 0


def cmd_list_categories(_args: argparse.Namespace) -> int:
    # Force lazy load of all five subpackages so they appear here even
    # if the user hasn't run any trials yet.
    for cid in (1, 2, 3, 4, 5):
        from axiom_5cat_benchmark.categories import _ensure_loaded
        _ensure_loaded(cid)
    for cid in available_categories():
        from axiom_5cat_benchmark.categories import get
        try:
            cat = get(cid)
            print(f"  {cid}  {cat.name}  max/trial={cat.max_score_per_trial}")
        except KeyError:
            print(f"  {cid}  <unregistered>")
    return 0


def cmd_list_adapters(_args: argparse.Namespace) -> int:
    for p in _KNOWN_PROVIDERS:
        print(f"  {p}")
    return 0


def _render_md(data: dict) -> str:
    lines = [
        "# AXIOM 5-category benchmark — results",
        "",
        f"- schema: `{data['meta'].get('schema')}`",
        f"- axiom_commit: `{data['meta'].get('axiom_commit')}`",
        f"- master_key_fingerprint: `{data['meta'].get('master_key_fingerprint')}`",
        f"- raw_avg: **{data['raw_avg']}**, axiom_avg: **{data['axiom_avg']}**",
        f"- improvement_pct: **{data['improvement_pct']}%**, "
        f"axiom_wins: **{data['axiom_wins']}**/**{data['total_tests']}**, "
        f"criteria_met: **{data['criteria_met']}**",
        "",
        "## per-category",
        "",
    ]
    for cid, rep in sorted(data.get("per_category", {}).items()):
        lines.append(
            f"- **{cid}**: avg={rep['avg']}, gate={rep['gate']}, "
            f"trials={rep['n_trials']}"
        )
    return "\n".join(lines)


def _render_html(data: dict) -> str:
    # Tiny render; full marketing version can land later.
    import html as _h
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>AXIOM 5cat — {_h.escape(data['meta'].get('schema',''))}</title>"
        f"<pre>{_h.escape(json.dumps(data, indent=2, sort_keys=True))}</pre>"
    )


_DISPATCH = {
    "run":              cmd_run,
    "verify":           cmd_verify,
    "report":           cmd_report,
    "list-categories":  cmd_list_categories,
    "list-adapters":    cmd_list_adapters,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    fn = _DISPATCH[args.cmd]
    return fn(args)
