"""Complete Colab benchmark script — SRD E3 real-pack end-to-end.

Run each STEP as its own Colab cell, top to bottom.
Steps 1-2 (install + clone) must run first.
Steps 3-5 are sequential.
Steps 6-7 (llama.cpp) are optional — skip if you only want the PyTorch numbers.

Assumed file layout after Step 2:
    /content/axiom/       ← this repo
    /content/axiom/artifacts/   ← created automatically
    AXIOM_MASTER_KEY set in env (Step 2 handles this)
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
from pathlib import Path

# ── shared config ───────────────────────────────────────────────────────────
REPO      = Path("/content/axiom")
AXM_PATH  = Path("/content/tinyllama_srd_7bpw_REAL.axm")
RESULTS   = REPO / "results"
MODEL     = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
PROMPT    = "Write a Python function to reverse a linked list."
TOKENS    = 80
N_RUNS    = 3     # run 1 = cold; runs 2-3 averaged for warm stats

LLAMA_DIR = Path("/content/llama.cpp")
LLAMA_CLI = LLAMA_DIR / "build/bin/llama-cli"
GGUF_PATH = REPO / "artifacts/tinyllama_q4km.gguf"


# ════════════════════════════════════════════════════════════════════════════
# STEP 3  — pack TinyLlama with real-pack + run all validation checks
#           (calls the existing colab_realpack_validate.py)
# ════════════════════════════════════════════════════════════════════════════
def step3_pack_and_validate():
    assert REPO.is_dir(), "repo missing — run Step 2 first"
    sys.path.insert(0, str(REPO))
    os.chdir(REPO)

    # The validate script sets AXM_OUT = /content/tinyllama_srd_7bpw_REAL.axm
    # and exports the archive.  We exec it so its globals (pack_stats etc.)
    # are available here for the summary.
    validate = REPO / "research/quant/colab_realpack_validate.py"
    exec(compile(validate.read_text(), str(validate), "exec"), {"__name__": "__main__"})  # noqa: S102
    print("\n✓ Step 3 complete — .axm packed and validated")


# ════════════════════════════════════════════════════════════════════════════
# STEP 4  — PyTorch benchmark (CUDA path, n_runs=3)
# ════════════════════════════════════════════════════════════════════════════
def step4_pytorch_benchmark():
    assert AXM_PATH.is_file(), f".axm not found at {AXM_PATH} — run Step 3 first"
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_json = RESULTS / "colab_pytorch_benchmark.json"

    print("=" * 60)
    print("STEP 4: PyTorch (CUDA) benchmark")
    print("=" * 60)

    subprocess.run(
        [sys.executable, "axm_cli.py", "run", str(AXM_PATH),
         "--device", "cuda",
         "--prompt", PROMPT,
         "--tokens", str(TOKENS),
         "--n-runs", str(N_RUNS),
         "--stats-json", str(out_json)],
        cwd=REPO, check=True,
    )

    stats = json.loads(out_json.read_text())
    runs  = stats.get("runs", [])
    print(f"\n── PyTorch (CUDA) ──────────────────────────────────")
    print(f"  warm avg TTFT  : {stats['timing']['avg_ttft_ms']:.0f} ms")
    print(f"  warm avg tok/s : {stats['timing']['avg_tok_per_s']:.1f}")
    print(f"  peak RSS       : {stats['memory']['peak_rss_mb']} MB")
    if stats["memory"].get("vram_mb"):
        print(f"  VRAM           : {stats['memory']['vram_mb']:.0f} MB")
    print(f"  runs           :")
    for i, r in enumerate(runs):
        label = "cold" if i == 0 else "warm"
        print(f"    run {i+1} ({label}): {r['ttft_ms']:.0f} ms TTFT  "
              f"{r['tok_per_s']:.1f} tok/s  {r['peak_rss_mb']} MB RSS")
    print(f"\n  results → {out_json}")
    return stats


# ════════════════════════════════════════════════════════════════════════════
# STEP 5  — download results so far (PyTorch JSON + .axm)
# ════════════════════════════════════════════════════════════════════════════
def step5_download_pytorch_results():
    from google.colab import files as colab_files  # type: ignore

    to_dl = [
        RESULTS / "colab_pytorch_benchmark.json",
        AXM_PATH,
    ]
    for f in to_dl:
        if f.exists():
            size_mb = f.stat().st_size / 1024 ** 2
            print(f"↓ {f.name}  ({size_mb:.0f} MB)")
            colab_files.download(str(f))
        else:
            print(f"⚠  {f.name} not found — did the previous step run?")


# ════════════════════════════════════════════════════════════════════════════
# STEP 6  — build llama.cpp (one-time, ~3 min on Colab T4)
#           SM 7.5 = GTX 1660 or Colab T4
# ════════════════════════════════════════════════════════════════════════════
def step6_build_llamacpp():
    print("=" * 60)
    print("STEP 6: build llama.cpp")
    print("=" * 60)

    import torch  # noqa: F401 (confirm torch CUDA before building)

    if not LLAMA_DIR.is_dir():
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/ggerganov/llama.cpp.git", str(LLAMA_DIR)],
            check=True,
        )
    else:
        subprocess.run(["git", "-C", str(LLAMA_DIR), "pull"], check=True)

    # Detect SM version automatically; fall back to 75 (T4 / GTX 1660)
    try:
        import torch
        p = torch.cuda.get_device_properties(0)
        arch = f"{p.major}{p.minor}"
        print(f"Detected GPU: {p.name}  SM {p.major}.{p.minor} → CUDA_ARCH={arch}")
    except Exception:
        arch = "75"
        print(f"Could not detect GPU — defaulting to SM 7.5 (T4 / GTX 1660)")

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
         "-j", nproc, "-t", "llama-cli", "llama-quantize"],
        check=True,
    )
    assert LLAMA_CLI.is_file(), f"llama-cli not found at {LLAMA_CLI}"
    print(f"\n✓ llama-cli ready at {LLAMA_CLI}")


# ════════════════════════════════════════════════════════════════════════════
# STEP 7  — extract .axm → GGUF + run llama.cpp benchmark
# ════════════════════════════════════════════════════════════════════════════
def step7_gguf_benchmark():
    assert AXM_PATH.is_file(), f".axm not found — run Step 3 first"
    assert LLAMA_CLI.is_file(), f"llama-cli not found — run Step 6 first"
    RESULTS.mkdir(parents=True, exist_ok=True)
    GGUF_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_json = RESULTS / "colab_gguf_benchmark.json"

    print("=" * 60)
    print("STEP 7: GGUF extract + llama.cpp benchmark")
    print("=" * 60)

    if not GGUF_PATH.is_file():
        print("Extracting .axm → GGUF (CPU, one-time ~60s)...")
        subprocess.run(
            [sys.executable, "axm_cli.py", "extract", str(AXM_PATH),
             "--gguf-out", str(GGUF_PATH),
             "--llamacpp", str(LLAMA_DIR),
             "--device", "cpu"],
            cwd=REPO, check=True,
        )
        print(f"✓ GGUF: {GGUF_PATH.stat().st_size / 1024**2:.0f} MB")
    else:
        print(f"✓ GGUF already exists: {GGUF_PATH.stat().st_size / 1024**2:.0f} MB")

    subprocess.run(
        [sys.executable, "-m", "research.quant.bench_llamacpp_infer",
         "--gguf", str(GGUF_PATH),
         "--llama-cli", str(LLAMA_CLI),
         "--ngl", "99",
         "--n-runs", str(N_RUNS),
         "--stats-json", str(out_json)],
        cwd=REPO, check=True,
    )

    stats = json.loads(out_json.read_text())
    runs  = stats.get("runs", [])
    print(f"\n── llama.cpp GGUF Q4_K_M (CUDA) ───────────────────")
    print(f"  warm avg TTFT  : {stats['timing']['avg_ttft_ms']:.0f} ms")
    print(f"  warm avg tok/s : {stats['timing']['avg_tok_per_s']:.1f}")
    print(f"  peak RSS       : {stats['memory']['peak_rss_mb']} MB")
    if stats["memory"].get("vram_mb"):
        print(f"  VRAM           : {stats['memory']['vram_mb']:.0f} MB")
    print(f"  runs           :")
    for i, r in enumerate(runs):
        label = "cold" if i == 0 else "warm"
        print(f"    run {i+1} ({label}): {r['ttft_ms']:.0f} ms TTFT  "
              f"{r['tok_per_s']:.1f} tok/s")
    print(f"\n  results → {out_json}")

    # download both JSON results
    from google.colab import files as colab_files  # type: ignore
    for f in [RESULTS / "colab_pytorch_benchmark.json", out_json]:
        if f.exists():
            print(f"↓ {f.name}")
            colab_files.download(str(f))
    return stats


# ════════════════════════════════════════════════════════════════════════════
# direct run  — execute all steps in sequence
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    step3_pack_and_validate()
    step4_pytorch_benchmark()
    step5_download_pytorch_results()
    # step6_build_llamacpp()   # uncomment to also run llama.cpp path
    # step7_gguf_benchmark()
