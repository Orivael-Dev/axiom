"""Mistral-7B KV cache benchmark — validate simulation predictions on a 6 GB GPU.

Tests whether the analytic formula
    kv_bytes_per_token = n_layers × 2 × n_kv_heads × head_dim × 2
gives an accurate prediction of actual VRAM growth in llama.cpp.

Theoretical values for Mistral-7B:
    32 layers × 2 × 8 KV heads × 128 head_dim × 2 bytes = 131,072 B/token = 128 KB/token

Run on your laptop (WSL2 + GTX 1660 Ti or similar):
    python3 research/quant/bench_mistral_kv.py \\
        --llamacpp ~/llama.cpp/build/bin \\
        --gguf ~/models/mistral-7b-instruct-v0.3-Q4_K_M.gguf

To download the model first:
    pip install huggingface-hub
    huggingface-cli download \
        bartowski/Mistral-7B-Instruct-v0.3-GGUF \
        Mistral-7B-Instruct-v0.3-Q4_K_M.gguf \
        --local-dir ~/models/

Expected outputs (GTX 1660 Ti 6GB, llama.cpp Jan 2025):
    Context  VRAM used  VRAM delta  Actual B/tok  Theory B/tok  Err%
    256      ~4.4 GB    —           —             131,072       —
    512      ~4.4 GB    +32 MB      ~131,072      131,072       ~0%
    1024     ~4.5 GB    +64 MB      ~131,072      131,072       ~0%
    2048     ~4.7 GB    +128 MB     ~131,072      131,072       ~0%
    4096     ~5.2 GB    +256 MB     ~131,072      131,072       ~0%
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


# ── Theoretical prediction ───────────────────────────────────────────────────

MISTRAL_7B = dict(
    n_layers=32,
    n_kv_heads=8,
    head_dim=128,
    q4km_gb=4.07,
)

def theoretical_kv_bytes_per_token(n_layers, n_kv_heads, head_dim, dtype_bytes=2):
    return n_layers * 2 * n_kv_heads * head_dim * dtype_bytes


# ── VRAM reading via nvidia-smi ──────────────────────────────────────────────

def read_vram_mb() -> Optional[float]:
    """Read current GPU 0 used VRAM in MB via nvidia-smi."""
    paths = [
        "nvidia-smi",
        "/usr/lib/wsl/lib/nvidia-smi",
        "/usr/local/cuda/bin/nvidia-smi",
    ]
    for p in paths:
        try:
            out = subprocess.check_output(
                [p, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=5,
            )
            return float(out.decode().strip().split("\n")[0])
        except Exception:
            continue
    return None


# ── llama.cpp runner ─────────────────────────────────────────────────────────

def run_llama(
    llamacpp_bin: str,
    gguf_path: str,
    ctx_size: int,
    n_predict: int = 8,
    n_gpu_layers: int = 99,
) -> tuple[float, str]:
    """Run llama-cli and return (elapsed_s, stdout).

    Uses a fixed prompt so the token sequence is deterministic.
    """
    cli = Path(llamacpp_bin) / "llama-cli"
    if not cli.exists():
        cli = Path(llamacpp_bin) / "main"   # older llama.cpp name
    if not cli.exists():
        raise FileNotFoundError(
            f"llama-cli / main not found in {llamacpp_bin}. "
            "Build llama.cpp first: cmake .. -DGGML_CUDA=ON && cmake --build . -j"
        )

    cmd = [
        str(cli),
        "-m", gguf_path,
        "--ctx-size", str(ctx_size),
        "--n-predict", str(n_predict),
        "--n-gpu-layers", str(n_gpu_layers),
        "--threads", "4",
        "--no-mmap",
        "--log-disable",
        "--prompt", "Once upon a time in a land far away",
    ]
    t0 = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
    )
    elapsed = time.time() - t0
    return elapsed, result.stdout + result.stderr


# ── KV memory extraction from llama.cpp log ──────────────────────────────────

def parse_kv_vram_from_log(log: str) -> Optional[float]:
    """Extract KV cache VRAM from llama.cpp log line like 'kv self size = X MiB'."""
    m = re.search(r"kv self size\s*=\s*([0-9.]+)\s*MiB", log, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Newer format: 'KV cache size: X MiB'
    m = re.search(r"KV cache size[:\s]+([0-9.]+)\s*MiB", log, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def parse_total_vram_from_log(log: str) -> Optional[float]:
    """Extract total VRAM allocation from llama.cpp log."""
    # e.g. 'total VRAM used: 4321.45 MiB'
    for pattern in [
        r"total VRAM used[:\s]+([0-9.]+)\s*MiB",
        r"VRAM used[:\s]+([0-9.]+)\s*MiB",
        r"mem required\s*=\s*([0-9.]+)\s*MB",
    ]:
        m = re.search(pattern, log, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


# ── Main benchmark ─────────────────────────────────────────────────────────

def run_benchmark(
    llamacpp_bin: str,
    gguf_path: str,
    ctx_sizes: list[int],
    n_gpu_layers: int = 99,
    pause_s: float = 2.0,
) -> list[dict]:
    theory_bpt = theoretical_kv_bytes_per_token(**{k: MISTRAL_7B[k] for k in
                                                    ("n_layers", "n_kv_heads", "head_dim")})

    print(f"\n{'='*68}")
    print("Mistral-7B KV Cache Benchmark — simulation vs reality")
    print(f"{'='*68}")
    print(f"\nModel:      {gguf_path}")
    print(f"  Q4_K_M size: {MISTRAL_7B['q4km_gb']:.2f} GB")
    print(f"  Layers:      {MISTRAL_7B['n_layers']} | KV heads: {MISTRAL_7B['n_kv_heads']}"
          f" | head_dim: {MISTRAL_7B['head_dim']}")
    print(f"\nTheoretical KV bytes/token: {theory_bpt:,} B = {theory_bpt/1024:.0f} KB")
    print(f"  = {MISTRAL_7B['n_layers']} × 2 × {MISTRAL_7B['n_kv_heads']}"
          f" × {MISTRAL_7B['head_dim']} × 2 bytes FP16")
    print(f"\nBaseline VRAM (nvidia-smi before any run):")
    baseline_vram = read_vram_mb()
    if baseline_vram is not None:
        print(f"  {baseline_vram:.0f} MB")
    else:
        print("  (nvidia-smi not reachable — VRAM delta column will be N/A)")
        baseline_vram = None

    print(f"\nRunning {len(ctx_sizes)} context sizes: {ctx_sizes}")
    print(f"{'─'*68}")

    results = []
    prev_kv_log = None

    for ctx in ctx_sizes:
        print(f"\n  ctx={ctx:5d} tokens  ...", end="", flush=True)

        # VRAM before this run
        vram_before = read_vram_mb()
        elapsed, log = run_llama(llamacpp_bin, gguf_path, ctx, n_gpu_layers=n_gpu_layers)

        # VRAM right after (process still alive momentarily for shared libs)
        time.sleep(pause_s)
        vram_after_raw = read_vram_mb()

        # Extract from log
        kv_log_mb  = parse_kv_vram_from_log(log)
        tot_log_mb = parse_total_vram_from_log(log)

        row = {
            "ctx_size":        ctx,
            "elapsed_s":       round(elapsed, 2),
            "kv_log_mib":      kv_log_mb,
            "total_log_mib":   tot_log_mb,
            "vram_before_mb":  vram_before,
            "vram_after_mb":   vram_after_raw,
            "theory_bpt":      theory_bpt,
        }

        # Actual bytes/token from log-reported KV size
        if kv_log_mb is not None:
            actual_bpt = int(kv_log_mb * 1024 * 1024 / ctx)
            err_pct = abs(actual_bpt - theory_bpt) / theory_bpt * 100
            row["actual_bpt"]  = actual_bpt
            row["err_pct"]     = round(err_pct, 1)
            status = f"  kv={kv_log_mb:.1f} MiB  actual={actual_bpt:,} B/tok  err={err_pct:.1f}%"
        elif tot_log_mb is not None:
            status = f"  total_vram={tot_log_mb:.0f} MiB  (kv line not found in log)"
        else:
            status = f"  done in {elapsed:.1f}s  (no VRAM lines in log)"
        print(status)

        results.append(row)
        prev_kv_log = kv_log_mb
        time.sleep(pause_s)   # let process release GPU before next run

    return results


def print_summary(results: list[dict]):
    theory_bpt = results[0]["theory_bpt"]
    print(f"\n{'='*68}")
    print("SUMMARY — predicted vs actual KV bytes/token")
    print(f"{'='*68}")
    print(f"  Theoretical: {theory_bpt:,} B/token ({theory_bpt/1024:.0f} KB/token)")
    print()
    print(f"  {'ctx':>6}  {'KV log (MiB)':>14}  {'actual B/tok':>14}  "
          f"{'theory B/tok':>14}  {'error':>6}  {'elapsed':>8}")
    print(f"  {'─'*6}  {'─'*14}  {'─'*14}  {'─'*14}  {'─'*6}  {'─'*8}")
    for r in results:
        kv_str  = f"{r['kv_log_mib']:.2f}" if r.get("kv_log_mib") is not None else "N/A"
        act_str = f"{r['actual_bpt']:,}"   if r.get("actual_bpt") is not None else "N/A"
        err_str = f"{r['err_pct']:.1f}%"  if r.get("err_pct")    is not None else "N/A"
        print(f"  {r['ctx_size']:>6}  {kv_str:>14}  {act_str:>14}  "
              f"{theory_bpt:>14,}  {err_str:>6}  {r['elapsed_s']:>6.1f}s")

    print()
    valid = [r for r in results if r.get("err_pct") is not None]
    if valid:
        avg_err = sum(r["err_pct"] for r in valid) / len(valid)
        print(f"  Average error: {avg_err:.1f}%")
        if avg_err < 5:
            print("  ✓ Simulation is accurate (< 5% error) — KV formula matches reality")
        elif avg_err < 15:
            print("  ~ Simulation is close (< 15%) — minor deviation, likely KV dtype or GQA variant")
        else:
            print("  ✗ Significant deviation — check --ngl flag or model variant (sliding window?)")
    else:
        print("  Could not extract KV size from llama.cpp log.")
        print("  Tip: pass --verbose to llama-cli to get more log output.")
        print("  Or check: build/bin/llama-cli -m <model> --ctx-size 512 2>&1 | grep -i kv")
    print(f"{'='*68}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Benchmark Mistral-7B KV memory vs analytic prediction"
    )
    p.add_argument("--llamacpp", required=True,
                   help="path to llama.cpp build/bin/ directory")
    p.add_argument("--gguf", required=True,
                   help="path to Mistral-7B-Instruct Q4_K_M .gguf file")
    p.add_argument("--ctx-sizes", nargs="+", type=int,
                   default=[256, 512, 1024, 2048, 4096],
                   help="context window sizes to test (default: 256 512 1024 2048 4096)")
    p.add_argument("--ngl", type=int, default=99,
                   help="number of GPU layers (default 99 = all)")
    p.add_argument("--stats-json", default=None,
                   help="write results to JSON file")
    p.add_argument("--theory-only", action="store_true",
                   help="just print the theoretical prediction without running any model")
    args = p.parse_args()

    theory_bpt = theoretical_kv_bytes_per_token(
        MISTRAL_7B["n_layers"], MISTRAL_7B["n_kv_heads"], MISTRAL_7B["head_dim"]
    )

    if args.theory_only:
        print("\nMistral-7B KV cache — theoretical values:")
        print(f"  Bytes/token (FP16): {theory_bpt:,} B = {theory_bpt/1024:.0f} KB")
        for ctx in args.ctx_sizes:
            mb = theory_bpt * ctx / 1024**2
            print(f"  {ctx:5d} tokens → {mb:.1f} MiB KV cache")
        gtx_vram_gb = 5.2
        q4_gb = MISTRAL_7B["q4km_gb"]
        kv_budget_mib = (gtx_vram_gb - q4_gb) * 1024 * 0.50
        max_ctx = int(kv_budget_mib * 1024 * 1024 / theory_bpt)
        print(f"\n  GTX 1660 Ti 6GB ({gtx_vram_gb} GB usable):")
        print(f"    Q4_K_M weights: {q4_gb} GB")
        print(f"    KV budget (50%): {kv_budget_mib:.0f} MiB")
        print(f"    Predicted max context: {max_ctx:,} tokens")
        return 0

    results = run_benchmark(args.llamacpp, args.gguf, args.ctx_sizes, n_gpu_layers=args.ngl)
    print_summary(results)

    if args.stats_json:
        Path(args.stats_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats_json).write_text(json.dumps(results, indent=2) + "\n")
        print(f"Results written to {args.stats_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
