"""Convert a verified .axm archive to GGUF for llama.cpp inference.

The .axm stays what it is good at: signed, tamper-evident delivery.
llama.cpp becomes the inference engine that runs what is inside it.

Pipeline
--------
1. Verify the .axm proof ledger  (``axm verify`` equivalent)
2. Locate the weights/ directory
3. Reconstruct a standard HF FP16 checkpoint in a temp dir
   - real-packed containers:  srd_unpack → FP16 → save_pretrained
   - fake-quant containers:   weights/ already is HF format, copy as-is
4. Run llama.cpp ``convert_hf_to_gguf.py`` → F16 GGUF
5. Optionally run ``llama-quantize`` → Q4_K_M (default)
6. Report archive size, verify fingerprint matches

CLI
---
    python -m research.quant.axm_to_gguf \\
        --container artifacts/tinyllama_srd_7bpw_REAL.axm \\
        --gguf-out  artifacts/tinyllama_q4km.gguf \\
        --llamacpp  ~/llama.cpp

    # keep F16 GGUF, skip re-quantization
    python -m research.quant.axm_to_gguf \\
        --container artifacts/tinyllama_srd_7bpw_REAL.axm \\
        --gguf-out  artifacts/tinyllama_f16.gguf \\
        --llamacpp  ~/llama.cpp --quant none
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axiom_axm import AXMContainer, AXMError   # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_convert_script(llamacpp_dir: Path) -> Path:
    candidates = [
        llamacpp_dir / "convert_hf_to_gguf.py",
        llamacpp_dir / "convert-hf-to-gguf.py",
        llamacpp_dir / "convert.py",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        f"Cannot find convert_hf_to_gguf.py in {llamacpp_dir}. "
        "Make sure --llamacpp points to the root of a llama.cpp checkout."
    )


def _find_quantize_bin(llamacpp_dir: Path) -> Optional[Path]:
    candidates = [
        llamacpp_dir / "build" / "bin" / "llama-quantize",
        llamacpp_dir / "build" / "bin" / "quantize",
        llamacpp_dir / "llama-quantize",
        llamacpp_dir / "quantize",
    ]
    for p in candidates:
        if p.is_file():
            return p
    # Broader search: any llama-quantize binary anywhere under llamacpp_dir
    for pattern in ("**/llama-quantize", "**/quantize"):
        for found in sorted(llamacpp_dir.glob(pattern)):
            if found.is_file() and not found.suffix:
                return found
    return None


def _mb(p: Path) -> float:
    return p.stat().st_size / (1024 ** 2)


# ── core ─────────────────────────────────────────────────────────────────────

def convert_axm_to_gguf(
    container_path: str,
    gguf_out: str,
    *,
    llamacpp_dir: str,
    quant_type: str = "Q4_K_M",
    device: str = "cpu",
) -> dict:
    """Verify .axm, reconstruct FP16 weights, convert to GGUF.

    Args:
        container_path: path to the .axm archive.
        gguf_out:        where to write the final .gguf file.
        llamacpp_dir:    root of a llama.cpp checkout (needs build/bin/ +
                         convert_hf_to_gguf.py).
        quant_type:      GGUF quantization type passed to llama-quantize
                         (e.g. "Q4_K_M", "Q5_K_M", "F16"). Use "none" to
                         skip quantization and keep the F16 GGUF.
        device:          device for weight reconstruction ("cpu" is safe
                         on the Orin Nano — this step only happens once).
    """
    t0 = time.monotonic()
    llamacpp_dir = Path(llamacpp_dir).expanduser().resolve()
    gguf_out     = Path(gguf_out)
    gguf_out.parent.mkdir(parents=True, exist_ok=True)

    # ── Step 1: verify ────────────────────────────────────────────────────
    print(f"[extract] opening {container_path}...")
    container = AXMContainer.from_path(container_path)
    print(f"[extract] verifying proofs ({len(container.proofs)} entries)...")
    ok = container.verify_proofs()
    if not ok:
        raise AXMError("proof verification failed — container may be tampered")
    fingerprint = container.fingerprint()
    print(f"[extract] verified ✓  fingerprint={fingerprint}")

    weights_path = container.weights_path
    if weights_path is None:
        raise AXMError(
            "No weights/ directory in this container. "
            "Re-pack with pack_to_axm.py to include model weights."
        )

    # ── Step 2: reconstruct HF FP16 checkpoint ───────────────────────────
    convert_script = _find_convert_script(llamacpp_dir)
    work = Path(tempfile.mkdtemp(prefix="axm_gguf_"))
    try:
        from research.quant.srd_realpack import is_real_packed, load_real_packed
        if is_real_packed(weights_path):
            print(f"[extract] real-packed — reconstructing FP16 weights on {device}...")
            import torch
            from transformers import AutoTokenizer
            t1 = time.monotonic()
            model, tokenizer = load_real_packed(
                weights_path, device=device, dtype=torch.float16,
            )
            recon_s = time.monotonic() - t1
            print(f"[extract] reconstructed in {recon_s:.1f}s — saving HF checkpoint...")
            hf_dir = work / "hf_fp16"
            hf_dir.mkdir()
            model.save_pretrained(str(hf_dir))
            if tokenizer is not None:
                tokenizer.save_pretrained(str(hf_dir))
            else:
                # Copy tokenizer files directly from the weights dir.
                for f in weights_path.iterdir():
                    if f.suffix in {".json", ".model", ".txt"} and \
                       f.name not in {"srd_index.json"}:
                        shutil.copy2(f, hf_dir / f.name)
            del model
        else:
            print("[extract] fake-quant container — using weights/ directly...")
            hf_dir = weights_path       # already standard HF format

        # ── Step 3: convert HF → F16 GGUF ────────────────────────────────
        gguf_f16 = work / "model_f16.gguf"
        print(f"[extract] running convert_hf_to_gguf.py...")
        cmd = [sys.executable, str(convert_script),
               str(hf_dir), "--outfile", str(gguf_f16),
               "--outtype", "f16"]
        result = subprocess.run(cmd, check=True, capture_output=False)

        # ── Step 4: quantize (optional) ───────────────────────────────────
        final_gguf = gguf_f16
        quant_applied = "f16"
        if quant_type.lower() not in {"none", "f16", "fp16"}:
            q_bin = _find_quantize_bin(llamacpp_dir)
            if q_bin is None:
                raise FileNotFoundError(
                    f"llama-quantize not found under {llamacpp_dir}.\n"
                    "Build it first:\n"
                    f"  cmake {llamacpp_dir} -B {llamacpp_dir}/build -DCMAKE_BUILD_TYPE=Release\n"
                    f"  cmake --build {llamacpp_dir}/build --target llama-quantize -j$(nproc)\n"
                    "Then re-run extraction. Alternatively pass --quant none to keep the F16 GGUF."
                )
            else:
                gguf_q = work / f"model_{quant_type.lower()}.gguf"
                print(f"[extract] quantizing to {quant_type}...")
                subprocess.run(
                    [str(q_bin), str(gguf_f16), str(gguf_q), quant_type],
                    check=True, capture_output=False,
                )
                final_gguf = gguf_q
                quant_applied = quant_type

        # ── Step 5: move final GGUF to output path ────────────────────────
        shutil.move(str(final_gguf), str(gguf_out))
        total_s = time.monotonic() - t0

        stats = {
            "container":      container_path,
            "fingerprint":    fingerprint,
            "gguf_out":       str(gguf_out),
            "quant_applied":  quant_applied,
            "gguf_mb":        round(_mb(gguf_out), 1),
            "total_s":        round(total_s, 1),
        }
        print(f"\n[extract] ── done ─────────────────────────────────────────")
        print(f"  output    : {gguf_out}  ({stats['gguf_mb']:.0f} MB)")
        print(f"  quant     : {quant_applied}")
        print(f"  total     : {total_s:.1f}s")
        print(f"  fingerprint: {fingerprint}")
        return stats

    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract and convert a .axm archive to GGUF for llama.cpp")
    p.add_argument("--container", required=True, help=".axm archive path")
    p.add_argument("--gguf-out",  required=True, help="output .gguf path")
    p.add_argument("--llamacpp",  required=True,
                   help="root of a llama.cpp checkout (needs build/bin/ + "
                        "convert_hf_to_gguf.py)")
    p.add_argument("--quant", default="Q4_K_M",
                   help="GGUF quant type (Q4_K_M / Q5_K_M / F16 / none)")
    p.add_argument("--device", default="cpu")
    p.add_argument("--stats-json", type=Path, default=None)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    stats = convert_axm_to_gguf(
        args.container,
        args.gguf_out,
        llamacpp_dir=args.llamacpp,
        quant_type=args.quant,
        device=args.device,
    )
    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(stats, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
