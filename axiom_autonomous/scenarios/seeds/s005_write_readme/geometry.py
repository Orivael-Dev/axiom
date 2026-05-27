"""Geometry helpers."""
from __future__ import annotations

from math import pi


def circle_area(radius: float) -> float:
    """Return the area of a circle with `radius`."""
    if radius < 0:
        raise ValueError("radius must be non-negative")
    return pi * radius * radius


def rectangle_area(width: float, height: float) -> float:
    """Return the area of a rectangle with `width` x `height`."""
    if width < 0 or height < 0:
        raise ValueError("width and height must be non-negative")
    return width * height
