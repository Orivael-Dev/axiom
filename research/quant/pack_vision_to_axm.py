"""Pack a vision model into a signed .axm container.

Supports SmolVLM (256M, 500M), Florence-2 (base/large), LLaVA-style models,
PaliGemma, Idefics3, and any other HuggingFace vision-language model that
AutoModelForVision2Seq can load.

What this does vs the text packer (pack_to_axm.py):
  - Loads via AutoModelForVision2Seq + AutoProcessor (not AutoModelForCausalLM)
  - Applies SRD fake-quantization to all nn.Linear layers — same kernel as text
    (Conv2d layers in the vision encoder are automatically skipped)
  - Saves both model weights and processor config into the AXM
  - Records modality="vision" in quant_map so fleet_router routes correctly
  - No GGUF extraction — inference uses transformers + bitsandbytes at runtime

Bpw accounting:
  - SRD is applied to linear layers (~70-85% of params in typical vision models)
  - Conv2d / patch-embed layers stay FP16 (not targeted by SRD)
  - Reported bpw is the linear-layer bpw (same convention as text packers)

Usage
-----
    python3 research/quant/pack_vision_to_axm.py \\
        --model HuggingFaceTB/SmolVLM-256M-Instruct \\
        --output /workspace/smolvlm_256m.axm

    # Skip SRD — store FP16 weights (useful as baseline or for tiny models)
    python3 research/quant/pack_vision_to_axm.py \\
        --model HuggingFaceTB/SmolVLM-256M-Instruct \\
        --output /workspace/smolvlm_256m_fp16.axm \\
        --no-srd

    # Use smaller group size for better quality on tiny models
    python3 research/quant/pack_vision_to_axm.py \\
        --model microsoft/Florence-2-base \\
        --output /workspace/florence2_base.axm \\
        --group-size 32
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

import torch                                                        # noqa: E402

from axiom_axm import AXMContainer, FORMAT_VERSION                 # noqa: E402
from axiom_quant import DEFAULT_GROUP_SIZE, srd_bits_per_weight    # noqa: E402
from research.quant.quantize_model import (                         # noqa: E402
    DEFAULT_SKIP_MODULES,
    quantize_hf_model_inplace,
)

# Modules to skip in vision models (on top of the text defaults)
_VISION_SKIP_MODULES: tuple[str, ...] = DEFAULT_SKIP_MODULES + (
    "patch_embedding",
    "pos_embed",
    "position_embedding",
)


def _vision_quant_map(
    top_k_pct:  Optional[float],
    group_size: int,
    bpw:        float,
    model_type: str,
) -> dict:
    if top_k_pct is None:
        return {
            "scheme":      "fp16",
            "bpw":         16.0,
            "modality":    "vision",
            "architecture": model_type,
        }
    return {
        "scheme":      "srd",
        "group_size":  group_size,
        "top_k_pct":   top_k_pct,
        "bpw":         round(bpw, 4),
        "alpha":       1.0,
        "modality":    "vision",
        "architecture": model_type,
        "note": (
            "SRD applied to nn.Linear layers only; Conv2d/patch-embed stay FP16. "
            "bpw reflects linear-layer compression ratio."
        ),
    }


def _get_model_type(model_name: str) -> str:
    """Read model_type from HF config without loading weights."""
    try:
        from transformers import AutoConfig
        cfg = AutoConfig.from_pretrained(model_name, trust_remote_code=True)
        return getattr(cfg, "model_type", "unknown")
    except Exception:
        return "unknown"


def pack_vision_model(
    model_name:    str,
    output_path:   str,
    *,
    srd_top_k_pct: Optional[float] = 0.25,   # None = FP16 (no SRD)
    group_size:    int              = DEFAULT_GROUP_SIZE,
    hardware_map:  str              = "auto",
    compresslevel: int              = 1,
    stats_json:    Optional[str]    = None,
) -> dict:
    """Quantize (SRD) and pack a vision-language model into .axm.

    Returns a stats dict matching the format used by pack_to_axm.py.
    """
    try:
        from transformers import AutoModelForVision2Seq, AutoProcessor
    except ImportError as e:
        print(f"[pack_vision] transformers>=4.45 required: {e}")
        sys.exit(1)

    model_type = _get_model_type(model_name)
    print(f"[pack_vision] model_type: {model_type}")

    # Device selection — same logic as pack_to_axm.py
    cuda_ok = torch.cuda.is_available()
    vram_gb  = torch.cuda.get_device_properties(0).total_memory / 1024**3 if cuda_ok else 0

    # Vision models in the ≤0.5B target are tiny — always fit on one device
    if cuda_ok and vram_gb >= 2.0:
        device     = "cuda"
        dtype      = torch.float16
        device_map = None
    else:
        device     = "cpu"
        dtype      = torch.float32
        device_map = None

    print(f"[pack_vision] loading {model_name} (dtype={dtype}, device={device})...")
    t0 = time.monotonic()

    load_kwargs: dict = dict(torch_dtype=dtype, low_cpu_mem_usage=True, trust_remote_code=True)
    if device_map:
        load_kwargs["device_map"] = device_map

    try:
        model = AutoModelForVision2Seq.from_pretrained(model_name, **load_kwargs)
    except Exception as e:
        # Some vision models (e.g. Florence-2) use AutoModel
        print(f"[pack_vision] AutoModelForVision2Seq failed ({e}), trying AutoModel...")
        from transformers import AutoModel
        model = AutoModel.from_pretrained(model_name, **load_kwargs)

    if device_map is None:
        model = model.to(device)

    processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
    model.eval()
    load_s = time.monotonic() - t0
    print(f"[pack_vision] loaded in {load_s:.1f}s")

    # Count parameters
    total_params = sum(p.numel() for p in model.parameters())
    linear_params = sum(
        p.numel() for n, m in model.named_modules()
        if isinstance(m, torch.nn.Linear)
        and not any(skip in n for skip in _VISION_SKIP_MODULES)
        for p in m.parameters()
    )
    print(f"[pack_vision] total params: {total_params/1e6:.1f}M  "
          f"linear (SRD target): {linear_params/1e6:.1f}M")

    # SRD quantization (linear layers only)
    bpw = 16.0
    packed = None
    quant_s = 0.0
    if srd_top_k_pct is not None:
        print(f"[pack_vision] SRD quantizing linear layers "
              f"(top_k_pct={srd_top_k_pct}, group={group_size})...")
        t1 = time.monotonic()
        packed = quantize_hf_model_inplace(
            model, alpha=1.0, group_size=group_size,
            top_k_pct=srd_top_k_pct, progress=True,
            skip_modules=_VISION_SKIP_MODULES,
        )
        quant_s = time.monotonic() - t1
        if packed:
            first_pack = next(iter(packed.values()))
            bpw = srd_bits_per_weight(first_pack)
        print(f"[pack_vision] quantized {len(packed or {})} layers "
              f"in {quant_s:.1f}s (~{bpw:.1f} bpw linear-layer)")

    quant_map = _vision_quant_map(srd_top_k_pct, group_size, bpw, model_type)
    short_name = Path(model_name).name

    with tempfile.TemporaryDirectory(prefix="axm_vision_") as tmp:
        weights_dir = Path(tmp) / "weights"
        weights_dir.mkdir()

        print(f"[pack_vision] saving checkpoint (safetensors)...")
        t2 = time.monotonic()
        model.save_pretrained(weights_dir, safe_serialization=True)
        processor.save_pretrained(weights_dir)
        save_s = time.monotonic() - t2

        spec = {
            "format_version": FORMAT_VERSION,
            "core_logic":     f"{short_name}_vision_srd" if srd_top_k_pct else f"{short_name}_vision_fp16",
            "quant_map":      quant_map,
            "hardware_map":   hardware_map,
            "safety_proofs":  True,
            "core": {
                "name":          model_name,
                "modality":      "vision",
                "model_type":    model_type,
                "quant_map":     quant_map,
                "skip_modules":  list(_VISION_SKIP_MODULES),
            },
        }

        print(f"[pack_vision] writing .axm archive...")
        t3 = time.monotonic()
        container = AXMContainer.pack(
            weights_dir,
            output_path=Path(output_path),
            spec=spec,
            compresslevel=compresslevel,
        )
        pack_s = time.monotonic() - t3

    out_path = Path(output_path)
    out_gb   = out_path.stat().st_size / 1024**3
    total_s  = time.monotonic() - t0
    fp       = container.fingerprint

    print(f"[pack_vision] ✓ done in {total_s:.1f}s")
    print(f"  .axm:        {out_path}  ({out_gb:.3f} GB)")
    print(f"  fingerprint: {fp}")
    print(f"  bpw:         {bpw:.1f}  (linear layers)")

    stats = {
        "model":         model_name,
        "model_type":    model_type,
        "modality":      "vision",
        "fingerprint":   fp,
        "proofs":        len(container.proofs),
        "bpw_theoretical": bpw,
        "total_params_m":  round(total_params / 1e6, 1),
        "linear_params_m": round(linear_params / 1e6, 1),
        "size":          {"archive_mb": round(out_gb * 1024, 1)},
        "timing":        {
            "load_s":  round(load_s, 1),
            "quant_s": round(quant_s, 1),
            "save_s":  round(save_s, 1),
            "pack_s":  round(pack_s, 1),
            "total_s": round(total_s, 1),
        },
        "quant_map": quant_map,
    }

    if stats_json:
        Path(stats_json).write_text(json.dumps(stats, indent=2))
        print(f"  stats:       {stats_json}")

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Pack a vision-language model into a signed .axm container (SRD-4).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--model",      required=True,
                    help="HuggingFace model ID or local path")
    ap.add_argument("--output",     required=True,
                    help="Output .axm path")
    ap.add_argument("--no-srd",     action="store_true",
                    help="Skip SRD quantization — store FP16 weights")
    ap.add_argument("--top-k-pct",  type=float, default=0.25,
                    help="SRD top-k-pct (default 0.25 ≈ 4.5 bpw)")
    ap.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE,
                    help=f"SRD group size (default {DEFAULT_GROUP_SIZE})")
    ap.add_argument("--stats-json", default=None,
                    help="Write pack stats to this JSON file")
    args = ap.parse_args()

    pack_vision_model(
        model_name    = args.model,
        output_path   = args.output,
        srd_top_k_pct = None if args.no_srd else args.top_k_pct,
        group_size    = args.group_size,
        stats_json    = args.stats_json,
    )


if __name__ == "__main__":
    main()
