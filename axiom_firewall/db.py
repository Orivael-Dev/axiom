"""SQLite-per-tenant data layer.

Includes:
  tenants/registry.db    — master tenant registry
  tenants/{id}.db        — per-tenant api_keys, usage_records,
                           decisions (flight recorder), agent_access_rules


Per docs/PHASE_1_DECISIONS.md §3.

Layout:
  tenants/registry.db        — tenant rows (one master DB)
  tenants/{tenant_id}.db     — that tenant's api_keys + usage_records

Migration to Postgres triggers when a single tenant exceeds 100M
decision events or a customer requires multi-region replication.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
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
                tenant_id              TEXT PRIMARY KEY,
                email                  TEXT UNIQUE NOT NULL,
                pw_hash                TEXT NOT NULL,
                tier                   TEXT NOT NULL,
                created_at             TEXT NOT NULL,
                stripe_customer_id     TEXT,
                stripe_subscription_id TEXT,
                recovery_hash          TEXT
            )
        """)
        # Idempotent migration for tenants tables created by an earlier release.
        existing = {r[1] for r in c.execute("PRAGMA table_info(tenants)").fetchall()}
        if "stripe_customer_id" not in existing:
            c.execute("ALTER TABLE tenants ADD COLUMN stripe_customer_id TEXT")
        if "stripe_subscription_id" not in existing:
            c.execute("ALTER TABLE tenants ADD COLUMN stripe_subscription_id TEXT")
        if "recovery_hash" not in existing:
            c.execute("ALTER TABLE tenants ADD COLUMN recovery_hash TEXT")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_stripe_customer "
            "ON tenants(stripe_customer_id)"
        )


def init_tenant_db(tenant_id: str) -> None:
    with _conn(_tenant_path(tenant_id)) as c:
        # NOTE: `secret` column is legacy (kept to satisfy UNIQUE NOT NULL
        # on existing files); plaintext is no longer stored there. New
        # rows write a placeholder + the real digest into `secret_hash`.
        # See _migrate_api_keys_to_hashed below for the back-fill path.
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
        existing = {r[1] for r in c.execute("PRAGMA table_info(api_keys)").fetchall()}
        if "secret_hash" not in existing:
            c.execute("ALTER TABLE api_keys ADD COLUMN secret_hash TEXT")
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_hash "
                "ON api_keys(secret_hash) WHERE secret_hash IS NOT NULL"
            )
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
        # Flight Recorder — full decision log
        c.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id   TEXT PRIMARY KEY,
                tenant_id     TEXT NOT NULL,
                api_key_id    TEXT NOT NULL,
                endpoint      TEXT NOT NULL,
                verdict       TEXT NOT NULL,
                intent_class  TEXT NOT NULL,
                confidence    REAL NOT NULL,
                latency_ms    REAL NOT NULL,
                input_text    TEXT,
                output_text   TEXT,
                pattern_matched TEXT,
                constitutional_block INTEGER NOT NULL DEFAULT 0,
                ftc_reportable INTEGER NOT NULL DEFAULT 0,
                manifest_id   TEXT,
                signature     TEXT,
                timestamp     TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_dec_ts "
            "ON decisions(timestamp)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_dec_intent "
            "ON decisions(intent_class, timestamp)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_dec_verdict "
            "ON decisions(verdict, timestamp)"
        )
    _migrate_api_keys_to_hashed(tenant_id)


def _migrate_api_keys_to_hashed(tenant_id: str) -> None:
    """Back-fill secret_hash for any rows still holding plaintext.

    Idempotent: runs once per row, then no-ops. Triggered from
    init_tenant_db so every code path that opens a tenant DB also
    migrates. Plaintext is overwritten with a sentinel (`hashed:<key_id>`)
    that preserves the legacy UNIQUE constraint without leaking key
    material.

    We import auth.hash_api_secret lazily so this module stays
    importable in tests that haven't set AXIOM_MASTER_KEY yet.
    """
    from .auth import hash_api_secret  # circular-import dance

    with _conn(_tenant_path(tenant_id)) as c:
        rows = c.execute(
            "SELECT key_id, secret FROM api_keys "
            "WHERE secret_hash IS NULL AND secret LIKE 'axfw_%'"
        ).fetchall()
        for r in rows:
            digest = hash_api_secret(r["secret"])
            sentinel = f"hashed:{r['key_id']}"
            c.execute(
                "UPDATE api_keys SET secret_hash = ?, secret = ? "
                "WHERE key_id = ?",
                (digest, sentinel, r["key_id"]),
            )


def insert_tenant(t: Tenant) -> None:
    init_registry()
    init_tenant_db(t.tenant_id)
    with _conn(_registry_path()) as c:
        c.execute(
            "INSERT INTO tenants "
            "(tenant_id, email, pw_hash, tier, created_at, "
            " stripe_customer_id, stripe_subscription_id, recovery_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t.tenant_id, t.email, t.pw_hash, t.tier,
                t.created_at.isoformat(),
                t.stripe_customer_id, t.stripe_subscription_id,
                t.recovery_hash,
            ),
        )


def _row_to_tenant(r) -> Tenant:
    keys = r.keys()
    return Tenant(
        tenant_id=r["tenant_id"], email=r["email"], pw_hash=r["pw_hash"],
        tier=r["tier"], created_at=datetime.fromisoformat(r["created_at"]),
        stripe_customer_id=r["stripe_customer_id"]
            if "stripe_customer_id" in keys else None,
        stripe_subscription_id=r["stripe_subscription_id"]
            if "stripe_subscription_id" in keys else None,
        recovery_hash=r["recovery_hash"] if "recovery_hash" in keys else None,
    )


def update_tenant_password(tenant_id: str, *, pw_hash: str) -> None:
    """Set a new password hash for the tenant. Used by the reset flow."""
    init_registry()
    with _conn(_registry_path()) as c:
        c.execute(
            "UPDATE tenants SET pw_hash = ? WHERE tenant_id = ?",
            (pw_hash, tenant_id),
        )


def update_tenant_recovery_hash(tenant_id: str, *, recovery_hash: str) -> None:
    """Rotate the recovery-code hash for the tenant (also after a reset)."""
    init_registry()
    with _conn(_registry_path()) as c:
        c.execute(
            "UPDATE tenants SET recovery_hash = ? WHERE tenant_id = ?",
            (recovery_hash, tenant_id),
        )


def delete_tenant(tenant_id: str) -> None:
    """Right-to-erasure: remove the tenant row + the per-tenant DB file.

    Order: drop the registry row FIRST so any concurrent lookup sees the
    tenant as gone before the data file disappears. Best-effort unlink
    of the SQLite file; missing file is not an error (already removed).
    """
    init_registry()
    with _conn(_registry_path()) as c:
        c.execute("DELETE FROM tenants WHERE tenant_id = ?", (tenant_id,))
    db_path = _tenant_path(tenant_id)
    try:
        db_path.unlink()
    except FileNotFoundError:
        pass


def update_tenant_tier(
    tenant_id: str, *, tier: str,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
) -> None:
    """Webhook + checkout-completion path. Updates tier + Stripe linkage."""
    init_registry()
    with _conn(_registry_path()) as c:
        c.execute(
            "UPDATE tenants SET tier = ?, "
            "stripe_customer_id = COALESCE(?, stripe_customer_id), "
            "stripe_subscription_id = ? "
            "WHERE tenant_id = ?",
            (tier, stripe_customer_id, stripe_subscription_id, tenant_id),
        )


def find_tenant_by_stripe_customer(customer_id: str) -> Tenant | None:
    init_registry()
    with _conn(_registry_path()) as c:
        row = c.execute(
            "SELECT * FROM tenants WHERE stripe_customer_id = ?", (customer_id,)
        ).fetchone()
        return _row_to_tenant(row) if row else None


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
    """Persist a new API key. Plaintext secret is NEVER stored.

    The `secret` column gets a sentinel placeholder (still UNIQUE to
    satisfy the legacy constraint) and the peppered HMAC of the
    plaintext lands in `secret_hash`. The caller is responsible for
    showing the plaintext to the user exactly once (typically via a
    one-shot flash message in the dashboard).
    """
    from .auth import hash_api_secret  # circular-import dance

    init_tenant_db(k.tenant_id)
    digest = hash_api_secret(k.secret)
    sentinel = f"hashed:{k.key_id}"
    with _conn(_tenant_path(k.tenant_id)) as c:
        c.execute(
            "INSERT INTO api_keys "
            "(key_id, tenant_id, secret, name, created_at, revoked_at, secret_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                k.key_id, k.tenant_id, sentinel, k.name,
                k.created_at.isoformat(),
                k.revoked_at.isoformat() if k.revoked_at else None,
                digest,
            ),
        )


def revoke_api_key(tenant_id: str, key_id: str) -> bool:
    """Mark `key_id` as revoked for `tenant_id`.

    Soft-delete: sets `revoked_at = now` and clears `secret_hash` so
    `find_tenant_for_secret` can no longer match the bearer token
    (which is the auth fastpath; revoked_at IS NULL is also checked
    there but clearing the hash is belt-and-braces). The row stays
    in the DB so historical `usage_records.api_key_id` foreign-key
    references remain joinable for billing/audit.

    Returns True if a row was updated (active key for this tenant
    existed), False if the key doesn't exist OR is already revoked
    OR belongs to a different tenant. Cross-tenant isolation is
    enforced via the (tenant_id, key_id) WHERE clause — caller must
    pass its OWN tenant_id from the session, never one supplied by
    the client.
    """
    init_tenant_db(tenant_id)
    now = datetime.utcnow().isoformat()
    with _conn(_tenant_path(tenant_id)) as c:
        cur = c.execute(
            "UPDATE api_keys SET revoked_at = ?, secret_hash = NULL "
            "WHERE tenant_id = ? AND key_id = ? AND revoked_at IS NULL",
            (now, tenant_id, key_id),
        )
        return cur.rowcount > 0


def list_api_keys(tenant_id: str) -> list[ApiKey]:
    """Return active keys for the tenant. `secret` is intentionally
    blanked — the plaintext was shown once at creation and is not
    recoverable from the DB. UI should only render `key_id`/`name`.
    """
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        rows = c.execute(
            "SELECT * FROM api_keys WHERE revoked_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
        return [
            ApiKey(
                key_id=r["key_id"], tenant_id=r["tenant_id"],
                secret="",  # never round-trip a usable secret out of the DB
                name=r["name"],
                created_at=datetime.fromisoformat(r["created_at"]),
                revoked_at=(
                    datetime.fromisoformat(r["revoked_at"]) if r["revoked_at"] else None
                ),
            )
            for r in rows
        ]


def find_tenant_for_secret(secret_hash: str) -> tuple[Tenant, ApiKey] | None:
    """Look up tenant + key by peppered API-secret HASH.

    Callers pass the digest from `auth.hash_api_secret()`, never the
    raw bearer token. O(N tenants) full scan; acceptable for Phase 1's
    sub-1000-tenant target. Postgres migration in Phase 3 collapses
    this to an indexed O(1) lookup.
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
                "SELECT * FROM api_keys "
                "WHERE secret_hash = ? AND revoked_at IS NULL",
                (secret_hash,),
            ).fetchone()
            if kr:
                return (
                    _row_to_tenant(trow),
                    ApiKey(
                        key_id=kr["key_id"], tenant_id=kr["tenant_id"],
                        secret="",  # plaintext never re-emitted
                        name=kr["name"],
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


# ── Flight Recorder ───────────────────────────────────────────────────────

def insert_decision(d: dict) -> None:
    """Write a full decision record to the per-tenant decisions table."""
    tenant_id = d["tenant_id"]
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute(
            """INSERT OR IGNORE INTO decisions
               (decision_id, tenant_id, api_key_id, endpoint, verdict,
                intent_class, confidence, latency_ms, input_text,
                output_text, pattern_matched, constitutional_block,
                ftc_reportable, manifest_id, signature, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d.get("decision_id") or str(uuid.uuid4()),
                tenant_id,
                d.get("api_key_id", ""),
                d.get("endpoint", ""),
                d.get("verdict", ""),
                d.get("intent_class", ""),
                float(d.get("confidence", 0.0)),
                float(d.get("latency_ms", 0.0)),
                d.get("input_text"),
                d.get("output_text"),
                d.get("pattern_matched"),
                int(bool(d.get("constitutional_block", False))),
                int(bool(d.get("ftc_reportable", False))),
                d.get("manifest_id"),
                d.get("signature"),
                d.get("timestamp") or datetime.utcnow().isoformat(),
            ),
        )


def get_decision(tenant_id: str, decision_id: str) -> dict | None:
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        row = c.execute(
            "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
            (decision_id, tenant_id),
        ).fetchone()
    return dict(row) if row else None


def get_decision_with_trace(tenant_id: str, decision_id: str) -> dict:
    """Fetch a decision row and enrich it with its latent manifest trace.

    Steps:
      1. Query decisions table for the row.
      2. Extract manifest_id from the row.
      3. Scan latent_manifests.jsonl in the repo root for a matching entry.
      4. Return {"decision": {...} | None, "trace": {...} | None}.
    """
    decision = get_decision(tenant_id, decision_id)
    if decision is None:
        return {"decision": None, "trace": None}

    manifest_id = decision.get("manifest_id")
    trace = None
    if manifest_id:
        candidates = [
            Path(__file__).parent.parent / "latent_manifests.jsonl",
            Path("latent_manifests.jsonl"),
        ]
        for manifest_path in candidates:
            if not manifest_path.is_file():
                continue
            try:
                with manifest_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("manifest_id") == manifest_id:
                            trace = entry
                            break
            except OSError:
                pass
            if trace:
                break

    return {"decision": decision, "trace": trace}


def query_decisions(
    tenant_id: str,
    *,
    verdict: str | None = None,
    intent_class: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Filtered query over the decisions table. Returns rows as dicts."""
    init_tenant_db(tenant_id)
    clauses = ["tenant_id = ?"]
    params: list = [tenant_id]
    if verdict:
        clauses.append("verdict = ?")
        params.append(verdict)
    if intent_class:
        clauses.append("intent_class = ?")
        params.append(intent_class)
    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    if until:
        clauses.append("timestamp <= ?")
        params.append(until)
    params += [limit, offset]
    sql = (
        "SELECT * FROM decisions WHERE "
        + " AND ".join(clauses)
        + " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    )
    with _conn(_tenant_path(tenant_id)) as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Right-to-erasure ──────────────────────────────────────────────────────

def _hmac_cert(payload: str) -> str:
    """HMAC-SHA256 over a payload using AXIOM_MASTER_KEY.  Falls back to
    a zero-key if the env var is absent (so erasure never silently fails)."""
    key = bytes.fromhex(
        os.environ.get("AXIOM_MASTER_KEY", "0" * 64)
    )
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def erase_subject_data(
    tenant_id: str,
    subject_id: str,
) -> dict:
    """Locate and delete all decisions whose input_text or output_text
    contains subject_id (simple substring match).  Returns a signed
    deletion certificate.

    Note: this operates only on the structured decision log.  Latent
    encodings in model weights are outside the scope of this erasure.
    """
    init_tenant_db(tenant_id)
    erased: list[str] = []
    with _conn(_tenant_path(tenant_id)) as c:
        rows = c.execute(
            "SELECT decision_id, input_text, output_text FROM decisions "
            "WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchall()
        for r in rows:
            text = (r["input_text"] or "") + " " + (r["output_text"] or "")
            if subject_id in text:
                c.execute(
                    "DELETE FROM decisions WHERE decision_id = ?",
                    (r["decision_id"],),
                )
                erased.append(r["decision_id"])

    cert_payload = json.dumps({
        "cert_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "subject_id_hash": hashlib.sha256(subject_id.encode()).hexdigest(),
        "records_erased": len(erased),
        "erased_ids": erased,
        "erased_at": datetime.utcnow().isoformat(),
        "scope": "decision_log_only",
        "limitation": (
            "Latent encodings in model weights are outside scope of this erasure. "
            "Model retraining required for complete weight-level erasure."
        ),
    }, sort_keys=True)

    return {
        **json.loads(cert_payload),
        "signature": _hmac_cert(cert_payload),
    }


def init_studio_containers(tenant_id: str) -> None:
    """Ensure the studio_containers table exists in the tenant DB."""
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS studio_containers (
                container_id  TEXT PRIMARY KEY,
                tenant_id     TEXT NOT NULL,
                name          TEXT NOT NULL,
                config_json   TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
        """)


def count_studio_containers(tenant_id: str) -> int:
    init_studio_containers(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        return c.execute("SELECT COUNT(*) FROM studio_containers").fetchone()[0]


def list_studio_containers(tenant_id: str) -> list[dict]:
    init_studio_containers(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        rows = c.execute(
            "SELECT container_id, name, config_json, created_at "
            "FROM studio_containers ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "container_id": r["container_id"],
                "name": r["name"],
                "config": json.loads(r["config_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]


def insert_studio_container(tenant_id: str, name: str, config: dict) -> str:
    init_studio_containers(tenant_id)
    container_id = uuid.uuid4().hex
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute(
            "INSERT INTO studio_containers "
            "(container_id, tenant_id, name, config_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (container_id, tenant_id, name, json.dumps(config),
             datetime.utcnow().isoformat()),
        )
    return container_id


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
