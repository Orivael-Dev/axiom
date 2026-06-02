"""Mistral-7B KV cache benchmark — Colab cells.

Validates that the analytic KV simulation (simulate_kv_context.py) matches
reality on a Colab T4 (15 GB VRAM) / A100 (40 GB VRAM).

Run each CELL in sequence.  Each cell is a standalone function you paste into
a Colab code cell and call on the last line.

Expected layout:
    /content/axiom/          ← this repo (Cell 1 clones it)
    /content/models/         ← GGUF model downloaded here (Cell 3)
    /content/llama.cpp/      ← built in Cell 4

Quick reference — simulation prediction for T4 (14 GB usable):
    Mistral-7B Q4_K_M (4.07 GB weights)
    → KV budget ≈ 4.97 GB  (50% of remaining 9.93 GB)
    → predicted max context ≈ 39,700 tokens
    → KV bytes/token: 131,072 B (= 128 KB)
    Benchmark goal: actual bytes/token within 5% of 131,072
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO       = Path("/content/axiom")
MODELS_DIR = Path("/content/models")
LLAMA_DIR  = Path("/content/llama.cpp")
LLAMA_CLI  = LLAMA_DIR / "build/bin/llama-cli"
RESULTS    = REPO / "results"

GGUF_REPO  = "bartowski/Mistral-7B-Instruct-v0.3-GGUF"
GGUF_FILE  = "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf"


# ════════════════════════════════════════════════════════════════════════════
# CELL 1  — GPU check + clone repo
# ════════════════════════════════════════════════════════════════════════════
def cell1_setup():
    """Paste into Colab cell 1 and run.  Checks GPU and clones the repo."""
    import subprocess, os

    # ── GPU info ──
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
         "--format=csv,noheader"],
        capture_output=True, text=True,
    )
    print("GPU:", result.stdout.strip())

    import torch
    p = torch.cuda.get_device_properties(0)
    print(f"PyTorch sees: {p.name}  SM {p.major}.{p.minor}  {p.total_memory/1024**3:.1f} GB")

    # ── clone repo ──
    repo = Path("/content/axiom")
    branch = "claude/srd-prototype-benchmark-JRtv1"   # files live on this branch, not main
    if not repo.is_dir():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch,
             "https://github.com/orivael-dev/axiom.git", str(repo)],
            check=True,
        )
    else:
        subprocess.run(["git", "-C", str(repo), "pull", "origin", branch], check=True)

    # ── master key ──
    if not os.environ.get("AXIOM_MASTER_KEY"):
        import secrets
        os.environ["AXIOM_MASTER_KEY"] = secrets.token_hex(32)
        print("AXIOM_MASTER_KEY set (random, session-only)")

    sys.path.insert(0, str(repo))
    print(f"\n✓ Repo ready at {repo}")


# ════════════════════════════════════════════════════════════════════════════
# CELL 2  — print simulation prediction for this GPU
# ════════════════════════════════════════════════════════════════════════════
def cell2_simulation_prediction():
    """Shows what the analytic model predicts before we run anything."""
    import torch
    sys.path.insert(0, str(REPO))
    from research.quant.simulate_kv_context import (
        MODELS, max_context_q4km, kv_bytes_per_token, Hardware
    )

    p     = torch.cuda.get_device_properties(0)
    total = p.total_memory / 1024**3
    usable = total * 0.93          # ~7% reserved by CUDA context + driver

    hw = Hardware(name=p.name, memory_gb=round(usable, 1))
    mistral = next(m for m in MODELS if "Mistral" in m.name)

    bpt       = kv_bytes_per_token(mistral)
    ctx_q4    = max_context_q4km(mistral, hw)
    kv_budget = (hw.memory_gb - mistral.q4km_gb) * 1024 * 0.50  # MiB

    print("=" * 62)
    print(f"Simulation prediction for {p.name}")
    print("=" * 62)
    print(f"  GPU VRAM:          {total:.1f} GB total  ({usable:.1f} GB usable)")
    print(f"  Model:             Mistral-7B Q4_K_M  ({mistral.q4km_gb} GB)")
    print(f"  KV budget (50%):   {kv_budget:.0f} MiB")
    print()
    print(f"  KV bytes/token:    {bpt:,} B  = {bpt/1024:.0f} KB")
    print(f"    formula:  {mistral.n_layers} layers × 2 × {mistral.n_kv_heads} KV heads"
          f" × {mistral.head_dim} head_dim × 2 bytes FP16")
    print()
    print(f"  Predicted max ctx: {ctx_q4:,} tokens  ({ctx_q4/1000:.0f}K)")
    print()
    print("  KV size at test context lengths:")
    for ctx in [256, 512, 1024, 2048, 4096, 8192, 16384]:
        mib = bpt * ctx / 1024**2
        fits = "✓" if mib < kv_budget else "✗ OOM"
        print(f"    {ctx:6d} tokens → {mib:6.1f} MiB  {fits}")
    print("=" * 62)
    print("\n  GOAL: actual bytes/token from llama.cpp < 5% off from", f"{bpt:,}")


# ════════════════════════════════════════════════════════════════════════════
# CELL 3  — download Mistral-7B Q4_K_M GGUF  (~4 GB, ~3 min)
# ════════════════════════════════════════════════════════════════════════════
def cell3_download_model():
    """Downloads Mistral-7B-Instruct-v0.3 Q4_K_M from HuggingFace (~4 GB)."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    gguf_path = MODELS_DIR / GGUF_FILE

    if gguf_path.is_file():
        size_gb = gguf_path.stat().st_size / 1024**3
        print(f"✓ Already downloaded: {gguf_path}  ({size_gb:.2f} GB)")
        return gguf_path

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "huggingface-hub"],
        check=True,
    )
    subprocess.run(
        ["huggingface-cli", "download", GGUF_REPO, GGUF_FILE,
         "--local-dir", str(MODELS_DIR)],
        check=True,
    )
    size_gb = gguf_path.stat().st_size / 1024**3
    print(f"✓ Downloaded: {gguf_path}  ({size_gb:.2f} GB)")
    return gguf_path


# ════════════════════════════════════════════════════════════════════════════
# CELL 4  — build llama.cpp  (~3 min on T4)
# ════════════════════════════════════════════════════════════════════════════
def cell4_build_llamacpp():
    """Clones and builds llama.cpp with CUDA support."""
    if LLAMA_CLI.is_file():
        print(f"✓ llama-cli already built at {LLAMA_CLI}")
        return

    import torch
    p = torch.cuda.get_device_properties(0)
    arch = f"{p.major}{p.minor}"
    print(f"Building for {p.name}  SM {p.major}.{p.minor} → CUDA arch {arch}")

    if not LLAMA_DIR.is_dir():
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/ggerganov/llama.cpp.git", str(LLAMA_DIR)],
            check=True,
        )

    subprocess.run(
        ["cmake", "-B", str(LLAMA_DIR / "build"), "-S", str(LLAMA_DIR),
         "-DGGML_CUDA=ON",
         f"-DCMAKE_CUDA_ARCHITECTURES={arch}",
         "-DCMAKE_BUILD_TYPE=Release"],
        check=True,
    )
    nproc = subprocess.check_output(["nproc"]).decode().strip()
    subprocess.run(
        ["cmake", "--build", str(LLAMA_DIR / "build"),
         "-j", nproc, "-t", "llama-cli"],
        check=True,
    )
    assert LLAMA_CLI.is_file(), f"Build failed — {LLAMA_CLI} not found"
    print(f"\n✓ llama-cli ready at {LLAMA_CLI}")


# ════════════════════════════════════════════════════════════════════════════
# CELL 5  — run KV benchmark across context sizes
# ════════════════════════════════════════════════════════════════════════════
def cell5_kv_benchmark(ctx_sizes=None):
    """Runs bench_mistral_kv.py and compares actual KV bytes/token to theory."""
    if ctx_sizes is None:
        # T4 can comfortably handle up to ~16K tokens; cap there
        import torch
        total_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
        if total_gb >= 35:       # A100 40 GB
            ctx_sizes = [512, 1024, 2048, 4096, 8192, 16384, 32768]
        elif total_gb >= 20:     # A10G 24 GB
            ctx_sizes = [512, 1024, 2048, 4096, 8192, 16384]
        else:                    # T4 15 GB
            ctx_sizes = [512, 1024, 2048, 4096, 8192]

    gguf_path = MODELS_DIR / GGUF_FILE
    assert gguf_path.is_file(), "Model not found — run cell3 first"
    assert LLAMA_CLI.is_file(), "llama-cli not found — run cell4 first"
    RESULTS.mkdir(parents=True, exist_ok=True)

    out_json = RESULTS / "mistral_kv_bench.json"
    ctx_args = [str(c) for c in ctx_sizes]

    subprocess.run(
        [sys.executable, "-m", "research.quant.bench_mistral_kv",
         "--llamacpp", str(LLAMA_DIR / "build/bin"),
         "--gguf",     str(gguf_path),
         "--ctx-sizes", *ctx_args,
         "--stats-json", str(out_json)],
        cwd=REPO, check=True,
    )

    results = json.loads(out_json.read_text())
    return results


# ════════════════════════════════════════════════════════════════════════════
# CELL 6  — comparison table + verdict
# ════════════════════════════════════════════════════════════════════════════
def cell6_comparison(results=None):
    """Prints side-by-side simulation vs actual table and a pass/fail verdict."""
    if results is None:
        out_json = RESULTS / "mistral_kv_bench.json"
        assert out_json.is_file(), "No results — run cell5 first"
        results = json.loads(out_json.read_text())

    sys.path.insert(0, str(REPO))
    from research.quant.simulate_kv_context import (
        MODELS, kv_bytes_per_token, max_context_q4km, Hardware
    )
    import torch
    p      = torch.cuda.get_device_properties(0)
    usable = p.total_memory / 1024**3 * 0.93
    hw     = Hardware(name=p.name, memory_gb=round(usable, 1))
    mistral = next(m for m in MODELS if "Mistral" in m.name)
    theory  = kv_bytes_per_token(mistral)
    pred_ctx = max_context_q4km(mistral, hw)

    print("=" * 68)
    print(f"Mistral-7B KV Benchmark — {p.name}")
    print("=" * 68)
    print(f"  Predicted max context: {pred_ctx:,} tokens  ({pred_ctx/1000:.0f}K)")
    print(f"  Theoretical KV B/tok:  {theory:,}  ({theory/1024:.0f} KB)")
    print()
    print(f"  {'ctx':>6}  {'KV (MiB)':>10}  {'actual B/tok':>14}  "
          f"{'theory B/tok':>14}  {'error':>7}")
    print(f"  {'─'*6}  {'─'*10}  {'─'*14}  {'─'*14}  {'─'*7}")

    errors = []
    for r in results:
        kv   = f"{r['kv_log_mib']:.2f}" if r.get("kv_log_mib") is not None else "N/A"
        act  = f"{r['actual_bpt']:,}"   if r.get("actual_bpt") is not None else "N/A"
        err  = f"{r['err_pct']:.1f}%"  if r.get("err_pct")    is not None else "N/A"
        print(f"  {r['ctx_size']:>6}  {kv:>10}  {act:>14}  {theory:>14,}  {err:>7}")
        if r.get("err_pct") is not None:
            errors.append(r["err_pct"])

    print()
    if errors:
        avg = sum(errors) / len(errors)
        max_e = max(errors)
        print(f"  Average error: {avg:.1f}%   Max error: {max_e:.1f}%")
        print()
        if avg < 5:
            print("  ✓ PASS — simulation is accurate (avg < 5% error)")
            print("    The formula n_layers × 2 × n_kv_heads × head_dim × 2")
            print("    correctly predicts real llama.cpp KV memory.")
        elif avg < 15:
            print("  ~ CLOSE — avg < 15%; likely KV cache dtype differs")
            print("    (llama.cpp may use F32 KV instead of F16 — 2× expected)")
        else:
            print("  ✗ SIGNIFICANT deviation — check model variant or --ngl flag")
    else:
        print("  Could not extract KV size from logs.")
        print("  Check: llama-cli 2>&1 | grep -i 'kv self'")
    print("=" * 68)


# ════════════════════════════════════════════════════════════════════════════
# CELL 7  — download results JSON
# ════════════════════════════════════════════════════════════════════════════
def cell7_download():
    from google.colab import files as colab_files  # type: ignore
    out_json = RESULTS / "mistral_kv_bench.json"
    if out_json.is_file():
        print(f"↓ {out_json.name}")
        colab_files.download(str(out_json))
    else:
        print("No results file found — run cell5 first")


# ════════════════════════════════════════════════════════════════════════════
# Colab cell snippets  (copy each block into a separate Colab cell)
# ════════════════════════════════════════════════════════════════════════════
CELL_SNIPPETS = """\
# ─── CELL 1: setup ───────────────────────────────────────────────────────────
# The pipeline files live on the feature branch, not main — clone that branch.
import subprocess, sys
subprocess.run(["git", "clone", "--depth", "1",
    "--branch", "claude/srd-prototype-benchmark-JRtv1",
    "https://github.com/orivael-dev/axiom.git", "/content/axiom"], check=True)
sys.path.insert(0, "/content/axiom")
from research.quant.colab_mistral_kv_bench import *
cell1_setup()

# ─── CELL 2: simulation prediction ───────────────────────────────────────────
cell2_simulation_prediction()

# ─── CELL 3: download Mistral-7B Q4_K_M (~4 GB) ──────────────────────────────
cell3_download_model()

# ─── CELL 4: build llama.cpp with CUDA (~3 min) ───────────────────────────────
cell4_build_llamacpp()

# ─── CELL 5: KV benchmark (context sizes auto-selected per GPU) ───────────────
results = cell5_kv_benchmark()

# ─── CELL 6: comparison table + pass/fail verdict ────────────────────────────
cell6_comparison(results)

# ─── CELL 7: download results JSON ───────────────────────────────────────────
cell7_download()
"""

if __name__ == "__main__":
    print(CELL_SNIPPETS)
