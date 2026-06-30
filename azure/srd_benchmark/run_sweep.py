"""
SRD sweep orchestrator — optionally CREATE the SRD model, then BENCHMARK it, per
model, in one job. Writes results incrementally so a Spot eviction loses at most the
in-flight model.

  # bench only (models already quantized):
  python azure/srd_benchmark/run_sweep.py --models smollm2-135m mistral-7b

  # full loop — create the SRD (.axm) then benchmark it:
  python azure/srd_benchmark/run_sweep.py --create --models mistralai/Mistral-7B-v0.3

Create step  = `axm_cli.py pack --model <m> --srd4 --output <m>.axm --stats-json …`
               (the same packer the Colab T4 pipeline uses — device_map=auto splits
               GPU + system RAM, so 7B fits a 16 GB T4).
Bench step   = `research/quant/bench_sidecar_hallucination.py --model <m>` (emits
               a JSON line with wikitext2_ppl). Point --bench elsewhere to sweep a
               different metric.
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


def _safe(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


def _last_json(stdout: str) -> dict | None:
    for b in reversed(re.findall(r"\{[^{}]*\}", stdout or "", re.DOTALL)):
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            continue
    return None


def _create_srd(model: str, out_dir: Path) -> dict:
    """Create the SRD .axm for `model`. Returns pack stats (bpw, fingerprint, size)."""
    axm = out_dir / f"{_safe(model)}.axm"
    stats_path = out_dir / f"{_safe(model)}_pack.json"
    cmd = [sys.executable, "axm_cli.py", "pack", "--model", model, "--srd4",
           "--output", str(axm), "--stats-json", str(stats_path)]
    print(f"\n--- CREATE {model} :: {' '.join(cmd)} ---", flush=True)
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    stats = {}
    if stats_path.exists():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"returncode": proc.returncode,
            "axm": str(axm) if axm.exists() else None,
            "bpw": (stats.get("quant", {}) or {}).get("bpw"),
            "fingerprint": stats.get("fingerprint"),
            "axm_gb": round(axm.stat().st_size / 1024**3, 2) if axm.exists() else None,
            "log_tail": ((proc.stdout or "") + (proc.stderr or ""))[-1500:]}


def _bench_one(bench: str, model: str, extra: list[str]) -> dict:
    cmd = [sys.executable, str(REPO / bench), "--model", model, *extra]
    print(f"\n--- BENCH {model} :: {' '.join(cmd)} ---", flush=True)
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    return {"returncode": proc.returncode, "result": _last_json(proc.stdout),
            "log_tail": ((proc.stdout or "") + (proc.stderr or ""))[-2000:]}


def main() -> int:
    ap = argparse.ArgumentParser(description="SRD create + benchmark sweep")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--create", action="store_true",
                    help="create the SRD (.axm) per model BEFORE benchmarking")
    ap.add_argument("--bench", default=DEFAULT_BENCH, help="repo-relative bench script")
    ap.add_argument("--out", default=str(Path(__file__).parent / "outputs"))
    ap.add_argument("--bench-args", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "srd_sweep_results.json"

    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else []
    done = {r["model"] for r in results}

    for model in args.models:
        if model in done:
            print(f"skip {model} (already in results)")
            continue
        row = {"model": model}
        if args.create:
            row["create"] = _create_srd(model, out_dir)
        row["bench"] = _bench_one(args.bench, model, args.bench_args)
        results.append(row)
        results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")  # incremental

    # Markdown summary
    lines = ["# SRD sweep results\n",
             "| Model | bpw | wikitext2_ppl | created | bench |", "|---|---|---|---|---|"]
    for r in results:
        cr = r.get("create") or {}
        bn = r.get("bench") or {}
        res = bn.get("result") or {}
        bpw = cr.get("bpw") or res.get("bpw") or res.get("bits_per_weight", "—")
        ppl = res.get("wikitext2_ppl", "—")
        created = ("ok" if cr.get("returncode") == 0 else
                   (f"FAIL({cr.get('returncode')})" if "create" in r else "—"))
        bench = "ok" if bn.get("returncode") == 0 else f"FAIL({bn.get('returncode')})"
        lines.append(f"| {r['model']} | {bpw} | {ppl} | {created} | {bench} |")
    (out_dir / "srd_sweep_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {results_path} and srd_sweep_results.md ({len(results)} models)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
