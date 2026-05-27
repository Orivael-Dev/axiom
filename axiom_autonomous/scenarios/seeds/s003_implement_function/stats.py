"""Greenfield: agent implements two statistics functions from the
docstrings + tests. Module starts with NotImplementedError stubs."""
from __future__ import annotations

from typing import Iterable, List


def median(values: Iterable[float]) -> float:
    """Return the median of `values`.

    Sorted middle for odd length; mean of the two middle for even.
    Raises ValueError on empty input.

    >>> median([1, 3, 2])
    2
    >>> median([1, 2, 3, 4])
    2.5
    """
    raise NotImplementedError


def percentile(values: Iterable[float], p: float) -> float:
    """Return the linear-interpolation percentile of `values` at `p`.

    `p` is in [0, 100].  Empty input raises ValueError; p outside
    [0, 100] raises ValueError.

    >>> percentile([1, 2, 3, 4], 50)
    2.5
    >>> percentile([10, 20, 30], 0)
    10
    """
    raise NotImplementedError
