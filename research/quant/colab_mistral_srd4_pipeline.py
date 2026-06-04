"""Mistral-7B SRD-4 → .axm → GGUF Q4_K_M pipeline — Colab cells.

Paste each CELL block into a separate Colab code cell and run top to bottom.

What this produces
------------------
  mistral_srd4.axm          — signed .axm container (W4-only SRD, ~4.5 bpw)
  mistral_srd4_q4km.gguf    — GGUF Q4_K_M ready for llama.cpp (~4.07 GB)

Hardware requirements
---------------------
  Colab A100 (40 GB):  easiest — model fits entirely in VRAM
  Colab Pro T4 (15 GB): device_map=auto splits GPU + system RAM — works
  Standard T4 (15 GB):  system RAM is 12.7 GB — tight, may OOM during pack
  Recommended: Runtime → Change runtime type → A100 (if available)

Time estimates (T4)
-------------------
  Cell 2 (pack):    ~20–30 min  — loads 13.5 GB FP16, quantizes 288 layers
  Cell 3 (verify):  ~10 s
  Cell 4 (extract): ~15 min     — reconstructs FP16, runs convert + quantize
  Cell 5 (download): ~2 min     — 4 GB download
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO      = Path("/content/axiom")
REPO_URL  = "https://github.com/orivael-dev/axiom.git"
REPO_BRANCH = "claude/srd-prototype-benchmark-JRtv1"   # files live on this branch, not main
OUT_DIR   = Path("/content")
AXM_PATH  = OUT_DIR / "mistral_srd4.axm"
GGUF_PATH = OUT_DIR / "mistral_srd4_q4km.gguf"
LLAMA_DIR = Path("/content/llama.cpp")
LLAMA_CLI = LLAMA_DIR / "build/bin/llama-cli"
RESULTS   = REPO / "results"
KEY_FILE  = OUT_DIR / "axiom_master.key"

# Business use: set SRD_MODEL_ID to your own HF model ID or local model path.
# Example: os.environ["SRD_MODEL_ID"] = "your-org/your-model"
#          os.environ["SRD_MODEL_ID"] = "/workspace/my_finetuned_model"
MODEL_ID  = os.environ.get("SRD_MODEL_ID", "mistralai/Mistral-7B-Instruct-v0.3")


# ════════════════════════════════════════════════════════════════════════════
# CELL 1 — GPU check + clone repo  (~30 s)
# ════════════════════════════════════════════════════════════════════════════
def cell1_setup():
    import torch

    p = torch.cuda.get_device_properties(0)
    vram_gb = p.total_memory / 1024**3
    print(f"GPU:  {p.name}  {vram_gb:.1f} GB VRAM  SM {p.major}.{p.minor}")

    ram_gb = 0
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / 1024**3
        print(f"RAM:  {ram_gb:.1f} GB system")
    except ImportError:
        pass

    if vram_gb < 14 and ram_gb < 20:
        print("\n  ⚠  T4 + < 20 GB RAM — pack may OOM during load.")
        print("  Recommended: Runtime → Change runtime type → A100 or T4 High-RAM")
    else:
        print("  ✓ Memory looks sufficient for Mistral-7B pack")

    if not REPO.is_dir():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", REPO_BRANCH,
             REPO_URL, str(REPO)],
            check=True,
        )
    else:
        subprocess.run(["git", "-C", str(REPO), "pull", "origin", REPO_BRANCH], check=True)

    # Persist AXIOM_MASTER_KEY across cell re-runs within the same session.
    # A new random key would invalidate any .axm signatures from a prior cell2 run.
    import secrets
    if KEY_FILE.is_file() and not os.environ.get("AXIOM_MASTER_KEY"):
        os.environ["AXIOM_MASTER_KEY"] = KEY_FILE.read_text().strip()
        print("AXIOM_MASTER_KEY restored from session key file")
    elif not os.environ.get("AXIOM_MASTER_KEY"):
        key = secrets.token_hex(32)
        os.environ["AXIOM_MASTER_KEY"] = key
        KEY_FILE.write_text(key)
        print("AXIOM_MASTER_KEY generated and saved to session key file")
    else:
        print("AXIOM_MASTER_KEY already set in environment")

    print(f"  Model: {MODEL_ID}")
    print("  (set SRD_MODEL_ID env var to use a different model)")

    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "transformers", "accelerate", "psutil"], check=True)

    sys.path.insert(0, str(REPO))
    RESULTS.mkdir(parents=True, exist_ok=True)
    print(f"\n✓ Ready.  Repo: {REPO}")


# ════════════════════════════════════════════════════════════════════════════
# CELL 2 — Pack: Mistral-7B FP16 → SRD-4 .axm  (~20–30 min on T4)
#
# SRD-4 = W4 base only (top_k_pct=0), no sparse D8 residual.
# ~4.5 bpw theoretical.  The .axm signs the quantized weights so
# axm verify can prove provenance before any deployment.
# ════════════════════════════════════════════════════════════════════════════
def cell2_pack():
    import torch
    sys.path.insert(0, str(REPO))

    print("=" * 60)
    print("CELL 2: Pack Mistral-7B FP16 → SRD-4 .axm")
    print("=" * 60)
    print(f"  Model:  {MODEL_ID}")
    print(f"  Mode:   SRD-4 (W4 base only, top_k_pct=0, ~4.5 bpw)")
    print(f"  Output: {AXM_PATH}")
    print()

    # Show memory before load
    import psutil
    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    ram_avail = psutil.virtual_memory().available / 1024**3
    print(f"  VRAM: {vram_gb:.1f} GB   Available RAM: {ram_avail:.1f} GB")
    print("  pack_model will use device_map=auto if model > 80% VRAM")
    print()

    t0 = time.time()
    subprocess.run(
        [sys.executable, "axm_cli.py", "pack",
         "--model",    MODEL_ID,
         "--srd4",
         "--output",   str(AXM_PATH),
         "--stats-json", str(RESULTS / "mistral_pack.json")],
        cwd=REPO, check=True,
    )
    elapsed = time.time() - t0

    size_gb = AXM_PATH.stat().st_size / 1024**3
    stats   = json.loads((RESULTS / "mistral_pack.json").read_text())
    print(f"\n✓ Packed in {elapsed/60:.1f} min")
    print(f"  .axm size : {size_gb:.2f} GB")
    print(f"  bpw       : {stats.get('quant', {}).get('bpw', 'N/A')}")
    print(f"  fingerprint: {stats.get('fingerprint', 'N/A')}")
    return stats


# ════════════════════════════════════════════════════════════════════════════
# CELL 3 — Verify: check every HMAC proof in the container  (~10 s)
# ════════════════════════════════════════════════════════════════════════════
def cell3_verify():
    assert AXM_PATH.is_file(), f".axm not found — run cell2 first"
    sys.path.insert(0, str(REPO))

    print("=" * 60)
    print("CELL 3: axm verify")
    print("=" * 60)

    result = subprocess.run(
        [sys.executable, "axm_cli.py", "verify", str(AXM_PATH)],
        cwd=REPO, capture_output=True, text=True,
    )
    output = json.loads(result.stdout)
    print(json.dumps(output, indent=2))

    if not output.get("verified"):
        raise RuntimeError("Verification FAILED — do not proceed to extract")

    print(f"\n✓ Verified  ({output['proofs_checked']} proofs)")
    print(f"  fingerprint: {output['fingerprint']}")
    return output


# ════════════════════════════════════════════════════════════════════════════
# CELL 4 — Extract: .axm → reconstruct FP16 → GGUF Q4_K_M  (~15 min)
#
# Requires llama.cpp to be built (cell4b below) the first time.
# The extract step:
#   1. Reconstructs FP16 weights from SRD-4 W4 base
#   2. Saves as a temp HF checkpoint
#   3. Runs convert_hf_to_gguf.py → F16 GGUF
#   4. Runs llama-quantize → Q4_K_M GGUF (~4.07 GB)
# ════════════════════════════════════════════════════════════════════════════
def cell4a_build_llamacpp():
    """Install llama.cpp — downloads pre-built CUDA binary (fast) then clones repo for Python scripts."""
    if LLAMA_CLI.is_file():
        print(f"✓ llama-cli already at {LLAMA_CLI}")
        _ensure_llama_repo()
        return

    print("Setting up llama.cpp...")
    _ensure_llama_repo()

    # Try pre-built binary first — avoids 15-min cmake CUDA compile
    if _download_prebuilt_llamacpp():
        return

    # Fall back to source build (capped at 4 jobs to avoid hanging)
    _build_llamacpp_source()


def _ensure_llama_repo():
    """Clone llama.cpp repo (needed for convert_hf_to_gguf.py Python script)."""
    if not LLAMA_DIR.is_dir():
        print("  Cloning llama.cpp repo (Python scripts)...")
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/ggerganov/llama.cpp.git", str(LLAMA_DIR)],
            check=True,
        )


def _download_prebuilt_llamacpp() -> bool:
    """Download pre-built CUDA binary from GitHub releases. Returns True on success."""
    import io, json, urllib.request, zipfile
    import torch

    cuda_ver = torch.version.cuda or ""          # e.g. "12.2" or "12.4"
    major_minor = ".".join(cuda_ver.split(".")[:2])
    print(f"  Looking for pre-built llama.cpp binary (CUDA {major_minor})...")

    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest",
            headers={"User-Agent": "axiom-srd-pipeline/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            release = json.loads(r.read())
    except Exception as e:
        print(f"  GitHub API unavailable: {e}")
        return False

    assets = release.get("assets", [])

    # Prefer exact CUDA match, fall back to any ubuntu CUDA binary
    def score(a: dict) -> int:
        n = a["name"]
        if "ubuntu-x64" not in n or "cuda" not in n:
            return 0
        if f"cu{major_minor}" in n:
            return 2
        return 1

    best = max(assets, key=score, default=None)
    if not best or score(best) == 0:
        print("  No pre-built CUDA binary found in latest release.")
        return False

    size_mb = best["size"] / 1024**2
    print(f"  Downloading {best['name']}  ({size_mb:.0f} MB)...")

    try:
        with urllib.request.urlopen(best["browser_download_url"], timeout=180) as r:
            data = r.read()
    except Exception as e:
        print(f"  Download failed: {e}")
        return False

    build_bin = LLAMA_DIR / "build" / "bin"
    build_bin.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            extracted = []
            for member in z.namelist():
                stem = Path(member).name
                if stem in ("llama-cli", "llama-quantize"):
                    dest = build_bin / stem
                    dest.write_bytes(z.read(member))
                    dest.chmod(0o755)
                    extracted.append(stem)
    except Exception as e:
        print(f"  Extraction failed: {e}")
        return False

    if LLAMA_CLI.is_file():
        print(f"  ✓ Pre-built binary ready: {', '.join(extracted)}")
        print(f"✓ llama-cli ready (pre-built — skipped cmake compile)")
        return True

    print("  Pre-built archive did not contain llama-cli.")
    return False


def _build_llamacpp_source():
    """Build llama.cpp from source — fallback when pre-built binary is unavailable."""
    import torch
    p    = torch.cuda.get_device_properties(0)
    arch = f"{p.major}{p.minor}"
    print(f"  Building from source for {p.name} SM {p.major}.{p.minor} (this takes ~10 min)...")

    subprocess.run(
        ["cmake", "-B", str(LLAMA_DIR / "build"), "-S", str(LLAMA_DIR),
         "-DGGML_CUDA=ON", f"-DCMAKE_CUDA_ARCHITECTURES={arch}",
         "-DCMAKE_BUILD_TYPE=Release"],
        check=True,
    )
    # Cap at 4 jobs — nproc (~12) saturates Colab CPU and causes hangs
    subprocess.run(
        ["cmake", "--build", str(LLAMA_DIR / "build"),
         "-j", "4", "--target", "llama-cli", "llama-quantize"],
        check=True,
        timeout=900,   # 15 min hard limit
    )
    print("✓ llama-cli ready (built from source)")


def cell4b_extract():
    assert AXM_PATH.is_file(),  "Run cell2 first (pack)"
    assert LLAMA_CLI.is_file(), "Run cell4a first (build llama.cpp)"
    sys.path.insert(0, str(REPO))

    print("=" * 60)
    print("CELL 4b: Extract .axm → GGUF Q4_K_M")
    print("=" * 60)
    print(f"  Input:  {AXM_PATH}  ({AXM_PATH.stat().st_size/1024**3:.2f} GB)")
    print(f"  Output: {GGUF_PATH}")
    print(f"  Steps:  reconstruct FP16 → convert → Q4_K_M quantize")
    print()

    t0 = time.time()
    subprocess.run(
        [sys.executable, "axm_cli.py", "extract", str(AXM_PATH),
         "--gguf-out",  str(GGUF_PATH),
         "--llamacpp",  str(LLAMA_DIR),
         "--quant",     "Q4_K_M",
         "--device",    "cpu",
         "--stats-json", str(RESULTS / "mistral_extract.json")],
        cwd=REPO, check=True,
    )
    elapsed = time.time() - t0

    size_gb = GGUF_PATH.stat().st_size / 1024**3
    stats   = json.loads((RESULTS / "mistral_extract.json").read_text())
    print(f"\n✓ Extracted in {elapsed/60:.1f} min")
    print(f"  GGUF size   : {size_gb:.2f} GB")
    print(f"  quant type  : Q4_K_M")
    print(f"  fingerprint : {stats.get('axm_fingerprint', 'N/A')}")
    return stats


# ════════════════════════════════════════════════════════════════════════════
# CELL 5 — Quick generation test  (~30 s)  [OPTIONAL]
#
# Validates the GGUF produces coherent output before download.
# Skip this cell for production runs — it does not affect the output files.
# Set SKIP_SMOKE_TEST=1 to bypass the guard.
# ════════════════════════════════════════════════════════════════════════════
def cell5_smoke_test():
    if os.environ.get("SKIP_SMOKE_TEST"):
        print("SKIP_SMOKE_TEST set — skipping smoke test")
        return None
    assert GGUF_PATH.is_file(), "Run cell4b first"

    print("=" * 60)
    print("CELL 5: Quick generation test")
    print("=" * 60)

    result = subprocess.run(
        [str(LLAMA_CLI), "-m", str(GGUF_PATH),
         "--ngl", "99",
         "--ctx-size", "512",
         "--n-predict", "64",
         "--log-disable",
         "--prompt", "Explain what makes the Mistral-7B architecture efficient:"],
        capture_output=True, text=True, timeout=120,
    )
    log = result.stdout + result.stderr

    import re
    tps_m = re.search(r"([\d.]+)\s*tokens per second", log)
    tps   = float(tps_m.group(1)) if tps_m else None

    print(log[-800:])
    if tps:
        print(f"\n  tok/s: {tps:.2f}")
    print("✓ Smoke test complete")
    return tps


# ════════════════════════════════════════════════════════════════════════════
# CELL 6 — Download .axm + GGUF
# ════════════════════════════════════════════════════════════════════════════
def cell6_download():
    from google.colab import files as colab_files  # type: ignore

    for f, label in [
        (AXM_PATH,  "signed .axm"),
        (GGUF_PATH, "GGUF Q4_K_M"),
        (RESULTS / "mistral_pack.json",    "pack stats"),
        (RESULTS / "mistral_extract.json", "extract stats"),
    ]:
        if Path(f).is_file():
            size = Path(f).stat().st_size / 1024**3
            print(f"↓ {Path(f).name}  ({size:.2f} GB)  — {label}")
            colab_files.download(str(f))
        else:
            print(f"  ⚠ {Path(f).name} not found")


# ════════════════════════════════════════════════════════════════════════════
# Colab cell snippets — copy each block into a separate Colab cell
# ════════════════════════════════════════════════════════════════════════════
CELL_SNIPPETS = """\
# ── CELL 1: GPU check + clone repo (~30 s) ───────────────────────────────────
# The pipeline files live on the feature branch, not main — clone that branch.
import subprocess, sys
subprocess.run(["git", "clone", "--depth", "1",
    "--branch", "claude/srd-prototype-benchmark-JRtv1",
    "https://github.com/orivael-dev/axiom.git", "/content/axiom"], check=True)
sys.path.insert(0, "/content/axiom")
from research.quant.colab_mistral_srd4_pipeline import *
cell1_setup()

# ── CELL 2: Pack Mistral-7B FP16 → SRD-4 .axm (~20–30 min) ──────────────────
cell2_pack()

# ── CELL 3: Verify every HMAC proof in the .axm (~10 s) ──────────────────────
cell3_verify()

# ── CELL 4a: Build llama.cpp with CUDA (~3 min, skip if already built) ────────
cell4a_build_llamacpp()

# ── CELL 4b: Extract .axm → reconstruct FP16 → GGUF Q4_K_M (~15 min) ─────────
cell4b_extract()

# ── CELL 5 (OPTIONAL): Quick generation test on GPU (~30 s) ──────────────────
# Core pipeline ends at Cell 4b. Cell 5 validates output quality before download.
# Skip for production runs: just don't run this cell, or set SKIP_SMOKE_TEST=1.
cell5_smoke_test()

# ── CELL 6: Download .axm + GGUF ─────────────────────────────────────────────
cell6_download()
"""

if __name__ == "__main__":
    print(CELL_SNIPPETS)
