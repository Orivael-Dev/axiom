"""
SRD benchmark sweep orchestrator — runs the repo's quant benchmarks across a model
matrix on one GPU node and collects a results table.

Thin subprocess wrapper around `research/quant/bench_sidecar_hallucination.py` (which
emits a JSON line with `wikitext2_ppl`). Writes results incrementally so a Spot
eviction loses at most the in-flight model.

    python azure/srd_benchmark/run_sweep.py \
        --models smollm2-135m tinyllama-1.1b mistral-7b \
        --out azure/srd_benchmark/outputs

Point `--bench` at a different research/quant script to sweep a different metric; the
orchestrator just captures the JSON the script prints.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BENCH = "research/quant/bench_sidecar_hallucination.py"
DEFAULT_MODELS = ["smollm2-135m", "tinyllama-1.1b", "mistral-7b"]


def _run_one(bench: str, model: str, extra: list[str]) -> dict:
    """Run one benchmark; return the last JSON object the script printed."""
    cmd = [sys.executable, str(REPO / bench), "--model", model, *extra]
    print(f"\n=== {model} :: {' '.join(cmd)} ===", flush=True)
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # Grab the last {...} block the script emitted.
    blocks = re.findall(r"\{[^{}]*\}", proc.stdout or "", re.DOTALL)
    parsed = None
    for b in reversed(blocks):
        try:
            parsed = json.loads(b)
            break
        except json.JSONDecodeError:
            continue
    return {"model": model, "returncode": proc.returncode,
            "result": parsed, "log_tail": out[-2000:]}


def main() -> int:
    ap = argparse.ArgumentParser(description="SRD benchmark sweep")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--bench", default=DEFAULT_BENCH, help="repo-relative bench script")
    ap.add_argument("--out", default=str(Path(__file__).parent / "outputs"))
    ap.add_argument("--bench-args", nargs=argparse.REMAINDER, default=[],
                    help="extra args forwarded to the bench script (after --bench-args)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "srd_sweep_results.json"

    results = []
    if results_path.exists():                      # resume after eviction
        results = json.loads(results_path.read_text(encoding="utf-8"))
    done = {r["model"] for r in results}

    for model in args.models:
        if model in done:
            print(f"skip {model} (already in results)")
            continue
        results.append(_run_one(args.bench, model, args.bench_args))
        results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")  # incremental

    # Markdown summary
    lines = ["# SRD sweep results\n",
             "| Model | wikitext2_ppl | bpw | status |", "|---|---|---|---|"]
    for r in results:
        res = r.get("result") or {}
        ppl = res.get("wikitext2_ppl", "—")
        bpw = res.get("bpw", res.get("bits_per_weight", "—"))
        status = "ok" if r["returncode"] == 0 else f"FAIL({r['returncode']})"
        lines.append(f"| {r['model']} | {ppl} | {bpw} | {status} |")
    (out_dir / "srd_sweep_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {results_path} and srd_sweep_results.md ({len(results)} models)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
