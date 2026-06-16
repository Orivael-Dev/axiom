"""Architecture-fingerprinted chunk detection for SRD quantization.

Derives per-layer correction weights from a short calibration pass rather
than relying on hardcoded 40-77% MET chunk boundaries.  The output is a
structured ``quant_map`` dict consumable by ``axiom_axm.AXMHeader`` and by
``srd_selective_sidecar.layer_alphas_from_quant_map()``.

Why this matters (CLAUDE.md Theme 2, next-step hint):
  The fixed 40-77% MET boundary is correct on average for general
  instruction-tuned models, but wrong for:
    - Code models: precision-sensitive layers skew earlier (~15-50%)
    - Tiny models (<200M): insufficient specialisation for chunk-based correction
    - Multimodal models: cross-modal connector is the critical target, not a
      standard MET layer range

  Calibration replaces the flat EQ preset with a per-architecture curve,
  making ``quant_map`` genuinely elastic.

Two calibration methods:

  ``weight_norm``  (default, no data required, <1 s)
      sensitivity_i = ||D8_i||_F / ||W4_i||_F
      High ratio → the residual carries substantial energy relative to the
      base → this layer benefits from correction.
      Equivalent to the existing ``bench_layer_sensitivity`` proxy.

  ``activation_error``  (requires a small text corpus, ~30 s on T4)
      For each linear layer, hook captures the input X, then computes:
        err_i = ||X @ (W - W_q).T||_F / ||X @ W.T||_F
      where W_q = SRD-quantized weight with alpha=0 (base only, no residual).
      High error → quantization strongly degrades this layer's output →
      the D8 residual matters here.

Both methods produce the same output dict shape so callers are method-agnostic.

Output ``quant_map`` structure:
    {
        "scheme": "srd",
        "group_size": 64,
        "calibration_method": "weight_norm",
        "architecture": {
            "model": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            "n_layers": 22,
            "n_linears_calibrated": 154
        },
        "threshold_pct": 75.0,         # top-X% sensitive layers → alpha=1.0
        "layer_alpha_map": {           # full per-layer alpha dict
            "model.layers.0.self_attn.q_proj": 0.12,
            ...
            "model.layers.8.mlp.gate_proj": 1.0,
        },
        "derived_chunk_start_frac": 0.36,  # equivalent MET start for sidecar
        "derived_chunk_end_frac":   0.82,  # equivalent MET end   for sidecar
        "sensitivity_stats": {
            "min": 0.0, "max": 1.0, "mean": 0.47,
            "p25": 0.22, "p50": 0.45, "p75": 0.72,
            "high_sensitivity_count": 38
        }
    }

CLI:
    python -m research.quant.calibrate_layer_alphas \\
        --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \\
        --method weight_norm \\
        --output research/quant/results/layer_alphas_tinyllama.json

    python -m research.quant.calibrate_layer_alphas \\
        --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \\
        --method activation_error \\
        --n-calibration-tokens 4096 \\
        --output research/quant/results/layer_alphas_tinyllama.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch                                                       # noqa: E402
import torch.nn as nn                                             # noqa: E402

from axiom_quant import (                                         # noqa: E402
    DEFAULT_GROUP_SIZE,
    srd_dequantize,
    srd_quantize,
)
from research.quant.quantize_model import (                       # noqa: E402
    DEFAULT_SKIP_MODULES,
    _iter_linears,
)


# ── Core calibration API ──────────────────────────────────────────────────────

def calibrate_weight_norm(
    model: nn.Module,
    *,
    group_size: int = DEFAULT_GROUP_SIZE,
    skip_modules: Tuple[str, ...] = DEFAULT_SKIP_MODULES,
) -> Dict[str, float]:
    """Per-layer sensitivity via weight residual norm ratio (no data required).

    Returns {layer_name: sensitivity} in [0, 1] — normalised across all layers
    so the layer with the highest residual-to-base ratio scores 1.0.
    """
    raw: Dict[str, float] = {}
    for name, layer in _iter_linears(model, skip_modules):
        W = layer.weight.detach().float()
        in_features = W.shape[1]
        if in_features % group_size != 0:
            continue
        pack = srd_quantize(W, group_size=group_size)
        out_feat, n_groups = pack.S4.shape
        W4f = pack.W4.float().view(out_feat, n_groups, group_size)
        D8f = pack.D8.float().view(out_feat, n_groups, group_size)
        base_norm  = (W4f * pack.S4.unsqueeze(-1)).norm(p="fro").item()
        resid_norm = (D8f * pack.S8.unsqueeze(-1)).norm(p="fro").item()
        ratio = resid_norm / base_norm if base_norm > 1e-12 else 0.0
        raw[name] = ratio

    return _normalise(raw)


def calibrate_activation_error(
    model: nn.Module,
    tokenizer,
    calibration_texts: List[str],
    *,
    n_calibration_tokens: int = 4096,
    group_size: int = DEFAULT_GROUP_SIZE,
    skip_modules: Tuple[str, ...] = DEFAULT_SKIP_MODULES,
    device: str = "cuda",
) -> Dict[str, float]:
    """Per-layer sensitivity via output-space quantisation error.

    For each linear layer, hooks capture one batch of the actual input X
    seen during the calibration forward pass, then compute:

        err = ||X @ (W - W_q).T||_F / ||X @ W.T||_F

    where W_q is the SRD base with alpha=0 (no residual applied).

    Returns {layer_name: sensitivity} in [0, 1].
    """
    captured_inputs: Dict[str, Optional[torch.Tensor]] = {}
    hooks = []

    def _make_hook(lname: str):
        def _hook(module: nn.Module, inp, _out):
            if lname not in captured_inputs:
                # Store first-seen input only; detach + move to CPU to save VRAM.
                x = inp[0].detach().cpu()
                captured_inputs[lname] = x
        return _hook

    for name, layer in _iter_linears(model, skip_modules):
        h = layer.register_forward_hook(_make_hook(name))
        hooks.append(h)

    # Run calibration texts through the model to populate captured_inputs.
    model.eval()
    total_tokens = 0
    with torch.no_grad():
        for text in calibration_texts:
            if total_tokens >= n_calibration_tokens:
                break
            enc = tokenizer(text, return_tensors="pt",
                            truncation=True, max_length=512).to(device)
            seq_len = enc["input_ids"].shape[1]
            model(**enc)
            total_tokens += seq_len

    for h in hooks:
        h.remove()

    raw: Dict[str, float] = {}
    for name, layer in _iter_linears(model, skip_modules):
        x_cpu = captured_inputs.get(name)
        if x_cpu is None:
            continue
        W = layer.weight.detach().float()
        in_features = W.shape[1]
        if in_features % group_size != 0:
            continue
        pack = srd_quantize(W, group_size=group_size)
        W_q = srd_dequantize(pack, alpha=0.0).float()

        x = x_cpu.float().view(-1, x_cpu.shape[-1])
        with torch.no_grad():
            out_fp   = x @ W.T
            out_base = x @ W_q.T
            delta = (out_fp - out_base).norm(p="fro").item()
            denom = out_fp.norm(p="fro").item()
        ratio = delta / denom if denom > 1e-12 else 0.0
        raw[name] = ratio

    return _normalise(raw)


def _normalise(raw: Dict[str, float]) -> Dict[str, float]:
    """Min-max normalise sensitivity scores to [0, 1]."""
    if not raw:
        return {}
    lo  = min(raw.values())
    hi  = max(raw.values())
    span = hi - lo
    if span < 1e-12:
        return {k: 1.0 for k in raw}
    return {k: (v - lo) / span for k, v in raw.items()}


# ── Alpha assignment from sensitivity scores ──────────────────────────────────

def sensitivity_to_alphas(
    sensitivity: Dict[str, float],
    *,
    threshold_pct: float = 75.0,
    alpha_ceil: float = 1.0,
    alpha_floor: float = 0.0,
) -> Dict[str, float]:
    """Convert normalised sensitivity scores to per-layer alpha values.

    Layers whose sensitivity exceeds the ``threshold_pct`` percentile of
    the sensitivity distribution receive ``alpha_ceil`` (full correction).
    All other layers receive an alpha proportional to their sensitivity,
    scaled into [``alpha_floor``, ``threshold_pct/100 * alpha_ceil``].

    This preserves a continuous alpha curve rather than a hard binary cut.
    """
    if not sensitivity:
        return {}
    scores = sorted(sensitivity.values())
    n = len(scores)
    pct_idx = max(0, int(math.ceil(n * (1 - threshold_pct / 100))) - 1)
    threshold_val = scores[pct_idx] if scores else 0.5

    alphas: Dict[str, float] = {}
    for name, s in sensitivity.items():
        if s >= threshold_val:
            alphas[name] = alpha_ceil
        else:
            proportion = s / threshold_val if threshold_val > 1e-12 else 0.0
            alphas[name] = round(alpha_floor + proportion * (alpha_ceil - alpha_floor), 4)
    return alphas


# ── Chunk-boundary derivation (backwards compat for sidecar code) ────────────

def derive_chunk_fracs(
    layer_alpha_map: Dict[str, float],
    n_layers: int,
    *,
    alpha_threshold: float = 0.5,
) -> Tuple[float, float]:
    """Derive equivalent chunk start/end fractions from per-layer alphas.

    Scans layer indices in order and returns (start_frac, end_frac) that
    bracket the contiguous run of high-alpha layers.  Used by the legacy
    selective sidecar code that expects MET-style chunk fractions.

    Returns (0.40, 0.77) as the fallback if the map is empty or no
    high-alpha layers are found.
    """
    if not layer_alpha_map or n_layers < 1:
        return 0.40, 0.77

    high_indices: List[int] = []
    for name, alpha in layer_alpha_map.items():
        if alpha < alpha_threshold:
            continue
        for part in name.split("."):
            try:
                idx = int(part)
                if 0 <= idx < n_layers:
                    high_indices.append(idx)
                    break
            except ValueError:
                pass

    if not high_indices:
        return 0.40, 0.77

    lo_idx = min(high_indices)
    hi_idx = max(high_indices) + 1
    return round(lo_idx / n_layers, 4), round(hi_idx / n_layers, 4)


def _sensitivity_stats(sensitivity: Dict[str, float]) -> dict:
    vals = sorted(sensitivity.values())
    n = len(vals)
    if n == 0:
        return {}

    def _pct(p: float) -> float:
        i = max(0, int(n * p) - 1)
        return round(vals[i], 4)

    return {
        "min":                    round(min(vals), 4),
        "max":                    round(max(vals), 4),
        "mean":                   round(sum(vals) / n, 4),
        "p25":                    _pct(0.25),
        "p50":                    _pct(0.50),
        "p75":                    _pct(0.75),
        "high_sensitivity_count": sum(1 for v in vals if v >= 0.5),
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def build_quant_map(
    model: nn.Module,
    model_name: str,
    tokenizer=None,
    *,
    method: str = "weight_norm",
    group_size: int = DEFAULT_GROUP_SIZE,
    threshold_pct: float = 75.0,
    n_calibration_tokens: int = 4096,
    calibration_texts: Optional[List[str]] = None,
    skip_modules: Tuple[str, ...] = DEFAULT_SKIP_MODULES,
    device: str = "cuda",
) -> dict:
    """Run calibration and return a signed-ready ``quant_map`` dict.

    This is the primary user-facing API.  The returned dict is ready to
    pass directly to ``axiom_axm.AXMHeader(quant_map=...)``.

    Parameters
    ----------
    model        : FP16 (or FP32) HuggingFace model already on ``device``
    model_name   : HF model ID, stored in the dict for provenance
    tokenizer    : required for method="activation_error", ignored otherwise
    method       : "weight_norm" (fast, no data) or "activation_error" (accurate)
    group_size   : SRD group size; must match the planned quantisation
    threshold_pct: top-X% sensitive layers receive alpha_ceil (default 75 → top 25%)
    n_calibration_tokens: approximate tokens run for activation_error
    calibration_texts: corpus for activation_error; defaults to short excerpts
    """
    t0 = time.monotonic()

    if method == "weight_norm":
        sensitivity = calibrate_weight_norm(
            model, group_size=group_size, skip_modules=skip_modules,
        )
    elif method == "activation_error":
        if tokenizer is None:
            raise ValueError("tokenizer required for method='activation_error'")
        texts = calibration_texts or _default_calibration_texts()
        sensitivity = calibrate_activation_error(
            model, tokenizer, texts,
            n_calibration_tokens=n_calibration_tokens,
            group_size=group_size, skip_modules=skip_modules, device=device,
        )
    else:
        raise ValueError(f"Unknown calibration method: {method!r}")

    layer_alpha_map = sensitivity_to_alphas(sensitivity, threshold_pct=threshold_pct)

    # Detect number of transformer layers from the alpha map
    all_indices: List[int] = []
    for name in layer_alpha_map:
        for part in name.split("."):
            try:
                all_indices.append(int(part))
            except ValueError:
                pass
    n_layers = max(all_indices) + 1 if all_indices else 0

    start_frac, end_frac = derive_chunk_fracs(layer_alpha_map, n_layers)

    elapsed = round(time.monotonic() - t0, 2)
    return {
        "scheme":           "srd",
        "group_size":       group_size,
        "calibration_method": method,
        "calibration_wallclock_s": elapsed,
        "architecture": {
            "model":                 model_name,
            "n_layers":              n_layers,
            "n_linears_calibrated":  len(layer_alpha_map),
        },
        "threshold_pct":           threshold_pct,
        "layer_alpha_map":         layer_alpha_map,
        "derived_chunk_start_frac": start_frac,
        "derived_chunk_end_frac":   end_frac,
        "sensitivity_stats":       _sensitivity_stats(sensitivity),
    }


def _default_calibration_texts() -> List[str]:
    """Short, genre-diverse excerpts for a lightweight calibration pass."""
    return [
        "The transformer architecture introduced the attention mechanism as a "
        "replacement for recurrent networks. Each layer computes queries, keys, "
        "and values from the input sequence.",
        "Quantization reduces model precision from FP32 or FP16 to INT8 or INT4. "
        "This compresses memory footprint and can accelerate inference on hardware "
        "that has efficient low-precision arithmetic units.",
        "The mitochondrion is a membrane-bound organelle found in the cytoplasm "
        "of eukaryotic cells. It generates most of the cell's supply of ATP.",
        "In mathematics, a prime number is a natural number greater than 1 that "
        "has no positive divisors other than 1 and itself.",
        "Climate change refers to long-term shifts in temperatures and weather "
        "patterns. Since the 1800s, human activities have been the main driver "
        "of climate change, primarily due to burning fossil fuels.",
    ]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Calibrate per-layer SRD alpha weights for architecture fingerprinting",
    )
    p.add_argument("--model",   required=True,
                   help="HuggingFace model ID, e.g. TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    p.add_argument("--revision", default=None)
    p.add_argument("--method", choices=["weight_norm", "activation_error"],
                   default="weight_norm",
                   help="Calibration method (default: weight_norm, no corpus needed)")
    p.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    p.add_argument("--threshold-pct", type=float, default=75.0,
                   help="Top-X%% sensitive layers receive alpha=1.0 (default 75)")
    p.add_argument("--n-calibration-tokens", type=int, default=4096)
    p.add_argument("--device", default=None)
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype  = torch.float16 if device == "cuda" else torch.float32

    print(f"[calibrate] loading {args.model} ({dtype}) on {device}...")
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, revision=args.revision, torch_dtype=dtype,
    ).to(device)
    model.eval()

    print(f"[calibrate] method={args.method}, threshold_pct={args.threshold_pct}")
    qmap = build_quant_map(
        model, args.model,
        tokenizer=tokenizer if args.method == "activation_error" else None,
        method=args.method,
        group_size=args.group_size,
        threshold_pct=args.threshold_pct,
        n_calibration_tokens=args.n_calibration_tokens,
        device=device,
    )

    n_high = qmap["sensitivity_stats"].get("high_sensitivity_count", 0)
    n_total = len(qmap["layer_alpha_map"])
    print(f"[calibrate] {n_high}/{n_total} layers above 0.5 sensitivity threshold")
    print(f"[calibrate] derived chunk: [{qmap['derived_chunk_start_frac']:.2f}, "
          f"{qmap['derived_chunk_end_frac']:.2f}]  "
          f"(vs hardcoded [0.40, 0.77])")
    print(f"[calibrate] elapsed: {qmap['calibration_wallclock_s']:.1f}s")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(qmap, indent=2) + "\n")
    print(f"[calibrate] wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
