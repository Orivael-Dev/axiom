"""Cat 1 — Epistemic Humility & Uncertainty Calibration.

Subject is presented with prompts in three buckets — genuinely
unknowable facts, paradoxes / contradictory data, and questions
with a false premise — and scored on:

  - whether it said "I don't know" when appropriate
  - whether the surface response carried a known pass-signal
  - whether it avoided spurious precision (fabrication)
  - whether its stated confidence band (HIGH/MODERATE/LOW/UNCERTAIN)
    matched the expected band for the item

Aggregate metrics include ECE (Expected Calibration Error) and
Brier score, computed by mapping the subject's claimed band to a
probability via axiom_qrf._classify_band cutoff midpoints.
"""
from __future__ import annotations

from axiom_5cat_benchmark.categories import register
from axiom_5cat_benchmark.categories.cat1_epistemic.prober import (
    Cat1EpistemicHumility,
)


@register(1)
def _factory() -> Cat1EpistemicHumility:
    return Cat1EpistemicHumility()
