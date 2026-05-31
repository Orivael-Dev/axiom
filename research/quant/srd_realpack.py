"""Phase E3 real-pack: save/load SRD weights in genuinely small on-disk form.

The fake-quant path (quantize_hf_model_inplace + save_pretrained) keeps
weights as FP16 — same size as the original. Real-pack instead writes the
*packed* representation produced by the E3 primitives in axiom_quant.py:

    weights/
      config.json          HF config (architecture, for from_config)
      tokenizer*           tokenizer files
      srd_dense.pt         FP16 state_dict of every NON-quantized param
                           (embeddings, lm_head, norms, biases)
      srd_packed.pt        per-layer packed dict:
                             w4_packed  uint8  (2 int4 nibbles / byte)
                             d8_mask    uint8  (1 bit / element bitmask)
                             d8_vals    int8   (non-zero residue values)
                             s4, s8     float32 scales
      srd_index.json       metadata: scheme, group_size, top_k_pct, alpha,
                           list of quantized layer names

On disk this is roughly  N*(0.5 + 0.125 + top_k_pct) bytes per quantized
weight + FP16 dense params, vs N*2 bytes for the full FP16 checkpoint.
At top_k_pct=0.25 the quantized layers land near 0.875 bytes/weight.

Load reconstructs the model architecture from config (meta-init when
`accelerate` is available, so we never allocate a full random FP16 copy),
loads the dense params, then unpacks each quantized layer to FP16 and
assigns it. The result is a normal HF model ready to generate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch                                                       # noqa: E402
import torch.nn as nn                                              # noqa: E402

from axiom_quant import (                                          # noqa: E402
    SRDPackedTensor,
    srd_dequantize,
    srd_pack_d8_sparse,
    srd_pack_w4,
    srd_unpack_d8_sparse,
    srd_unpack_w4,
)

PACKED_FILE = "srd_packed.pt"
DENSE_FILE  = "srd_dense.pt"
INDEX_FILE  = "srd_index.json"


def _dtype_to_str(dt: torch.dtype) -> str:
    return str(dt).replace("torch.", "")


def _str_to_dtype(name: str) -> torch.dtype:
    return getattr(torch, name)


def save_real_packed(
    model: nn.Module,
    packed: Dict[str, SRDPackedTensor],
    weights_dir: Path,
    *,
    alpha: float,
    group_size: int,
    top_k_pct: float,
    config,
    tokenizer=None,
) -> dict:
    """Write the packed representation into weights_dir. Returns a size report.

    `packed` is the {layer_name: SRDPackedTensor} dict returned by
    quantize_hf_model_inplace. `model` is the (already-quantized) model —
    only its non-quantized params are saved as FP16; the quantized layer
    weights are reconstructed from `packed` at load time.
    """
    weights_dir = Path(weights_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)

    config.save_pretrained(weights_dir)
    if tokenizer is not None:
        tokenizer.save_pretrained(weights_dir)

    # Dense state = every param except the quantized layer weights.
    quantized_weight_keys = {f"{name}.weight" for name in packed}
    full_state = model.state_dict()
    dense_state = {
        k: v.half().cpu() if v.is_floating_point() else v.cpu()
        for k, v in full_state.items()
        if k not in quantized_weight_keys
    }
    torch.save(dense_state, weights_dir / DENSE_FILE)

    # Packed blob — pack each layer with the E3 primitives.
    blob: Dict[str, dict] = {}
    for name, pack in packed.items():
        w4_packed   = srd_pack_w4(pack.W4)
        d8_mask, d8_vals = srd_pack_d8_sparse(pack.D8)
        blob[name] = {
            "w4_packed":      w4_packed.cpu(),
            "orig_cols":      int(pack.W4.shape[1]),
            "d8_mask":        d8_mask.cpu(),
            "d8_vals":        d8_vals.cpu(),
            "d8_shape":       tuple(int(s) for s in pack.D8.shape),
            "s4":             pack.S4.cpu(),
            "s8":             pack.S8.cpu(),
            "group_size":     int(pack.group_size),
            "top_k_pct":      float(pack.top_k_pct),
            "original_dtype": _dtype_to_str(pack.original_dtype),
        }
    torch.save(blob, weights_dir / PACKED_FILE)

    index = {
        "packed":      True,
        "scheme":      "srd",
        "alpha":       alpha,
        "group_size":  group_size,
        "top_k_pct":   top_k_pct,
        "n_quantized": len(packed),
        "layers":      sorted(packed.keys()),
    }
    (weights_dir / INDEX_FILE).write_text(
        json.dumps(index, indent=2, sort_keys=True), encoding="utf-8"
    )

    # Size report
    def _mb(p: Path) -> float:
        return p.stat().st_size / (1024 ** 2)

    return {
        "dense_mb":  round(_mb(weights_dir / DENSE_FILE), 1),
        "packed_mb": round(_mb(weights_dir / PACKED_FILE), 1),
        "n_quantized_layers": len(packed),
    }


def is_real_packed(weights_dir: Path) -> bool:
    return (Path(weights_dir) / INDEX_FILE).is_file() and \
           (Path(weights_dir) / PACKED_FILE).is_file()


def load_real_packed(
    weights_dir: Path,
    *,
    device: str = "cpu",
    dtype: torch.dtype = torch.float16,
):
    """Reconstruct a HF model from a real-packed weights dir.

    Returns (model, tokenizer). tokenizer is None if no tokenizer files
    were saved.
    """
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    weights_dir = Path(weights_dir)
    index = json.loads((weights_dir / INDEX_FILE).read_text())
    config = AutoConfig.from_pretrained(weights_dir)

    # Meta-init avoids allocating a full random FP16 copy (matters on 8 GB
    # edge devices like the Orin Nano). Falls back to plain init if
    # accelerate is unavailable.
    try:
        from accelerate import init_empty_weights
        with init_empty_weights():
            model = AutoModelForCausalLM.from_config(config)
        meta_init = True
    except Exception:
        model = AutoModelForCausalLM.from_config(config)
        meta_init = False

    # Dense params first (assign=True replaces meta tensors in place).
    dense_state = torch.load(weights_dir / DENSE_FILE, map_location=device,
                             weights_only=True)
    dense_state = {k: v.to(dtype) if v.is_floating_point() else v
                   for k, v in dense_state.items()}
    if meta_init:
        model.load_state_dict(dense_state, strict=False, assign=True)
    else:
        model.load_state_dict(dense_state, strict=False)

    # Unpack each quantized layer → FP16 → assign onto the module.
    blob = torch.load(weights_dir / PACKED_FILE, map_location=device,
                      weights_only=True)
    for name, entry in blob.items():
        W4 = srd_unpack_w4(
            entry["w4_packed"].to(device), entry["orig_cols"],
        )
        D8 = srd_unpack_d8_sparse(
            entry["d8_mask"].to(device), entry["d8_vals"].to(device),
            tuple(entry["d8_shape"]),
        )
        pack = SRDPackedTensor(
            W4=W4, D8=D8,
            S4=entry["s4"].to(device), S8=entry["s8"].to(device),
            group_size=entry["group_size"],
            original_dtype=_str_to_dtype(entry["original_dtype"]),
            top_k_pct=entry["top_k_pct"],
        )
        W_hat = srd_dequantize(pack, alpha=index.get("alpha", 1.0)).to(dtype)
        module = model.get_submodule(name)
        module.weight = nn.Parameter(W_hat.to(device), requires_grad=False)

    model.to(device).eval()

    tokenizer = None
    try:
        tokenizer = AutoTokenizer.from_pretrained(weights_dir)
    except Exception:
        pass
    return model, tokenizer
