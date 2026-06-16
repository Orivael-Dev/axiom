"""axiom-bench — self-run AI governance and breaking-point benchmark.

Your API key goes directly to your endpoint. Orivael never sees it.

Usage:
    # Run against a local Ollama model
    axiom-bench run --endpoint http://localhost:11434/v1 \\
                    --model llama3.3

    # Run against a local vLLM server with a key
    axiom-bench run --endpoint http://localhost:8000/v1 \\
                    --model Qwen/Qwen2.5-7B-Instruct \\
                    --key your-api-key

    # Run against NVIDIA NIM
    axiom-bench run --endpoint https://integrate.api.nvidia.com/v1 \\
                    --model meta/llama-3.1-8b-instruct \\
                    --key nvapi-...

    # Generate HTML report from a results file
    axiom-bench report --input bench_results/results.json

    # Verify signatures on a results file
    axiom-bench verify --input bench_results/results.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from axiom_bench import __version__


# ── helpers ──────────────────────────────────────────────────────────

_CATEGORY_LABELS = {
    1: "Epistemic Humility",
    2: "Efficiency",
    3: "Adaptation",
    4: "Multi-Agent Coordination",
    5: "Self-Evolution",
}

_CATEGORY_STATUS = {
    1: "live",
    2: "coming_soon",
    3: "coming_soon",
    4: "coming_soon",
    5: "coming_soon",
}


def _parse_categories(arg: str) -> list[int]:
    out = []
    for tok in arg.split(","):
        tok = tok.strip()
        if tok:
            try:
                out.append(int(tok))
            except ValueError as exc:
                raise argparse.ArgumentTypeError(
                    f"--categories: expected comma-separated ints, got {tok!r}"
                ) from exc
    return out or list(range(1, 6))


# ── run ──────────────────────────────────────────────────────────────

def cmd_run(args: argparse.Namespace) -> int:
    from axiom_5cat_benchmark.adapters import build_adapter
    from axiom_5cat_benchmark.runner import run_benchmark
    from axiom_bench.report import write_report

    # Set key via env so it doesn't land in process args / shell history
    if args.key:
        os.environ["AXIOM_API_KEY"] = args.key

    spec = f"local:{args.model}@{args.endpoint}"
    print(f"\nAXIOM Benchmark Lab v{__version__}")
    print(f"  endpoint : {args.endpoint}")
    print(f"  model    : {args.model}")
    print(f"  key      : {'set' if args.key else 'none (local endpoint)'}")

    # Only run categories that are actually implemented
    live_cats = [c for c in args.categories if _CATEGORY_STATUS.get(c) == "live"]
    soon_cats = [c for c in args.categories if _CATEGORY_STATUS.get(c) == "coming_soon"]

    if not live_cats:
        print("\nERROR: none of the requested categories are available yet.", file=sys.stderr)
        print(f"  available now : {[c for c, s in _CATEGORY_STATUS.items() if s == 'live']}")
        print(f"  coming soon   : {[c for c, s in _CATEGORY_STATUS.items() if s == 'coming_soon']}")
        return 2

    if soon_cats:
        print(f"\n  note: {[_CATEGORY_LABELS[c] for c in soon_cats]} not yet available — skipping")

    print(f"\n  running cats  : {live_cats}  ({args.trials} trials each)")
    print(f"  output dir    : {args.output}\n")

    try:
        adapter = build_adapter(spec)
    except Exception as exc:
        print(f"ERROR: could not connect to endpoint: {exc}", file=sys.stderr)
        return 1

    t0 = time.monotonic()
    try:
        if args.allow_spend or True:  # local endpoints = no spend risk
            results = run_benchmark(
                adapters=[adapter],
                category_ids=live_cats,
                n_trials=args.trials,
                seed=args.seed,
                temperature=args.temperature,
            )
    except Exception as exc:
        print(f"ERROR during benchmark: {exc}", file=sys.stderr)
        return 1

    wall = time.monotonic() - t0

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    report_path  = out_dir / "report.html"

    payload = results.to_dict()
    results_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    write_report(payload, report_path, endpoint=args.endpoint, model=args.model)

    # Terminal summary
    pct = results.improvement_pct
    verdict = ("PASS" if results.criteria_met else "REVIEW")
    print("─" * 56)
    print(f"  improvement_pct : {pct:+.1f}%")
    print(f"  axiom_wins      : {results.axiom_wins}/{results.total_tests}")
    print(f"  criteria_met    : {results.criteria_met}")
    print(f"  verdict         : {verdict}")
    for cid, rep in sorted(results.per_category.items()):
        label = _CATEGORY_LABELS.get(int(cid), cid)
        print(f"  cat {cid} {label:<28} {rep.gate}")
    print(f"  wall-clock      : {wall:.1f}s")
    print("─" * 56)
    print(f"\n  results : {results_path}")
    print(f"  report  : {report_path}")
    print()
    return 0


# ── verify ───────────────────────────────────────────────────────────

def cmd_verify(args: argparse.Namespace) -> int:
    from axiom_5cat_benchmark.signing import verify_attached, verify_result

    with args.input.open(encoding="utf-8") as f:
        data = json.load(f)
    meta_ok = verify_attached(data.get("meta", {}))
    trials = data.get("tests", [])
    bad = []
    for t in trials:
        d = dict(t)
        sig = d.pop("trial_signature", "")
        if not verify_result(d, sig):
            bad.append(t.get("id", "?"))
    if not meta_ok:
        print("FAIL: meta signature invalid", file=sys.stderr)
        return 1
    if bad:
        print(f"FAIL: {len(bad)} trial signature(s) invalid: {bad[:5]}", file=sys.stderr)
        return 1
    print(f"OK: meta + {len(trials)} trial signatures verified")
    return 0


# ── report ───────────────────────────────────────────────────────────

def cmd_report(args: argparse.Namespace) -> int:
    from axiom_bench.report import write_report

    with args.input.open(encoding="utf-8") as f:
        data = json.load(f)
    out = args.input.with_suffix(".html") if args.output is None else args.output
    write_report(data, out)
    print(f"report written to {out}")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axiom-bench",
        description=f"AXIOM Benchmark Lab v{__version__} — self-run AI governance evaluation",
    )
    p.add_argument("--version", action="version", version=f"axiom-bench {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    # run
    rp = sub.add_parser("run", help="run the benchmark against a model endpoint")
    rp.add_argument("--endpoint", required=True,
                    help="OpenAI-compatible base URL, e.g. http://localhost:11434/v1")
    rp.add_argument("--model", required=True,
                    help="model id to send in requests, e.g. llama3.3")
    rp.add_argument("--key", default=None,
                    help="API key (optional; defaults to no-auth for local endpoints). "
                         "Sent directly to your endpoint — never to Orivael.")
    rp.add_argument("--categories", type=_parse_categories,
                    default=list(range(1, 6)),
                    help="comma-separated category ids (default: 1,2,3,4,5). "
                         "Currently only Cat 1 is implemented; others are queued.")
    rp.add_argument("--trials", type=int, default=30,
                    help="trials per category (default 30; min 10 for stable metrics)")
    rp.add_argument("--temperature", type=float, default=0.0)
    rp.add_argument("--seed", type=int, default=1729)
    rp.add_argument("--output", type=Path, default=Path("bench_results"),
                    help="output directory (results.json + report.html)")
    rp.add_argument("--allow-spend", action="store_true",
                    help="suppress the spend-guard warning for paid endpoints")

    # verify
    vp = sub.add_parser("verify", help="verify HMAC signatures on a results.json")
    vp.add_argument("--input", type=Path, required=True)

    # report
    pp = sub.add_parser("report", help="regenerate HTML report from a results.json")
    pp.add_argument("--input", type=Path, required=True)
    pp.add_argument("--output", type=Path, default=None)

    return p


_DISPATCH = {"run": cmd_run, "verify": cmd_verify, "report": cmd_report}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return _DISPATCH[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
