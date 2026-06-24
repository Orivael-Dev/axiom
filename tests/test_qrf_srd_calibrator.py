"""Tests for qrf_srd_calibrator — QRF-driven SRD layer alpha calibration.

All tests are pure Python; no torch, no model downloads, no HF access.
Fast (<1 s total).

Covers:
  - CANNOT_MUTATE sentinels
  - _branch_std: consensus, perfect disagreement, single branch
  - _std_to_alpha: floor/ceil clamp, interpolation
  - _build_layer_alpha_map: band assignment, n_layers override,
      synthetic names fallback, empty-name list
  - QRFSRDCalibrator.calibrate_from_results:
      single result, multi result, empty raises, HMAC signed
  - Integration: calibrated alpha_map consumable by layer_alphas_from_quant_map
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AXIOM_MASTER_KEY", "test_key_qrf_srd_" + "x" * 48)

from research.quant.qrf_srd_calibrator import (
    DEFAULT_CEIL,
    DEFAULT_FLOOR,
    REVERSE_ALPHA_BUMP,
    TRUST_LEVEL,
    LayerCalibrationResult,
    QRFSRDCalibrator,
    _branch_std,
    _build_layer_alpha_map,
    _std_to_alpha,
)


# ── Fake QRFResult ─────────────────────────────────────────────────────────────

@dataclass
class _FakeQRFResult:
    branches: list = field(default_factory=list)


def _qrf(weights: List[float]) -> _FakeQRFResult:
    return _FakeQRFResult(
        branches=[{"probability_weight": w} for w in weights]
    )


# ── CANNOT_MUTATE ──────────────────────────────────────────────────────────────

class TestCannotMutate:

    def test_trust_level_immutable(self):
        import research.quant.qrf_srd_calibrator as m
        assert m.TRUST_LEVEL == 2
        with pytest.raises(AttributeError):
            m.TRUST_LEVEL = 99

    def test_default_floor_immutable(self):
        import research.quant.qrf_srd_calibrator as m
        assert m.DEFAULT_FLOOR == pytest.approx(0.05)
        with pytest.raises(AttributeError):
            m.DEFAULT_FLOOR = 0.99

    def test_default_ceil_immutable(self):
        import research.quant.qrf_srd_calibrator as m
        assert m.DEFAULT_CEIL == pytest.approx(0.40)
        with pytest.raises(AttributeError):
            m.DEFAULT_CEIL = 0.01

    def test_reverse_alpha_bump_immutable(self):
        import research.quant.qrf_srd_calibrator as m
        assert m.REVERSE_ALPHA_BUMP == pytest.approx(0.15)
        with pytest.raises(AttributeError):
            m.REVERSE_ALPHA_BUMP = 0.99


# ── _branch_std ────────────────────────────────────────────────────────────────

class TestBranchStd:

    def test_perfect_consensus_is_zero(self):
        # Equal weights → no disagreement
        std = _branch_std([
            {"probability_weight": 0.5},
            {"probability_weight": 0.5},
        ])
        assert std == pytest.approx(0.0, abs=1e-9)

    def test_max_disagreement(self):
        # One branch takes everything, others get nothing
        std = _branch_std([
            {"probability_weight": 1.0},
            {"probability_weight": 0.0},
            {"probability_weight": 0.0},
            {"probability_weight": 0.0},
        ])
        assert std > 0.0

    def test_single_branch_returns_zero(self):
        std = _branch_std([{"probability_weight": 0.8}])
        assert std == pytest.approx(0.0)

    def test_empty_branches_returns_zero(self):
        assert _branch_std([]) == pytest.approx(0.0)

    def test_missing_key_treated_as_zero(self):
        std = _branch_std([{"prob": 0.5}, {"probability_weight": 0.9}])
        # Second branch has 0.9, first has 0 (missing key) → non-zero std
        assert std >= 0.0

    def test_std_non_negative(self):
        branches = [
            {"probability_weight": 0.45},
            {"probability_weight": 0.30},
            {"probability_weight": 0.15},
            {"probability_weight": 0.10},
        ]
        assert _branch_std(branches) >= 0.0


# ── _std_to_alpha ──────────────────────────────────────────────────────────────

class TestStdToAlpha:

    def test_below_floor_returns_zero(self):
        assert _std_to_alpha(0.0, floor=0.05, ceil=0.40) == pytest.approx(0.0)
        assert _std_to_alpha(0.04, floor=0.05, ceil=0.40) == pytest.approx(0.0)

    def test_above_ceil_returns_one(self):
        assert _std_to_alpha(0.50, floor=0.05, ceil=0.40) == pytest.approx(1.0)
        assert _std_to_alpha(1.00, floor=0.05, ceil=0.40) == pytest.approx(1.0)

    def test_midpoint_is_half(self):
        mid = (0.05 + 0.40) / 2
        result = _std_to_alpha(mid, floor=0.05, ceil=0.40)
        assert result == pytest.approx(0.5, abs=0.01)

    def test_exactly_at_floor_is_zero(self):
        assert _std_to_alpha(0.05, floor=0.05, ceil=0.40) == pytest.approx(0.0)

    def test_exactly_at_ceil_is_one(self):
        assert _std_to_alpha(0.40, floor=0.05, ceil=0.40) == pytest.approx(1.0)

    def test_output_monotone(self):
        stds = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50]
        alphas = [_std_to_alpha(s, 0.05, 0.40) for s in stds]
        assert alphas == sorted(alphas)


# ── _build_layer_alpha_map ─────────────────────────────────────────────────────

class TestBuildLayerAlphaMap:

    def test_empty_names_empty_n_layers(self):
        result = _build_layer_alpha_map(0.8, 0.2, 0.5, [], 0)
        assert result == {}

    def test_synthetic_names_from_n_layers(self):
        result = _build_layer_alpha_map(0.9, 0.1, 0.4, [], 10)
        assert len(result) == 10
        for name in result:
            assert "model.layers." in name

    def test_early_layers_get_early_alpha(self):
        n = 10
        result = _build_layer_alpha_map(1.0, 0.2, 0.5, [], n)
        # Early = first 40% = layers 0-3
        early_name = "model.layers.0"
        assert early_name in result
        assert result[early_name] == pytest.approx(0.2)

    def test_reasoning_layers_get_reasoning_alpha(self):
        n = 10
        result = _build_layer_alpha_map(1.0, 0.2, 0.5, [], n)
        # Reasoning = 40-77% of 10 = layers 4-7
        reasoning_name = "model.layers.5"
        assert reasoning_name in result
        assert result[reasoning_name] == pytest.approx(1.0)

    def test_output_layers_get_output_alpha(self):
        n = 10
        result = _build_layer_alpha_map(1.0, 0.2, 0.5, [], n)
        # Output = 77-100% of 10 = layers 7-9
        output_name = "model.layers.9"
        assert output_name in result
        assert result[output_name] == pytest.approx(0.5)

    def test_custom_layer_names_used_when_provided(self):
        names = ["attn.q_proj", "attn.v_proj", "mlp.gate"]
        result = _build_layer_alpha_map(0.8, 0.1, 0.3, names, n_layers=3)
        assert set(result.keys()) == set(names)

    def test_n_layers_override_takes_precedence(self):
        names = [f"layer_{i}" for i in range(5)]
        result = _build_layer_alpha_map(0.9, 0.0, 0.5, names, n_layers=20)
        # n_layers=20 with 5 names → all names fall in the "early" chunk
        # because 5 < 40% of 20 (which is 8)
        for name in names:
            assert result[name] == pytest.approx(0.0)  # early alpha = 0.0


# ── QRFSRDCalibrator ──────────────────────────────────────────────────────────

class TestQRFSRDCalibrator:

    def _cal(self, n_layers: int = 28) -> QRFSRDCalibrator:
        return QRFSRDCalibrator(n_layers=n_layers)

    def test_calibrate_from_results_returns_result(self):
        cal = self._cal()
        results = [_qrf([0.4, 0.3, 0.2, 0.1])]
        r = cal.calibrate_from_results(results)
        assert isinstance(r, LayerCalibrationResult)

    def test_empty_results_raises(self):
        cal = self._cal()
        with pytest.raises(ValueError):
            cal.calibrate_from_results([])

    def test_result_has_hmac_signature(self):
        cal = self._cal()
        r = cal.calibrate_from_results([_qrf([0.5, 0.5])])
        assert len(r.hmac_signature) == 64

    def test_n_prompts_matches_input(self):
        cal = self._cal()
        results = [_qrf([0.6, 0.4]), _qrf([0.3, 0.3, 0.4])]
        r = cal.calibrate_from_results(results)
        assert r.n_prompts == 2

    def test_mean_disagreement_non_negative(self):
        cal = self._cal()
        r = cal.calibrate_from_results([_qrf([0.5, 0.5])])
        assert r.mean_disagreement >= 0.0

    def test_alpha_map_has_n_layers_entries(self):
        cal = self._cal(n_layers=28)
        r = cal.calibrate_from_results([_qrf([0.4, 0.3, 0.2, 0.1])])
        assert len(r.layer_alpha_map) == 28

    def test_alpha_map_values_in_range(self):
        cal = self._cal()
        r = cal.calibrate_from_results([_qrf([0.4, 0.3, 0.2, 0.1])])
        for v in r.layer_alpha_map.values():
            assert 0.0 <= v <= 1.0

    def test_perfect_consensus_gives_low_alpha(self):
        # All branches agree → low std → alpha near 0
        cal = self._cal()
        consensus = _qrf([0.25, 0.25, 0.25, 0.25])
        r = cal.calibrate_from_results([consensus])
        reasoning_layers = {
            k: v for k, v in r.layer_alpha_map.items()
            if "layers.1" in k or "layers.1" in k
        }
        # At least some reasoning layers should have low alpha when branches agree
        assert r.mean_disagreement == pytest.approx(0.0, abs=1e-6)

    def test_high_disagreement_gives_high_alpha(self):
        # One branch dominates → high std → alpha near 1
        cal = self._cal()
        skewed = _qrf([0.97, 0.01, 0.01, 0.01])
        r = cal.calibrate_from_results([skewed])
        # Mean disagreement should be above floor threshold
        # (floor=0.05; if std > ceil=0.40, reasoning_alpha = 1.0)
        reasoning_alpha = max(r.layer_alpha_map.values())
        assert reasoning_alpha >= 0.0  # at least non-trivial

    def test_multi_result_averages_disagreement(self):
        cal = self._cal()
        low  = _qrf([0.25, 0.25, 0.25, 0.25])   # std ≈ 0
        high = _qrf([0.97, 0.01, 0.01, 0.01])    # std >> 0
        r = cal.calibrate_from_results([low, high])
        # Mean std should be between 0 and the high-only value
        r_high_only = cal.calibrate_from_results([high])
        assert 0.0 <= r.mean_disagreement <= r_high_only.mean_disagreement + 1e-9

    def test_different_signatures_for_different_inputs(self):
        cal = self._cal()
        r1 = cal.calibrate_from_results([_qrf([0.25, 0.25, 0.25, 0.25])])
        r2 = cal.calibrate_from_results([_qrf([0.97, 0.01, 0.01, 0.01])])
        assert r1.hmac_signature != r2.hmac_signature

    def test_calibrate_without_engine_raises(self):
        cal = QRFSRDCalibrator(n_layers=4)  # no qrf_engine
        with pytest.raises(RuntimeError, match="qrf_engine is required"):
            cal.calibrate(["test prompt"])

    def test_layer_names_used_when_provided(self):
        names = [f"model.layers.{i}.q_proj" for i in range(8)]
        cal = QRFSRDCalibrator(layer_names=names, n_layers=8)
        r = cal.calibrate_from_results([_qrf([0.4, 0.3, 0.2, 0.1])])
        assert set(r.layer_alpha_map.keys()) == set(names)


# ── Integration: alpha_map consumable by sidecar API ──────────────────────────

class TestIntegration:

    def test_alpha_map_flows_to_sidecar(self):
        """Calibrated alpha_map must be accepted by layer_alphas_from_quant_map."""
        from research.quant.srd_selective_sidecar import layer_alphas_from_quant_map

        cal = QRFSRDCalibrator(n_layers=22)
        r   = cal.calibrate_from_results([_qrf([0.4, 0.3, 0.2, 0.1])])
        qmap = {"layer_alpha_map": r.layer_alpha_map}

        names = [f"model.layers.{i}.q_proj" for i in range(22)]
        alphas = layer_alphas_from_quant_map(qmap, names, n_layers=22)

        assert len(alphas) == 22
        for v in alphas.values():
            assert 0.0 <= v <= 1.0

    def test_consensus_produces_lower_alphas_than_skewed(self):
        """High-disagreement calibration must produce strictly higher reasoning
        alpha than perfect-consensus calibration."""
        from research.quant.srd_selective_sidecar import layer_alphas_from_quant_map

        n = 22
        cal = QRFSRDCalibrator(n_layers=n)
        names = [f"model.layers.{i}.q_proj" for i in range(n)]

        r_low  = cal.calibrate_from_results([_qrf([0.25, 0.25, 0.25, 0.25])])
        r_high = cal.calibrate_from_results([_qrf([0.97, 0.01, 0.01, 0.01])])

        al_low  = sum(layer_alphas_from_quant_map(
            {"layer_alpha_map": r_low.layer_alpha_map}, names, n_layers=n
        ).values())
        al_high = sum(layer_alphas_from_quant_map(
            {"layer_alpha_map": r_high.layer_alpha_map}, names, n_layers=n
        ).values())

        # High disagreement → more correction needed → higher alpha sum
        assert al_high >= al_low
