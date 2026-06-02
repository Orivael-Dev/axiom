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

NVMe M.2 note
-------------
If the model is stored on an NVMe M.2 drive (250 GB recommended),
mmap is fast (~3 GB/s read) — do NOT pass --no-mmap. llama.cpp will
mmap the weight file and only page in what it needs, keeping cold-load
time to ~1-2 s for a 4 GB model. Contrast with microSD (45 s) or
the Windows 9p mount over WSL2 (minutes — always --no-mmap there).

The benchmark measures:
  - Cold load time (model file → GPU, incl. CUDA kernel init)
  - tok/s for each mode at 1K and 4K/8K contexts
  - Actual KV bytes/token (compare to theoretical 131,072 B)
  - Storage bandwidth implied by the cold-load time

Run on Orin Nano with NVMe (SSH or terminal):
    # store the model on the NVMe mount, e.g. /mnt/nvme/models/
    python3 -m research.quant.bench_orin_mistral7b \\
        --llamacpp ~/llama.cpp/build/bin \\
        --gguf /mnt/nvme/models/mistral-7b-instruct-v0.3-Q4_K_M.gguf \\
        --nvme    # enables mmap, measures cold-load time

Download the model directly to NVMe:
    huggingface-cli download bartowski/Mistral-7B-Instruct-v0.3-GGUF \\
        Mistral-7B-Instruct-v0.3-Q4_K_M.gguf \\
        --local-dir /mnt/nvme/models/

Or scp from laptop:
    scp ~/models/Mistral-7B-Instruct-v0.3-Q4_K_M.gguf orin:/mnt/nvme/models/
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


def _storage_bw_gbps(file_bytes: int, load_s: float) -> Optional[float]:
    if load_s <= 0:
        return None
    return (file_bytes / 1024**3) / load_s


def run_llama(
    cli: Path, gguf: str, ctx: int, ngl: int,
    n_predict: int = 64, use_mmap: bool = False,
) -> dict:
    cmd = [
        str(cli), "-m", gguf,
        "--ctx-size",  str(ctx),
        "--n-predict", str(n_predict),
        "--ngl",       str(ngl),
        "--threads",   "4",
        "--log-disable",
        "--prompt",    PROMPT,
    ]
    if not use_mmap:
        cmd.append("--no-mmap")
    # --mmap is default in llama.cpp; only need to suppress no-mmap

    t0 = time.perf_counter()
    r  = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    elapsed = time.perf_counter() - t0
    log = r.stdout + r.stderr

    # Extract load time from log: "load_time =   1234.56 ms"
    load_s = None
    m2 = re.search(r"load_time\s*=\s*([\d.]+)\s*ms", log, re.IGNORECASE)
    if m2:
        load_s = float(m2.group(1)) / 1000.0

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
        "elapsed_s":  round(elapsed, 2),
        "load_s":     round(load_s, 3) if load_s is not None else None,
        "tok_per_s":  tps,
        "kv_log_mib": kv_mib,
        "returncode": r.returncode,
        "output_snippet": log[-400:] if log else "",
    }


# ── Main benchmark ────────────────────────────────────────────────────────────

def run_benchmark(
    llamacpp_bin: str,
    gguf_path: str,
    skip_8k: bool = False,
    nvme: bool = False,
) -> dict:
    cli  = find_cli(llamacpp_bin)
    bpt       = theoretical_kv_bpt()
    gguf_size = Path(gguf_path).stat().st_size if Path(gguf_path).exists() else 0
    use_mmap  = nvme   # mmap safe on NVMe; avoid on microSD/9p/eMMC

    print(f"\n{'='*66}")
    print("Mistral-7B on Orin Nano — modes that 'shouldn't work'")
    print(f"{'='*66}")
    print(f"Model:   {gguf_path}")
    print(f"Storage: {'NVMe M.2 (mmap enabled)' if nvme else 'microSD/eMMC (--no-mmap)'}")
    print(f"Size:    {gguf_size/1024**3:.2f} GB")
    print(f"Theoretical KV bytes/token: {bpt:,} B ({bpt/1024:.0f} KB)")
    print()

    modes = [
        ("A: full GPU",           32,  1024),
        ("A: full GPU 4K",        32,  4096),
        ("B: partial (ngl=22) 4K", 22,  4096),
        ("B: partial (ngl=22) 8K", 22,  8192),
    ]
    if skip_8k:
        modes = [m for m in modes if "8K" not in m[0]]

    results = {}
    for label, ngl, ctx in modes:
        pred = predicted_ctx(ngl)
        over = " ← over budget" if ctx > pred else ""
        print(f"  Running {label}  ctx={ctx:,}  ngl={ngl}  (predicted max {pred:,}){over}")

        row = run_llama(cli, gguf_path, ctx=ctx, ngl=ngl, use_mmap=use_mmap)
        time.sleep(2)

        kv_mib  = row["kv_log_mib"]
        act_bpt = int(kv_mib * 1024 * 1024 / ctx) if kv_mib else None
        err_pct = abs(act_bpt - bpt) / bpt * 100 if act_bpt else None

        tok_s  = f"{row['tok_per_s']:.2f} tok/s" if row["tok_per_s"] else "N/A"
        kv_s   = f"{kv_mib:.1f} MiB" if kv_mib else "N/A"
        err_s  = f"{err_pct:.1f}%" if err_pct is not None else "N/A"
        load_s = row.get("load_s")

        if load_s and gguf_size:
            bw_gbps = _storage_bw_gbps(gguf_size, load_s)
            bw_s = f"  load={load_s:.2f}s ({bw_gbps:.2f} GB/s)" if bw_gbps else f"  load={load_s:.2f}s"
        else:
            bw_s = ""

        print(f"    tok/s: {tok_s}  KV: {kv_s}{bw_s}")
        if err_pct is not None:
            verdict = "✓ PASS" if err_pct < 5 else ("~ close" if err_pct < 15 else "✗ check")
            print(f"    simulation error: {err_s}  ({verdict})")
        if row["returncode"] != 0:
            print(f"    ✗ OOM or error (returncode {row['returncode']})")
        print()

        row.update({
            "label": label, "ngl": ngl, "ctx": ctx,
            "predicted_max_ctx": pred, "actual_bpt": act_bpt,
            "err_pct": round(err_pct, 2) if err_pct else None,
            "storage_bw_gbps": round(_storage_bw_gbps(gguf_size, load_s), 3)
                if (load_s and gguf_size) else None,
        })
        results[label] = row

    _print_summary(results, bpt, nvme=nvme, gguf_size=gguf_size)
    return results


def _print_summary(results: dict, bpt: int, nvme: bool = False, gguf_size: int = 0):
    print(f"{'='*66}")
    print("SUMMARY")
    print(f"{'='*66}")
    print(f"  {'Mode':<26}  {'ctx':>6}  {'ngl':>4}  {'tok/s':>7}  {'KV MiB':>7}  "
          f"{'err%':>5}  {'load s':>6}  {'GB/s':>5}  status")
    print(f"  {'─'*26}  {'─'*6}  {'─'*4}  {'─'*7}  {'─'*7}  "
          f"{'─'*5}  {'─'*6}  {'─'*5}  {'─'*6}")
    for label, r in results.items():
        tps  = f"{r['tok_per_s']:.2f}"  if r.get("tok_per_s")   else "N/A"
        kv   = f"{r['kv_log_mib']:.1f}" if r.get("kv_log_mib")  else "N/A"
        err  = f"{r['err_pct']:.1f}"    if r.get("err_pct") is not None else "N/A"
        ld   = f"{r['load_s']:.2f}"     if r.get("load_s")       else "N/A"
        bw   = f"{r['storage_bw_gbps']:.2f}" if r.get("storage_bw_gbps") else "N/A"
        ok   = "✓ OK" if r["returncode"] == 0 else "✗ OOM"
        print(f"  {label:<26}  {r['ctx']:>6,}  {r['ngl']:>4}  {tps:>7}  "
              f"{kv:>7}  {err:>5}  {ld:>6}  {bw:>5}  {ok}")

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
            print("    ✓ Unified memory: CPU offload costs < 20% — nearly free context doubling")
        elif slowdown < 40:
            print("    ~ Moderate cost — still worthwhile for 2× context gain")
        else:
            print("    ~ Significant — but a discrete 6 GB GPU would be 5-10× worse here")

    # NVMe storage verdict
    if nvme:
        bw_samples = [r["storage_bw_gbps"] for r in results.values()
                      if r.get("storage_bw_gbps") is not None]
        if bw_samples:
            avg_bw = sum(bw_samples) / len(bw_samples)
            print()
            print(f"  NVMe storage bandwidth (implied by load_time):")
            print(f"    Measured:   {avg_bw:.2f} GB/s")
            print(f"    microSD:    ~0.09 GB/s  ({avg_bw/0.09:.0f}× faster)")
            print(f"    eMMC:       ~0.30 GB/s  ({avg_bw/0.30:.0f}× faster)")
            if avg_bw >= 1.5:
                print("    ✓ NVMe confirmed — cold load is no longer the bottleneck")
            elif avg_bw >= 0.5:
                print("    ~ PCIe Gen 2 speed — still 5× faster than eMMC")
            else:
                print("    ~ Lower than expected — check M.2 slot is PCIe, not SATA")

    print(f"{'='*66}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Mistral-7B on Orin Nano — full GPU vs partial offload benchmark"
    )
    p.add_argument("--llamacpp", required=True, help="path to llama.cpp build/bin/")
    p.add_argument("--gguf",     required=True, help="path to Mistral-7B Q4_K_M .gguf")
    p.add_argument("--nvme",     action="store_true",
                   help="model is on NVMe M.2 — enable mmap and measure storage bandwidth")
    p.add_argument("--skip-8k",  action="store_true",
                   help="skip the 8K context test (use if RAM is very tight)")
    p.add_argument("--stats-json", default=None)
    args = p.parse_args()

    results = run_benchmark(args.llamacpp, args.gguf,
                            skip_8k=args.skip_8k, nvme=args.nvme)

    if args.stats_json:
        Path(args.stats_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats_json).write_text(json.dumps(results, indent=2) + "\n")
        print(f"Results → {args.stats_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
