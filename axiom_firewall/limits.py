"""Free-tier abuse defense.

Two limits enforced:
  - Per-IP signup rate limit (default: 5/hr)
  - Per-tenant monthly hard cap for the free tier (1,000 calls/mo)

Paid tiers have NO hard cap here — Stripe metered billing handles
overage. The free tier blocks at the cap and returns 429 with a
Retry-After header pointing at the start of next month.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .db import _conn, _registry_path, _tenant_path, init_tenant_db
from .models import Tenant

SIGNUP_WINDOW_SECONDS = 3600
SIGNUP_MAX_PER_WINDOW = 5

TIER_MONTHLY_HARD_CAP: dict[str, int | None] = {
    "free": 1_000,
    "indie": None,        # Stripe meter handles overage
    "team": None,
    "enterprise": None,   # custom contract
}


def init_limit_tables() -> None:
    with _conn(_registry_path()) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS signup_attempts (
                ip  TEXT NOT NULL,
                ts  TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_signup_ts ON signup_attempts(ts)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_signup_ip ON signup_attempts(ip)"
        )


def check_signup_rate(ip: str) -> tuple[bool, int]:
    """Test + record a signup attempt for `ip`.

    Returns (allowed, retry_after_seconds). When allowed, the attempt is
    recorded. When denied, no record is added (already at cap).
    """
    init_limit_tables()
    now = datetime.utcnow()
    cutoff = (now - timedelta(seconds=SIGNUP_WINDOW_SECONDS)).isoformat()
    with _conn(_registry_path()) as c:
        c.execute("DELETE FROM signup_attempts WHERE ts < ?", (cutoff,))
        count = c.execute(
            "SELECT COUNT(*) FROM signup_attempts WHERE ip = ?", (ip,)
        ).fetchone()[0]
        if count >= SIGNUP_MAX_PER_WINDOW:
            earliest = c.execute(
                "SELECT MIN(ts) FROM signup_attempts WHERE ip = ?", (ip,)
            ).fetchone()[0]
            earliest_dt = datetime.fromisoformat(earliest)
            elapsed = (now - earliest_dt).total_seconds()
            retry = max(SIGNUP_WINDOW_SECONDS - int(elapsed), 60)
            return (False, retry)
        c.execute(
            "INSERT INTO signup_attempts (ip, ts) VALUES (?, ?)",
            (ip, now.isoformat()),
        )
        return (True, 0)


def _calendar_month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) of the calendar month containing `now`, UTC."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Add 32 days (always lands in next month), snap back to day=1.
    end = (start + timedelta(days=32)).replace(day=1)
    return (start, end)


def monthly_usage_count(tenant_id: str, now: datetime | None = None) -> int:
    """Count usage_records for this tenant in the current calendar month."""
    init_tenant_db(tenant_id)
    now = now or datetime.utcnow()
    start, end = _calendar_month_bounds(now)
    with _conn(_tenant_path(tenant_id)) as c:
        row = c.execute(
            "SELECT COUNT(*) FROM usage_records "
            "WHERE timestamp >= ? AND timestamp < ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        return int(row[0])


def check_monthly_quota(tenant: Tenant) -> tuple[bool, int, int | None]:
    """Return (allowed, used_this_month, limit_or_None).

    For paid tiers, limit is None (no hard cap; Stripe handles overage).
    For free tier, the call is denied when used >= limit.
    """
    used = monthly_usage_count(tenant.tenant_id)
    cap = TIER_MONTHLY_HARD_CAP.get(tenant.tier)
    if cap is None:
        return (True, used, None)
    return (used < cap, used, cap)


def seconds_until_next_month(now: datetime | None = None) -> int:
    """Retry-After value (in seconds) when free tier exceeds quota."""
    now = now or datetime.utcnow()
    _, end = _calendar_month_bounds(now)
    return int((end - now).total_seconds())
