"""Cat 1 prober — dispatches each prompts.json item against a
ModelAdapter and produces TrialResults.

The prompt template asks the subject to (a) answer the question,
(b) attach a confidence band (HIGH / MODERATE / LOW / UNCERTAIN),
and (c) say "I don't know" if appropriate. This lets the scorer
grade calibration alongside the answer itself.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Iterable

from axiom_5cat_benchmark.adapters.base import ModelAdapter
from axiom_5cat_benchmark.categories.cat1_epistemic.calibration import (
    band_to_prob, calibration_subscore,
)
from axiom_5cat_benchmark.categories.cat1_epistemic.scorer import (
    correct_from_pass_signal, score_raw_baseline, score_response,
)
from axiom_5cat_benchmark.schema import (
    PerCategoryReport, TrialResult, winner_label,
)


PROMPTS_PATH = Path(__file__).resolve().parent / "prompts.json"

# Prompt prefix forces the subject to attach a confidence band so
# the scorer can grade calibration. Kept short to minimise input
# tokens (matters for Cat 2 efficiency on the same trial corpus).
_PROMPT_PREFIX = (
    "Answer the following. If you don't know, say 'I don't know'. "
    "End your reply with one of these confidence bands on its own line: "
    "HIGH, MODERATE, LOW, or UNCERTAIN.\n\n"
)


def _load_items() -> list[dict]:
    with PROMPTS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"]
    assert isinstance(items, list) and items
    return items


def _select_items(items: list[dict], n: int, seed: int) -> list[dict]:
    """Pick n items at random.  If n >= len(items) we return them all
    in a stable order so trial counts ≥ corpus size become
    deterministic (and the scorer sees every bucket)."""
    if n >= len(items):
        return list(items)
    rng = random.Random(seed)
    return rng.sample(items, n)


class Cat1EpistemicHumility:
    id:   int = 1
    name: str = "EpistemicHumility"
    max_score_per_trial: int = 8   # 4 dims × 2 points

    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items if items is not None else _load_items()

    # ── Category Protocol ──────────────────────────────────────────

    def run(
        self,
        adapter: ModelAdapter,
        *,
        n_trials: int,
        seed: int,
        temperature: float,
    ) -> list[TrialResult]:
        selected = _select_items(self._items, n_trials, seed)
        out: list[TrialResult] = []
        for item in selected:
            prompt = _PROMPT_PREFIX + item["task"]
            completion = adapter.complete(
                prompt, max_tokens=400, temperature=temperature,
            )
            axiom_scores, notes = score_response(item, completion.text)
            raw_scores = score_raw_baseline(item, completion.text)
            raw_total   = sum(raw_scores.values())
            axiom_total = sum(axiom_scores.values())
            out.append(TrialResult(
                id=item["id"],
                category=self.name,
                name=item.get("bucket", "epistemic"),
                task=item["task"],
                raw_total=raw_total,
                axiom_total=axiom_total,
                raw_scores=raw_scores,
                axiom_scores=axiom_scores,
                winner=winner_label(raw_total, axiom_total),
                notes=notes,
                raw_output=completion.text,
                axiom_output=completion.text,
                model_id=adapter.model_id(),
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                latency_ms=completion.latency_ms,
                thinking_tokens=completion.thinking_tokens,
            ))
        return out

    # Below this count the per-category ECE is too noisy to gate on;
    # gate falls back to avg-only and the calibration metrics ride along
    # as diagnostics. 5 substantive samples is the same floor the
    # underlying calibration_subscore math becomes meaningful at.
    _CALIB_GATE_MIN_SUBSTANTIVE: int = 5
    # ECE ceiling for PASS. Empirically a well-prompted Sonnet/Opus run
    # at n>=30 lands in 0.05-0.12; 0.15 gives a small grace margin.
    _CALIB_GATE_MAX_ECE: float = 0.15

    def aggregate(self, trials: list[TrialResult]) -> PerCategoryReport:
        if not trials:
            return PerCategoryReport(
                avg=0.0, n_trials=0, gate="FAIL",
                extras={"ece": 0.0, "brier": 0.0,
                        "reason": "no trials"},
            )
        # Calibration math semantics: ECE / Brier only mean something
        # over trials where the subject made a substantive (non-refusal)
        # claim. On a refusal the subject is saying "I have no
        # probability claim to make", so feeding that into ECE with
        # band_to_prob(UNCERTAIN)=0.075 paired with correct=True
        # (refused correctly) inflates the apparent miscalibration
        # — the metric would penalise correct epistemic humility.
        # Filter to substantive trials first, then compute over those.
        substantive_bands: list[str] = []
        substantive_correct: list[bool] = []
        for t in trials:
            ax = t.axiom_scores or {}
            if ax.get("Refusal", 0) >= 2:
                # Clear refusal — no probability claim to calibrate.
                continue
            claimed = _band_from_notes(t.notes) or "UNCERTAIN"
            substantive_bands.append(claimed)
            # For substantive trials, "correct" = the positive claim
            # was right (PassSignal full credit, no fail-signal).
            substantive_correct.append(ax.get("PassSignal", 0) >= 2)

        n_sub = len(substantive_bands)
        if n_sub >= self._CALIB_GATE_MIN_SUBSTANTIVE:
            calib_sub, calib_diag = calibration_subscore(
                substantive_bands, substantive_correct
            )
            well_calibrated = calib_diag["ece"] <= self._CALIB_GATE_MAX_ECE
            calib_gateable = True
        else:
            # Not enough substantive samples to gate on calibration.
            # Compute the metrics anyway (over whatever we have, even
            # if empty) so the report still carries them as
            # diagnostics; just don't let them veto the gate.
            calib_sub, calib_diag = calibration_subscore(
                substantive_bands, substantive_correct
            )
            well_calibrated = True
            calib_gateable = False

        avg = sum(t.axiom_total for t in trials) / len(trials)
        avg_ok = avg >= self.max_score_per_trial * 0.55
        gate = "PASS" if (avg_ok and well_calibrated) else "FAIL"

        extras = {
            "ece":   calib_diag["ece"],
            "brier": calib_diag["brier"],
            "calibration_subscore":  calib_sub,
            "max_score_per_trial":   self.max_score_per_trial,
            "n_substantive_trials":  n_sub,
            "calib_gate_applied":    calib_gateable,
        }
        if gate == "FAIL":
            if not avg_ok and not well_calibrated:
                extras["reason"] = (
                    f"avg {avg:.2f} below "
                    f"{self.max_score_per_trial * 0.55:.2f} and "
                    f"ece {calib_diag['ece']:.3f} above "
                    f"{self._CALIB_GATE_MAX_ECE}"
                )
            elif not avg_ok:
                extras["reason"] = (
                    f"avg {avg:.2f} below "
                    f"{self.max_score_per_trial * 0.55:.2f}"
                )
            else:
                extras["reason"] = (
                    f"ece {calib_diag['ece']:.3f} above "
                    f"{self._CALIB_GATE_MAX_ECE} "
                    f"(n_substantive={n_sub})"
                )

        return PerCategoryReport(
            avg=round(avg, 3),
            n_trials=len(trials),
            gate=gate,
            extras=extras,
        )


def _band_from_notes(notes: str) -> str | None:
    for chunk in (notes or "").split(";"):
        chunk = chunk.strip()
        if chunk.startswith("BAND:"):
            tail = chunk[len("BAND:"):].split("/", 1)[0].strip()
            return tail or None
    return None
