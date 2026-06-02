"""Per-layer residual sensitivity analysis for SRD-quantized models.

Two modes:

  Fast (default): for each quantized linear layer, computes the
  "residual norm ratio" = ||D8_reconstructed||_F / ||W4_reconstructed||_F.
  Layers with a high ratio benefit most from the residual; those with a
  near-zero ratio are candidates for alpha=0 (residue-free) to save bpw.
  No PPL evaluation — runs in <5 minutes on any hardware.

  Full (--run-ppl): additionally runs group ablation — for each of the
  7 canonical LLaMA-style layer types (q/k/v/o/gate/up/down), zeros out
  the D8 residual for that group and measures PPL delta. Produces a
  ranked table guiding the layer_alphas config for the selective sweep.
  Runtime: ~7 × PPL eval ≈ 30–60 min on T4 depending on model size.

CLI:
    # Fast proxy only
    python -m research.quant.bench_layer_sensitivity \\
        --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \\
        --output research/quant/results/layer_sensitivity_tinyllama.json

    # Full ablation
    python -m research.quant.bench_layer_sensitivity \\
        --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \\
        --run-ppl \\
        --output research/quant/results/layer_sensitivity_tinyllama.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch                                                        # noqa: E402
import torch.nn as nn                                              # noqa: E402

from axiom_quant import (                                          # noqa: E402
    DEFAULT_GROUP_SIZE,
    SRDPackedTensor,
    srd_dequantize,
)
from research.quant.quantize_model import (                        # noqa: E402
    DEFAULT_SKIP_MODULES,
    quantize_hf_model_inplace,
    _iter_linears,
)
from research.quant.bench_perplexity import eval_wikitext2         # noqa: E402

# Canonical LLaMA-style layer type substrings for group ablation
_GROUP_ABLATION_KEYS = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


def _residual_norm_ratio(pack: SRDPackedTensor) -> float:
    """||reconstructed D8|| / ||reconstructed W4|| (Frobenius norms).

    Proxy for how much the residual contributes relative to the base —
    layers with a high ratio benefit most from storing D8.
    """
    out_features, in_features = pack.W4.shape
    n_groups = in_features // pack.group_size

    W4f = pack.W4.float().view(out_features, n_groups, pack.group_size)
    D8f = pack.D8.float().view(out_features, n_groups, pack.group_size)

    base_norm = (W4f * pack.S4.unsqueeze(-1)).norm(p="fro").item()
    resid_norm = (D8f * pack.S8.unsqueeze(-1)).norm(p="fro").item()

    if base_norm < 1e-12:
        return 0.0
    return resid_norm / base_norm


def compute_sensitivity_proxy(
    packed: Dict[str, SRDPackedTensor],
) -> List[dict]:
    """Compute residual norm ratio for every packed layer, sorted descending."""
    rows = []
    for name, pack in packed.items():
        ratio = _residual_norm_ratio(pack)
        rows.append({
            "layer_name": name,
            "residual_norm_ratio": round(ratio, 6),
            "top_k_pct": pack.top_k_pct,
        })
    rows.sort(key=lambda r: r["residual_norm_ratio"], reverse=True)
    return rows


def run_group_ablation(
    model_name: str,
    model_revision: Optional[str],
    group_size: int,
    *,
    stride: int = 512,
    context: int = 2048,
    max_tokens: Optional[int] = None,
    device: Optional[str] = None,
) -> List[dict]:
    """Evaluate PPL impact of zeroing each canonical layer group's residual.

    Returns list of {group_key, ppl_baseline, ppl_ablated, ppl_delta}
    sorted by ppl_delta descending (most sensitive group first).
    """
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device == "cuda" else torch.float32

    print(f"[sensitivity] loading {model_name} for group ablation...")
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=model_revision)

    # Baseline: fully quantized (alpha=1 on all layers)
    print("[sensitivity] evaluating baseline (all layers alpha=1)...")
    model = AutoModelForCausalLM.from_pretrained(
        model_name, revision=model_revision, torch_dtype=dtype
    ).to(device)
    model.eval()
    quantize_hf_model_inplace(
        model, alpha=1.0, group_size=group_size, progress=False,
    )
    ppl_baseline, _ = eval_wikitext2(
        model, tokenizer, stride=stride, context=context,
        max_tokens=max_tokens, device=device,
    )
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    results = []
    for group_key in _GROUP_ABLATION_KEYS:
        print(f"[sensitivity] ablating group '{group_key}'...")
        model = AutoModelForCausalLM.from_pretrained(
            model_name, revision=model_revision, torch_dtype=dtype
        ).to(device)
        model.eval()

        # alpha=1 globally, but alpha=0 for this group
        from research.quant.quantize_model import _resolve_layer_alpha
        quantize_hf_model_inplace(
            model, alpha=1.0, group_size=group_size, progress=False,
            layer_alphas={group_key: 0.0},
        )
        ppl_ablated, _ = eval_wikitext2(
            model, tokenizer, stride=stride, context=context,
            max_tokens=max_tokens, device=device,
        )
        delta = round(ppl_ablated - ppl_baseline, 4)
        print(f"[sensitivity]   {group_key}: baseline={ppl_baseline:.4f}, "
              f"ablated={ppl_ablated:.4f}, delta={delta:+.4f}")
        results.append({
            "group_key":    group_key,
            "ppl_baseline": round(ppl_baseline, 4),
            "ppl_ablated":  round(ppl_ablated, 4),
            "ppl_delta":    delta,
        })
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    results.sort(key=lambda r: r["ppl_delta"], reverse=True)
    return results


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SRD per-layer residual sensitivity")
    p.add_argument("--model", default="TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    p.add_argument("--revision", default=None)
    p.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    p.add_argument("--run-ppl", action="store_true",
                   help="Also run group ablation PPL eval (slow, ~7× PPL evals)")
    p.add_argument("--stride", type=int, default=512)
    p.add_argument("--context", type=int, default=2048)
    p.add_argument("--max-tokens", type=int, default=None,
                   help="Cap PPL eval at N tokens (useful for quick smoke test)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--device", default=None)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device == "cuda" else torch.float32

    print(f"[sensitivity] loading {args.model} for proxy analysis...")
    model = AutoModelForCausalLM.from_pretrained(
        args.model, revision=args.revision, torch_dtype=dtype
    ).to(device)
    model.eval()

    packed = quantize_hf_model_inplace(
        model, alpha=1.0, group_size=args.group_size, progress=True,
    )

    proxy_rows = compute_sensitivity_proxy(packed)
    print(f"\n[sensitivity] Top-10 layers by residual norm ratio:")
    for row in proxy_rows[:10]:
        print(f"  {row['layer_name']}: ratio={row['residual_norm_ratio']:.4f}")

    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    output: dict = {
        "model": args.model,
        "model_revision": args.revision,
        "group_size": args.group_size,
        "proxy_rows": proxy_rows,
    }

    if args.run_ppl:
        ablation_rows = run_group_ablation(
            args.model, args.revision, args.group_size,
            stride=args.stride, context=args.context,
            max_tokens=args.max_tokens, device=device,
        )
        output["ablation_rows"] = ablation_rows
        print("\n[sensitivity] Group ablation results (most sensitive first):")
        for row in ablation_rows:
            print(f"  {row['group_key']}: delta={row['ppl_delta']:+.4f}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(f"\n[sensitivity] wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
