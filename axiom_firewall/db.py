"""SQLite-per-tenant data layer.

Per docs/PHASE_1_DECISIONS.md §3.

Layout:
  tenants/registry.db        — tenant rows (one master DB)
  tenants/{tenant_id}.db     — that tenant's api_keys + usage_records

Migration to Postgres triggers when a single tenant exceeds 100M
decision events or a customer requires multi-region replication.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import ApiKey, Tenant, UsageRecord


def _tenant_dir() -> Path:
    """Resolved at call time so tests can monkeypatch cwd."""
    d = Path(os.environ.get("AXIOM_FIREWALL_TENANT_DIR", "tenants"))
    d.mkdir(exist_ok=True)
    return d


def _registry_path() -> Path:
    return _tenant_dir() / "registry.db"


def _tenant_path(tenant_id: str) -> Path:
    return _tenant_dir() / f"{tenant_id}.db"


def _conn(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def init_registry() -> None:
    with _conn(_registry_path()) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id   TEXT PRIMARY KEY,
                email       TEXT UNIQUE NOT NULL,
                pw_hash     TEXT NOT NULL,
                tier        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)


def init_tenant_db(tenant_id: str) -> None:
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id      TEXT PRIMARY KEY,
                tenant_id   TEXT NOT NULL,
                secret      TEXT UNIQUE NOT NULL,
                name        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                revoked_at  TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS usage_records (
                record_id     TEXT PRIMARY KEY,
                tenant_id     TEXT NOT NULL,
                api_key_id    TEXT NOT NULL,
                endpoint      TEXT NOT NULL,
                verdict       TEXT NOT NULL,
                intent_class  TEXT NOT NULL,
                confidence    REAL NOT NULL,
                latency_ms    REAL NOT NULL,
                timestamp     TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_records(timestamp)")


def insert_tenant(t: Tenant) -> None:
    init_registry()
    init_tenant_db(t.tenant_id)
    with _conn(_registry_path()) as c:
        c.execute(
            "INSERT INTO tenants VALUES (?, ?, ?, ?, ?)",
            (t.tenant_id, t.email, t.pw_hash, t.tier, t.created_at.isoformat()),
        )


def _row_to_tenant(r) -> Tenant:
    return Tenant(
        tenant_id=r["tenant_id"], email=r["email"], pw_hash=r["pw_hash"],
        tier=r["tier"], created_at=datetime.fromisoformat(r["created_at"]),
    )


def find_tenant_by_email(email: str) -> Tenant | None:
    init_registry()
    with _conn(_registry_path()) as c:
        row = c.execute(
            "SELECT * FROM tenants WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        return _row_to_tenant(row) if row else None


def find_tenant_by_id(tenant_id: str) -> Tenant | None:
    init_registry()
    with _conn(_registry_path()) as c:
        row = c.execute(
            "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        return _row_to_tenant(row) if row else None


def insert_api_key(k: ApiKey) -> None:
    init_tenant_db(k.tenant_id)
    with _conn(_tenant_path(k.tenant_id)) as c:
        c.execute(
            "INSERT INTO api_keys VALUES (?, ?, ?, ?, ?, ?)",
            (
                k.key_id, k.tenant_id, k.secret, k.name,
                k.created_at.isoformat(),
                k.revoked_at.isoformat() if k.revoked_at else None,
            ),
        )


def list_api_keys(tenant_id: str) -> list[ApiKey]:
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        rows = c.execute(
            "SELECT * FROM api_keys WHERE revoked_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
        return [
            ApiKey(
                key_id=r["key_id"], tenant_id=r["tenant_id"],
                secret=r["secret"], name=r["name"],
                created_at=datetime.fromisoformat(r["created_at"]),
                revoked_at=(
                    datetime.fromisoformat(r["revoked_at"]) if r["revoked_at"] else None
                ),
            )
            for r in rows
        ]


def find_tenant_for_secret(secret: str) -> tuple[Tenant, ApiKey] | None:
    """Look up tenant + key for a given API secret.

    O(N tenants) full scan; acceptable for Phase 1's sub-1000-tenant target.
    Postgres migration in Phase 3 collapses this to an indexed O(1) lookup.
    """
    init_registry()
    with _conn(_registry_path()) as c:
        tenant_rows = c.execute("SELECT * FROM tenants").fetchall()
    for trow in tenant_rows:
        tdb = _tenant_path(trow["tenant_id"])
        if not tdb.exists():
            continue
        with _conn(tdb) as c:
            kr = c.execute(
                "SELECT * FROM api_keys WHERE secret = ? AND revoked_at IS NULL",
                (secret,),
            ).fetchone()
            if kr:
                return (
                    _row_to_tenant(trow),
                    ApiKey(
                        key_id=kr["key_id"], tenant_id=kr["tenant_id"],
                        secret=kr["secret"], name=kr["name"],
                        created_at=datetime.fromisoformat(kr["created_at"]),
                    ),
                )
    return None


def insert_usage(u: UsageRecord) -> None:
    init_tenant_db(u.tenant_id)
    with _conn(_tenant_path(u.tenant_id)) as c:
        c.execute(
            "INSERT INTO usage_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                u.record_id, u.tenant_id, u.api_key_id, u.endpoint,
                u.verdict, u.intent_class, u.confidence, u.latency_ms,
                u.timestamp.isoformat(),
            ),
        )


def usage_summary(tenant_id: str) -> dict:
    """Aggregate usage across the tenant's full history (Phase 1 — no time filter)."""
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        total = c.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0]
        blocked = c.execute(
            "SELECT COUNT(*) FROM usage_records WHERE verdict = 'block'"
        ).fetchone()[0]
        avg_latency = c.execute(
            "SELECT AVG(latency_ms) FROM usage_records"
        ).fetchone()[0] or 0.0
        return {
            "total_calls": int(total),
            "blocked": int(blocked),
            "avg_latency_ms": round(float(avg_latency), 1),
        }
