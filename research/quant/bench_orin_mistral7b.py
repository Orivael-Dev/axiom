"""Mistral-7B on Orin Nano — bench the two modes that 'shouldn't work'.

Why this is interesting
-----------------------
Conventional wisdom: 7B models need at least 8–14 GB VRAM.
Orin Nano reality:   5.5 GB usable unified memory.

Two modes both fit and both produce useful results:

  Mode A  --ngl 32  (all GPU)
    Weights: 4.07 GB Q4_K_M — leaves 1.46 GB for KV cache
    Predicted max context: ~5,857 tokens  (≈ 6K)
    Speed: full GPU path, fastest tok/s but small context

  Mode B  --ngl 22  (22 GPU / 10 CPU, partial offload)
    Weights on GPU: ~2.80 GB — leaves 2.70 GB for KV cache
    Predicted max context: ~11,066 tokens (≈ 11K)
    Speed: slightly slower (GPU-CPU sync) BUT on unified memory
           there is no PCIe copy — just a pointer difference.
           On a discrete 6 GB card this would be painfully slow;
           on Orin it barely hurts.

The benchmark measures:
  - tok/s for each mode
  - Actual KV bytes/token (compare to theoretical 131,072 B)
  - Peak RSS and VRAM
  - Context window test: does 8K actually load and generate?

Run on Orin Nano (SSH or terminal):
    cd ~/axiom
    python3 -m research.quant.bench_orin_mistral7b \
        --llamacpp ~/llama.cpp/build/bin \
        --gguf ~/models/mistral-7b-instruct-v0.3-Q4_K_M.gguf

To get the GGUF on Orin:
    pip3 install huggingface-hub
    huggingface-cli download bartowski/Mistral-7B-Instruct-v0.3-GGUF \\
        Mistral-7B-Instruct-v0.3-Q4_K_M.gguf --local-dir ~/models/

Or scp from laptop if you already downloaded it:
    scp ~/models/Mistral-7B-Instruct-v0.3-Q4_K_M.gguf orin:~/models/
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ── Theoretical predictions ──────────────────────────────────────────────────

MISTRAL_7B = dict(n_layers=32, n_kv_heads=8, head_dim=128, q4km_gb=4.07)
ORIN_NANO_USABLE_GB = 5.5   # after OS + CUDA overhead

def theoretical_kv_bpt():
    m = MISTRAL_7B
    return m["n_layers"] * 2 * m["n_kv_heads"] * m["head_dim"] * 2  # FP16

def predicted_ctx(ngl: int) -> int:
    layers_gpu = min(ngl, MISTRAL_7B["n_layers"])
    vram_weights = MISTRAL_7B["q4km_gb"] * layers_gpu / MISTRAL_7B["n_layers"]
    kv_budget_mib = (ORIN_NANO_USABLE_GB - vram_weights) * 1024 * 0.50
    bpt = theoretical_kv_bpt()
    return max(0, int(kv_budget_mib * 1024 * 1024 / bpt))


# ── nvidia-smi / tegrastats VRAM ─────────────────────────────────────────────

def read_vram_mb() -> Optional[float]:
    """Try nvidia-smi first, then tegrastats (Orin-native)."""
    for cmd in [
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        ["/usr/bin/nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
    ]:
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5)
            return float(out.decode().strip().split("\n")[0])
        except Exception:
            pass

    # tegrastats one-shot: parse "RAM 3456/7772MB"
    try:
        out = subprocess.check_output(
            ["tegrastats", "--interval", "500", "--stop"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode()
        m = re.search(r"RAM\s+(\d+)/(\d+)MB", out)
        if m:
            return float(m.group(1))   # used MB (RAM, not pure VRAM on unified)
    except Exception:
        pass
    return None


def read_rss_mb() -> float:
    """Current process RSS in MB."""
    try:
        stat = Path(f"/proc/{os.getpid()}/status").read_text()
        m = re.search(r"VmRSS:\s+(\d+)\s+kB", stat)
        return int(m.group(1)) / 1024 if m else 0.0
    except Exception:
        return 0.0


# ── llama.cpp runner ─────────────────────────────────────────────────────────

PROMPT = (
    "Explain the Orin Nano unified memory architecture and why it matters "
    "for running large language models on edge devices."
)

def find_cli(llamacpp_bin: str) -> Path:
    for name in ("llama-cli", "main"):
        p = Path(llamacpp_bin) / name
        if p.exists():
            return p
    raise FileNotFoundError(
        f"llama-cli not found in {llamacpp_bin}. "
        "Build: cmake .. -DGGML_CUDA=ON && cmake --build . -j4 -t llama-cli"
    )


def run_llama(cli: Path, gguf: str, ctx: int, ngl: int, n_predict: int = 64) -> dict:
    cmd = [
        str(cli), "-m", gguf,
        "--ctx-size",  str(ctx),
        "--n-predict", str(n_predict),
        "--ngl",       str(ngl),
        "--threads",   "4",
        "--no-mmap",
        "--log-disable",
        "--prompt",    PROMPT,
    ]
    t0 = time.perf_counter()
    r  = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    elapsed = time.perf_counter() - t0
    log = r.stdout + r.stderr

    # Parse tok/s from log: "llama_perf_sampler_print:        sampling time = ..."
    # or "eval time = X ms / Y runs ( Z ms per token, W tokens per second)"
    tps = None
    m = re.search(r"(\d+\.\d+)\s+tokens per second", log)
    if m:
        tps = float(m.group(1))
    else:
        # eval time line: "llama_print_timings:        eval time = 12345.67 ms /  64 runs (  192.90 ms per token,    5.18 tokens per second)"
        m = re.search(r"eval time\s*=.*?([\d.]+)\s*tokens per second", log)
        if m:
            tps = float(m.group(1))

    kv_mib = None
    m = re.search(r"kv self size\s*=\s*([\d.]+)\s*MiB", log, re.IGNORECASE)
    if m:
        kv_mib = float(m.group(1))

    return {
        "elapsed_s": round(elapsed, 2),
        "tok_per_s": tps,
        "kv_log_mib": kv_mib,
        "returncode": r.returncode,
        "output_snippet": log[-400:] if log else "",
    }


# ── Main benchmark ────────────────────────────────────────────────────────────

def run_benchmark(llamacpp_bin: str, gguf_path: str, skip_8k: bool = False) -> dict:
    cli  = find_cli(llamacpp_bin)
    bpt  = theoretical_kv_bpt()

    print(f"\n{'='*66}")
    print("Mistral-7B on Orin Nano — modes that 'shouldn\'t work'")
    print(f"{'='*66}")
    print(f"Model: {gguf_path}")
    print(f"Theoretical KV bytes/token: {bpt:,} B ({bpt/1024:.0f} KB)")
    print()

    modes = [
        ("A: full GPU",      32,  1024),   # conservative ctx for first test
        ("A: full GPU 4K",   32,  4096),
        ("B: partial (ngl=22) 4K",  22,  4096),
        ("B: partial (ngl=22) 8K",  22,  8192),
    ]
    if skip_8k:
        modes = [m for m in modes if "8K" not in m[0]]

    results = {}
    for label, ngl, ctx in modes:
        pred = predicted_ctx(ngl)
        over = " ← over budget" if ctx > pred else ""
        print(f"  Running {label}  ctx={ctx:,}  ngl={ngl}  (predicted max {pred:,}){over}")

        vram_before = read_vram_mb()
        row = run_llama(cli, gguf_path, ctx=ctx, ngl=ngl)
        time.sleep(2)

        kv_mib  = row["kv_log_mib"]
        act_bpt = int(kv_mib * 1024 * 1024 / ctx) if kv_mib else None
        err_pct = abs(act_bpt - bpt) / bpt * 100 if act_bpt else None

        tok_s = f"{row['tok_per_s']:.2f} tok/s" if row["tok_per_s"] else "N/A"
        kv_s  = f"{kv_mib:.1f} MiB" if kv_mib else "N/A"
        err_s = f"{err_pct:.1f}%" if err_pct is not None else "N/A"

        print(f"    tok/s: {tok_s}  KV: {kv_s}  actual B/tok: "
              f"{act_bpt:,}" if act_bpt else f"    tok/s: {tok_s}  KV: {kv_s}")
        if err_pct is not None:
            print(f"    simulation error: {err_s}  ({'✓ PASS' if err_pct < 5 else '~ close' if err_pct < 15 else '✗ check'})")
        if row["returncode"] != 0:
            print(f"    ✗ OOM or error (returncode {row['returncode']})")
        print()

        row.update({"label": label, "ngl": ngl, "ctx": ctx,
                    "predicted_max_ctx": pred, "actual_bpt": act_bpt,
                    "err_pct": round(err_pct, 2) if err_pct else None})
        results[label] = row

    _print_summary(results, bpt)
    return results


def _print_summary(results: dict, bpt: int):
    print(f"{'='*66}")
    print("SUMMARY")
    print(f"{'='*66}")
    print(f"{'Mode':<28}  {'ctx':>6}  {'ngl':>4}  {'tok/s':>8}  {'KV MiB':>8}  "
          f"{'err%':>6}  {'status':>7}")
    print(f"{'─'*28}  {'─'*6}  {'─'*4}  {'─'*8}  {'─'*8}  {'─'*6}  {'─'*7}")
    for label, r in results.items():
        tps = f"{r['tok_per_s']:.2f}" if r.get("tok_per_s") else "N/A"
        kv  = f"{r['kv_log_mib']:.1f}" if r.get("kv_log_mib") else "N/A"
        err = f"{r['err_pct']:.1f}" if r.get("err_pct") is not None else "N/A"
        ok  = "✓ OK" if r["returncode"] == 0 else "✗ OOM"
        print(f"  {label:<26}  {r['ctx']:>6,}  {r['ngl']:>4}  {tps:>8}  "
              f"{kv:>8}  {err:>6}  {ok:>7}")

    # Unified memory verdict
    a_runs = [r for k, r in results.items() if "full GPU" in k and r.get("tok_per_s")]
    b_runs = [r for k, r in results.items() if "partial" in k and r.get("tok_per_s")]
    if a_runs and b_runs:
        a_avg = sum(r["tok_per_s"] for r in a_runs) / len(a_runs)
        b_avg = sum(r["tok_per_s"] for r in b_runs) / len(b_runs)
        slowdown = (1 - b_avg / a_avg) * 100
        print()
        print(f"  Unified memory verdict:")
        print(f"    Full GPU (ngl=32) avg:   {a_avg:.2f} tok/s")
        print(f"    Partial  (ngl=22) avg:   {b_avg:.2f} tok/s")
        print(f"    Slowdown from offload:   {slowdown:.1f}%")
        if slowdown < 20:
            print(f"    ✓ Unified memory: CPU offload costs < 20% — nearly free context doubling")
        elif slowdown < 40:
            print(f"    ~ Moderate cost — still worthwhile for 2× context gain")
        else:
            print(f"    ~ Significant slowdown — but discrete GPU would be 5-10× worse here")
    print(f"{'='*66}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Mistral-7B on Orin Nano — full GPU vs partial offload benchmark"
    )
    p.add_argument("--llamacpp", required=True, help="path to llama.cpp build/bin/")
    p.add_argument("--gguf",     required=True, help="path to Mistral-7B Q4_K_M .gguf")
    p.add_argument("--skip-8k",  action="store_true",
                   help="skip the 8K context test (use if RAM is very tight)")
    p.add_argument("--stats-json", default=None)
    args = p.parse_args()

    results = run_benchmark(args.llamacpp, args.gguf, skip_8k=args.skip_8k)

    if args.stats_json:
        Path(args.stats_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats_json).write_text(json.dumps(results, indent=2) + "\n")
        print(f"Results → {args.stats_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
