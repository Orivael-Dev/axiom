"""Pack an SRD-quantized (or FP16 baseline) model into a signed .axm archive.

The resulting file is self-contained: header.json (quant_map = structured
SRD dict), proof ledger covering every sub-module including
weights/manifest.json, and the full HuggingFace checkpoint under weights/.

Loading the archive with load_from_axm.py extracts the weights and
reconstructs the model without any extra downloads.

CLI:
    # FP16 baseline (no quantization — useful as comparison point)
    python -m research.quant.pack_to_axm \\
        --model Qwen/Qwen2.5-Coder-7B-Instruct \\
        --output artifacts/qwen7b_fp16.axm

    # SRD dense (top_k_pct=1.0, 13 bpw)
    python -m research.quant.pack_to_axm \\
        --model Qwen/Qwen2.5-Coder-7B-Instruct \\
        --srd-top-k-pct 1.0 \\
        --output artifacts/qwen7b_srd_dense.axm

    # SRD 7 bpw (top_k_pct=0.25)
    python -m research.quant.pack_to_axm \\
        --model Qwen/Qwen2.5-Coder-7B-Instruct \\
        --srd-top-k-pct 0.25 \\
        --output artifacts/qwen7b_srd_7bpw.axm
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch                                                    # noqa: E402

from axiom_axm import AXMContainer, FORMAT_VERSION             # noqa: E402
from axiom_quant import DEFAULT_GROUP_SIZE, srd_bits_per_weight # noqa: E402
from research.quant.quantize_model import (                     # noqa: E402
    DEFAULT_SKIP_MODULES,
    quantize_hf_model_inplace,
)


def _srd_quant_map(top_k_pct: float, group_size: int,
                   bpw: float) -> dict:
    return {
        "scheme":      "srd",
        "group_size":  group_size,
        "top_k_pct":   top_k_pct,
        "bpw":         round(bpw, 4),
        "alpha":       1.0,
        "note":        "fake-quant — weights stored as FP16 on the SRD grid; "
                       "real storage savings require packed W4+D8 kernel (Phase E3)",
    }


def _fp16_quant_map() -> dict:
    return {"scheme": "fp16", "bpw": 16.0}


def pack_model(
    model_name: str,
    output_path: str,
    *,
    srd_top_k_pct: Optional[float],
    group_size: int,
    model_revision: Optional[str],
    hardware_map: str,
) -> dict:
    """Quantize (if requested) and pack to .axm. Returns a stats dict."""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype  = torch.float16 if device == "cuda" else torch.float32

    print(f"[pack] loading {model_name}...")
    t0 = time.monotonic()
    model     = AutoModelForCausalLM.from_pretrained(
        model_name, revision=model_revision, torch_dtype=dtype,
    ).to(device)
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=model_revision)
    model.eval()
    load_s = time.monotonic() - t0
    print(f"[pack] model loaded in {load_s:.1f}s")

    bpw = 16.0
    if srd_top_k_pct is not None:
        print(f"[pack] SRD quantizing (top_k_pct={srd_top_k_pct}, g={group_size})...")
        t1 = time.monotonic()
        packed = quantize_hf_model_inplace(
            model, alpha=1.0, group_size=group_size,
            top_k_pct=srd_top_k_pct, progress=True,
        )
        quant_s = time.monotonic() - t1
        # Compute honest bpw from the first packed layer (all have same config)
        first_pack = next(iter(packed.values()))
        bpw = srd_bits_per_weight(first_pack)
        print(f"[pack] quantized {len(packed)} layers in {quant_s:.1f}s "
              f"(~{bpw:.1f} bpw theoretical)")
    else:
        quant_s = 0.0

    quant_map = (
        _srd_quant_map(srd_top_k_pct, group_size, bpw)
        if srd_top_k_pct is not None else _fp16_quant_map()
    )

    with tempfile.TemporaryDirectory(prefix="axm_weights_") as tmp:
        weights_dir = Path(tmp)
        print(f"[pack] saving checkpoint to temp dir...")
        t2 = time.monotonic()
        model.save_pretrained(weights_dir)
        tokenizer.save_pretrained(weights_dir)
        save_s = time.monotonic() - t2

        # Build the AXM spec
        short_name = Path(model_name).name
        spec = {
            "format_version": FORMAT_VERSION,
            "core_logic":     f"{short_name}_srd" if srd_top_k_pct else short_name,
            "quant_map":      quant_map,
            "hardware_map":   hardware_map,
            "safety_proofs":  True,
            "core": {
                "name":          model_name,
                "revision":      model_revision or "default",
                "quant_map":     quant_map,
                "skip_modules":  list(DEFAULT_SKIP_MODULES),
            },
        }

        print(f"[pack] writing .axm archive...")
        t3 = time.monotonic()
        container = AXMContainer.pack(
            spec, output_path,
            archive=True,
            weights_source_dir=weights_dir,
        )
        pack_s = time.monotonic() - t3

    archive_bytes = Path(output_path).stat().st_size
    archive_mb    = archive_bytes / (1024 ** 2)

    # Theoretical size if the weights were actually packed at reported bpw
    param_count = sum(p.numel() for p in model.parameters())
    theoretical_mb = param_count * bpw / 8 / (1024 ** 2)

    stats = {
        "model":            model_name,
        "output":           output_path,
        "srd_top_k_pct":    srd_top_k_pct,
        "bpw_theoretical":  round(bpw, 4),
        "quant_map":        quant_map,
        "fingerprint":      container.fingerprint(),
        "proofs":           len(container.proofs),
        "timing": {
            "model_load_s":  round(load_s, 2),
            "quantize_s":    round(quant_s, 2),
            "hf_save_s":     round(save_s, 2),
            "axm_pack_s":    round(pack_s, 2),
            "total_s":       round(load_s + quant_s + save_s + pack_s, 2),
        },
        "size": {
            "archive_mb":       round(archive_mb, 1),
            "theoretical_mb":   round(theoretical_mb, 1),
            "size_ratio":       round(archive_mb / theoretical_mb, 2)
                                if theoretical_mb > 0 else None,
            "note": "archive_mb = actual .axm on disk (FP16 fake-quant); "
                    "theoretical_mb = what real packed W4+D8 kernel would produce",
        },
    }

    print(f"\n[pack] ── result ──────────────────────────────────────────")
    print(f"  output         : {output_path}")
    print(f"  fingerprint    : {container.fingerprint()}")
    print(f"  bpw theoretical: {bpw:.1f}")
    print(f"  archive size   : {archive_mb:.0f} MB  "
          f"(theoretical {theoretical_mb:.0f} MB at real packing)")
    print(f"  total time     : {stats['timing']['total_s']:.1f}s")
    return stats


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pack a model into a signed .axm archive")
    p.add_argument("--model", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    p.add_argument("--revision", default=None)
    p.add_argument("--srd-top-k-pct", type=float, default=None,
                   help="SRD sparsity fraction (e.g. 0.25 = 7 bpw). "
                        "Omit for FP16 baseline.")
    p.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    p.add_argument("--hardware-map", default="gpu",
                   choices=["cpu", "gpu", "npu", "fpga", "compile_on_load"])
    p.add_argument("--output", type=str, required=True,
                   help="Output .axm archive path")
    p.add_argument("--stats-json", type=Path, default=None,
                   help="Optional path to write stats JSON")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    stats = pack_model(
        model_name=args.model,
        output_path=args.output,
        srd_top_k_pct=args.srd_top_k_pct,
        group_size=args.group_size,
        model_revision=args.revision,
        hardware_map=args.hardware_map,
    )
    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(stats, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
