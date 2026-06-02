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

    top_k_pct: fraction of D8 elements retained by top-k sparsity masking
    (1.0 = dense, 0.0 = all-zeros residue). Used by `srd_bits_per_weight()`
    to compute honest sparse bpw.
    """
    W4: torch.Tensor
    D8: torch.Tensor
    S4: torch.Tensor
    S8: torch.Tensor
    group_size: int = DEFAULT_GROUP_SIZE
    alpha_default: float = 1.0
    original_dtype: torch.dtype = torch.float32
    top_k_pct: float = 1.0

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
        if not 0.0 <= self.top_k_pct <= 1.0:
            raise ValueError(
                f"top_k_pct must be in [0, 1], got {self.top_k_pct}"
            )

    @property
    def shape(self) -> torch.Size:
        return self.W4.shape


def srd_quantize(
    W: torch.Tensor,
    group_size: int = DEFAULT_GROUP_SIZE,
    top_k_pct: float = 1.0,
) -> SRDPackedTensor:
    """Quantize a 2D weight tensor with the SRD scheme.

    W: (out_features, in_features) — any float dtype.
    group_size: block size along the input dimension (default 64).
    top_k_pct: fraction of D8 elements to retain by top-k absolute magnitude.
      1.0 = dense (all elements kept); 0.5 = keep the 50% largest |values|,
      zero out the rest. Produces operating points between 4.5 and 13 bpw
      to fill the dead zone in the Pareto curve.

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
    if not 0.0 <= top_k_pct <= 1.0:
        raise ValueError(f"top_k_pct must be in [0, 1], got {top_k_pct}")
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

    # --- Top-k sparsity: zero out sub-threshold residual elements ---
    if top_k_pct < 1.0:
        n_total = D8.numel()
        k = max(1, round(n_total * top_k_pct))
        abs_vals = D8.float().abs()
        # k-th largest absolute value (kthvalue ranks ascending)
        rank = max(1, n_total - k + 1)
        threshold = abs_vals.flatten().kthvalue(rank).values
        mask = abs_vals >= threshold
        D8 = torch.where(mask, D8, torch.zeros_like(D8))

    return SRDPackedTensor(
        W4=W4.view(out_features, in_features),
        D8=D8.view(out_features, in_features),
        S4=S4,
        S8=S8,
        group_size=group_size,
        alpha_default=1.0,
        original_dtype=original_dtype,
        top_k_pct=top_k_pct,
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
    top_k_pct: float = 1.0,
) -> float:
    """Convenience: quantize then dequantize then return scalar MSE.

    Useful for unit tests and quick sanity checks. Not used in the
    perplexity benchmark — that path goes through `quantize_hf_model_inplace`.
    """
    pack = srd_quantize(W, group_size=group_size, top_k_pct=top_k_pct)
    W_hat = srd_dequantize(pack, alpha=alpha)
    return float(((W.to(torch.float32) - W_hat.to(torch.float32)) ** 2).mean().item())


def srd_bits_per_weight(pack: SRDPackedTensor) -> float:
    """Honest bits-per-weight, **including** S4 and S8 storage.

    Components for group size G and residual density p = top_k_pct:
      W4: 4 bits / weight
      D8: 8 * p bits / weight  (theoretical sparse minimum; no bitmask overhead)
      S4: 32 bits / block → 32 / G bits / weight
      S8: 32 bits / block → 32 / G bits / weight  (conservative: always stored)

    Dense (p=1.0, G=64): 4 + 8 + 0.5 + 0.5 = 13.0 bpw.
    Sparse p=0.25, G=64: 4 + 2 + 0.5 + 0.5 = 7.0 bpw.

    The bitmask needed for real sparse storage would add ~1 bit/weight
    (not counted here — this is a theoretical lower bound). The spec's
    §4 claim of "39% of FP16" ignores S4/S8 entirely; this function
    exists to keep that error from propagating into the benchmark.
    """
    G = float(pack.group_size)
    w4_bpw = 4.0
    d8_bpw = 8.0 * pack.top_k_pct
    s4_bpw = SCALE_BITS / G
    s8_bpw = SCALE_BITS / G
    return w4_bpw + d8_bpw + s4_bpw + s8_bpw


# ---------------------------------------------------------------------------
# Phase E3 — real bit-packing (no CUDA kernel required)
#
# srd_pack_w4 / srd_unpack_w4  — pack two int4 values into one uint8 byte.
#   W4 is int8 in [-8, 7]; we add 8 to shift into [0, 15] (unsigned nibble).
#   Low nibble  = element at even column index.
#   High nibble = element at odd  column index.
#   Reduces W4 storage from 8 bits/weight to 4 bits/weight.
#
# srd_pack_d8_sparse / srd_unpack_d8_sparse — COO-lite sparse format for D8.
#   At top_k_pct=0.25 only 25% of D8 bytes are non-zero.  Instead of storing
#   all N bytes we store:
#     - a 1-bit-per-element bitmask packed into ceil(N/8) uint8 bytes
#     - the non-zero int8 values tightly packed (no padding)
#   Memory per weight: 0.25 * 8 bits (values) + 1 bit (mask) = 3 bits
#   vs 8 bits for dense D8 → 2.67× reduction at top_k_pct=0.25.
# ---------------------------------------------------------------------------

def srd_pack_w4(W4: torch.Tensor) -> torch.Tensor:
    """Pack int8 W4 (values in [-8, 7]) into uint8 nibble pairs.

    Input:  (out, in_features) int8 — in_features must be even.
    Output: (out, in_features // 2) uint8 — each byte holds two nibbles.

    Bit layout per byte (b):
        b[3:0] = W4[:, 0::2] + 8    (even columns, low nibble)
        b[7:4] = W4[:, 1::2] + 8    (odd  columns, high nibble)
    """
    if W4.shape[-1] % 2 != 0:
        raise ValueError(
            f"srd_pack_w4: last dim must be even, got {W4.shape[-1]}"
        )
    shifted = W4.to(torch.int32) + 8          # [0, 15]
    lo = (shifted[:, 0::2] & 0xF).to(torch.uint8)
    hi = (shifted[:, 1::2] & 0xF).to(torch.uint8)
    return lo | (hi << 4)


def srd_unpack_w4(packed: torch.Tensor, original_cols: int) -> torch.Tensor:
    """Unpack uint8 nibble pairs back to int8 W4 in [-8, 7].

    Input:  (out, in_features // 2) uint8
    Output: (out, original_cols)    int8
    """
    lo = (packed & 0x0F).to(torch.int8) - 8
    hi = ((packed >> 4) & 0x0F).to(torch.int8) - 8
    out = torch.empty(packed.shape[0], original_cols,
                      dtype=torch.int8, device=packed.device)
    out[:, 0::2] = lo
    out[:, 1::2] = hi
    return out


def srd_pack_d8_sparse(
    D8: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """Pack sparse int8 D8 into (bitmask, values) pair.

    Input:  D8 (out, in_features) int8  — arbitrary sparsity pattern.
    Output:
        bitmask: (ceil(N/8),) uint8  — 1 bit per element, 1 = non-zero.
        values:  (nnz,)       int8  — non-zero values in row-major order.

    Flat layout (row-major, then packed into bytes, LSB first within each
    byte) makes the unpack trivially vectorizable on CPU and ARM NEON.
    """
    flat = D8.reshape(-1)                       # (N,)
    mask_bool = flat != 0                       # (N,) bool

    # Pack bitmask: 8 bools → 1 uint8, LSB = first element in group of 8
    N = flat.numel()
    pad = (8 - N % 8) % 8
    if pad:
        mask_bool = torch.cat(
            [mask_bool, mask_bool.new_zeros(pad)]
        )
    bitmask = mask_bool.view(-1, 8).to(torch.uint8)
    weights = torch.tensor([1, 2, 4, 8, 16, 32, 64, 128],
                           dtype=torch.uint8, device=D8.device)
    bitmask = (bitmask * weights).sum(dim=1).to(torch.uint8)  # (ceil(N/8),)

    values = flat[flat != 0]                    # (nnz,) int8
    return bitmask, values


def srd_unpack_d8_sparse(
    bitmask: torch.Tensor,
    values: torch.Tensor,
    original_shape: Tuple[int, ...],
) -> torch.Tensor:
    """Reconstruct D8 from (bitmask, values) produced by srd_pack_d8_sparse.

    Output: original_shape int8, zeros at masked positions.
    """
    N = 1
    for s in original_shape:
        N *= s

    # Unpack bitmask bytes → 1 bit per element
    weights = torch.tensor([1, 2, 4, 8, 16, 32, 64, 128],
                           dtype=torch.uint8, device=bitmask.device)
    bits = ((bitmask.unsqueeze(1) & weights) > 0)  # (ceil(N/8), 8)
    mask_flat = bits.reshape(-1)[:N]                # (N,) bool

    flat = torch.zeros(N, dtype=torch.int8, device=bitmask.device)
    flat[mask_flat] = values
    return flat.reshape(original_shape)


def srd_packed_bytes(pack: SRDPackedTensor) -> dict:
    """Compute the real on-disk byte count for each component if E3-packed.

    Returns a dict with per-component byte counts and a total, plus the
    achieved bpw (total_bits / n_weights). This is what pack_to_axm.py
    reports as 'real_packed_mb' when --real-pack is used.

    All counts are theoretical minima (no alignment padding, no file headers).
    """
    out_f, in_f = pack.W4.shape
    G            = pack.group_size
    n_groups     = in_f // G
    N            = out_f * in_f

    # W4: 4 bits each → ceil(N/2) bytes
    w4_bytes = (N + 1) // 2

    # D8: bitmask (ceil(N/8)) + nnz * 1 byte
    nnz = int((pack.D8 != 0).sum().item())
    mask_bytes = (N + 7) // 8
    d8_bytes   = mask_bytes + nnz

    # Scales: float32 each
    n_scale_entries = out_f * n_groups
    s4_bytes = n_scale_entries * 4
    s8_bytes = n_scale_entries * 4

    total_bytes = w4_bytes + d8_bytes + s4_bytes + s8_bytes
    real_bpw    = (total_bytes * 8) / N

    return {
        "w4_bytes":    w4_bytes,
        "d8_bytes":    d8_bytes,
        "s4_bytes":    s4_bytes,
        "s8_bytes":    s8_bytes,
        "total_bytes": total_bytes,
        "real_bpw":    round(real_bpw, 3),
        "n_weights":   N,
        "nnz_d8":      nnz,
    }


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
