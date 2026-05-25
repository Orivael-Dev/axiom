"""Tax helpers — illustrative only, not legal advice."""
from __future__ import annotations


def sales_tax(amount: float, rate_pct: float) -> float:
    """Return the sales-tax-only component for `amount` at `rate_pct` (0-100)."""
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if not 0 <= rate_pct <= 100:
        raise ValueError("rate_pct must be in [0, 100]")
    return round(amount * rate_pct / 100.0, 2)


def total_with_tax(amount: float, rate_pct: float) -> float:
    """Return `amount + sales_tax(amount, rate_pct)`."""
    return round(amount + sales_tax(amount, rate_pct), 2)
