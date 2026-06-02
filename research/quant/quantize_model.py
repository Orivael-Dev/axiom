"""Apply SRD fake-quantization to a HuggingFace model in-place.

For each nn.Linear (other than the skip list), this module:
  1. Runs `srd_quantize(layer.weight)`
  2. Writes `srd_dequantize(pack, alpha)` back into `layer.weight`
  3. Stashes the packed tensor in the returned dict for optional
     .axm packing later (Phase D)

After this returns, the model behaves like a fake-quantized variant —
weights still occupy FP16/FP32 memory, but their values lie on the
SRD reconstruction grid. This is the standard quality-only evaluation
pattern (matches AQLM / QuIP# eval scripts); real memory savings need
fused kernels which are explicitly out of scope.

CLI:
    python -m research.quant.quantize_model \\
        --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \\
        --alpha 1.0 \\
        --prompt "Once upon a time, " --tokens 80

Expected output: coherent English at alpha=1, noticeably degraded but
still English at alpha=0. If alpha=0 produces gibberish, the kernel
is bug-bait.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

# Allow `python research/quant/quantize_model.py` to find axiom_quant
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch                                                       # noqa: E402
import torch.nn as nn                                              # noqa: E402

from axiom_quant import (                                          # noqa: E402
    DEFAULT_GROUP_SIZE,
    SRDPackedTensor,
    srd_dequantize,
    srd_quantize,
    srd_quantize_per_tensor,
)

DEFAULT_SKIP_MODULES: Tuple[str, ...] = ("lm_head", "embed_tokens")


def _iter_linears(model: nn.Module,
                  skip_modules: Iterable[str]) -> Iterable[tuple[str, nn.Linear]]:
    """Yield (qualified_name, Linear) pairs, skipping names that
    contain any of the skip-list substrings."""
    skip_set = tuple(skip_modules)
    for name, module in model.named_modules():
        if not isinstance(module, nn.Linear):
            continue
        if any(skip in name for skip in skip_set):
            continue
        yield name, module


def _resolve_layer_alpha(
    name: str,
    layer_alphas: Optional[Dict[str, float]],
    default_alpha: float,
) -> float:
    """Look up per-layer alpha override; fall back to default_alpha."""
    if layer_alphas is None:
        return default_alpha
    for key, val in layer_alphas.items():
        if key in name:
            return val
    return default_alpha


def quantize_hf_model_inplace(
    model: nn.Module,
    *,
    alpha: float = 1.0,
    group_size: int = DEFAULT_GROUP_SIZE,
    skip_modules: Tuple[str, ...] = DEFAULT_SKIP_MODULES,
    per_tensor: bool = False,
    top_k_pct: float = 1.0,
    layer_alphas: Optional[Dict[str, float]] = None,
    progress: bool = True,
) -> Dict[str, SRDPackedTensor]:
    """Walk `model`, replace each Linear.weight with its SRD
    reconstruction at the given alpha. Returns a {name: packed_tensor}
    dict so callers can dump packing stats or write a .axm later.

    Args:
      model: any HuggingFace PreTrainedModel-shaped object
      alpha: residue mixing knob, see axiom_quant module docstring
      group_size: SRD per-block scale window (default 64)
      skip_modules: substrings — Linears whose names contain any of
                    these are left untouched (default: lm_head + embed)
      per_tensor: if True, use single per-row scale instead of per-block
      top_k_pct: fraction of D8 residual elements to retain (1.0 = dense).
                 Fills the 5–12 bpw dead zone on the Pareto curve.
      layer_alphas: optional {substring: alpha} dict to override alpha
                    per layer. Keys are matched as substrings of layer names
                    (e.g. "down_proj" matches any layer whose name contains
                    that string). Unmatched layers use the global `alpha`.
      progress: print a one-line summary at the end

    Bug-bait guard: if `lm_head` appears in the model's modules and
    is NOT in skip_modules, raises. The default skip list MUST be
    overridden explicitly to quantize lm_head; silently quantizing
    it has been known to corrupt the eval.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")
    if "lm_head" not in skip_modules:
        raise ValueError(
            "lm_head must be in skip_modules unless you explicitly "
            "opt out (pass skip_modules=()). Quantizing lm_head "
            "silently corrupts eval; if you really want it, do it "
            "knowingly."
        )

    packed: Dict[str, SRDPackedTensor] = {}
    total_layers = 0
    skipped = 0
    quantize_fn = srd_quantize_per_tensor if per_tensor else (
        lambda W: srd_quantize(W, group_size=group_size, top_k_pct=top_k_pct)
    )

    for name, layer in _iter_linears(model, skip_modules):
        in_features = layer.weight.shape[1]
        if not per_tensor and in_features % group_size != 0:
            # Some Linears (especially in newer arches) have odd
            # in_features. Skip them cleanly rather than padding.
            skipped += 1
            continue
        effective_alpha = _resolve_layer_alpha(name, layer_alphas, alpha)
        with torch.no_grad():
            pack = quantize_fn(layer.weight.detach())
            W_hat = srd_dequantize(pack, alpha=effective_alpha)
            layer.weight.copy_(W_hat.to(layer.weight.dtype))
        packed[name] = pack
        total_layers += 1

    if progress:
        print(f"[srd] quantized {total_layers} Linears "
              f"(alpha={alpha}, group_size={group_size}, "
              f"per_tensor={per_tensor}, top_k_pct={top_k_pct}, "
              f"skipped_non_divisible={skipped})")
    return packed


def smoke_generate(
    model: nn.Module,
    tokenizer,
    prompt: str,
    n_tokens: int = 80,
    *,
    device: Optional[str] = None,
) -> str:
    """One greedy generation. Used to eyeball whether quantization
    has broken the model's basic coherence before spending an hour
    on a perplexity sweep."""
    if device is None:
        device = next(model.parameters()).device
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    t0 = time.monotonic()
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=n_tokens,
            do_sample=False,
            num_beams=1,
            pad_token_id=tokenizer.eos_token_id,
        )
    wall = time.monotonic() - t0
    text = tokenizer.decode(output_ids[0], skip_special_tokens=True)
    print(f"[srd] generation: {n_tokens} tokens in {wall:.1f}s "
          f"({n_tokens / wall:.1f} tok/s)")
    return text


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SRD coherence smoke test")
    p.add_argument("--model",
                   default="TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    p.add_argument("--revision", default=None,
                   help="HF revision to pin (recommended for reproducibility)")
    p.add_argument("--alpha", type=float, default=1.0)
    p.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    p.add_argument("--per-tensor", action="store_true",
                   help="Use per-tensor scaling instead of per-block")
    p.add_argument("--prompt", default="Once upon a time, in a small village,")
    p.add_argument("--tokens", type=int, default=80)
    p.add_argument("--device", default=None,
                   help="cuda / cpu / mps; default = auto")
    p.add_argument("--skip-quantize", action="store_true",
                   help="Smoke-test the unquantized model (sanity FP baseline)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device == "cuda" else torch.float32

    print(f"[srd] loading {args.model} "
          f"(revision={args.revision or 'default'}) on {device} as {dtype}...")
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, revision=args.revision, torch_dtype=dtype
    ).to(device)
    model.eval()

    if not args.skip_quantize:
        quantize_hf_model_inplace(
            model,
            alpha=args.alpha,
            group_size=args.group_size,
            per_tensor=args.per_tensor,
        )

    text = smoke_generate(model, tokenizer, args.prompt, n_tokens=args.tokens,
                          device=device)
    print()
    print("─── generation ───────────────────────────────────────")
    print(text)
    print("──────────────────────────────────────────────────────")
    return 0


if __name__ == "__main__":
    sys.exit(main())
