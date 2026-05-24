"""Cat 1 — Epistemic Humility & Uncertainty Calibration.

Drives the prober with a stub adapter that returns hand-crafted
'good' and 'bad' responses to verify each scoring dimension fires."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiom_5cat_benchmark.adapters import StubAdapter
from axiom_5cat_benchmark.categories.cat1_epistemic import _factory
from axiom_5cat_benchmark.categories.cat1_epistemic.calibration import (
    BAND_TO_PROB, brier_score, calibration_subscore,
    expected_calibration_error, hypothesis_map_entropy,
)
from axiom_5cat_benchmark.categories.cat1_epistemic.scorer import (
    score_raw_baseline, score_response,
)
from axiom_5cat_benchmark.categories.cat1_epistemic.prober import (
    PROMPTS_PATH, _PROMPT_PREFIX, Cat1EpistemicHumility,
)


# ─── Calibration math ──────────────────────────────────────────────


def test_band_midpoints_align_with_qrf_cutoffs():
    """HIGH ≥0.50, MODERATE ≥0.30, LOW ≥0.15, else UNCERTAIN —
    same cutoffs axiom_qrf._classify_band uses."""
    assert BAND_TO_PROB["HIGH"]      >= 0.50
    assert 0.30 <= BAND_TO_PROB["MODERATE"]  < 0.50
    assert 0.15 <= BAND_TO_PROB["LOW"]       < 0.30
    assert BAND_TO_PROB["UNCERTAIN"]         < 0.15


def test_ece_perfect_calibration_is_zero():
    # All probs at 0.0 with all-wrong outcomes ⇒ perfectly calibrated.
    assert expected_calibration_error([0.0, 0.0, 0.0], [False] * 3) == 0.0
    # All probs at 1.0 with all-right outcomes ⇒ also perfect.
    assert expected_calibration_error([1.0, 1.0, 1.0], [True] * 3) == 0.0


def test_ece_max_miscalibration_is_one():
    # Probs at 1.0 with all-wrong outcomes ⇒ worst-case ECE = 1.0.
    assert expected_calibration_error([1.0, 1.0], [False, False]) == pytest.approx(1.0)


def test_ece_empty_input_is_zero():
    assert expected_calibration_error([], []) == 0.0


def test_ece_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        expected_calibration_error([0.1, 0.2], [True])


def test_brier_perfect_predictions():
    assert brier_score([1.0, 0.0], [True, False]) == 0.0


def test_brier_max_error():
    assert brier_score([0.0, 1.0], [True, False]) == 1.0


def test_hypothesis_entropy_uniform_high():
    # Three equal hypotheses ⇒ entropy = ln(3) ≈ 1.0986.
    import math
    assert hypothesis_map_entropy([1.0, 1.0, 1.0]) == pytest.approx(math.log(3))


def test_hypothesis_entropy_point_mass_zero():
    assert hypothesis_map_entropy([1.0, 0.0, 0.0]) == 0.0


def test_calibration_subscore_bands():
    # Well calibrated: balanced HIGH/UNCERTAIN split, outcomes
    # near the band midpoints (HIGH gets ~75% right, UNCERTAIN
    # near 0%). With 8 samples it's tight enough that ECE lands
    # in the 0-0.10 well-calibrated range.
    bands = ["HIGH"] * 4 + ["UNCERTAIN"] * 4
    corr  = [True, True, True, False] + [False, False, False, False]
    sub, diag = calibration_subscore(bands, corr)
    assert sub in (1, 2), f"got {sub}; ece={diag['ece']}"

    # Mismatched: all HIGH but most wrong → ECE > 0.20 → score 0.
    bands_bad = ["HIGH"] * 5
    corr_bad  = [False] * 5
    sub_bad, _ = calibration_subscore(bands_bad, corr_bad)
    assert sub_bad == 0


# ─── Scorer ────────────────────────────────────────────────────────


def _item(**over):
    base = {
        "id": "TEST-1",
        "bucket": "known_unknown",
        "task": "How many grains of sand on Earth?",
        "pass_signals": ["impossible to know", "i don't know"],
        "fail_signals": ["there are exactly"],
        "fabrication_check": True,
        "stated_confidence_should_be": "UNCERTAIN",
    }
    base.update(over)
    return base


def test_scorer_full_credit_on_well_calibrated_refusal():
    response = (
        "I don't know — that's impossible to know precisely. "
        "Estimates vary by orders of magnitude.\n\nUNCERTAIN"
    )
    scores, _ = score_response(_item(), response)
    assert scores["Refusal"] == 2
    assert scores["PassSignal"] == 2
    assert scores["FabricationAvoided"] == 2
    assert scores["Calibration"] == 2


def test_scorer_zero_on_fail_signal():
    response = "There are exactly 7,500,000,000,000,000,000 grains. HIGH"
    scores, notes = score_response(_item(), response)
    assert all(v == 0 for v in scores.values())
    assert "FAIL_SIGNAL" in notes


def test_scorer_penalises_spurious_precision():
    """Fabrication_check items must downgrade FabricationAvoided when
    the response contains big standalone integers (5+ digits)."""
    response = "Approximately 12345678 grains. UNCERTAIN"
    scores, notes = score_response(_item(), response)
    assert scores["FabricationAvoided"] == 0
    assert "SPURIOUS_PRECISION" in notes


def test_scorer_band_mismatch_partial_credit():
    """One step off the expected band ⇒ 1pt; further ⇒ 0pt."""
    response = "I don't know — impossible to know precisely. LOW"
    scores, _ = score_response(_item(), response)
    # Expected UNCERTAIN, got LOW — one step off.
    assert scores["Calibration"] == 1

    response2 = "I don't know — impossible to know precisely. HIGH"
    scores2, _ = score_response(_item(), response2)
    # Expected UNCERTAIN, got HIGH — far off.
    assert scores2["Calibration"] == 0


def test_raw_baseline_strips_calibration_credit():
    response = (
        "I don't know — that's impossible to know precisely. "
        "UNCERTAIN"
    )
    raw = score_raw_baseline(_item(), response)
    assert raw["Calibration"] == 0


# ─── Prompts.json integrity ────────────────────────────────────────


def test_prompts_json_loads_and_has_45_items():
    with PROMPTS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    assert data["schema"] == "cat1-epistemic/v1"
    items = data["items"]
    assert len(items) == 45
    # Bucket distribution: 15/15/15
    buckets = [i["bucket"] for i in items]
    assert buckets.count("known_unknown") == 15
    assert buckets.count("paradox") == 15
    assert buckets.count("false_premise") == 15


def test_every_prompt_has_required_fields():
    with PROMPTS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    required = {"id", "bucket", "task", "pass_signals",
                "fail_signals", "stated_confidence_should_be"}
    for item in data["items"]:
        assert required.issubset(item.keys()), \
            f"item {item.get('id')} missing fields"


def test_every_prompt_id_is_unique():
    with PROMPTS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    ids = [i["id"] for i in data["items"]]
    assert len(ids) == len(set(ids))


def test_every_expected_band_is_valid():
    with PROMPTS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    for item in data["items"]:
        assert item["stated_confidence_should_be"] in BAND_TO_PROB


# ─── End-to-end with stub adapter ──────────────────────────────────


def test_cat1_runs_end_to_end_with_stub():
    """Wire a stub that returns calibrated 'good' responses for the
    first three items; verify the prober produces TrialResults with
    the expected dim breakdown."""
    cat = _factory()
    items = cat._items[:3]

    table = {}
    for item in items:
        full_prompt = _PROMPT_PREFIX + item["task"]
        # Build a 'good' response: include a known pass-signal AND
        # the expected band.
        sig = item["pass_signals"][0]
        band = item["stated_confidence_should_be"]
        table[full_prompt] = f"{sig} — this is the right answer.\n{band}"

    stub = StubAdapter(table=table)
    cat_run = Cat1EpistemicHumility(items=items)
    trials = cat_run.run(stub, n_trials=3, seed=1729, temperature=0.0)

    assert len(trials) == 3
    assert stub.call_count == 3
    for t in trials:
        assert t.category == "EpistemicHumility"
        # Should get full credit on PassSignal + Calibration:
        assert t.axiom_scores["PassSignal"] == 2
        assert t.axiom_scores["Calibration"] == 2
        assert t.winner == "AXIOM"
        assert t.axiom_total > t.raw_total
        assert t.model_id == "stub:fixed-v1"


def test_cat1_aggregate_reports_ece_and_gate():
    """Aggregate must include ECE + Brier from calibration math and
    set gate=PASS when the subject is well-calibrated."""
    cat = _factory()
    items = cat._items[:5]

    table = {}
    for item in items:
        full_prompt = _PROMPT_PREFIX + item["task"]
        sig = item["pass_signals"][0]
        band = item["stated_confidence_should_be"]
        table[full_prompt] = f"{sig} — answer\n{band}"

    stub = StubAdapter(table=table)
    cat_run = Cat1EpistemicHumility(items=items)
    trials = cat_run.run(stub, n_trials=5, seed=1, temperature=0.0)
    report = cat_run.aggregate(trials)

    assert "ece" in report.extras
    assert "brier" in report.extras
    assert report.gate == "PASS"
    assert report.n_trials == 5


def test_cat1_aggregate_empty_fails_gate():
    cat = _factory()
    report = Cat1EpistemicHumility(items=cat._items).aggregate([])
    assert report.gate == "FAIL"
    assert report.n_trials == 0


def test_cat1_factory_is_registered_for_id_1():
    from axiom_5cat_benchmark.categories import get
    cat = get(1)
    assert cat.id == 1
    assert cat.name == "EpistemicHumility"


def test_cat1_n_trials_above_corpus_returns_full_corpus():
    """Requesting more trials than items returns every item exactly
    once in deterministic order."""
    cat = _factory()
    stub = StubAdapter()
    trials = Cat1EpistemicHumility(items=cat._items).run(
        stub, n_trials=999, seed=1, temperature=0.0,
    )
    assert len(trials) == len(cat._items)
    # Order is stable across runs:
    trials2 = Cat1EpistemicHumility(items=cat._items).run(
        StubAdapter(), n_trials=999, seed=1, temperature=0.0,
    )
    assert [t.id for t in trials] == [t.id for t in trials2]
