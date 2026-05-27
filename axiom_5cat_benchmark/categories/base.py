"""Category Protocol — the contract every category subpackage implements.

A Category produces a list of TrialResult records for a given
adapter under a given seed/temperature/trial-count budget. The
runner doesn't know which category is which — it just dispatches
``category.run(adapter, n_trials, seed, temperature)`` and collects.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from axiom_5cat_benchmark.adapters.base import ModelAdapter
from axiom_5cat_benchmark.schema import (
    PerCategoryReport, TrialResult,
)


@runtime_checkable
class Category(Protocol):
    """A scorable axis of the 5-category benchmark.

    Each category produces TrialResults and optionally an
    aggregation report (PerCategoryReport) with category-specific
    extras (ECE for Cat 1, median_perf_per_watt for Cat 2, etc.).

    Implementations are stateless across calls — the runner may
    invoke ``run()`` multiple times with different adapters in the
    same process.
    """

    id:       int   # 1-5
    name:     str   # "EpistemicHumility" | "Efficiency" | ...
    max_score_per_trial: int     # e.g. 16 for the benchmark_v1_0 8-dim/2pt scheme

    def run(
        self,
        adapter: ModelAdapter,
        *,
        n_trials: int,
        seed: int,
        temperature: float,
    ) -> list[TrialResult]: ...

    def aggregate(self, trials: list[TrialResult]) -> PerCategoryReport: ...
