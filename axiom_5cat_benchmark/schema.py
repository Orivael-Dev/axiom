"""Dataclasses + JSON-shape contract for benchmark results.

The per-test record shape is byte-compatible with the existing
tests/benchmark_v1_0.py output (id, category, raw_total, axiom_total,
raw_scores, axiom_scores, winner, notes, raw_output, axiom_output)
so review_scores.py works against our results without modification.

Additive fields:
  - meta block (signed, carries reproducibility info)
  - per_category block (per-category aggregates + gates)
  - per-trial: model_id, input_tokens, output_tokens, latency_ms,
    trial_signature
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ─── Completion ────────────────────────────────────────────────────
# Returned by every ModelAdapter.complete() call.

@dataclass(frozen=True)
class Completion:
    text:             str
    input_tokens:     int
    output_tokens:    int
    latency_ms:       int
    model_id:         str
    # sha256 of the raw provider response, so a verifier can detect
    # post-hoc tampering of the recorded completion.
    raw_response_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── TrialResult ───────────────────────────────────────────────────
# One row in the top-level results["tests"] array. Field names match
# benchmark_v1_0.py exactly for the compat keys; the rest are additive.

@dataclass
class TrialResult:
    id:            str
    category:      str
    name:          str
    task:          str
    raw_total:     int
    axiom_total:   int
    raw_scores:    dict[str, int]
    axiom_scores:  dict[str, int]
    winner:        str             # "AXIOM" | "RAW" | "TIE"
    notes:         str = ""
    raw_output:    str = ""        # truncated to 300 chars on serialisation
    axiom_output:  str = ""        # truncated to 300 chars on serialisation
    # Additive provenance:
    model_id:         str = ""
    input_tokens:     int = 0
    output_tokens:    int = 0
    latency_ms:       int = 0
    trial_signature:  str = ""     # filled by runner via signing.sign_result

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Mirror benchmark_v1_0.py's 300-char truncation so the
        # historical tooling that assumes that bound still works.
        d["raw_output"]   = (self.raw_output or "")[:300]
        d["axiom_output"] = (self.axiom_output or "")[:300]
        return d


# ─── PerCategoryReport ─────────────────────────────────────────────
# Aggregates emitted alongside the trial array. Each category's
# scorer adds whatever extra metrics it natively produces (ECE for
# Cat 1, median_perf_per_watt for Cat 2, etc.) into `extras`.

@dataclass
class PerCategoryReport:
    avg:        float
    n_trials:   int
    gate:       str               # "PASS" | "FAIL"
    extras:     dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {"avg": self.avg, "n_trials": self.n_trials, "gate": self.gate}
        d.update(self.extras)
        return d


# ─── BenchmarkResults ─────────────────────────────────────────────
# Top-level results.json. The five Phase-A scoring fields
# (raw_avg/axiom_avg/improvement_pct/axiom_wins/total_tests/criteria_met)
# match benchmark_v1_0.py:446-457 verbatim so external tooling stays
# compatible.

@dataclass
class BenchmarkResults:
    meta:           dict[str, Any]              # signed; built via reproducibility.build_meta
    raw_avg:        float
    axiom_avg:      float
    improvement_pct: float
    axiom_wins:     int
    total_tests:    int
    criteria_met:   bool
    per_category:   dict[str, PerCategoryReport]
    tests:          list[TrialResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta":            self.meta,
            "raw_avg":         self.raw_avg,
            "axiom_avg":       self.axiom_avg,
            "improvement_pct": self.improvement_pct,
            "axiom_wins":      self.axiom_wins,
            "total_tests":     self.total_tests,
            "criteria_met":    self.criteria_met,
            "per_category":    {k: v.to_dict() for k, v in self.per_category.items()},
            "tests":           [t.to_dict() for t in self.tests],
        }


def winner_label(raw_total: int, axiom_total: int) -> str:
    """Match benchmark_v1_0.py's labelling (without the unicode emoji)."""
    if axiom_total > raw_total:
        return "AXIOM"
    if axiom_total < raw_total:
        return "RAW"
    return "TIE"
