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
    """Build llama.cpp if needed. Returns path to llama-cli binary."""
    llama_cli = llamacpp_dir / "build/bin/llama-cli"
    if llama_cli.is_file():
        print(f"  llama-cli: already built at {llama_cli}")
        return llama_cli

    import torch
    p    = torch.cuda.get_device_properties(0)
    arch = f"{p.major}{p.minor}"
    print(f"  Building llama.cpp for {p.name} SM {p.major}.{p.minor} ...")

    if not llamacpp_dir.is_dir():
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/ggerganov/llama.cpp.git", str(llamacpp_dir)],
            check=True,
        )

    subprocess.run(
        ["cmake", "-B", str(llamacpp_dir / "build"), "-S", str(llamacpp_dir),
         "-DGGML_CUDA=ON", f"-DCMAKE_CUDA_ARCHITECTURES={arch}",
         "-DCMAKE_BUILD_TYPE=Release"],
        check=True,
    )
    nproc = subprocess.check_output(["nproc"]).decode().strip()
    subprocess.run(
        ["cmake", "--build", str(llamacpp_dir / "build"),
         "-j", nproc, "-t", "llama-cli", "llama-quantize"],
        check=True,
    )
    print(f"  llama-cli: built at {llama_cli}")
    return llama_cli


def run_pipeline(
    model: str,
    output_dir: Path,
    llamacpp_dir: Path | None,
    quant: str = "Q4_K_M",
    skip_extract: bool = False,
    smoke_test: bool = False,
    bench: bool = False,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
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
             "--ngl", "99", "--ctx-size", "512", "--n-predict", "64",
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
    args = p.parse_args()

    run_pipeline(
        model        = args.model,
        output_dir   = Path(args.output_dir),
        llamacpp_dir = Path(args.llamacpp) if args.llamacpp else None,
        quant        = args.quant,
        skip_extract = args.skip_extract,
        smoke_test   = args.smoke_test,
        bench        = args.bench,
    )


if __name__ == "__main__":
    main()
