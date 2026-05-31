"""Integration test for Phase E3 real-pack save/load round-trip.

Builds a tiny LlamaForCausalLM from config (no download), SRD-quantizes it,
saves it real-packed, loads it back, and asserts the reconstructed model
produces the same logits as the in-place quantized model.

Gated on `transformers` — skips cleanly in environments without it.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import torch

transformers = pytest.importorskip("transformers")


def _tiny_causal_lm():
    from transformers import LlamaConfig, LlamaForCausalLM
    cfg = LlamaConfig(
        vocab_size=256,
        hidden_size=128,        # divisible by group_size 64
        intermediate_size=256,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=4,
        max_position_embeddings=64,
        tie_word_embeddings=False,
    )
    torch.manual_seed(0)
    model = LlamaForCausalLM(cfg)
    model.eval()
    return model, cfg


def test_real_pack_roundtrip_matches_inplace():
    from research.quant.quantize_model import quantize_hf_model_inplace
    from research.quant.srd_realpack import (
        is_real_packed, load_real_packed, save_real_packed,
    )

    model, cfg = _tiny_causal_lm()
    ids = torch.randint(0, 256, (1, 16))

    # Quantize in place, capture reference logits + packed dict
    packed = quantize_hf_model_inplace(
        model, alpha=1.0, group_size=64, top_k_pct=0.25, progress=False,
    )
    with torch.no_grad():
        ref_logits = model(ids).logits

    with tempfile.TemporaryDirectory() as tmp:
        wdir = Path(tmp) / "weights"
        report = save_real_packed(
            model, packed, wdir,
            alpha=1.0, group_size=64, top_k_pct=0.25,
            config=cfg, tokenizer=None,
        )
        assert report["n_quantized_layers"] == len(packed)
        assert is_real_packed(wdir)

        # Load back on CPU in float32 for exact comparison
        loaded, _ = load_real_packed(wdir, device="cpu", dtype=torch.float32)
        with torch.no_grad():
            out_logits = loaded(ids).logits

    # Reconstruction is bit-exact on the weights → logits match closely.
    assert torch.allclose(ref_logits, out_logits, atol=1e-4, rtol=1e-4)


def test_real_pack_smaller_than_fp16():
    """Packed weight files must be meaningfully smaller than the FP16
    state_dict of the same quantized layers."""
    from research.quant.quantize_model import quantize_hf_model_inplace
    from research.quant.srd_realpack import save_real_packed, PACKED_FILE

    model, cfg = _tiny_causal_lm()
    packed = quantize_hf_model_inplace(
        model, alpha=1.0, group_size=64, top_k_pct=0.25, progress=False,
    )

    # FP16 byte cost of the quantized layers only (W4 shape == weight shape)
    fp16_bytes = sum(pack.W4.numel() * 2 for pack in packed.values())

    with tempfile.TemporaryDirectory() as tmp:
        wdir = Path(tmp) / "weights"
        save_real_packed(model, packed, wdir, alpha=1.0, group_size=64,
                         top_k_pct=0.25, config=cfg, tokenizer=None)
        packed_bytes = (wdir / PACKED_FILE).stat().st_size

    # At top_k_pct=0.25 the packed layers are ~0.875 byte/weight vs 2 → well
    # under 70% of FP16 even with pickle/zip overhead on a tiny model.
    assert packed_bytes < fp16_bytes * 0.70
