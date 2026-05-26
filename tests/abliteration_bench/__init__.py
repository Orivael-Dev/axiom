"""AXIOM abliteration-resistance benchmark.

Measures whether AXIOM's runtime gate holds when the underlying model
has had its internal refusal direction stripped (Heretic, abliteration,
weight-orthogonalization attacks).
"""

from .corpus import Prompt, load_builtin_corpus
from .runner import BenchRow, BenchReport, run_bench
from .adapters import ModelAdapter, MockAlignedModel, MockAbliteratedModel

__all__ = [
    "Prompt",
    "load_builtin_corpus",
    "BenchRow",
    "BenchReport",
    "run_bench",
    "ModelAdapter",
    "MockAlignedModel",
    "MockAbliteratedModel",
]
