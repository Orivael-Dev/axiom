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
                   bpw: float, real_pack: bool = False) -> dict:
    qmap = {
        "scheme":      "srd",
        "group_size":  group_size,
        "top_k_pct":   top_k_pct,
        "bpw":         round(bpw, 4),
        "alpha":       1.0,
        "packed":      real_pack,
    }
    qmap["note"] = (
        "E3 real-packed — W4 nibble-packed + sparse-D8 bitmask; weights are "
        "genuinely smaller than FP16 on disk"
        if real_pack else
        "fake-quant — weights stored as FP16 on the SRD grid; real storage "
        "savings require packed W4+D8 kernel (use --real-pack)"
    )
    return qmap


def _fp16_quant_map() -> dict:
    return {"scheme": "fp16", "bpw": 16.0}


def _model_size_gb(model_name: str, revision=None) -> float:
    """Estimate FP16 model size in GB from HF config without loading weights."""
    try:
        from transformers import AutoConfig
        cfg = AutoConfig.from_pretrained(model_name, revision=revision)
        vocab  = getattr(cfg, "vocab_size", 32000)
        hidden = getattr(cfg, "hidden_size", 4096)
        layers = getattr(cfg, "num_hidden_layers", 32)
        inter  = getattr(cfg, "intermediate_size", hidden * 4)
        heads  = getattr(cfg, "num_attention_heads", 32)
        kv_h   = getattr(cfg, "num_key_value_heads", heads)
        head_d = hidden // heads
        # Rough param count: embed + (attn + mlp) × layers + lm_head
        params = vocab * hidden * 2 + layers * (
            hidden * (heads + kv_h + kv_h) * head_d   # q + k + v proj
            + heads * head_d * hidden                   # o proj
            + hidden * inter * 3                        # gate + up + down
        )
        return params * 2 / 1024**3   # FP16 bytes
    except Exception:
        return 0.0


def pack_model(
    model_name: str,
    output_path: str,
    *,
    srd_top_k_pct: Optional[float],
    group_size: int,
    model_revision: Optional[str],
    hardware_map: str,
    compresslevel: int = 1,
    real_pack: bool = False,
) -> dict:
    """Quantize (if requested) and pack to .axm. Returns a stats dict."""
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import psutil

    # Auto-select device and whether to use device_map for large models.
    # A model that exceeds 80% of available VRAM needs CPU help or device_map.
    cuda_available = torch.cuda.is_available()
    vram_gb  = torch.cuda.get_device_properties(0).total_memory / 1024**3 if cuda_available else 0
    ram_gb   = psutil.virtual_memory().available / 1024**3
    est_gb   = _model_size_gb(model_name, model_revision) or 2.0
    big_model = est_gb > 8.0

    if cuda_available and est_gb <= vram_gb * 0.80:
        device     = "cuda"
        dtype      = torch.float16
        device_map = None
        print(f"[pack] loading {model_name} on GPU (est {est_gb:.1f} GB, VRAM {vram_gb:.1f} GB)...")
    elif cuda_available and (est_gb <= vram_gb + ram_gb * 0.60):
        # Too big for VRAM alone — split across GPU + CPU RAM
        device     = "cuda"
        dtype      = torch.float16
        device_map = "auto"
        print(f"[pack] loading {model_name} with device_map=auto "
              f"(est {est_gb:.1f} GB > VRAM {vram_gb:.1f} GB — splitting GPU+CPU)...")
    else:
        device     = "cpu"
        dtype      = torch.float32
        device_map = None
        print(f"[pack] loading {model_name} on CPU "
              f"(est {est_gb:.1f} GB, available RAM {ram_gb:.1f} GB)...")

    t0 = time.monotonic()
    load_kwargs: dict = dict(
        revision=model_revision,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
    )
    if device_map:
        load_kwargs["device_map"] = device_map
    model = AutoModelForCausalLM.from_pretrained(model_name, **load_kwargs)
    if device_map is None:
        model = model.to(device)
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=model_revision)
    model.eval()
    load_s = time.monotonic() - t0
    print(f"[pack] model loaded in {load_s:.1f}s")

    if real_pack and srd_top_k_pct is None:
        raise ValueError("--real-pack requires --srd-top-k-pct (FP16 has "
                         "nothing to pack)")

    bpw = 16.0
    packed = None
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
        _srd_quant_map(srd_top_k_pct, group_size, bpw, real_pack=real_pack)
        if srd_top_k_pct is not None else _fp16_quant_map()
    )

    with tempfile.TemporaryDirectory(prefix="axm_weights_") as tmp:
        weights_dir = Path(tmp)
        t2 = time.monotonic()
        if real_pack:
            print(f"[pack] real-packing weights (W4 nibble + sparse-D8)...")
            from research.quant.srd_realpack import save_real_packed
            rp = save_real_packed(
                model, packed, weights_dir,
                alpha=1.0, group_size=group_size, top_k_pct=srd_top_k_pct,
                config=model.config, tokenizer=tokenizer,
            )
            print(f"[pack] real-packed: dense={rp['dense_mb']:.0f} MB + "
                  f"packed={rp['packed_mb']:.0f} MB "
                  f"({rp['n_quantized_layers']} layers)")
        else:
            print(f"[pack] saving checkpoint to temp dir...")
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
            compresslevel=compresslevel,
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
            "real_pack":        real_pack,
            "note": (
                "archive_mb = actual E3 real-packed .axm on disk; "
                "theoretical_mb = ideal bit-cost (archive is slightly larger "
                "due to FP16 dense params + zip overhead)"
                if real_pack else
                "archive_mb = actual .axm on disk (FP16 fake-quant); "
                "theoretical_mb = what real packed W4+D8 kernel would produce"
            ),
        },
    }

    print(f"\n[pack] ── result ──────────────────────────────────────────")
    print(f"  output         : {output_path}")
    print(f"  fingerprint    : {container.fingerprint()}")
    print(f"  bpw theoretical: {bpw:.1f}")
    if real_pack:
        print(f"  archive size   : {archive_mb:.0f} MB  "
              f"(REAL-PACKED — genuine on-disk savings)")
    else:
        print(f"  archive size   : {archive_mb:.0f} MB  "
              f"(theoretical {theoretical_mb:.0f} MB at real packing)")
    print(f"  total time     : {stats['timing']['total_s']:.1f}s")
    return stats


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pack a model into a signed .axm archive")
    p.add_argument("--model", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    p.add_argument("--revision", default=None)
    p.add_argument("--srd-top-k-pct", type=float, default=None,
                   help="SRD sparsity fraction (e.g. 0.25 = ~7 bpw, 0 = W4-only ~4.5 bpw). "
                        "Omit for FP16 baseline.")
    p.add_argument("--srd4", action="store_true",
                   help="Shorthand for --srd-top-k-pct 0 (pure W4, no D8 residual, ~4.5 bpw). "
                        "Equivalent to the SRD-4 mode.")
    p.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    p.add_argument("--hardware-map", default="gpu",
                   choices=["cpu", "gpu", "npu", "fpga", "compile_on_load"])
    p.add_argument("--compresslevel", type=int, default=1,
                   choices=range(0, 10), metavar="[0-9]",
                   help="zip DEFLATE level. 1 (default) = fast; 0 = store "
                        "(no compression, fastest); 6-9 = smaller, slower.")
    p.add_argument("--real-pack", action="store_true",
                   help="E3: store W4-nibble + sparse-D8 packed weights "
                        "(genuinely smaller on disk) instead of FP16 fake-quant. "
                        "Requires --srd-top-k-pct.")
    p.add_argument("--output", type=str, required=True,
                   help="Output .axm archive path")
    p.add_argument("--stats-json", type=Path, default=None,
                   help="Optional path to write stats JSON")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    top_k = args.srd_top_k_pct
    if args.srd4:
        if top_k is not None and top_k != 0.0:
            raise SystemExit("--srd4 and --srd-top-k-pct are mutually exclusive")
        top_k = 0.0
    stats = pack_model(
        model_name=args.model,
        output_path=args.output,
        srd_top_k_pct=top_k,
        group_size=args.group_size,
        model_revision=args.revision,
        hardware_map=args.hardware_map,
        compresslevel=args.compresslevel,
        real_pack=args.real_pack,
    )
    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(stats, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
