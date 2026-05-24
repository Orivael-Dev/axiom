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
            ))
        return out

    def aggregate(self, trials: list[TrialResult]) -> PerCategoryReport:
        if not trials:
            return PerCategoryReport(
                avg=0.0, n_trials=0, gate="FAIL",
                extras={"ece": 0.0, "brier": 0.0,
                        "reason": "no trials"},
            )
        # Re-derive calibration metrics from the trials.  We use the
        # claimed-band heuristic from scorer._claimed_band — same as
        # was used to score Calibration — so the aggregate matches
        # the per-trial verdicts.
        bands_claimed: list[str] = []
        correct: list[bool] = []
        for t in trials:
            # The per-trial notes carry BAND:claimed/expected — parse
            # it back out to avoid re-running the regex.
            claimed = _band_from_notes(t.notes) or "UNCERTAIN"
            bands_claimed.append(claimed)
            # An item is "correct" if the axiom PassSignal dim got
            # full credit OR Refusal got full credit (says "I don't
            # know" on a known-unknown counts as correct).
            ax = t.axiom_scores or {}
            correct.append(ax.get("PassSignal", 0) >= 2
                           or ax.get("Refusal", 0) >= 2)

        calib_sub, calib_diag = calibration_subscore(bands_claimed, correct)
        avg = sum(t.axiom_total for t in trials) / len(trials)
        # The gate is purely the average score threshold — ECE is
        # reported as a diagnostic but not gated on. Reason: in this
        # benchmark "correct" often means "successfully refused" on a
        # known-unknown, and saying UNCERTAIN there is the *right*
        # answer. Standard ECE punishes that as underconfidence, which
        # would invert the intended scoring. The Calibration dim
        # (band-match against expected band) already feeds into avg
        # so calibration influence is preserved without double-counting.
        gate = "PASS" if avg >= self.max_score_per_trial * 0.55 else "FAIL"
        return PerCategoryReport(
            avg=round(avg, 3),
            n_trials=len(trials),
            gate=gate,
            extras={
                "ece":   calib_diag["ece"],
                "brier": calib_diag["brier"],
                "calibration_subscore": calib_sub,
                "max_score_per_trial":  self.max_score_per_trial,
            },
        )


def _band_from_notes(notes: str) -> str | None:
    for chunk in (notes or "").split(";"):
        chunk = chunk.strip()
        if chunk.startswith("BAND:"):
            tail = chunk[len("BAND:"):].split("/", 1)[0].strip()
            return tail or None
    return None
