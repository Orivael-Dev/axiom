"""SRD re-quantization pipeline — 3 models in one Colab notebook.

Paste each CELL block into a separate Colab code cell and run top to bottom.

Models covered
--------------
  1. Qwen2.5-Coder-0.5B-Instruct  (FP16 → SRD4 → Q4_K_M GGUF)
  2. TinyLlama-1.1B-Chat-v1.0     (re-do: was mislabelled Q4_K_M, now real SRD)
  3. Mistral-7B-Instruct-v0.3     (re-extract from existing .axiom on Drive)

Hardware recommendations
------------------------
  Models 1+2 (≤1.1B): Standard T4 (15 GB) is fine
  Model 3 (7B)       : A100 or T4 High-RAM strongly recommended
  Runtime → Change runtime type → GPU → A100

Output files (saved to Google Drive under /content/drive/MyDrive/srd_output/)
  qwen25_coder_0p5b_srd4_q4km.gguf
  tinyllama_1b_srd4_q4km.gguf
  mistral_srd4_q4km_v2.gguf
"""
from __future__ import annotations
import os, subprocess, sys, time, shutil
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
REPO       = Path("/content/axiom")
REPO_URL   = "https://github.com/orivael-dev/axiom.git"
REPO_BRANCH = "claude/srd-prototype-benchmark-JRtv1"
LLAMA_DIR  = Path("/content/llama.cpp")
OUT_DIR    = Path("/content/drive/MyDrive/srd_output")
DRIVE_GGUF = Path("/content/drive/MyDrive/gguf")   # where old files live


# ════════════════════════════════════════════════════════════════════════════
# CELL 1 — GPU check + mount Drive + clone repo  (~60 s)
# ════════════════════════════════════════════════════════════════════════════
def cell1_setup():
    import torch
    p = torch.cuda.get_device_properties(0)
    vram = p.total_memory / 1024**3
    print(f"GPU: {p.name}  {vram:.1f} GB VRAM")
    if vram < 14:
        print("⚠  Low VRAM — models 1+2 ok, model 3 (Mistral 7B) may OOM")
    else:
        print("✓  Memory ok for all three models")

    from google.colab import drive
    drive.mount("/content/drive")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not REPO.is_dir():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", REPO_BRANCH,
             REPO_URL, str(REPO)], check=True,
        )
    else:
        subprocess.run(["git", "-C", str(REPO), "pull"], check=True)

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q",
         "transformers", "accelerate", "sentencepiece", "protobuf"],
        check=True,
    )
    sys.path.insert(0, str(REPO))
    print("✓  Setup complete")


# ════════════════════════════════════════════════════════════════════════════
# CELL 2 — Build llama.cpp  (~5 min, skip if already built)
# ════════════════════════════════════════════════════════════════════════════
def cell2_build_llamacpp():
    if (LLAMA_DIR / "build/bin/llama-quantize").exists():
        print("llama.cpp already built — skipping")
        return
    subprocess.run(["git", "clone", "--depth", "1",
                    "https://github.com/ggerganov/llama.cpp",
                    str(LLAMA_DIR)], check=True)
    subprocess.run(["cmake", "-B", "build", "-DGGML_CUDA=ON"],
                   cwd=LLAMA_DIR, check=True)
    subprocess.run(["cmake", "--build", "build", "--config", "Release",
                    "-j", "4"],
                   cwd=LLAMA_DIR, check=True)
    print("✓  llama.cpp built")


# ════════════════════════════════════════════════════════════════════════════
# CELL 3 — Helper: SRD quantize + convert to GGUF
# ════════════════════════════════════════════════════════════════════════════
def _srd_to_gguf(model_id: str, out_name: str, *, hf_token: str = "") -> Path:
    """Load model from HF, apply SRD, save FP16, convert to GGUF Q4_K_M."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from research.quant.quantize_model import quantize_hf_model_inplace

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype  = torch.float16 if device == "cuda" else torch.float32

    print(f"\n{'='*60}")
    print(f"  Processing: {model_id}")
    print(f"{'='*60}")

    kwargs = {"torch_dtype": dtype, "device_map": "auto"}
    if hf_token:
        kwargs["token"] = hf_token

    print("  Loading model...")
    tok   = AutoTokenizer.from_pretrained(model_id, **({"token": hf_token} if hf_token else {}))
    model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
    model.eval()

    print("  Applying SRD quantization (alpha=1.0, group_size=64)...")
    quantize_hf_model_inplace(model, alpha=1.0, group_size=64)

    # Save as FP16 HF model for llama.cpp conversion
    hf_out = Path(f"/content/{out_name}_hf")
    print(f"  Saving SRD-corrected FP16 to {hf_out} ...")
    model.save_pretrained(hf_out, safe_serialization=True)
    tok.save_pretrained(hf_out)

    # Convert to GGUF F16 then quantize to Q4_K_M
    gguf_f16  = Path(f"/content/{out_name}_f16.gguf")
    gguf_out  = OUT_DIR / f"{out_name}.gguf"

    print("  Converting to GGUF F16...")
    subprocess.run(
        [sys.executable,
         str(LLAMA_DIR / "convert_hf_to_gguf.py"),
         str(hf_out),
         "--outfile", str(gguf_f16),
         "--outtype", "f16"],
        check=True,
    )

    print("  Quantizing to Q4_K_M...")
    subprocess.run(
        [str(LLAMA_DIR / "build/bin/llama-quantize"),
         str(gguf_f16), str(gguf_out), "Q4_K_M"],
        check=True,
    )

    # Cleanup intermediates
    shutil.rmtree(hf_out, ignore_errors=True)
    gguf_f16.unlink(missing_ok=True)

    size_mb = gguf_out.stat().st_size / 1024**2
    print(f"  ✓  {gguf_out.name}  ({size_mb:.0f} MB)")
    return gguf_out


# ════════════════════════════════════════════════════════════════════════════
# CELL 4 — Model 1: Qwen2.5-Coder-0.5B-Instruct  (~5 min on T4)
# ════════════════════════════════════════════════════════════════════════════
def cell4_qwen():
    _srd_to_gguf(
        model_id="Qwen/Qwen2.5-Coder-0.5B-Instruct",
        out_name="qwen25_coder_0p5b_srd4_q4km",
    )


# ════════════════════════════════════════════════════════════════════════════
# CELL 5 — Model 2: TinyLlama-1.1B  (~8 min on T4)
# Replaces the mislabelled tinyllama_actually-Q4_K_M file
# ════════════════════════════════════════════════════════════════════════════
def cell5_tinyllama():
    _srd_to_gguf(
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        out_name="tinyllama_1b_srd4_q4km",
    )


# ════════════════════════════════════════════════════════════════════════════
# CELL 6 — Model 3: Mistral re-extract from .axiom  (~15 min on T4)
# Reads existing .axiom from Drive — no re-download of 7B weights needed
# ════════════════════════════════════════════════════════════════════════════
def cell6_mistral_reextract():
    axiom_file = next(
        (DRIVE_GGUF / f"mistral_srd4_q4km{ext}" for ext in (".axm", ".axiom")
         if (DRIVE_GGUF / f"mistral_srd4_q4km{ext}").exists()),
        None,
    )
    if axiom_file is None:
        print(f"ERROR: mistral_srd4_q4km.axm/.axiom not found in {DRIVE_GGUF}")
        print("Upload the file to MyDrive/gguf/ and retry")
        return

    gguf_out = OUT_DIR / "mistral_srd4_q4km_v2.gguf"
    srd4_out = OUT_DIR / "mistral_srd4_v2.srd4"

    subprocess.run(
        [sys.executable,
         str(REPO / "research/quant/axm_to_srd4_gguf.py"),
         "--container", str(axiom_file),
         "--srd4-out",  str(srd4_out),
         "--gguf-out",  str(gguf_out),
         "--llamacpp",  str(LLAMA_DIR)],
        check=True,
    )
    size_mb = gguf_out.stat().st_size / 1024**2
    print(f"✓  {gguf_out.name}  ({size_mb:.0f} MB)")


# ════════════════════════════════════════════════════════════════════════════
# CELL 7 — Verify all outputs
# ════════════════════════════════════════════════════════════════════════════
def cell7_verify():
    expected = [
        "qwen25_coder_0p5b_srd4_q4km.gguf",
        "tinyllama_1b_srd4_q4km.gguf",
        "mistral_srd4_q4km_v2.gguf",
    ]
    print(f"\nOutputs in {OUT_DIR}:")
    all_ok = True
    for fname in expected:
        p = OUT_DIR / fname
        if p.exists():
            print(f"  ✓  {fname}  ({p.stat().st_size/1024**2:.0f} MB)")
        else:
            print(f"  ✗  {fname}  MISSING")
            all_ok = False
    if all_ok:
        print("\nAll 3 models ready — upload to srd-lab/benchmark-collection on HF")
    else:
        print("\nSome outputs missing — check cell errors above")
