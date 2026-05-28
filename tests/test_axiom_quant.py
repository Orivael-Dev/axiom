"""Unit tests for the SRD quantization kernel.

Fast (<5 s) tests that don't download any model. Verify:
  - shapes / dtypes / value-range invariants on the packed tensor
  - dequantize honors alpha semantics (0 = base only, 1 = full residue)
  - residue strictly improves MSE vs base-only (monotonic in alpha)
  - bits_per_weight matches the hand calculation
  - per-tensor variant degrades meaningfully vs per-block
  - error paths: wrong shape, non-divisible in_features, invalid alpha
"""
from __future__ import annotations

import math

import pytest
import torch

from axiom_quant import (
    DEFAULT_GROUP_SIZE,
    SRDPackedTensor,
    srd_bits_per_weight,
    srd_dequantize,
    srd_quantize,
    srd_quantize_per_tensor,
    srd_round_trip_mse,
)


# Fixed seed: tests must be deterministic across CI runs.
def _make_W(out_features: int = 32, in_features: int = 128,
            seed: int = 17) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.randn(out_features, in_features, generator=g,
                       dtype=torch.float32)


# ── Pack shape + value-range invariants ──────────────────────────────


def test_packed_shapes_match_input():
    W = _make_W(64, 256)
    pack = srd_quantize(W, group_size=64)
    assert pack.W4.shape == (64, 256)
    assert pack.D8.shape == (64, 256)
    assert pack.S4.shape == (64, 4)            # 256 / 64
    assert pack.S8.shape == (64, 4)
    assert pack.group_size == 64
    assert pack.original_dtype == torch.float32


def test_w4_values_in_range():
    pack = srd_quantize(_make_W(), group_size=64)
    assert pack.W4.dtype == torch.int8
    assert pack.W4.min().item() >= -8
    assert pack.W4.max().item() <= 7


def test_d8_values_in_range():
    pack = srd_quantize(_make_W(), group_size=64)
    assert pack.D8.dtype == torch.int8
    assert pack.D8.min().item() >= -127
    assert pack.D8.max().item() <= 127


def test_scales_are_positive():
    pack = srd_quantize(_make_W(), group_size=64)
    assert (pack.S4 > 0).all()
    assert (pack.S8 > 0).all()


def test_packed_tensor_preserves_dtype():
    W = _make_W().to(torch.float16)
    pack = srd_quantize(W, group_size=64)
    assert pack.original_dtype == torch.float16
    W_hat = srd_dequantize(pack)
    assert W_hat.dtype == torch.float16


# ── Dequant honors alpha ─────────────────────────────────────────────


def test_alpha_zero_drops_residue():
    W = _make_W()
    pack = srd_quantize(W, group_size=64)
    out_a0 = srd_dequantize(pack, alpha=0.0)
    out_a1 = srd_dequantize(pack, alpha=1.0)
    # alpha=0 path should not equal alpha=1 (residue must contribute)
    assert not torch.allclose(out_a0, out_a1)


def test_alpha_default_is_one():
    W = _make_W()
    pack = srd_quantize(W, group_size=64)
    explicit = srd_dequantize(pack, alpha=1.0)
    default = srd_dequantize(pack, alpha=None)
    assert torch.allclose(explicit, default)


def test_alpha_monotonic_mse():
    """MSE strictly decreases as more residue is added — this is the
    whole point of the residue term."""
    W = _make_W()
    pack = srd_quantize(W, group_size=64)
    mse_0   = ((W - srd_dequantize(pack, alpha=0.0)) ** 2).mean().item()
    mse_05  = ((W - srd_dequantize(pack, alpha=0.5)) ** 2).mean().item()
    mse_1   = ((W - srd_dequantize(pack, alpha=1.0)) ** 2).mean().item()
    assert mse_0 > mse_05 > mse_1
    # Sanity: residue should give >10x improvement at alpha=1
    assert mse_0 / mse_1 > 10


def test_alpha_invalid_raises():
    pack = srd_quantize(_make_W(), group_size=64)
    with pytest.raises(ValueError, match=r"alpha"):
        srd_dequantize(pack, alpha=-0.1)
    with pytest.raises(ValueError, match=r"alpha"):
        srd_dequantize(pack, alpha=1.5)


# ── Bits-per-weight matches hand calc ────────────────────────────────


def test_bpw_group_64_is_13_0():
    """Honest bpw for g=64: 4 + 8 + 32/64 + 32/64 = 13.0."""
    pack = srd_quantize(_make_W(), group_size=64)
    bpw = srd_bits_per_weight(pack)
    assert math.isclose(bpw, 13.0, abs_tol=0.01)


def test_bpw_group_128_is_12_5():
    """Larger group → less scale overhead: 4 + 8 + 32/128 + 32/128 = 12.5."""
    pack = srd_quantize(_make_W(in_features=256), group_size=128)
    bpw = srd_bits_per_weight(pack)
    assert math.isclose(bpw, 12.5, abs_tol=0.01)


def test_bpw_per_tensor_is_smaller_than_per_block():
    """Per-tensor has one scale per row vs many per row — lower bpw."""
    W = _make_W(in_features=512)
    block_pack = srd_quantize(W, group_size=64)
    tensor_pack = srd_quantize_per_tensor(W)
    assert srd_bits_per_weight(tensor_pack) < srd_bits_per_weight(block_pack)


# ── Round-trip MSE ───────────────────────────────────────────────────


def test_round_trip_helper_matches_manual():
    W = _make_W()
    manual = ((W - srd_dequantize(srd_quantize(W, group_size=64),
                                  alpha=1.0)) ** 2).mean().item()
    helper = srd_round_trip_mse(W, alpha=1.0, group_size=64)
    assert math.isclose(manual, helper, rel_tol=1e-6)


def test_per_tensor_degrades_vs_per_block():
    """Per-tensor should reconstruct worse than per-block at alpha=1
    because a single scale can't track variance across the row."""
    W = _make_W(in_features=512)
    per_block_pack = srd_quantize(W, group_size=64)
    per_tensor_pack = srd_quantize_per_tensor(W)
    mse_block = ((W - srd_dequantize(per_block_pack, alpha=1.0)) ** 2).mean().item()
    mse_tensor = ((W - srd_dequantize(per_tensor_pack, alpha=1.0)) ** 2).mean().item()
    assert mse_tensor > mse_block


# ── Error paths ──────────────────────────────────────────────────────


def test_quantize_rejects_1d():
    with pytest.raises(ValueError, match="2D"):
        srd_quantize(torch.randn(64))


def test_quantize_rejects_non_divisible():
    W = torch.randn(8, 100)   # 100 / 64 = not integer
    with pytest.raises(ValueError, match="divisible"):
        srd_quantize(W, group_size=64)


def test_pack_shape_mismatch_rejected():
    W4 = torch.zeros(4, 64, dtype=torch.int8)
    D8 = torch.zeros(4, 32, dtype=torch.int8)            # wrong shape
    S4 = torch.ones(4, 1)
    S8 = torch.ones(4, 1)
    with pytest.raises(ValueError, match="shape"):
        SRDPackedTensor(W4=W4, D8=D8, S4=S4, S8=S8, group_size=64)


def test_pack_scale_shape_mismatch_rejected():
    W4 = torch.zeros(4, 64, dtype=torch.int8)
    D8 = torch.zeros(4, 64, dtype=torch.int8)
    S4 = torch.ones(4, 1)
    S8 = torch.ones(4, 2)                                # wrong shape
    with pytest.raises(ValueError, match="shape"):
        SRDPackedTensor(W4=W4, D8=D8, S4=S4, S8=S8, group_size=64)


def test_pack_scale_size_mismatch_rejected():
    """S4 must have in_features // group_size groups."""
    W4 = torch.zeros(4, 128, dtype=torch.int8)
    D8 = torch.zeros(4, 128, dtype=torch.int8)
    S4 = torch.ones(4, 1)                                # should be (4, 2)
    S8 = torch.ones(4, 1)
    with pytest.raises(ValueError, match="match"):
        SRDPackedTensor(W4=W4, D8=D8, S4=S4, S8=S8, group_size=64)


# ── Edge cases ───────────────────────────────────────────────────────


def test_zero_weight_doesnt_nan():
    W = torch.zeros(8, 64)
    pack = srd_quantize(W, group_size=64)
    out = srd_dequantize(pack, alpha=1.0)
    assert not torch.isnan(out).any()
    assert torch.allclose(out, torch.zeros_like(out), atol=1e-6)


def test_constant_weight_recovers_exactly_at_alpha_one():
    """Constant tensor should round-trip well even though it stresses
    the symmetric grid (all values map to the same int level)."""
    W = torch.full((8, 64), 0.5)
    pack = srd_quantize(W, group_size=64)
    out = srd_dequantize(pack, alpha=1.0)
    err = (W - out).abs().max().item()
    # Residue cleans up the base's quantization error to floating-point noise
    assert err < 1e-4
