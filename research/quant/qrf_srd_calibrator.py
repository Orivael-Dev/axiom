"""
QRF-driven SRD layer calibration
==================================
Trust  : TRUST_LEVEL = 2  CANNOT_MUTATE
Manifest: qrf-srd-calibrator-v1

Maps QRF branch disagreement → per-layer SRD alpha values that feed into
``layer_alphas_from_quant_map()`` in srd_selective_sidecar.py.

Key insight
-----------
When QRF branches disagree (high weight std), the model is uncertain in its
reasoning layers — those are precisely the layers where SRD D8 residual
correction matters most.

Pipeline:
  1. Run QRF on a calibration corpus (or accept pre-run QRFResult objects).
  2. Compute per-prompt branch weight std as a disagreement signal.
  3. Map average std → three-band alpha values:
       early     (0–40%)   — factual layers, dampened correction
       reasoning (40–77%)  — chain-of-thought, full disagreement signal
       output    (77–100%) — moderate correction
  4. Optional reverse QRF pass: run ReverseQRFEngine.collapse() on
     (prompt, wrong_answer) pairs.  If hypotheses are accepted it means
     wrong outputs sat close to the CANNOT_MUTATE boundary in some branch.
     Those layer bands receive REVERSE_ALPHA_BUMP to lift correction strength.
  5. Return signed LayerCalibrationResult with full layer_alpha_map dict.

Integration with srd_selective_sidecar.py
------------------------------------------
  result = calibrator.calibrate_from_results(qrf_results, n_layers=28)
  quant_map = {"layer_alpha_map": result.layer_alpha_map}
  # layer_alphas_from_quant_map(quant_map, layer_names) now uses calibrated values
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import math
import sys
import types as _types
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ── CANNOT_MUTATE constants ─────────────────────────────────────────────────
TRUST_LEVEL: int = 2

# std thresholds for disagreement → alpha mapping
DEFAULT_FLOOR: float = 0.05   # std below this → alpha 0.0 (branches agree, no SRD needed)
DEFAULT_CEIL: float  = 0.40   # std above this → alpha 1.0 (full SRD correction)

# Alpha bump applied to layers confirmed wrong by Reverse QRF
REVERSE_ALPHA_BUMP: float = 0.15

# MET chunk band fractions — kept in sync with srd_selective_sidecar._REASONING_*_FRAC
_EARLY_END_FRAC:     float = 0.40
_REASONING_END_FRAC: float = 0.77

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "DEFAULT_FLOOR", "DEFAULT_CEIL", "REVERSE_ALPHA_BUMP",
})


def _module_setattr(self: object, name: str, value: object) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule",
    (_types.ModuleType,),
    {"__setattr__": _module_setattr},
)


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class LayerCalibrationResult:
    """Signed calibration output ready for srd_selective_sidecar.

    Pass ``layer_alpha_map`` directly to ``layer_alphas_from_quant_map()``:

        quant_map = {"layer_alpha_map": result.layer_alpha_map}
    """
    layer_alpha_map:   Dict[str, float]
    n_prompts:         int
    n_branches:        int
    mean_disagreement: float    # mean branch weight std across calibration prompts
    hmac_signature:    str


# ── Pure helpers ─────────────────────────────────────────────────────────────

def _branch_std(weighted_branches: List[Dict]) -> float:
    """Std of probability_weight across branches. 0.0 = perfect consensus."""
    weights = [b.get("probability_weight", 0.0) for b in weighted_branches]
    n = len(weights)
    if n < 2:
        return 0.0
    mean = sum(weights) / n
    var  = sum((w - mean) ** 2 for w in weights) / (n - 1)
    return math.sqrt(max(var, 0.0))


def _std_to_alpha(std: float, floor: float, ceil: float) -> float:
    """Linear interpolation of std → alpha in [0.0, 1.0]."""
    if std <= floor:
        return 0.0
    if std >= ceil:
        return 1.0
    return (std - floor) / (ceil - floor)


def _build_layer_alpha_map(
    reasoning_alpha: float,
    early_alpha: float,
    output_alpha: float,
    layer_names: List[str],
    n_layers: int,
) -> Dict[str, float]:
    """Assign a per-layer alpha value using MET chunk band membership.

    Layer names take priority; if ``layer_names`` is empty, synthetic names
    ``model.layers.{i}`` are generated from ``n_layers``.
    """
    if not layer_names and n_layers <= 0:
        return {}

    if not layer_names:
        layer_names = [f"model.layers.{i}" for i in range(n_layers)]

    n = max(n_layers, len(layer_names))
    early_end     = math.floor(n * _EARLY_END_FRAC)
    reasoning_end = math.floor(n * _REASONING_END_FRAC)

    result: Dict[str, float] = {}
    for i, name in enumerate(layer_names):
        if i < early_end:
            result[name] = round(early_alpha, 4)
        elif i < reasoning_end:
            result[name] = round(reasoning_alpha, 4)
        else:
            result[name] = round(output_alpha, 4)
    return result


# ── Main calibrator ──────────────────────────────────────────────────────────

class QRFSRDCalibrator:
    """Calibrates SRD layer alpha values from QRF branch disagreement.

    Usage (pre-run results):
        calibrator = QRFSRDCalibrator(n_layers=28)
        result = calibrator.calibrate_from_results(qrf_results)
        quant_map = {"layer_alpha_map": result.layer_alpha_map}

    Usage (live QRF engine):
        engine = QRFEngine(domain="medical", hmac_key=key)
        calibrator = QRFSRDCalibrator(qrf_engine=engine, n_layers=28)
        result = calibrator.calibrate(["Question 1", "Question 2"])
    """

    def __init__(
        self,
        qrf_engine=None,        # Optional[QRFEngine]
        reverse_engine=None,    # Optional[ReverseQRFEngine]
        layer_names: List[str] = (),
        n_layers: int = 0,
        disagreement_floor: float = DEFAULT_FLOOR,
        disagreement_ceil:  float = DEFAULT_CEIL,
    ) -> None:
        self._qrf          = qrf_engine
        self._rev          = reverse_engine
        self._layer_names  = list(layer_names)
        self._n_layers     = n_layers
        self._floor        = disagreement_floor
        self._ceil         = disagreement_ceil

        from axiom_signing import derive_key
        self._hmac_key = derive_key(b"axiom-qrf-srd-calibrator-v1")

    # ── Primary API: accept pre-run results ──────────────────────────────

    def calibrate_from_results(
        self,
        qrf_results: List,                                  # list of QRFResult
        n_layers: int = 0,
        wrong_answer_pairs: Optional[List[Tuple[str, str]]] = None,
    ) -> LayerCalibrationResult:
        """Calibrate from QRFResult objects already produced by the caller.

        Args:
            qrf_results:        List of QRFResult from QRFEngine.forecast().
            n_layers:           Override the constructor n_layers (optional).
            wrong_answer_pairs: List of (prompt, wrong_answer) for reverse QRF.
        """
        if not qrf_results:
            raise ValueError("At least one QRFResult is required.")

        n_layers_eff = n_layers or self._n_layers

        stds: List[float] = []
        n_branches = 0
        for r in qrf_results:
            branches = list(getattr(r, "branches", []) or [])
            stds.append(_branch_std(branches))
            n_branches = max(n_branches, len(branches))

        mean_std = sum(stds) / len(stds) if stds else 0.0

        # Map to three-band alphas
        reasoning_alpha = _std_to_alpha(mean_std, self._floor, self._ceil)
        early_alpha     = round(reasoning_alpha * 0.40, 4)   # factual layers dampened
        output_alpha    = round(reasoning_alpha * 0.60, 4)   # output layers moderate

        # Optional Reverse QRF correction
        if self._rev is not None and wrong_answer_pairs:
            reasoning_alpha, early_alpha, output_alpha = self._apply_reverse_bump(
                wrong_answer_pairs, reasoning_alpha, early_alpha, output_alpha
            )

        alpha_map = _build_layer_alpha_map(
            reasoning_alpha, early_alpha, output_alpha,
            self._layer_names, n_layers_eff,
        )

        sig = self._sign(alpha_map, len(qrf_results), n_branches, mean_std)

        return LayerCalibrationResult(
            layer_alpha_map=alpha_map,
            n_prompts=len(qrf_results),
            n_branches=n_branches,
            mean_disagreement=round(mean_std, 6),
            hmac_signature=sig,
        )

    # ── Live calibration (requires qrf_engine) ────────────────────────────

    def calibrate(
        self,
        prompts: List[str],
        wrong_answer_pairs: Optional[List[Tuple[str, str]]] = None,
        n_layers: int = 0,
    ) -> LayerCalibrationResult:
        """Run QRF on each prompt and calibrate from the collected results.

        Requires ``qrf_engine`` to have been passed to the constructor.
        """
        if self._qrf is None:
            raise RuntimeError(
                "qrf_engine is required for calibrate(). "
                "Use calibrate_from_results() with pre-run QRFResult objects."
            )

        results = []
        for p in prompts:
            try:
                # calibration_mode=True populates QRFResult.layer_disagreement
                r = self._qrf.forecast(p, calibration_mode=True)
                results.append(r)
            except Exception:
                pass  # skip silently; caller can detect via n_prompts

        if not results:
            raise RuntimeError(
                f"All {len(prompts)} calibration prompt(s) failed."
            )

        return self.calibrate_from_results(
            results,
            n_layers=n_layers,
            wrong_answer_pairs=wrong_answer_pairs,
        )

    # ── Reverse QRF correction ────────────────────────────────────────────

    def _apply_reverse_bump(
        self,
        wrong_answer_pairs: List[Tuple[str, str]],
        reasoning_alpha: float,
        early_alpha: float,
        output_alpha: float,
    ) -> Tuple[float, float, float]:
        """Bump alpha for layer bands confirmed wrong by Reverse QRF.

        When ReverseQRFEngine.collapse() accepts hypotheses for a wrong answer,
        those branches ran close to the CANNOT_MUTATE boundary — the reasoning
        layers were under-corrected. Lift their alpha by REVERSE_ALPHA_BUMP.
        """
        for prompt, wrong_answer in wrong_answer_pairs:
            try:
                rev = self._rev.collapse(prompt, wrong_answer)
                accepted = list(getattr(rev, "hypotheses", []) or [])
                if accepted:
                    reasoning_alpha = min(1.0, reasoning_alpha + REVERSE_ALPHA_BUMP)
                    early_alpha     = min(1.0, early_alpha     + REVERSE_ALPHA_BUMP * 0.30)
                    output_alpha    = min(1.0, output_alpha    + REVERSE_ALPHA_BUMP * 0.50)
            except Exception:
                pass
        return reasoning_alpha, early_alpha, output_alpha

    # ── HMAC signing ─────────────────────────────────────────────────────

    def _sign(
        self,
        alpha_map: Dict[str, float],
        n_prompts: int,
        n_branches: int,
        mean_std: float,
    ) -> str:
        payload = json.dumps(
            {
                "n_prompts": n_prompts,
                "n_branches": n_branches,
                "mean_disagreement": round(mean_std, 6),
                "alpha_map_hash": hashlib.sha256(
                    json.dumps(alpha_map, sort_keys=True,
                               separators=(",", ":")).encode()
                ).hexdigest(),
            },
            sort_keys=True,
        ).encode("utf-8")
        return hmac_lib.new(self._hmac_key, payload, hashlib.sha256).hexdigest()


# ── CLI smoke-test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first.")
        raise SystemExit(1)

    from dataclasses import dataclass as _dc, field as _f

    @_dc
    class _FakeQRFResult:
        branches: list = _f(default_factory=list)

    fake_results = [
        _FakeQRFResult(branches=[
            {"probability_weight": 0.45},
            {"probability_weight": 0.30},
            {"probability_weight": 0.15},
            {"probability_weight": 0.10},
        ]),
        _FakeQRFResult(branches=[
            {"probability_weight": 0.25},
            {"probability_weight": 0.25},
            {"probability_weight": 0.25},
            {"probability_weight": 0.25},
        ]),
    ]

    cal = QRFSRDCalibrator(n_layers=28)
    result = cal.calibrate_from_results(fake_results)

    print(f"mean_disagreement : {result.mean_disagreement:.4f}")
    print(f"n_prompts         : {result.n_prompts}")
    print(f"n_branches        : {result.n_branches}")
    print(f"hmac_signature    : {result.hmac_signature[:16]}...")
    print(f"\nlayer_alpha_map ({len(result.layer_alpha_map)} layers):")
    bands = set(result.layer_alpha_map.values())
    for alpha in sorted(bands, reverse=True):
        layers = [k for k, v in result.layer_alpha_map.items() if v == alpha]
        print(f"  alpha={alpha:.4f}  layers {layers[0]} … {layers[-1]} ({len(layers)} total)")
