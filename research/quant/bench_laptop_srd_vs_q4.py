#!/usr/bin/env python3
"""
bench_laptop_srd_vs_q4.py

Side-by-side comparison of SRD-processed Q4_K_M vs plain Q4_K_M on any
machine (laptop / workstation / RunPod) using the same three benchmarks
run on the Jetson.

Usage
-----
  python3 research/quant/bench_laptop_srd_vs_q4.py \\
      --srd-gguf   /path/to/llama32_srd_q4km.gguf \\
      --q4-gguf    /path/to/llama32_q4km.gguf \\
      --llama-cli  /path/to/llama-cli \\
      [--n-gpu-layers 0]   # 0=CPU only, 99=full GPU offload \\
      [--threads 8]        # CPU threads (0=let llama.cpp pick) \\
      [--output results/laptop_srd_vs_q4.json]

Auto-download reference GGUF (bartowski Q4_K_M, no --q4-gguf needed)
----------------------------------------------------------------------
  python3 ... --download-q4 --q4-gguf /tmp/llama32_q4km.gguf [other args]

Run only one model (compare later or just log one side)
-------------------------------------------------------
  python3 ... --srd-only   # skips plain Q4 run
  python3 ... --q4-only    # skips SRD run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_RESULTS_DIR = Path(__file__).parent / "results"

# Reference Q4_K_M: plain llama.cpp quantize, no SRD — bartowski HF mirror
_REF_REPO = "bartowski/Llama-3.2-1B-Instruct-GGUF"
_REF_FILE = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"

# ── timing-line patterns (same as bench_llamacpp_infer.py) ───────────────────

_PAT_PROMPT = re.compile(
    r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens"
)
_PAT_EVAL = re.compile(
    r"eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*runs.*?([\d.]+)\s*tokens per second",
    re.S,
)
_PAT_RSS = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")


def _parse_timings(text: str) -> dict:
    out: dict = {}
    m = _PAT_PROMPT.search(text)
    if m:
        out["prefill_ms"] = float(m.group(1))
        out["prefill_tokens"] = int(m.group(2))
    m = _PAT_EVAL.search(text)
    if m:
        out["gen_tok_per_s"] = float(m.group(3))
    m = _PAT_RSS.search(text)
    if m:
        out["peak_rss_mb"] = round(int(m.group(1)) / 1024, 1)
    return out


# ── GPU power monitor ─────────────────────────────────────────────────────────

def _nvml_watts() -> Optional[float]:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
        line = r.stdout.strip().split("\n")[0]
        return float(line)
    except Exception:
        return None


def _power_sampler(samples: list, stop: threading.Event, interval: float = 1.0):
    while not stop.is_set():
        w = _nvml_watts()
        if w is not None:
            samples.append(w)
        stop.wait(interval)


# ── core runner ───────────────────────────────────────────────────────────────

def _run(
    llama_cli: Path,
    gguf: Path,
    prompt: str,
    *,
    ctx_size: int,
    n_predict: int,
    n_gpu_layers: int,
    threads: int,
) -> dict:
    """Run llama-cli once and return parsed timing + memory dict."""
    cmd = [
        str(llama_cli),
        "-m", str(gguf),
        "--ctx-size", str(ctx_size),
        "--n-predict", str(n_predict),
        "--n-gpu-layers", str(n_gpu_layers),
        "--prompt", prompt,
    ]
    if threads:
        cmd += ["--threads", str(threads)]

    # Wrap with /usr/bin/time -v on Linux for accurate peak RSS
    time_bin = Path("/usr/bin/time")
    if time_bin.is_file():
        cmd = [str(time_bin), "-v"] + cmd

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    combined = proc.stderr + proc.stdout
    return _parse_timings(combined)


# ── benchmark prompts ─────────────────────────────────────────────────────────

# ~110 words ≈ 140 tokens; repeat to build a 3k-token predictable prefix
_STORY = (
    "Once upon a time in a quiet valley a small brown rabbit named Clover "
    "woke each morning, wiggled his nose, and hopped to the meadow. "
    "The meadow had three rules: share the berry bushes, avoid the "
    "field-mouse nests in the tall grass, and gather on the flat rock at "
    "sunset to count the colours as the sky turned orange, then pink, then "
    "purple, then caught its first bright star. Clover's friends Pippa the "
    "hedgehog and Drummond the tortoise always sat beside him. Drummond said "
    "some days had four colours, some days only two — the sky keeping things "
    "interesting. "
)

_PIVOT = (
    "Now solve this rigorously using conditional probability and Bayes' "
    "theorem: Three closed doors. One hides a car; two hide goats. You "
    "pick door 1. The host (knowing all) opens door 3 — a goat. He offers "
    "a switch to door 2. Prove whether you should switch and by how much "
    "your odds improve. Show every calculation step."
)

_BREADCRUMB = (
    "Earlier I mentioned my favourite imaginary creature is a neon green "
    "turtle named Sparky who can solve differential equations. "
)
_FILLER = (
    "The history of mathematics stretches from ancient Babylonian clay "
    "tablets through Euclid, Newton, Leibniz, Cauchy, and Weierstrass to "
    "the hundreds of specialisations studied today by mathematicians around "
    "the world in universities and research institutions. "
)
_RECALL_Q = (
    "Based on everything I have told you in this conversation, what is the "
    "name and colour of the imaginary creature I described? List every "
    "detail I mentioned."
)

_DEBUG_PROMPT = (
    "Debug this Python carefully. Find every syntax error, logic error, "
    "and off-by-one error. For each, state what is wrong and show the fix.\n\n"
    "```python\n"
    "def find_primes(limit):\n"
    "    primes = []\n"
    "    for n in range(2, limit):\n"
    "        is_prime = True\n"
    "        for i in range(2, n):\n"
    "            if n % i = 0:\n"
    "                is_prime = False\n"
    "        if is_prime = True:\n"
    "            primes.append\n"
    "    return prime\n"
    "result = find_primes(20\n"
    "print('Primes:', result)\n"
    "```\n\n"
    "After correcting, trace through find_primes(10) and list every prime found."
)


# ── benchmarks ────────────────────────────────────────────────────────────────

def bench1_cognitive_shift(llama_cli, gguf, n_gpu_layers, threads) -> dict:
    """~3k-token predictable prefix then abrupt pivot to a logic puzzle."""
    # 22 × _STORY ≈ 3 080 tokens; fits with ctx_size=4096
    prompt = (_STORY * 22).strip() + "\n\n" + _PIVOT
    t = _run(llama_cli, gguf, prompt,
             ctx_size=4096, n_predict=256,
             n_gpu_layers=n_gpu_layers, threads=threads)
    return {
        "gen_tok_per_s": t.get("gen_tok_per_s"),
        "prefill_s":     round(t["prefill_ms"] / 1000, 2) if "prefill_ms" in t else None,
        "prefill_tokens": t.get("prefill_tokens"),
        "peak_rss_mb":   t.get("peak_rss_mb"),
    }


def bench2_context_sweep(llama_cli, gguf, n_gpu_layers, threads) -> dict:
    """Generation throughput at 2048 / 4096 / 8192 token windows."""
    results = []
    # tokens_per_filler ≈ 55; target ~55% fill so generation fits
    filler_per_ctx = {2048: 20, 4096: 40, 8192: 80}

    for ctx, reps in filler_per_ctx.items():
        prompt = (
            (_FILLER * reps).strip()
            + " " + _BREADCRUMB
            + (_FILLER * 4).strip()
            + " " + _RECALL_Q
        )
        t = _run(llama_cli, gguf, prompt,
                 ctx_size=ctx, n_predict=128,
                 n_gpu_layers=n_gpu_layers, threads=threads)
        results.append({
            "context_window": ctx,
            "gen_tok_per_s":  t.get("gen_tok_per_s"),
        })
        print(f"    @{ctx:5d}: {t.get('gen_tok_per_s', 'N/A')} tok/s")
    return {"results": results}


def bench3_power_reasoning(llama_cli, gguf, n_gpu_layers, threads) -> dict:
    """Sustained reasoning task with GPU power monitoring."""
    power_samples: list[float] = []
    stop = threading.Event()
    monitor = threading.Thread(
        target=_power_sampler, args=(power_samples, stop), daemon=True
    )
    monitor.start()

    t = _run(llama_cli, gguf, _DEBUG_PROMPT,
             ctx_size=2048, n_predict=384,
             n_gpu_layers=n_gpu_layers, threads=threads)
    stop.set()
    monitor.join(timeout=3)

    out: dict = {
        "gen_tok_per_s": t.get("gen_tok_per_s"),
        "prefill_s":     round(t["prefill_ms"] / 1000, 2) if "prefill_ms" in t else None,
        "peak_rss_mb":   t.get("peak_rss_mb"),
    }
    if power_samples:
        out["power_peak_w"]  = round(max(power_samples), 1)
        out["power_avg_w"]   = round(sum(power_samples) / len(power_samples), 1)
        tps = t.get("gen_tok_per_s")
        if tps and out["power_avg_w"]:
            out["tok_per_watt"] = round(tps / out["power_avg_w"], 2)
    else:
        out["power_note"] = "no nvidia-smi — run on NVIDIA GPU or add psutil for CPU monitoring"
    return out


# ── orchestration ─────────────────────────────────────────────────────────────

def run_all(label: str, llama_cli: Path, gguf: Path,
            n_gpu_layers: int, threads: int) -> dict:
    print(f"\n{'━'*60}")
    print(f"  {label}")
    print(f"  {gguf.name}  ({gguf.stat().st_size / 1024**3:.2f} GB)")
    print(f"  GPU layers: {n_gpu_layers}  |  threads: {threads or 'auto'}")
    print(f"{'━'*60}")

    print("  Bench 1 — cognitive shift (~3k-token pivot) ...", flush=True)
    b1 = bench1_cognitive_shift(llama_cli, gguf, n_gpu_layers, threads)
    print(f"    gen: {b1['gen_tok_per_s']} tok/s  |  prefill: {b1['prefill_s']}s ({b1['prefill_tokens']} tokens)")

    print("  Bench 2 — context sweep (2048 / 4096 / 8192) ...", flush=True)
    b2 = bench2_context_sweep(llama_cli, gguf, n_gpu_layers, threads)

    print("  Bench 3 — power + reasoning ...", flush=True)
    b3 = bench3_power_reasoning(llama_cli, gguf, n_gpu_layers, threads)
    pwr = (f"{b3['power_peak_w']} W peak / {b3['power_avg_w']} W avg"
           if "power_peak_w" in b3 else b3.get("power_note", "—"))
    print(f"    gen: {b3['gen_tok_per_s']} tok/s  |  power: {pwr}")

    return {"bench1": b1, "bench2": b2, "bench3": b3}


# ── comparison table ──────────────────────────────────────────────────────────

def _gain(srd_val, q4_val, *, lower_better: bool = False) -> str:
    if srd_val is None or q4_val is None or q4_val == 0:
        return "N/A"
    if lower_better:
        pct = (q4_val - srd_val) / q4_val * 100
    else:
        pct = (srd_val - q4_val) / q4_val * 100
    return f"{pct:+.1f}%"


def print_comparison(srd: dict, q4: dict) -> None:
    s1, q1 = srd["bench1"], q4["bench1"]
    s2, q2 = srd["bench2"]["results"], q4["bench2"]["results"]
    s3, q3 = srd["bench3"], q4["bench3"]

    W = 72
    print("\n" + "═" * W)
    print("  RESULTS — SRD Q4_K_M vs Regular Q4_K_M")
    print("═" * W)
    hdr = f"  {'Metric':<32} {'Reg Q4':>10} {'SRD Q4':>10} {'SRD gain':>9}"
    sep = f"  {'-'*32} {'-'*10} {'-'*10} {'-'*9}"
    print(hdr)
    print(sep)

    def row(label, q_val, s_val, fmt=".1f", lower_better=False, unit=""):
        q_str = f"{q_val:{fmt}}{unit}" if q_val is not None else "N/A"
        s_str = f"{s_val:{fmt}}{unit}" if s_val is not None else "N/A"
        g = _gain(s_val, q_val, lower_better=lower_better)
        print(f"  {label:<32} {q_str:>10} {s_str:>10} {g:>9}")

    row("Bench1 gen tok/s",           q1.get("gen_tok_per_s"), s1.get("gen_tok_per_s"))
    row("Bench1 prefill s",            q1.get("prefill_s"),     s1.get("prefill_s"),
        fmt=".2f", lower_better=True, unit="s")

    for sq, qq in zip(s2, q2):
        ctx = sq["context_window"]
        row(f"Bench2 @{ctx} tok/s", qq.get("gen_tok_per_s"), sq.get("gen_tok_per_s"))

    row("Bench3 gen tok/s",           q3.get("gen_tok_per_s"), s3.get("gen_tok_per_s"))
    if "power_peak_w" in s3 and "power_peak_w" in q3:
        row("Bench3 peak W",           q3["power_peak_w"], s3["power_peak_w"],
            lower_better=True, unit="W")
        row("Bench3 avg W",            q3["power_avg_w"],  s3["power_avg_w"],
            lower_better=True, unit="W")
    if "tok_per_watt" in s3 and "tok_per_watt" in q3:
        row("Bench3 tok/s/W",          q3["tok_per_watt"], s3["tok_per_watt"], fmt=".2f")
    if "peak_rss_mb" in s3 and "peak_rss_mb" in q3:
        row("Bench3 peak RAM MB",      q3["peak_rss_mb"],  s3["peak_rss_mb"],
            lower_better=True, fmt=".0f", unit=" MB")

    print("═" * W + "\n")


# ── download helper ───────────────────────────────────────────────────────────

def download_ref_q4(dest: Path) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        sys.exit("pip install huggingface_hub  (needed for --download-q4)")
    print(f"Downloading {_REF_REPO}/{_REF_FILE} → {dest} ...")
    import shutil
    src = hf_hub_download(repo_id=_REF_REPO, filename=_REF_FILE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)
    print(f"Saved {dest.stat().st_size / 1024**3:.2f} GB")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--srd-gguf",      type=Path, help="SRD-processed Q4_K_M GGUF")
    p.add_argument("--q4-gguf",       type=Path, help="Reference plain Q4_K_M GGUF")
    p.add_argument("--llama-cli",     type=Path, required=True,
                   help="Path to llama-cli binary")
    p.add_argument("--n-gpu-layers",  type=int,  default=0,
                   help="GPU layers (0=CPU, 99=full GPU)")
    p.add_argument("--threads",       type=int,  default=0,
                   help="CPU threads (0=auto)")
    p.add_argument("--output",        type=Path, default=None,
                   help="JSON results output path")
    p.add_argument("--download-q4",   action="store_true",
                   help="Auto-download bartowski Q4_K_M GGUF to --q4-gguf path")
    p.add_argument("--srd-only",      action="store_true",
                   help="Only run SRD model")
    p.add_argument("--q4-only",       action="store_true",
                   help="Only run plain Q4 model")
    args = p.parse_args()

    if args.srd_only and args.q4_only:
        sys.exit("--srd-only and --q4-only are mutually exclusive")
    if not args.srd_only and args.srd_gguf is None:
        sys.exit("--srd-gguf is required (or use --q4-only)")
    if not args.q4_only and args.q4_gguf is None:
        sys.exit("--q4-gguf is required (or use --srd-only or --download-q4)")

    llama_cli = args.llama_cli.expanduser().resolve()
    if not llama_cli.is_file():
        sys.exit(f"llama-cli not found: {llama_cli}")

    if args.download_q4:
        download_ref_q4(args.q4_gguf)

    import datetime
    payload: dict = {
        "run_date":     datetime.date.today().isoformat(),
        "n_gpu_layers": args.n_gpu_layers,
        "threads":      args.threads,
    }

    q4_results = srd_results = None

    if not args.srd_only:
        q4 = args.q4_gguf.expanduser().resolve()
        if not q4.is_file():
            sys.exit(f"Q4 GGUF not found: {q4}  (try --download-q4)")
        q4_results = run_all("Regular Q4_K_M (no SRD)", llama_cli, q4,
                              args.n_gpu_layers, args.threads)
        payload["regular_q4km"] = q4_results

    if not args.q4_only:
        srd = args.srd_gguf.expanduser().resolve()
        if not srd.is_file():
            sys.exit(f"SRD GGUF not found: {srd}")
        srd_results = run_all("SRD Q4_K_M", llama_cli, srd,
                               args.n_gpu_layers, args.threads)
        payload["srd_q4km"] = srd_results

    if srd_results and q4_results:
        print_comparison(srd_results, q4_results)

    out = args.output or (_RESULTS_DIR / "laptop_srd_vs_q4.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"Results saved → {out}")


if __name__ == "__main__":
    main()
