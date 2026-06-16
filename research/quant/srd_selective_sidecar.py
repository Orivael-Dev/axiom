"""
SRD Selective Sidecar Loading — design + Python prototype
==========================================================

Problem
-------
SLMs (≤1.7B) hallucinate more than larger models because:
  1. Limited capacity — they can't memorise all facts
  2. INT4 quantization degrades the subtle weight precision needed for
     multi-step reasoning (the reasoning chunk, ~37% of layers)
  3. These errors compound — each degraded layer feeds the next

Insight
-------
Not all layers need the same precision:
  - Early layers (embeddings, early attention): factual lookup, INT4 fine
  - Reasoning layers (middle-late transformer blocks): chain-of-thought,
    inference composition — need higher precision to avoid hallucination

The .srd4 sidecar stores the D8 residuals for every layer. We only need
to apply them to the reasoning chunk. For sparse D8 (top_k_pct=0.25):

  Model          Reasoning layers  D8 overhead (sparse 25%)
  SmolLM2-135M       11 layers          10 MB   ← trivial
  Qwen2.5-0.5B        9 layers          35 MB   ← fits on mobile
  Gemma3-1B           7 layers          49 MB   ← fits on mobile
  TinyLlama-1.1B      8 layers          98 MB   ← feasible on phone

Fork design
-----------
At model load time:
  1. Check for companion <model>.srd4 sidecar
  2. Parse sidecar header → layer_id → (D8, S8) block map
  3. Classify layers by MET chunk:
       reasoning_start = floor(n_layers * 0.40)
       reasoning_end   = floor(n_layers * 0.77)
  4. For reasoning layers: dequant Q4_K_M → add D8 residual → store as
     corrected FP16 (static correction, zero runtime overhead)
  5. For non-reasoning layers: load Q4_K_M as-is

The correction is applied ONCE at load time — inference speed is
identical to vanilla llama.cpp after that.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch

# MET chunk boundaries (matches met_ram_estimator.CHUNK_FRACS)
_REASONING_START_FRAC = 0.40   # layers before this = early + factual
_REASONING_END_FRAC   = 0.77   # layers after  this = output

SRD4_MAGIC = b"AXMSRD4\x00"


def reasoning_layer_ids(
    n_layers: int,
    start_frac: Optional[float] = None,
    end_frac: Optional[float] = None,
) -> List[int]:
    """Return the layer indices that fall in the reasoning MET chunk.

    start_frac / end_frac override the module-level defaults when provided,
    allowing per-architecture calibration without touching the constants.
    """
    s = start_frac if start_frac is not None else _REASONING_START_FRAC
    e = end_frac   if end_frac   is not None else _REASONING_END_FRAC
    start = math.floor(n_layers * s)
    end   = math.floor(n_layers * e)
    return list(range(start, end))


# ── Sidecar loader (reads the binary .srd4 produced by axm_to_srd4_gguf) ──

def load_sidecar(path: Path) -> Dict[str, Tuple[torch.Tensor, torch.Tensor]]:
    """Parse a .srd4 sidecar file.

    Returns {tensor_name: (D8, S8)} for every quantized layer stored
    in the sidecar. D8 is int8, S8 is float32 — same shapes as the
    original SRDPackedTensor fields.
    """
    import json

    data = path.read_bytes()
    if data[:8] != SRD4_MAGIC:
        raise ValueError(f"{path.name}: bad magic — not a .srd4 file")

    header_len = struct.unpack_from("<I", data, 8)[0]
    header     = json.loads(data[12 : 12 + header_len])
    tensors    = header["tensors"]

    offset = 12 + header_len
    result: Dict[str, Tuple[torch.Tensor, torch.Tensor]] = {}

    G = header.get("group_size", 64)

    for t in tensors:
        name       = t["name"]
        out_f, in_f = t["shape"]
        n_groups   = in_f // G
        n_blocks   = out_f * n_groups

        # Each block: 32 bytes w4_packed + 4 bytes s4 + 64 bytes d8 + 4 bytes s8
        block_bytes = 32 + 4 + 64 + 4   # 104 bytes

        D8_list, S8_list = [], []
        for _ in range(n_blocks):
            _w4  = data[offset : offset + 32];  offset += 32
            _s4  = data[offset : offset + 4];   offset += 4
            d8b  = data[offset : offset + 64];  offset += 64
            s8b  = data[offset : offset + 4];   offset += 4

            D8_list.append(torch.frombuffer(bytearray(d8b), dtype=torch.int8))
            S8_list.append(struct.unpack("<f", s8b)[0])

        D8 = torch.stack(D8_list).view(out_f, in_f)
        S8 = torch.tensor(S8_list, dtype=torch.float32).view(out_f, n_groups)
        result[name] = (D8, S8)

    return result


# ── Correction kernel ──────────────────────────────────────────────────────

def apply_d8_correction(
    weight_q4km: torch.Tensor,   # already dequantized FP16 from GGUF
    D8: torch.Tensor,            # int8  (out, in)
    S8: torch.Tensor,            # float32 (out, n_groups)
    group_size: int = 64,
) -> torch.Tensor:
    """Add the D8 residual to a dequantized Q4_K_M weight.

    This is the static correction — called once at load time.
    Returns corrected FP16 weight; the layer runs at FP16 precision
    from this point with zero additional per-token cost.
    """
    out_f, in_f = weight_q4km.shape
    n_groups    = in_f // group_size

    D8f = D8.to(torch.float32).view(out_f, n_groups, group_size)
    S8e = S8.unsqueeze(-1)                  # (out, n_groups, 1)
    residue = (D8f * S8e).view(out_f, in_f)

    return (weight_q4km.to(torch.float32) + residue).to(torch.float16)


# ── High-level: patch a loaded HF model in-place ──────────────────────────

def apply_sidecar_to_reasoning_layers(
    model: "torch.nn.Module",
    sidecar_path: Path,
    group_size: int = 64,
    verbose: bool = True,
    start_frac: Optional[float] = None,
    end_frac: Optional[float] = None,
) -> int:
    """Load the .srd4 sidecar and apply D8 correction to reasoning-chunk
    Linear layers only. Non-reasoning layers are untouched.

    Returns the number of layers corrected.
    """
    import torch.nn as nn

    sidecar = load_sidecar(sidecar_path)

    # Count transformer layers
    n_layers = sum(
        1 for name, _ in model.named_modules()
        if "layers." in name and name.endswith(".self_attn")
    )
    if n_layers == 0:
        # Fallback: count by unique layer indices
        seen = set()
        for name, _ in model.named_modules():
            parts = name.split(".")
            for i, p in enumerate(parts):
                if p == "layers" and i + 1 < len(parts):
                    seen.add(parts[i + 1])
        n_layers = len(seen)

    reasoning_ids = set(reasoning_layer_ids(n_layers,
                                              start_frac=start_frac,
                                              end_frac=end_frac))

    if verbose:
        print(f"[srd-sidecar] {n_layers} layers total, "
              f"reasoning chunk = layers {min(reasoning_ids)}–{max(reasoning_ids)} "
              f"({len(reasoning_ids)} layers)")

    corrected = 0
    for name, module in model.named_modules():
        if not isinstance(module, nn.Linear):
            continue

        # Extract layer index from name e.g. "model.layers.14.mlp.gate_proj"
        parts = name.split(".")
        layer_idx = None
        for i, p in enumerate(parts):
            if p == "layers" and i + 1 < len(parts):
                try:
                    layer_idx = int(parts[i + 1])
                except ValueError:
                    pass

        if layer_idx not in reasoning_ids:
            continue   # leave non-reasoning layers at Q4_K_M quality

        if name not in sidecar:
            continue   # sidecar doesn't have this tensor (skip list, etc.)

        D8, S8 = sidecar[name]
        with torch.no_grad():
            corrected_w = apply_d8_correction(
                module.weight.data, D8, S8, group_size=group_size,
            )
            module.weight.data.copy_(corrected_w)
        corrected += 1

    if verbose:
        print(f"[srd-sidecar] corrected {corrected} Linear layers "
              f"in reasoning chunk — zero runtime overhead from here")
    return corrected


# ── Memory estimate ────────────────────────────────────────────────────────

def sidecar_ram_mb(
    n_layers: int,
    hidden: int,
    intermediate: int,
    group_size: int = 64,
    top_k_pct: float = 0.25,
) -> dict:
    """Estimate RAM added by loading D8 for reasoning layers only."""
    reasoning_ids = reasoning_layer_ids(n_layers)
    attn_params  = hidden * hidden * 4   # q k v o
    mlp_params   = hidden * intermediate * 3
    per_layer    = attn_params + mlp_params
    total_params = len(reasoning_ids) * per_layer

    d8_dense_mb  = total_params / 1024**2
    d8_sparse_mb = d8_dense_mb * top_k_pct
    s8_mb        = (total_params / group_size * 4) / 1024**2

    return {
        "reasoning_layers": len(reasoning_ids),
        "reasoning_params_M": round(total_params / 1e6, 1),
        "d8_dense_MB":  round(d8_dense_mb,  1),
        "d8_sparse_MB": round(d8_sparse_mb, 1),
        "s8_MB":        round(s8_mb, 2),
        "total_MB":     round(d8_sparse_mb + s8_mb, 1),
    }


if __name__ == "__main__":
    print("SRD selective sidecar — RAM estimates for anti-hallucination patch\n")
    # (name, n_layers, hidden, intermediate, start_frac, end_frac)
    # start_frac/end_frac=None → use module defaults (0.40 / 0.77)
    # Gemma 4 12B boundaries are pending calibration sweep; None uses defaults.
    configs = [
        ("SmolLM2-135M",   30,  576,   1536,  None, None),
        ("Qwen2.5-0.5B",   24,  896,   4864,  None, None),
        ("Gemma3-1B",      18, 1152,   6912,  None, None),
        ("TinyLlama-1.1B", 22, 2048,   5632,  None, None),
        ("Gemma4-12B",     28, 3072,  24576,  None, None),
    ]
    for name, nl, h, inter, sf, ef in configs:
        est = sidecar_ram_mb(nl, h, inter)
        ids = reasoning_layer_ids(nl, sf, ef)
        print(f"  {name:<20} reasoning={est['reasoning_layers']} layers  "
              f"({ids[0] if ids else '?'}–{ids[-1] if ids else '?'})  "
              f"D8 sparse={est['d8_sparse_MB']} MB  "
              f"total overhead={est['total_MB']} MB")
