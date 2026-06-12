"""
SRD Multi-Band Correction for Vision-Language Models
=====================================================

Three independent SRD correction bands applied to separate model components:

  Band 1 — vision_encoder : ViT image-patch processing layers
            Correction mode: full (uniform training, like TinyLlama)
            All ViT transformer layers get D8 restoration

  Band 2 — connector      : Cross-modal projector / fusion MLP
            Correction mode: full (small, critical, always correct all layers)
            This is the highest-leverage band — vision→language mapping
            degrades badly under Q4 in small connectors

  Band 3 — language_model : LM backbone (e.g. SmolLM2-135M inside SmolVLM)
            Correction mode: selective (reasoning layers 40–77% of LM depth)
            Same logic as single-modal SRD — already validated

The combination tests whether vision-encoder and connector corrections
compound with language corrections, or whether the bands are independent.

Supported architectures
-----------------------
  SmolVLM-256M   (Idefics3ForConditionalGeneration) — primary target
  moondream2     (MoondreamForConditionalGeneration) — secondary
  Qwen2-VL-0.5B  (Qwen2VLForConditionalGeneration)  — tertiary

CLI
---
  python research/quant/srd_multimodal.py \\
      --model HuggingFace/SmolVLM-Instruct \\
      --dry-run       # check component detection only

  python research/quant/srd_multimodal.py \\
      --model HuggingFace/SmolVLM-Instruct \\
      --bands all     # correct all three bands (default)

  python research/quant/srd_multimodal.py \\
      --model HuggingFace/SmolVLM-Instruct \\
      --bands lm      # language-only correction (baseline comparison)
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch
import torch.nn as nn

from axiom_quant import srd_quantize, srd_dequantize
from research.quant.srd_selective_sidecar import reasoning_layer_ids


# ── Architecture detection ────────────────────────────────────────────────

@dataclass
class VLMComponents:
    """Layer name prefixes for the three SRD bands of a VLM."""
    vision_prefix:    str        # e.g. "model.vision_model"
    connector_prefix: str        # e.g. "model.connector"
    lm_prefix:        str        # e.g. "model.text_model"
    n_lm_layers:      int = 0    # filled in after detection

    def band_for(self, name: str) -> Optional[str]:
        """Return 'vision', 'connector', or 'lm' for a layer name."""
        if name.startswith(self.vision_prefix):
            return "vision"
        if name.startswith(self.connector_prefix):
            return "connector"
        if name.startswith(self.lm_prefix):
            return "lm"
        return None


# Known prefix maps per architecture class name
_ARCH_PREFIXES: Dict[str, VLMComponents] = {
    # SmolVLM / Idefics3
    "Idefics3ForConditionalGeneration": VLMComponents(
        vision_prefix    = "model.vision_model",
        connector_prefix = "model.connector",
        lm_prefix        = "model.text_model",
    ),
    # moondream2
    "MoondreamForConditionalGeneration": VLMComponents(
        vision_prefix    = "vision_encoder",
        connector_prefix = "vision_projection",
        lm_prefix        = "text_model",
    ),
    # Qwen2-VL
    "Qwen2VLForConditionalGeneration": VLMComponents(
        vision_prefix    = "visual",
        connector_prefix = "visual.merger",
        lm_prefix        = "model",
    ),
    # LLaVA (1.5/1.6)
    "LlavaForConditionalGeneration": VLMComponents(
        vision_prefix    = "vision_tower",
        connector_prefix = "multi_modal_projector",
        lm_prefix        = "language_model",
    ),
}


def detect_components(model: nn.Module) -> VLMComponents:
    """Auto-detect VLM component prefixes from the model's class name.
    Falls back to heuristic scan if architecture is unknown.
    """
    arch = type(model).__name__
    if arch in _ARCH_PREFIXES:
        comps = _ARCH_PREFIXES[arch]
    else:
        comps = _heuristic_detect(model)

    # Count LM transformer layers
    seen: Set[int] = set()
    for name, _ in model.named_modules():
        if not name.startswith(comps.lm_prefix):
            continue
        parts = name.split(".")
        for i, p in enumerate(parts):
            if p == "layers" and i + 1 < len(parts):
                try:
                    seen.add(int(parts[i + 1]))
                except ValueError:
                    pass
    comps.n_lm_layers = len(seen)
    return comps


def _heuristic_detect(model: nn.Module) -> VLMComponents:
    """Fallback: scan for common submodule names."""
    names = {n.split(".")[0] for n, _ in model.named_modules() if "." in n}
    vision = next((n for n in names if any(k in n for k in
                   ("vision", "visual", "image_encoder", "vit"))), "vision_model")
    connector = next((n for n in names if any(k in n for k in
                      ("connector", "projector", "projection", "merger"))), "connector")
    lm = next((n for n in names if any(k in n for k in
               ("text_model", "language_model", "text", "model"))), "model")
    return VLMComponents(vision_prefix=vision, connector_prefix=connector, lm_prefix=lm)


# ── Per-band SRD correction ───────────────────────────────────────────────

@dataclass
class BandResult:
    band:       str
    corrected:  int = 0
    skipped:    int = 0

    def __str__(self) -> str:
        return f"{self.band}: corrected={self.corrected} skipped={self.skipped}"


def apply_multiband_srd(
    model: nn.Module,
    *,
    bands: str = "all",          # "all" | "lm" | "vision" | "connector"
    group_size: int = 64,
    alpha: float = 1.0,
    verbose: bool = True,
) -> Dict[str, BandResult]:
    """Apply SRD correction independently to each VLM component band.

    bands="all"       → correct vision + connector + LM selective
    bands="lm"        → language backbone only (single-modal comparison)
    bands="connector" → connector + LM (no vision encoder)
    bands="vision"    → vision + LM (no connector)

    Returns {band_name: BandResult} for inspection.

    Strategy per band:
      vision_encoder — full correction (all ViT layers)
      connector      — full correction (all projector layers)
      lm_backbone    — selective correction (reasoning layers 40–77%)

    All corrections are applied ONCE at call time. After this returns,
    inference is identical in speed to uncorrected Q4.
    """
    from research.quant.quantize_model import DEFAULT_SKIP_MODULES

    comps = detect_components(model)
    if verbose:
        print(f"[srd-multiband] architecture: {type(model).__name__}")
        print(f"  vision   prefix: {comps.vision_prefix}")
        print(f"  connector prefix: {comps.connector_prefix}")
        print(f"  lm       prefix: {comps.lm_prefix}  ({comps.n_lm_layers} layers)")

    active_bands = _resolve_bands(bands)
    if verbose:
        print(f"  active bands: {sorted(active_bands)}")

    # Identify reasoning layer IDs for LM selective mode
    reasoning_ids: Set[int] = set()
    if "lm" in active_bands and comps.n_lm_layers > 0:
        reasoning_ids = set(reasoning_layer_ids(comps.n_lm_layers))
        if verbose:
            print(f"  reasoning chunk: layers {min(reasoning_ids)}–{max(reasoning_ids)}"
                  f" ({len(reasoning_ids)} / {comps.n_lm_layers})")

    results: Dict[str, BandResult] = {b: BandResult(b) for b in active_bands}

    skip_set = tuple(DEFAULT_SKIP_MODULES)

    for name, module in model.named_modules():
        if not isinstance(module, nn.Linear):
            continue
        if any(s in name for s in skip_set):
            continue

        band = comps.band_for(name)
        if band not in active_bands:
            continue

        # Check in_features divisibility
        in_f = module.weight.shape[1]
        if in_f % group_size != 0:
            results[band].skipped += 1
            continue

        # For LM band: only apply to reasoning layers
        if band == "lm":
            lm_layer_idx = _lm_layer_idx(name, comps.lm_prefix)
            if lm_layer_idx not in reasoning_ids:
                results[band].skipped += 1
                continue

        with torch.no_grad():
            pack = srd_quantize(module.weight.detach(), group_size=group_size)
            corrected = srd_dequantize(pack, alpha=alpha)
            module.weight.copy_(corrected.to(module.weight.dtype))

        results[band].corrected += 1

    if verbose:
        for band, res in results.items():
            print(f"  [{band}] {res}")

    return results


def _resolve_bands(bands: str) -> Set[str]:
    if bands == "all":
        return {"vision", "connector", "lm"}
    return {b.strip() for b in bands.split(",")}


def _lm_layer_idx(name: str, lm_prefix: str) -> Optional[int]:
    """Extract transformer layer index from a module name under the LM prefix."""
    relative = name[len(lm_prefix):].lstrip(".")
    parts = relative.split(".")
    for i, p in enumerate(parts):
        if p == "layers" and i + 1 < len(parts):
            try:
                return int(parts[i + 1])
            except ValueError:
                pass
    return None


# ── Sidecar format (multi-band extension) ────────────────────────────────

def multiband_sidecar_dict(
    model: nn.Module,
    band_results: Dict[str, BandResult],
    *,
    base_model_id: str,
    pipeline_commit: str = "",
) -> dict:
    """Build a sidecar metadata dict for a multi-band SRD build.

    Compatible with check_srd_model.py — extend it to handle vlm type.
    """
    import datetime
    comps = detect_components(model)
    bands_meta = {}
    for band, res in band_results.items():
        prefix = {
            "vision":    comps.vision_prefix,
            "connector": comps.connector_prefix,
            "lm":        comps.lm_prefix,
        }.get(band, band)
        correction_mode = "selective" if band == "lm" else "full"
        entry = {
            "prefix":          prefix,
            "correction_mode": correction_mode,
            "layers_corrected": res.corrected,
            "layers_skipped":  res.skipped,
        }
        if band == "lm" and comps.n_lm_layers > 0:
            rids = reasoning_layer_ids(comps.n_lm_layers)
            entry["reasoning_layers"] = f"{min(rids)}-{max(rids)}"
        bands_meta[band] = entry

    return {
        "srd_version":     "4",
        "model_type":      "vlm",
        "base_model":      base_model_id,
        "architecture":    type(model).__name__,
        "bands":           bands_meta,
        "group_size":      64,
        "pipeline_commit": pipeline_commit,
        "build_timestamp": datetime.datetime.utcnow().isoformat(),
    }


# ── CLI (dry-run / component detection helper) ───────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SRD multi-band VLM correction")
    p.add_argument("--model", default="HuggingFace/SmolVLM-Instruct")
    p.add_argument("--bands", default="all",
                   help="Bands to correct: all | lm | connector | vision | comma-list")
    p.add_argument("--group-size", type=int, default=64)
    p.add_argument("--dry-run", action="store_true",
                   help="Detect components + count layers, skip correction")
    p.add_argument("--hf-token", default="")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    from transformers import AutoProcessor, AutoModelForVision2Seq

    dtype  = torch.float16 if torch.cuda.is_available() else torch.float32
    device = "cuda" if torch.cuda.is_available() else "cpu"
    kw = {"torch_dtype": dtype, "device_map": "auto"}
    if args.hf_token:
        kw["token"] = args.hf_token

    print(f"Loading {args.model} ...")
    model = AutoModelForVision2Seq.from_pretrained(args.model, **kw)
    model.eval()

    comps = detect_components(model)
    print(f"\nDetected components:")
    print(f"  vision    → {comps.vision_prefix}")
    print(f"  connector → {comps.connector_prefix}")
    print(f"  lm        → {comps.lm_prefix}  ({comps.n_lm_layers} layers)")

    # Count linears per band
    counts = {"vision": 0, "connector": 0, "lm": 0, "other": 0}
    for name, mod in model.named_modules():
        if isinstance(mod, nn.Linear):
            b = comps.band_for(name) or "other"
            counts[b] = counts.get(b, 0) + 1
    print(f"\nLinear layers per band: {counts}")

    if args.dry_run:
        print("\n[dry-run] component detection complete — skipping correction")
        return 0

    results = apply_multiband_srd(model, bands=args.bands,
                                  group_size=args.group_size)
    print("\nCorrection summary:")
    for band, res in results.items():
        print(f"  {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
