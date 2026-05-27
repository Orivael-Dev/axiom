"""Tests the agent must satisfy by implementing stats.median + stats.percentile."""
import pytest

import stats


def test_median_odd_length():
    assert stats.median([3, 1, 2]) == 2


def test_median_even_length():
    assert stats.median([1, 2, 3, 4]) == 2.5


def test_median_empty_raises():
    with pytest.raises(ValueError):
        stats.median([])


def test_percentile_midpoint():
    assert stats.percentile([1, 2, 3, 4], 50) == 2.5


def test_percentile_boundaries():
    assert stats.percentile([10, 20, 30], 0) == 10
    assert stats.percentile([10, 20, 30], 100) == 30


def test_percentile_invalid_p():
    with pytest.raises(ValueError):
        stats.percentile([1, 2, 3], -5)
    with pytest.raises(ValueError):
        stats.percentile([1, 2, 3], 150)
