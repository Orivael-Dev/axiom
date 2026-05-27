"""AXIOM 5-category AI benchmark.

A signed, third-party-verifiable benchmark spanning:

  1. Epistemic Humility & Uncertainty Calibration
  2. Resource & Compute Efficiency
  3. Dynamic Environment Adaptation
  4. Multi-Agent Game Theory & Alignment
  5. Self-Evolution & Recursive Guardrail Preservation

CLI entry point:
    python3 -m axiom_5cat_benchmark run --models <list> --output results.json
"""
from __future__ import annotations

__version__ = "0.1.0"

# Re-export the public surface so importing the package is enough.
from axiom_5cat_benchmark.schema import (
    Completion,
    TrialResult,
    PerCategoryReport,
    BenchmarkResults,
)
from axiom_5cat_benchmark.signing import sign_result, verify_result

__all__ = [
    "__version__",
    "Completion",
    "TrialResult",
    "PerCategoryReport",
    "BenchmarkResults",
    "sign_result",
    "verify_result",
]
