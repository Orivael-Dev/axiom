"""Tiny formatting helpers."""
from __future__ import annotations


def humanize_bytes(n: int) -> str:
    """Return `n` formatted as a short human-readable byte count.

    >>> humanize_bytes(1024)
    '1.0 KB'
    >>> humanize_bytes(1500000)
    '1.4 MB'
    """
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} {units[-1]}"
