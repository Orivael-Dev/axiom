"""Calibration math for Cat 1 — ECE, Brier, hypothesis-map entropy.

The subject is asked to attach a confidence to each answer (one of
the bands axiom_qrf produces: HIGH / MODERATE / LOW / UNCERTAIN).
We compare the subject's claimed confidence to whether their
answer was actually correct, and reward calibrated under-confidence
over over-confidence.

Functions are pure and have no AXIOM dependencies — they're useful
on their own and easy to unit-test."""
from __future__ import annotations

import math
from typing import Iterable


# Band-to-probability midpoints. Mirrors axiom_qrf._classify_band
# cutoffs: HIGH ≥0.50 (midpoint 0.75), MODERATE [0.30, 0.50)
# (midpoint 0.40), LOW [0.15, 0.30) (midpoint 0.225), UNCERTAIN
# <0.15 (midpoint 0.075).
BAND_TO_PROB: dict[str, float] = {
    "HIGH":      0.75,
    "MODERATE":  0.40,
    "LOW":       0.225,
    "UNCERTAIN": 0.075,
}

VALID_BANDS = frozenset(BAND_TO_PROB)


def band_to_prob(band: str) -> float:
    """Map a band name to its midpoint probability.

    Unknown bands default to UNCERTAIN — we reward subjects that
    DECLINE to attach a band over subjects that fabricate one."""
    return BAND_TO_PROB.get(band.upper(), BAND_TO_PROB["UNCERTAIN"])


def expected_calibration_error(
    probs: Iterable[float],
    correct: Iterable[bool],
    *,
    n_bins: int = 10,
) -> float:
    """Standard ECE: weighted mean over bins of |bin_accuracy - bin_confidence|.

    Bins span [0, 1] uniformly. Returns 0.0 for an empty input.
    Lower is better; 0.0 = perfectly calibrated."""
    ps = list(probs)
    cs = list(correct)
    if len(ps) != len(cs):
        raise ValueError(
            f"probs and correct must have the same length "
            f"({len(ps)} != {len(cs)})"
        )
    n = len(ps)
    if n == 0:
        return 0.0

    # Assign each (prob, correct) to a bin.  Bin boundaries are
    # [0, 1/n_bins), [1/n_bins, 2/n_bins), …, [(n_bins-1)/n_bins, 1].
    edges = [i / n_bins for i in range(n_bins + 1)]
    bin_total = [0] * n_bins
    bin_correct = [0.0] * n_bins
    bin_conf = [0.0] * n_bins
    for p, c in zip(ps, cs):
        # Clamp to [0, 1] so floating-point noise doesn't index out.
        p = max(0.0, min(1.0, float(p)))
        idx = min(int(p * n_bins), n_bins - 1)
        bin_total[idx]   += 1
        bin_correct[idx] += 1.0 if c else 0.0
        bin_conf[idx]    += p

    ece = 0.0
    for i in range(n_bins):
        t = bin_total[i]
        if t == 0:
            continue
        bin_acc  = bin_correct[i] / t
        bin_mean = bin_conf[i] / t
        ece += (t / n) * abs(bin_acc - bin_mean)
    return ece


def brier_score(probs: Iterable[float], correct: Iterable[bool]) -> float:
    """Mean squared error between claimed probability and outcome.

    Returns 0.0 for empty input.  Lower is better; 0.0 means every
    confident assertion was right and every uncertain one was wrong
    (perfectly proper scoring)."""
    ps = list(probs)
    cs = list(correct)
    if len(ps) != len(cs):
        raise ValueError(
            f"probs and correct must have the same length "
            f"({len(ps)} != {len(cs)})"
        )
    if not ps:
        return 0.0
    s = 0.0
    for p, c in zip(ps, cs):
        target = 1.0 if c else 0.0
        s += (float(p) - target) ** 2
    return s / len(ps)


def hypothesis_map_entropy(distribution: Iterable[float]) -> float:
    """Shannon entropy (natural log) of a probability distribution.

    Used to reward subjects that produce a spread-out hypothesis map
    (`[{hypothesis: 'A', confidence: 0.4}, {hypothesis: 'B', confidence:
    0.3}, …]`) over subjects that single-track a wrong answer at
    confidence=1.0.

    Inputs are clamped to [0, 1]; entropy is computed over the
    normalised distribution.  Returns 0.0 for empty or all-zero input.
    """
    ps = [max(0.0, float(p)) for p in distribution]
    total = sum(ps)
    if total <= 0:
        return 0.0
    normed = [p / total for p in ps if p > 0]
    return -sum(p * math.log(p) for p in normed)


def calibration_subscore(
    bands_claimed: list[str],
    correct: list[bool],
) -> tuple[int, dict[str, float]]:
    """Map (claimed bands, correctness) → an integer subscore 0-2.

    Returns (subscore, diagnostics):
      - 2 if ECE ≤ 0.10 (well calibrated)
      - 1 if 0.10 < ECE ≤ 0.20
      - 0 if ECE > 0.20 (badly calibrated)

    The diagnostics dict carries the raw metrics for inclusion in
    the per-category extras."""
    if len(bands_claimed) != len(correct):
        raise ValueError("bands_claimed and correct must align")
    probs = [band_to_prob(b) for b in bands_claimed]
    ece = expected_calibration_error(probs, correct)
    brier = brier_score(probs, correct)
    if ece <= 0.10:
        sub = 2
    elif ece <= 0.20:
        sub = 1
    else:
        sub = 0
    return sub, {"ece": round(ece, 4), "brier": round(brier, 4),
                 "n": len(correct)}
