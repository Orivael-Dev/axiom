"""SRD-4 compression pipeline — runs on any Linux GPU machine (RunPod, local, etc.).

Packs a HuggingFace model (or local checkpoint) into a signed .axm container,
then optionally extracts a GGUF Q4_K_M for llama.cpp.

Usage
-----
    python3 research/quant/run_srd4_local.py \\
        --model mistralai/Mistral-7B-Instruct-v0.3 \\
        --output-dir /workspace/srd_output \\
        --llamacpp  /workspace/llama.cpp \\
        --quant     Q4_K_M

    # Your own fine-tuned model (local path)
    python3 research/quant/run_srd4_local.py \\
        --model /workspace/my_finetuned_model \\
        --output-dir /workspace/srd_output \\
        --llamacpp  /workspace/llama.cpp

    # Pack only — skip GGUF extraction
    python3 research/quant/run_srd4_local.py \\
        --model my-org/my-model \\
        --output-dir /workspace/out \\
        --skip-extract

    # Pack + extract + smoke test + KV benchmark
    python3 research/quant/run_srd4_local.py \\
        --model mistralai/Mistral-7B-Instruct-v0.3 \\
        --output-dir /workspace/out \\
        --llamacpp /workspace/llama.cpp \\
        --smoke-test --bench

Hardware requirements
---------------------
  A100 40 GB:  easiest — model fits entirely in VRAM
  RTX 4090 24 GB: device_map=auto — works, slightly slower
  A10G 24 GB: same as 4090
  Minimum: 14 GB VRAM + 20 GB system RAM (uses device_map=auto)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

# HuggingFace model_type values that indicate a vision-language model.
# AutoModelForCausalLM will reject these; the vision pipeline handles them.
_VISION_MODEL_TYPES: frozenset[str] = frozenset({
    "idefics", "idefics2", "idefics3",          # SmolVLM / HF Idefics family
    "llava", "llava_next", "llava_next_video",   # LLaVA family
    "florence2",                                  # Microsoft Florence-2
    "paligemma",                                  # Google PaLiGemma
    "qwen2_vl",                                   # Qwen-VL
    "internvl_chat",                              # InternVL
    "blip", "blip-2", "blip2",                   # Salesforce BLIP
    "git",                                        # GIT (GenerativeImage2Text)
    "cogvlm", "cogvlm2",                          # CogVLM
    "phi3_v",                                     # Phi-3 Vision
    "mllama",                                     # Meta LLaMA Vision
})


def _detect_modality(model: str) -> str:
    """Return "vision" or "text" by reading the HF config without loading weights."""
    try:
        from transformers import AutoConfig
        cfg = AutoConfig.from_pretrained(model, trust_remote_code=True)
        model_type = getattr(cfg, "model_type", "").lower()
        if model_type in _VISION_MODEL_TYPES:
            print(f"  detected modality: vision (model_type={model_type!r})")
            return "vision"
    except Exception as e:
        print(f"  modality detection failed ({e}) — assuming text")
    print(f"  detected modality: text")
    return "text"


def _ensure_key(output_dir: Path) -> str:
    """Return AXIOM_MASTER_KEY, persisting it to output_dir/axiom_master.key."""
    key_file = output_dir / "axiom_master.key"
    if os.environ.get("AXIOM_MASTER_KEY"):
        print("  AXIOM_MASTER_KEY: from environment")
        return os.environ["AXIOM_MASTER_KEY"]
    if key_file.is_file():
        key = key_file.read_text().strip()
        os.environ["AXIOM_MASTER_KEY"] = key
        print(f"  AXIOM_MASTER_KEY: restored from {key_file}")
        return key
    key = secrets.token_hex(32)
    os.environ["AXIOM_MASTER_KEY"] = key
    key_file.write_text(key)
    print(f"  AXIOM_MASTER_KEY: generated and saved to {key_file}")
    return key


def _build_llamacpp(llamacpp_dir: Path) -> Path:
    """Set up llama.cpp — tries pre-built binary first, falls back to source build."""
    llama_cli = llamacpp_dir / "build/bin/llama-cli"
    if llama_cli.is_file():
        print(f"  llama-cli: already at {llama_cli}")
        _ensure_llama_repo(llamacpp_dir)
        return llama_cli

    _ensure_llama_repo(llamacpp_dir)

    if _download_prebuilt_llamacpp(llamacpp_dir):
        return llama_cli

    _build_llamacpp_source(llamacpp_dir)
    return llama_cli


def _ensure_llama_repo(llamacpp_dir: Path) -> None:
    if not llamacpp_dir.is_dir():
        print("  Cloning llama.cpp repo...")
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/ggerganov/llama.cpp.git", str(llamacpp_dir)],
            check=True,
        )


def _download_prebuilt_llamacpp(llamacpp_dir: Path) -> bool:
    """Download pre-built CUDA binary from GitHub releases. Returns True on success."""
    import io, json, urllib.request, zipfile
    import torch

    cuda_ver = torch.version.cuda or ""
    major_minor = ".".join(cuda_ver.split(".")[:2])
    print(f"  Fetching pre-built llama.cpp binary (CUDA {major_minor})...")

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

    def score(a: dict) -> int:
        n = a["name"]
        if "ubuntu-x64" not in n or "cuda" not in n:
            return 0
        if f"cu{major_minor}" in n:
            return 2
        return 1

    best = max(assets, key=score, default=None)
    if not best or score(best) == 0:
        print("  No pre-built CUDA ubuntu binary found.")
        return False

    size_mb = best["size"] / 1024**2
    print(f"  Downloading {best['name']}  ({size_mb:.0f} MB)...")

    try:
        with urllib.request.urlopen(best["browser_download_url"], timeout=300) as r:
            data = r.read()
    except Exception as e:
        print(f"  Download failed: {e}")
        return False

    build_bin = llamacpp_dir / "build" / "bin"
    build_bin.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for member in z.namelist():
                stem = Path(member).name
                if stem in ("llama-cli", "llama-quantize"):
                    dest = build_bin / stem
                    dest.write_bytes(z.read(member))
                    dest.chmod(0o755)
                    print(f"  ✓ {stem}")
    except Exception as e:
        print(f"  Extraction failed: {e}")
        return False

    if (llamacpp_dir / "build/bin/llama-cli").is_file():
        print("  llama-cli: pre-built binary ready")
        return True
    return False


def _build_llamacpp_source(llamacpp_dir: Path) -> None:
    """Build llama.cpp from source — fallback only."""
    import torch
    p    = torch.cuda.get_device_properties(0)
    arch = f"{p.major}{p.minor}"
    print(f"  Building from source for {p.name} SM {p.major}.{p.minor} (~10 min)...")

    subprocess.run(
        ["cmake", "-B", str(llamacpp_dir / "build"), "-S", str(llamacpp_dir),
         "-DGGML_CUDA=ON", f"-DCMAKE_CUDA_ARCHITECTURES={arch}",
         "-DCMAKE_BUILD_TYPE=Release"],
        check=True,
    )
    # Cap at 4 jobs — full nproc saturates CPU and causes hangs
    subprocess.run(
        ["cmake", "--build", str(llamacpp_dir / "build"),
         "-j", "4", "--target", "llama-cli", "llama-quantize"],
        check=True,
        timeout=900,
    )
    print(f"  llama-cli: built from source")


def _run_vision_pipeline(
    model:      str,
    output_dir: Path,
    quant:      str,
    smoke_test: bool,
) -> dict:
    """Vision model pipeline: SRD pack → verify → (optional smoke test).

    No GGUF extraction — runtime inference uses transformers + bitsandbytes.
    """
    results_dir = output_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    model_slug = Path(model).name.replace("/", "_").replace(" ", "_").lower()
    axm_path   = output_dir / f"{model_slug}_vision_srd4.axm"
    stats_json = str(results_dir / "pack_stats.json")

    print("=" * 64)
    print("SRD-4 Vision Compression Pipeline")
    print("=" * 64)
    print(f"  Model:      {model}")
    print(f"  Output dir: {output_dir}")
    print(f"  .axm:       {axm_path}")
    print(f"  Note: GGUF skipped — inference via transformers + 4-bit bnb")
    print()

    _ensure_key(output_dir)

    # ── Step 1: Pack ──────────────────────────────────────────────────────────
    print("─" * 64)
    print("Step 1/2  Pack vision model → SRD-4 .axm")
    print("─" * 64)

    t0 = time.time()
    pack_vision_script = _REPO / "research" / "quant" / "pack_vision_to_axm.py"
    subprocess.run(
        [sys.executable, str(pack_vision_script),
         "--model",      model,
         "--output",     str(axm_path),
         "--stats-json", stats_json],
        check=True,
    )
    pack_elapsed = time.time() - t0

    pack_stats: dict = {}
    if Path(stats_json).is_file():
        pack_stats = json.loads(Path(stats_json).read_text())
    print(f"\n  ✓ Packed in {pack_elapsed/60:.1f} min")
    print(f"    .axm size:   {axm_path.stat().st_size / 1024**3:.3f} GB")
    print(f"    bpw (linear):{pack_stats.get('bpw_theoretical', 'N/A')}")
    print(f"    fingerprint: {pack_stats.get('fingerprint', 'N/A')}")

    # ── Step 2: Verify ────────────────────────────────────────────────────────
    print()
    print("─" * 64)
    print("Step 2/2  Verify HMAC proofs")
    print("─" * 64)

    result = subprocess.run(
        [sys.executable, "axm_cli.py", "verify", str(axm_path)],
        cwd=_REPO, capture_output=True, text=True,
    )
    try:
        verify_out = json.loads(result.stdout)
    except json.JSONDecodeError:
        verify_out = {"verified": False, "error": result.stdout + result.stderr}

    if not verify_out.get("verified"):
        print(f"  ✗ Verification FAILED: {verify_out}")
        sys.exit(1)

    print(f"  ✓ Verified  ({verify_out.get('proofs_checked', '?')} proofs)")
    print(f"    fingerprint: {verify_out.get('fingerprint', 'N/A')}")

    # ── Optional: vision smoke test ───────────────────────────────────────────
    if smoke_test:
        print()
        print("─" * 64)
        print("Vision smoke test (64×64 dummy image)")
        print("─" * 64)
        _vision_smoke_test(model, axm_path)

    summary = {
        "model":       model,
        "modality":    "vision",
        "axm_path":    str(axm_path),
        "gguf_path":   None,
        "pack_min":    round(pack_elapsed / 60, 1),
        "axm_gb":      round(axm_path.stat().st_size / 1024**3, 3),
        "verified":    True,
        "fingerprint": pack_stats.get("fingerprint"),
        "bpw":         pack_stats.get("bpw_theoretical"),
        "total_params_m": pack_stats.get("total_params_m"),
    }
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print()
    print("=" * 64)
    print(f"  Done in {pack_elapsed/60:.1f} min")
    print(f"  .axm:    {axm_path}")
    print(f"  Summary: {results_dir / 'summary.json'}")
    print("=" * 64)
    return summary


def _vision_smoke_test(model_name: str, axm_path: Path) -> None:
    """Load vision model from AXM weights, run 20-token inference on a dummy image."""
    import zipfile, tempfile as _tmp
    try:
        import torch
        from transformers import AutoProcessor, AutoModelForVision2Seq
        from transformers import BitsAndBytesConfig
        from PIL import Image
    except ImportError as e:
        import transformers as _tf
        if "AutoModelForVision2Seq" in str(e):
            print(f"  smoke test skipped — transformers {_tf.__version__} removed "
                  f"AutoModelForVision2Seq (v5.x). "
                  f"Pin with: pip install \"transformers==4.44.2\" accelerate")
        else:
            print(f"  smoke test skipped (missing dep: {e})")
        return

    # Extract weights from AXM — safetensors layout: weights/model.safetensors
    tmp_dir = Path(_tmp.mkdtemp(prefix="axm_vision_smoke_"))
    print(f"  extracting weights from {axm_path.name}...")
    try:
        with zipfile.ZipFile(axm_path) as zf:
            members = [n for n in zf.namelist() if n.startswith("weights/")]
            if not members:
                raise RuntimeError("no weights/ in AXM")
            zf.extractall(tmp_dir, members)
        weights_src = str(tmp_dir / "weights")
        # Confirm at least one safetensors or config file landed
        wdir = Path(weights_src)
        if not any(wdir.glob("*.safetensors")) and not (wdir / "config.json").exists():
            raise RuntimeError("weights dir looks empty after extraction")
    except Exception as e:
        print(f"  extraction failed ({e}) — using HF source directly")
        weights_src = model_name

    # 64×64 gray dummy image
    img = Image.new("RGB", (64, 64), color=(100, 100, 100))

    quant_cfg = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16
    )
    try:
        processor = AutoProcessor.from_pretrained(weights_src, trust_remote_code=True)
        mdl = AutoModelForVision2Seq.from_pretrained(
            weights_src,
            quantization_config=quant_cfg,
            device_map="auto",
            trust_remote_code=True,
        )
        mdl.eval()
    except Exception as e:
        print(f"  model load failed: {e}")
        return

    messages = [{"role": "user", "content": [
        {"type": "image"},
        {"type": "text", "text": "Describe this image in one sentence."},
    ]}]
    prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
    inputs = processor(text=prompt, images=[img], return_tensors="pt").to(
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    t0 = time.time()
    with torch.no_grad():
        ids = mdl.generate(**inputs, max_new_tokens=20, do_sample=False)
    elapsed = time.time() - t0

    text = processor.batch_decode(
        ids[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True
    )[0].strip()
    tps = 20 / elapsed if elapsed > 0 else 0.0

    print(f"  output: {text!r}")
    print(f"  tok/s:  {tps:.1f}")
    print("  ✓ Vision smoke test passed")


def run_pipeline(
    model: str,
    output_dir: Path,
    llamacpp_dir: Path | None,
    quant: str = "Q4_K_M",
    skip_extract: bool = False,
    smoke_test: bool = False,
    bench: bool = False,
    modality: str = "auto",
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve modality — auto-detect from HF config if not explicit
    if modality == "auto":
        modality = _detect_modality(model)

    if modality == "vision":
        return _run_vision_pipeline(
            model=model,
            output_dir=output_dir,
            quant=quant,
            smoke_test=smoke_test,
        )
    results_dir = output_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Derive output filenames from model name
    model_slug = Path(model).name.replace("/", "_").replace(" ", "_").lower()
    axm_path   = output_dir / f"{model_slug}_srd4.axm"
    gguf_path  = output_dir / f"{model_slug}_srd4_{quant.lower()}.gguf"

    print("=" * 64)
    print("SRD-4 Compression Pipeline")
    print("=" * 64)
    print(f"  Model:      {model}")
    print(f"  Output dir: {output_dir}")
    print(f"  .axm:       {axm_path}")
    if not skip_extract:
        print(f"  GGUF:       {gguf_path}  ({quant})")
    print()

    _ensure_key(output_dir)

    # ── Step 1: Pack ──────────────────────────────────────────────────────────
    print("─" * 64)
    print("Step 1/3  Pack → SRD-4 .axm")
    print("─" * 64)

    t0 = time.time()
    subprocess.run(
        [sys.executable, "axm_cli.py", "pack",
         "--model",      model,
         "--srd4",
         "--output",     str(axm_path),
         "--stats-json", str(results_dir / "pack_stats.json")],
        cwd=_REPO, check=True,
    )
    pack_elapsed = time.time() - t0

    size_gb = axm_path.stat().st_size / 1024**3
    pack_stats: dict = {}
    stats_file = results_dir / "pack_stats.json"
    if stats_file.is_file():
        pack_stats = json.loads(stats_file.read_text())
    print(f"\n  ✓ Packed in {pack_elapsed/60:.1f} min")
    print(f"    .axm size:   {size_gb:.2f} GB")
    print(f"    bpw:         {pack_stats.get('quant', {}).get('bpw', 'N/A')}")
    print(f"    fingerprint: {pack_stats.get('fingerprint', 'N/A')}")

    # ── Step 2: Verify ────────────────────────────────────────────────────────
    print()
    print("─" * 64)
    print("Step 2/3  Verify HMAC proofs")
    print("─" * 64)

    result = subprocess.run(
        [sys.executable, "axm_cli.py", "verify", str(axm_path)],
        cwd=_REPO, capture_output=True, text=True,
    )
    try:
        verify_out = json.loads(result.stdout)
    except json.JSONDecodeError:
        verify_out = {"verified": False, "error": result.stdout + result.stderr}

    if not verify_out.get("verified"):
        print(f"  ✗ Verification FAILED: {verify_out}")
        sys.exit(1)

    proofs = verify_out.get("proofs_checked", "?")
    print(f"  ✓ Verified  ({proofs} proofs)")
    print(f"    fingerprint: {verify_out.get('fingerprint', 'N/A')}")

    if skip_extract:
        print("\n  --skip-extract set: stopping after .axm")
        summary = {"axm": str(axm_path), "pack_stats": pack_stats, "verified": True}
        (results_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        return summary

    # ── Step 3: Extract → GGUF ────────────────────────────────────────────────
    print()
    print("─" * 64)
    print("Step 3/3  Extract → GGUF")
    print("─" * 64)

    if llamacpp_dir is None:
        print("  ✗ --llamacpp required for extraction. Use --skip-extract to skip.")
        sys.exit(1)

    llama_cli = _build_llamacpp(llamacpp_dir)

    t0 = time.time()
    subprocess.run(
        [sys.executable, "axm_cli.py", "extract", str(axm_path),
         "--gguf-out",   str(gguf_path),
         "--llamacpp",   str(llamacpp_dir),
         "--quant",      quant,
         "--device",     "cpu",
         "--stats-json", str(results_dir / "extract_stats.json")],
        cwd=_REPO, check=True,
    )
    extract_elapsed = time.time() - t0

    gguf_size_gb = gguf_path.stat().st_size / 1024**3
    extract_stats: dict = {}
    extract_file = results_dir / "extract_stats.json"
    if extract_file.is_file():
        extract_stats = json.loads(extract_file.read_text())
    print(f"\n  ✓ Extracted in {extract_elapsed/60:.1f} min")
    print(f"    GGUF size:   {gguf_size_gb:.2f} GB  ({quant})")
    print(f"    fingerprint: {extract_stats.get('axm_fingerprint', 'N/A')}")

    # ── Optional: smoke test ──────────────────────────────────────────────────
    tps = None
    if smoke_test:
        print()
        print("─" * 64)
        print("Smoke test (64 tokens)")
        print("─" * 64)
        result = subprocess.run(
            [str(llama_cli), "-m", str(gguf_path),
             "--n-gpu-layers", "99", "--ctx-size", "512", "--n-predict", "64",
             "--log-disable",
             "--prompt", "Summarize what this model does in one sentence:"],
            capture_output=True, text=True, timeout=180,
        )
        log = result.stdout + result.stderr
        tps_m = re.search(r"([\d.]+)\s*tokens per second", log)
        tps = float(tps_m.group(1)) if tps_m else None
        print(log[-600:])
        if tps:
            print(f"\n  tok/s: {tps:.2f}")
        print("  ✓ Smoke test complete")

    # ── Optional: KV benchmark ────────────────────────────────────────────────
    bench_results = None
    if bench:
        print()
        print("─" * 64)
        print("KV simulation benchmark")
        print("─" * 64)
        bench_out = results_dir / "kv_bench.json"
        subprocess.run(
            [sys.executable, "-m", "research.quant.bench_mistral_kv",
             "--llamacpp", str(llamacpp_dir / "build/bin"),
             "--gguf",     str(gguf_path),
             "--stats-json", str(bench_out)],
            cwd=_REPO, check=True,
        )
        if bench_out.is_file():
            bench_results = json.loads(bench_out.read_text())

    # ── Summary ───────────────────────────────────────────────────────────────
    total_elapsed = pack_elapsed + extract_elapsed
    summary = {
        "model":          model,
        "axm_path":       str(axm_path),
        "gguf_path":      str(gguf_path),
        "quant":          quant,
        "pack_min":       round(pack_elapsed / 60, 1),
        "extract_min":    round(extract_elapsed / 60, 1),
        "total_min":      round(total_elapsed / 60, 1),
        "axm_gb":         round(size_gb, 2),
        "gguf_gb":        round(gguf_size_gb, 2),
        "verified":       True,
        "tps":            tps,
        "fingerprint":    pack_stats.get("fingerprint"),
    }
    if bench_results:
        summary["bench"] = bench_results

    summary_path = results_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print()
    print("=" * 64)
    print(f"  Done in {total_elapsed/60:.1f} min total")
    print(f"  .axm:    {axm_path}  ({size_gb:.2f} GB)")
    print(f"  GGUF:    {gguf_path}  ({gguf_size_gb:.2f} GB)")
    print(f"  Summary: {summary_path}")
    print("=" * 64)

    return summary


def main():
    p = argparse.ArgumentParser(
        description="SRD-4 model compression pipeline (non-Colab entry point)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--model",       required=True,
                   help="HuggingFace model ID or local path to model directory")
    p.add_argument("--output-dir",  required=True,
                   help="Directory to write .axm, GGUF, and stats files")
    p.add_argument("--llamacpp",    default=None,
                   help="Path to llama.cpp root (cloned and built here if not present)")
    p.add_argument("--quant",       default="Q4_K_M",
                   choices=["Q4_K_M", "Q5_K_M", "Q6_K", "F16"],
                   help="GGUF quantization type (default: Q4_K_M)")
    p.add_argument("--skip-extract", action="store_true",
                   help="Stop after .axm — skip GGUF extraction")
    p.add_argument("--smoke-test",  action="store_true",
                   help="Run a 64-token generation check after extraction")
    p.add_argument("--bench",       action="store_true",
                   help="Run KV simulation benchmark after extraction (optional)")
    p.add_argument("--modality",    default="auto",
                   choices=["auto", "text", "vision"],
                   help="Model modality (default: auto-detected from HF config). "
                        "Vision models skip GGUF extraction; use transformers + 4-bit bnb.")
    args = p.parse_args()

    run_pipeline(
        model        = args.model,
        output_dir   = Path(args.output_dir),
        llamacpp_dir = Path(args.llamacpp) if args.llamacpp else None,
        quant        = args.quant,
        skip_extract = args.skip_extract,
        smoke_test   = args.smoke_test,
        bench        = args.bench,
        modality     = args.modality,
    )


if __name__ == "__main__":
    main()
