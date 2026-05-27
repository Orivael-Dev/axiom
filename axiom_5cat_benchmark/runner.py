"""Top-level trial orchestrator.

Per (category × adapter × trial) build a TrialResult, sign it,
append to a crash-safe JSONL log, then aggregate into a
BenchmarkResults.

Crash safety: every trial is appended to
``~/.axiom/5cat-bench-trials.jsonl`` (or AXIOM_5CAT_BENCH_LOG) before
the next trial runs, so an OOM mid-run loses at most one trial.
The ``run_id`` lets ``--resume`` pick up where we left off.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Iterable

from axiom_5cat_benchmark import categories as _categories
from axiom_5cat_benchmark.adapters.base import ModelAdapter
from axiom_5cat_benchmark.reproducibility import build_meta, utcnow_iso
from axiom_5cat_benchmark.schema import (
    BenchmarkResults, PerCategoryReport, TrialResult,
)
from axiom_5cat_benchmark.signing import sign_result, sign_and_attach


def _crash_log_path() -> Path:
    p = os.environ.get("AXIOM_5CAT_BENCH_LOG")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".axiom" / "5cat-bench-trials.jsonl"


def _append_crash_log(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _sign_trial(trial: TrialResult) -> TrialResult:
    """Attach a per-trial HMAC signature, set in-place and returned."""
    payload = trial.to_dict()
    payload.pop("trial_signature", None)
    trial.trial_signature = sign_result(payload)
    return trial


def run_benchmark(
    *,
    adapters: list[ModelAdapter],
    category_ids: Iterable[int],
    n_trials: int,
    seed: int,
    temperature: float,
    crash_log: Path | None = None,
) -> BenchmarkResults:
    """Execute every (category × adapter × trial), aggregate, sign.

    Returns a fully-populated BenchmarkResults with meta signed and
    embedded. Caller is responsible for `to_dict()` + json.dump.
    """
    if not adapters:
        raise ValueError("at least one adapter required")
    cat_ids = sorted(set(category_ids))
    if not cat_ids:
        raise ValueError("at least one category id required")

    log_path = crash_log or _crash_log_path()
    run_id = uuid.uuid4().hex
    started_utc = utcnow_iso()

    all_trials: list[TrialResult] = []
    per_cat: dict[str, PerCategoryReport] = {}

    for cat_id in cat_ids:
        cat = _categories.get(cat_id)
        cat_trials: list[TrialResult] = []
        for adapter in adapters:
            trials = cat.run(
                adapter,
                n_trials=n_trials,
                seed=seed,
                temperature=temperature,
            )
            for t in trials:
                _sign_trial(t)
                _append_crash_log(log_path, {
                    "run_id":     run_id,
                    "category":   cat_id,
                    "adapter":    adapter.model_id(),
                    "trial":      t.to_dict(),
                })
                cat_trials.append(t)
        per_cat[str(cat_id)] = cat.aggregate(cat_trials)
        all_trials.extend(cat_trials)

    ended_utc = utcnow_iso()
    meta = build_meta(
        seed=seed, temperature=temperature,
        started_utc=started_utc, ended_utc=ended_utc,
    )

    # Top-level aggregates — match benchmark_v1_0.py field semantics.
    total_tests = len(all_trials)
    raw_avg     = _mean([t.raw_total   for t in all_trials])
    axiom_avg   = _mean([t.axiom_total for t in all_trials])
    axiom_wins  = sum(1 for t in all_trials if t.winner == "AXIOM")
    improvement_pct = (
        round(((axiom_avg - raw_avg) / raw_avg) * 100.0, 2)
        if raw_avg > 0 else 0.0
    )
    criteria_met = (
        improvement_pct >= 15.0
        and axiom_wins > total_tests / 2
        and all(r.gate == "PASS" for r in per_cat.values())
    )

    # Sign the meta block — embeds signature into meta itself.
    signed_meta = sign_and_attach(meta)

    return BenchmarkResults(
        meta=signed_meta,
        raw_avg=round(raw_avg, 3),
        axiom_avg=round(axiom_avg, 3),
        improvement_pct=improvement_pct,
        axiom_wins=axiom_wins,
        total_tests=total_tests,
        criteria_met=criteria_met,
        per_category=per_cat,
        tests=all_trials,
    )


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0
