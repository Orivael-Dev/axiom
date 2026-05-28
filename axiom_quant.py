"""SRD — Stochastic Residual Dithering quantization (honest prototype).

A 4-bit base + 8-bit residue weight-quantization scheme with a runtime
mixing knob α, evaluated as part of Axiom's industry-gap theme 2
(auto-quantization). The name is the user's spec label; the actual
algorithm here is **deterministic residual quantization** (same family
as AQLM / QuIP# / residual-k-means), with no §2.2 noise-shaping filter
— that part of the spec is undefined and is deliberately skipped.

The point of this module is to support an empirical answer to one
question: "Does SRD beat existing llama.cpp K-quants at matched
bits-per-weight?" Everything is fake-quantization (FP16 → SRD-grid →
FP16) — there are no fused inference kernels, latency, or memory
benchmarks. Quality only.

Public API:

    pack = srd_quantize(W, group_size=64)        # SRDPackedTensor
    W_hat = srd_dequantize(pack, alpha=1.0)      # back to W's dtype/shape
    mse = srd_round_trip_mse(W, alpha=1.0)       # convenience
    bpw = srd_bits_per_weight(pack)              # honest — incl. S4 + S8

α semantics:
  α = 0.0 → 4-bit base only, residue discarded
  α = 1.0 → full residue applied (default)
  α ∈ (0, 1) → partial residue blend

All quantization is symmetric per-block (per (out_row, in_block)).
Block size defaults to 64. Asymmetric / per-tensor variants are out
of scope for the prototype.

See `docs/SRD_RESULTS.md` (Phase E) for the benchmark write-up and
`docs/research_engine.md` for the surrounding research context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import torch

DEFAULT_GROUP_SIZE: int = 64
W4_RANGE: Tuple[int, int] = (-8, 7)
D8_RANGE: Tuple[int, int] = (-127, 127)
SCALE_BITS: int = 32


@dataclass(frozen=True)
class SRDPackedTensor:
    """4-bit base + 8-bit residue packed weights, per-block symmetric.

    Shapes (for a (out_features, in_features) input weight):
      W4: (out_features, in_features) int8 in [-8, 7]
      D8: (out_features, in_features) int8 in [-127, 127]
      S4: (out_features, in_features // group_size) float32, base scale
      S8: (out_features, in_features // group_size) float32, residue scale

    Stored as int8 even though W4 only uses 4 of those bits — packing
    two W4 values per byte is a real-kernel concern, not a fake-quant
    one. `srd_bits_per_weight()` reports the honest 4-bit cost.
    """
    W4: torch.Tensor
    D8: torch.Tensor
    S4: torch.Tensor
    S8: torch.Tensor
    group_size: int = DEFAULT_GROUP_SIZE
    alpha_default: float = 1.0
    original_dtype: torch.dtype = torch.float32

    def __post_init__(self) -> None:
        if self.W4.shape != self.D8.shape:
            raise ValueError(
                f"W4 shape {tuple(self.W4.shape)} != D8 shape "
                f"{tuple(self.D8.shape)}"
            )
        if self.S4.shape != self.S8.shape:
            raise ValueError(
                f"S4 shape {tuple(self.S4.shape)} != S8 shape "
                f"{tuple(self.S8.shape)}"
            )
        if self.W4.dim() != 2:
            raise ValueError(
                f"SRD expects 2D weight, got {self.W4.dim()}D"
            )
        out_features, in_features = self.W4.shape
        if in_features % self.group_size != 0:
            raise ValueError(
                f"in_features ({in_features}) must be divisible by "
                f"group_size ({self.group_size})"
            )
        expected_n_groups = in_features // self.group_size
        if self.S4.shape != (out_features, expected_n_groups):
            raise ValueError(
                f"S4 shape {tuple(self.S4.shape)} doesn't match "
                f"({out_features}, {expected_n_groups})"
            )

    @property
    def shape(self) -> torch.Size:
        return self.W4.shape


def srd_quantize(
    W: torch.Tensor,
    group_size: int = DEFAULT_GROUP_SIZE,
) -> SRDPackedTensor:
    """Quantize a 2D weight tensor with the SRD scheme.

    W: (out_features, in_features) — any float dtype.
    group_size: block size along the input dimension (default 64).

    Returns an `SRDPackedTensor`. All math runs in float32 internally
    for stability regardless of input dtype.
    """
    if W.dim() != 2:
        raise ValueError(f"srd_quantize expects 2D, got {W.dim()}D")
    out_features, in_features = W.shape
    if in_features % group_size != 0:
        raise ValueError(
            f"in_features ({in_features}) must be divisible by "
            f"group_size ({group_size})"
        )
    original_dtype = W.dtype
    n_groups = in_features // group_size
    Wf = W.detach().to(torch.float32)
    Wb = Wf.view(out_features, n_groups, group_size)

    # --- Base: symmetric 4-bit per block ---
    abs_max = Wb.abs().amax(dim=2)                      # (out, n_groups)
    S4 = (abs_max / float(W4_RANGE[1])).clamp_min(1e-12)
    W4 = torch.round(Wb / S4.unsqueeze(-1)).clamp(*W4_RANGE).to(torch.int8)
    W_hat_base = W4.to(torch.float32) * S4.unsqueeze(-1)

    # --- Residue: symmetric 8-bit per block ---
    R = Wb - W_hat_base                                  # (out, n_groups, G)
    r_abs_max = R.abs().amax(dim=2)
    S8 = (r_abs_max / float(D8_RANGE[1])).clamp_min(1e-12)
    D8 = torch.round(R / S8.unsqueeze(-1)).clamp(*D8_RANGE).to(torch.int8)

    return SRDPackedTensor(
        W4=W4.view(out_features, in_features),
        D8=D8.view(out_features, in_features),
        S4=S4,
        S8=S8,
        group_size=group_size,
        alpha_default=1.0,
        original_dtype=original_dtype,
    )


def srd_dequantize(
    pack: SRDPackedTensor,
    alpha: Optional[float] = None,
) -> torch.Tensor:
    """Reconstruct the weight from a packed tensor, optionally mixing
    in a fraction `alpha` of the residue.

    alpha = 0.0 → 4-bit base only
    alpha = 1.0 → full residue (default if pack was built with alpha_default=1)
    alpha in between → partial residue blend
    """
    if alpha is None:
        alpha = pack.alpha_default
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")
    out_features, in_features = pack.W4.shape
    n_groups = in_features // pack.group_size

    W4f = pack.W4.to(torch.float32).view(out_features, n_groups, pack.group_size)
    base = W4f * pack.S4.unsqueeze(-1)
    if alpha == 0.0:
        recon = base
    else:
        D8f = pack.D8.to(torch.float32).view(out_features, n_groups, pack.group_size)
        residue = D8f * pack.S8.unsqueeze(-1)
        recon = base + float(alpha) * residue

    return recon.view(out_features, in_features).to(pack.original_dtype)


def srd_round_trip_mse(
    W: torch.Tensor,
    alpha: float = 1.0,
    group_size: int = DEFAULT_GROUP_SIZE,
) -> float:
    """Convenience: quantize then dequantize then return scalar MSE.

    Useful for unit tests and quick sanity checks. Not used in the
    perplexity benchmark — that path goes through `quantize_hf_model_inplace`.
    """
    pack = srd_quantize(W, group_size=group_size)
    W_hat = srd_dequantize(pack, alpha=alpha)
    return float(((W.to(torch.float32) - W_hat.to(torch.float32)) ** 2).mean().item())


def srd_bits_per_weight(pack: SRDPackedTensor) -> float:
    """Honest bits-per-weight, **including** S4 and S8 storage.

    Components for group size G:
      W4: 4 bits / weight
      D8: 8 bits / weight
      S4: 32 bits / block → 32 / G bits / weight
      S8: 32 bits / block → 32 / G bits / weight

    For G=64: 4 + 8 + 0.5 + 0.5 = 13.0 bpw.

    The spec's §4 claim of "39% of FP16" assumes only the W4 + D8
    grids exist (12 bpw / 16 bpw = 75%) and ignores the per-block
    scales entirely. This function exists to keep that error from
    propagating into the benchmark.
    """
    G = float(pack.group_size)
    w4_bpw = 4.0
    d8_bpw = 8.0
    s4_bpw = SCALE_BITS / G
    s8_bpw = SCALE_BITS / G
    return w4_bpw + d8_bpw + s4_bpw + s8_bpw


# --- Convenience helpers used by tests + the research harness ----------

def srd_quantize_per_tensor(W: torch.Tensor) -> SRDPackedTensor:
    """Per-tensor symmetric variant — single scale across the whole
    weight. Implemented as group_size = in_features so the per-block
    plumbing stays uniform.

    The spec's §5 reference demo is per-tensor. Including this lets
    the benchmark sweep both per-tensor and per-block on the same
    axis, separating the effect of the residue from the effect of
    per-block scaling.
    """
    if W.dim() != 2:
        raise ValueError(f"per-tensor srd expects 2D, got {W.dim()}D")
    return srd_quantize(W, group_size=W.shape[1])
