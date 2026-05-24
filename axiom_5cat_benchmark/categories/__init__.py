"""Category registry.

Each category lives under its own subpackage (cat1_epistemic,
cat2_efficiency, …). Categories self-register their factory in
``CATEGORY_REGISTRY`` so the runner can dispatch by integer id.
"""
from __future__ import annotations

from typing import Callable

from axiom_5cat_benchmark.categories.base import Category

# Filled lazily on first .get() — keeps stub-only CI runs from
# importing Cat 3/5 sandbox infrastructure they don't need.
_FACTORIES: dict[int, Callable[[], Category]] = {}


def register(cat_id: int) -> Callable[[Callable[[], Category]], Callable[[], Category]]:
    """Decorator: register a category factory under its integer id."""
    def _wrap(fn: Callable[[], Category]) -> Callable[[], Category]:
        _FACTORIES[cat_id] = fn
        return fn
    return _wrap


def get(cat_id: int) -> Category:
    """Return the category instance for ``cat_id``, lazily importing
    the subpackage on first request."""
    if cat_id not in _FACTORIES:
        # Trigger subpackage import — each subpackage's __init__
        # imports its scorer + harness which call register(N).
        _ensure_loaded(cat_id)
    if cat_id not in _FACTORIES:
        raise KeyError(f"no category registered with id {cat_id}")
    return _FACTORIES[cat_id]()


def available() -> list[int]:
    """All currently-registered category ids."""
    return sorted(_FACTORIES)


def _ensure_loaded(cat_id: int) -> None:
    pkg_map = {
        1: "axiom_5cat_benchmark.categories.cat1_epistemic",
        2: "axiom_5cat_benchmark.categories.cat2_efficiency",
        3: "axiom_5cat_benchmark.categories.cat3_adaptation",
        4: "axiom_5cat_benchmark.categories.cat4_multiagent",
        5: "axiom_5cat_benchmark.categories.cat5_self_evolution",
        # Cat 6 (bias detection) is intentionally NOT registered —
        # see axiom_5cat_benchmark/categories/cat6_bias/__init__.py
        # for the gating condition (Cat 1 ECE ≤ 0.15 on real LLMs).
        6: "axiom_5cat_benchmark.categories.cat6_bias",
    }
    pkg = pkg_map.get(cat_id)
    if pkg is None:
        return
    try:
        __import__(pkg)
    except ImportError:
        # Subpackage may not exist yet (we land categories phased);
        # the KeyError raised by get() will surface the gap clearly.
        pass


__all__ = ["Category", "register", "get", "available"]
