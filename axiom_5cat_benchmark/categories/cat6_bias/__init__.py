"""Cat 6 — Bias Detection (PLANNED, NOT YET REGISTERED).

This subpackage is a placeholder. Cat 6 will measure demographic,
ideological, and framing bias in subject outputs.

GATING CONDITION
================
Cat 6 will not be implemented until Cat 1 (Epistemic Humility &
Uncertainty Calibration) demonstrates ECE ≤ 0.15 on real LLMs
across the full 45-prompt corpus.

Rationale: a bias detector that can't itself calibrate "this
output is biased with HIGH confidence" vs "this output is biased
with LOW confidence" produces noise. The Cat 1 scorer is the
calibration spine for every downstream judgement made by this
benchmark — including the bias verdicts Cat 6 would emit. Without
a calibrated Cat 1 we'd be publishing signed-but-uncalibrated
bias scores, which is worse than not publishing at all.

When the gate flips, expected scope:
  - 4 sub-dimensions: Demographic / Ideological / Framing /
    Counterfactual-consistency
  - Prompts drawn from BBQ + StereoSet + Winogender families,
    plus net-new false-balance items
  - Per-sub-dimension calibration band (HIGH / MODERATE / LOW /
    UNCERTAIN) — same axiom_qrf cutoffs as Cat 1
  - Gate: PASS iff mean ≥ 5/8 AND no sub-dimension shows
    statistically-significant directional bias at p < 0.05

This module intentionally does NOT call categories.register(6) —
attempting to run Cat 6 via the CLI / runner will raise a clear
KeyError, which is the correct behaviour until the gate flips.
"""
from __future__ import annotations

# Intentional no-op. Do not register(6) until the gating condition
# above is satisfied.
PLANNED: bool = True
GATING_CONDITION: str = (
    "Cat 1 ECE ≤ 0.15 on full 45-item corpus across "
    "anthropic + openai adapters"
)
