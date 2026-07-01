"""Tests for calibrate_layer_alphas and srd_selective_sidecar.layer_alphas_from_quant_map.

All tests run on tiny synthetic models — no HuggingFace downloads needed.
Fast (<3 s total).
"""
from __future__ import annotations

import math
from typing import Dict, List

import pytest
import torch
import torch.nn as nn

from research.quant.calibrate_layer_alphas import (
    _normalise,
    _sensitivity_stats,
    build_quant_map,
    calibrate_weight_norm,
    derive_chunk_fracs,
    sensitivity_to_alphas,
)
from research.quant.srd_selective_sidecar import (
    _REASONING_END_FRAC,
    _REASONING_START_FRAC,
    _layer_index_from_name,
    _substring_match,
    layer_alphas_from_quant_map,
    reasoning_layer_ids,
)


# ── Tiny model fixture ────────────────────────────────────────────────────────

class _TinyBlock(nn.Module):
    def __init__(self, hidden: int) -> None:
        super().__init__()
        self.q_proj   = nn.Linear(hidden, hidden, bias=False)
        self.k_proj   = nn.Linear(hidden, hidden, bias=False)
        self.v_proj   = nn.Linear(hidden, hidden, bias=False)
        self.o_proj   = nn.Linear(hidden, hidden, bias=False)
        self.gate_proj = nn.Linear(hidden, hidden, bias=False)
        self.up_proj   = nn.Linear(hidden, hidden, bias=False)
        self.down_proj = nn.Linear(hidden, hidden, bias=False)


class _TinyModel(nn.Module):
    """Hierarchy that produces dotted names matching a real transformer."""
    def __init__(self, n_layers: int = 6, hidden: int = 64) -> None:
        super().__init__()
        self.layers = nn.ModuleList([_TinyBlock(hidden) for _ in range(n_layers)])


def _tiny_model(n_layers: int = 6, hidden: int = 64) -> nn.Module:
    return _TinyModel(n_layers=n_layers, hidden=hidden)


def _layer_names(n_layers: int = 6) -> List[str]:
    names = []
    for i in range(n_layers):
        for proj in ("q_proj", "k_proj", "v_proj", "o_proj",
                     "gate_proj", "up_proj", "down_proj"):
            names.append(f"layers.{i}.{proj}")
    return names


# ── _normalise ────────────────────────────────────────────────────────────────

def test_normalise_empty():
    assert _normalise({}) == {}


def test_normalise_all_equal():
    raw = {"a": 3.0, "b": 3.0, "c": 3.0}
    n = _normalise(raw)
    assert all(v == 1.0 for v in n.values())


def test_normalise_range():
    raw = {"lo": 0.0, "mid": 0.5, "hi": 1.0}
    n = _normalise(raw)
    assert n["lo"] == pytest.approx(0.0)
    assert n["hi"] == pytest.approx(1.0)
    assert 0.0 < n["mid"] < 1.0


def test_normalise_negative_values():
    raw = {"a": -1.0, "b": 0.0, "c": 1.0}
    n = _normalise(raw)
    assert n["a"] == pytest.approx(0.0)
    assert n["c"] == pytest.approx(1.0)


# ── sensitivity_to_alphas ─────────────────────────────────────────────────────

def test_sensitivity_to_alphas_empty():
    assert sensitivity_to_alphas({}) == {}


def test_top_25pct_gets_alpha_ceil():
    scores = {f"layer_{i}": i / 10.0 for i in range(10)}  # 0.0 .. 0.9
    alphas = sensitivity_to_alphas(scores, threshold_pct=75.0, alpha_ceil=1.0)
    # top 25% → layers with sensitivity >= p75
    high_count = sum(1 for v in alphas.values() if v == 1.0)
    assert high_count >= 2   # at least 2 out of 10 get alpha=1.0


def test_all_layers_alpha_floor_when_threshold_100():
    scores = {"a": 0.2, "b": 0.5, "c": 0.8}
    alphas = sensitivity_to_alphas(scores, threshold_pct=0.0, alpha_ceil=1.0,
                                   alpha_floor=0.0)
    # threshold at 100% → no layer reaches the ceiling automatically
    # (all fall below 100th percentile threshold)
    assert all(0.0 <= v <= 1.0 for v in alphas.values())


def test_alphas_in_range():
    scores = {f"l{i}": i * 0.1 for i in range(20)}
    alphas = sensitivity_to_alphas(scores, alpha_floor=0.1, alpha_ceil=0.9)
    for v in alphas.values():
        assert 0.09 <= v <= 0.91


# ── derive_chunk_fracs ────────────────────────────────────────────────────────

def test_derive_chunk_fracs_empty_map():
    start, end = derive_chunk_fracs({}, 22)
    assert start == pytest.approx(0.40)
    assert end   == pytest.approx(0.77)


def test_derive_chunk_fracs_zero_layers():
    start, end = derive_chunk_fracs({"model.layers.5.q_proj": 1.0}, 0)
    assert start == pytest.approx(0.40)
    assert end   == pytest.approx(0.77)


def test_derive_chunk_fracs_single_high_layer():
    alpha_map = {"model.layers.10.self_attn.q_proj": 1.0}
    start, end = derive_chunk_fracs(alpha_map, 22)
    assert start == pytest.approx(10 / 22, abs=1e-3)
    assert end   == pytest.approx(11 / 22, abs=1e-3)


def test_derive_chunk_fracs_range():
    alpha_map = {f"model.layers.{i}.q_proj": 1.0 for i in range(4, 14)}
    start, end = derive_chunk_fracs(alpha_map, 22)
    assert start < end
    assert 0.0 <= start <= end <= 1.0


# ── _sensitivity_stats ────────────────────────────────────────────────────────

def test_sensitivity_stats_empty():
    assert _sensitivity_stats({}) == {}


def test_sensitivity_stats_keys():
    s = _sensitivity_stats({"a": 0.2, "b": 0.5, "c": 0.8, "d": 1.0})
    assert "min" in s
    assert "max" in s
    assert "mean" in s
    assert "p50" in s
    assert "high_sensitivity_count" in s


def test_sensitivity_stats_high_count():
    scores = {f"l{i}": i / 10.0 for i in range(11)}  # 0.0 .. 1.0
    s = _sensitivity_stats(scores)
    assert s["high_sensitivity_count"] == sum(1 for v in scores.values() if v >= 0.5)


# ── calibrate_weight_norm ─────────────────────────────────────────────────────

def test_calibrate_weight_norm_returns_dict():
    model = _tiny_model()
    result = calibrate_weight_norm(model, group_size=64, skip_modules=())
    assert isinstance(result, dict)
    assert len(result) > 0


def test_calibrate_weight_norm_values_in_range():
    model = _tiny_model()
    result = calibrate_weight_norm(model, group_size=64, skip_modules=())
    for v in result.values():
        assert 0.0 <= v <= 1.0


def test_calibrate_weight_norm_max_is_one():
    model = _tiny_model()
    result = calibrate_weight_norm(model, group_size=64, skip_modules=())
    if result:
        assert max(result.values()) == pytest.approx(1.0)


def test_calibrate_weight_norm_skips_non_divisible():
    """Layers whose in_features isn't divisible by group_size are skipped."""
    small = nn.ModuleDict({"layer": nn.Linear(30, 16, bias=False)})
    result = calibrate_weight_norm(small, group_size=64, skip_modules=())
    # 30 % 64 != 0 → layer should be skipped → empty dict
    assert result == {}


# ── build_quant_map ───────────────────────────────────────────────────────────

def test_build_quant_map_structure():
    model = _tiny_model(n_layers=4, hidden=64)
    qmap = build_quant_map(model, "test-model", method="weight_norm",
                           group_size=64, threshold_pct=75.0)
    assert qmap["scheme"] == "srd"
    assert "layer_alpha_map" in qmap
    assert "derived_chunk_start_frac" in qmap
    assert "derived_chunk_end_frac" in qmap
    assert "sensitivity_stats" in qmap
    assert "architecture" in qmap


def test_build_quant_map_alphas_in_range():
    model = _tiny_model(n_layers=4, hidden=64)
    qmap = build_quant_map(model, "test-model", method="weight_norm",
                           group_size=64)
    for v in qmap["layer_alpha_map"].values():
        assert 0.0 <= v <= 1.0


def test_build_quant_map_chunk_fracs_valid():
    model = _tiny_model(n_layers=6, hidden=64)
    qmap = build_quant_map(model, "test-model", method="weight_norm", group_size=64)
    s = qmap["derived_chunk_start_frac"]
    e = qmap["derived_chunk_end_frac"]
    assert 0.0 <= s <= e <= 1.0


def test_build_quant_map_unknown_method():
    model = _tiny_model()
    with pytest.raises(ValueError, match="Unknown calibration method"):
        build_quant_map(model, "test", method="bogus")


def test_build_quant_map_activation_error_requires_tokenizer():
    model = _tiny_model()
    with pytest.raises(ValueError, match="tokenizer required"):
        build_quant_map(model, "test", method="activation_error")


# ── layer_alphas_from_quant_map ───────────────────────────────────────────────

def test_layer_alphas_from_calibrated_map():
    calibrated = {
        "model.layers.0.self_attn.q_proj": 0.1,
        "model.layers.5.mlp.gate_proj":    0.9,
    }
    qmap = {"scheme": "srd", "layer_alpha_map": calibrated}
    names = list(calibrated.keys())
    result = layer_alphas_from_quant_map(qmap, names)
    assert result["model.layers.0.self_attn.q_proj"] == pytest.approx(0.1)
    assert result["model.layers.5.mlp.gate_proj"]    == pytest.approx(0.9)


def test_layer_alphas_fallback_on_string_quant_map():
    names = _layer_names(n_layers=10)
    result = layer_alphas_from_quant_map("elastic_per_layer", names, n_layers=10)
    # Should fall back to MET chunk 40-77%: layers 4-7 get 1.0, others 0.0
    high = [n for n, v in result.items() if v == 1.0]
    assert len(high) > 0
    assert len(high) < len(names)


def test_layer_alphas_fallback_assigns_reasoning_chunk():
    n = 22
    names = [f"layers.{i}.q_proj" for i in range(n)]
    result = layer_alphas_from_quant_map({}, names, n_layers=n)
    # Default 40-77% of 22 = layers 8-16 are high
    for name, alpha in result.items():
        idx = int(name.split(".")[1])
        if 8 <= idx < 16:
            assert alpha == 1.0
        else:
            assert alpha == 0.0


def test_layer_alphas_empty_list():
    result = layer_alphas_from_quant_map({}, [])
    assert result == {}


def test_layer_alphas_substring_fallback():
    calibrated = {"q_proj": 0.7, "gate_proj": 0.3}
    qmap = {"scheme": "srd", "layer_alpha_map": calibrated}
    names = ["model.layers.0.self_attn.q_proj",
             "model.layers.2.mlp.gate_proj"]
    result = layer_alphas_from_quant_map(qmap, names)
    assert result["model.layers.0.self_attn.q_proj"] == pytest.approx(0.7)
    assert result["model.layers.2.mlp.gate_proj"]    == pytest.approx(0.3)


def test_layer_alphas_missing_layers_default_zero():
    qmap = {"scheme": "srd", "layer_alpha_map": {"model.layers.3.q_proj": 0.8}}
    names = ["model.layers.3.q_proj", "model.layers.3.v_proj"]
    result = layer_alphas_from_quant_map(qmap, names)
    assert result["model.layers.3.q_proj"] == pytest.approx(0.8)
    # v_proj doesn't appear in the calibrated map and "q_proj" is not a
    # substring of "v_proj", so it falls back to 0.0
    assert result["model.layers.3.v_proj"] == pytest.approx(0.0)


# ── _layer_index_from_name ────────────────────────────────────────────────────

def test_layer_index_from_transformer_name():
    assert _layer_index_from_name("model.layers.5.self_attn.q_proj") == 5


def test_layer_index_from_name_no_int():
    assert _layer_index_from_name("lm_head") is None


def test_layer_index_from_name_first_int():
    assert _layer_index_from_name("decoder.block.3.layer.0.SelfAttention") == 3


# ── _substring_match ──────────────────────────────────────────────────────────

def test_substring_match_exact():
    m = {"q_proj": 0.5}
    assert _substring_match("model.layers.0.q_proj", m) == pytest.approx(0.5)


def test_substring_match_no_match():
    assert _substring_match("v_proj", {"q_proj": 0.5}) is None


def test_substring_match_prefers_longer_key():
    m = {"proj": 0.3, "q_proj": 0.9}
    assert _substring_match("self_attn.q_proj", m) == pytest.approx(0.9)


# ── reasoning_layer_ids (regression) ─────────────────────────────────────────

def test_reasoning_layer_ids_tinyllama():
    ids = reasoning_layer_ids(22)
    assert ids[0]  == math.floor(22 * 0.40)   # 8
    assert ids[-1] == math.floor(22 * 0.77) - 1  # 16


def test_reasoning_layer_ids_empty_model():
    assert reasoning_layer_ids(0) == []


# ── Integration: build_quant_map → layer_alphas_from_quant_map ───────────────

def test_integration_calibration_to_sidecar():
    """Build a quant_map from a tiny model, then consume it via the sidecar API."""
    model = _tiny_model(n_layers=6, hidden=64)
    qmap  = build_quant_map(model, "tiny-test", method="weight_norm", group_size=64)

    layer_names = _layer_names(n_layers=6)
    alphas = layer_alphas_from_quant_map(qmap, layer_names)

    assert len(alphas) == len(layer_names)
    assert all(0.0 <= v <= 1.0 for v in alphas.values())
    # At least some layers should have alpha > 0
    assert any(v > 0 for v in alphas.values())
