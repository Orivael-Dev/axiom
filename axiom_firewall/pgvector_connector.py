"""pgvector connector for Axiom Data Gate.

Wraps a PostgreSQL + pgvector deployment to provide governed vector storage.
Requires: psycopg2-binary and pgvector extension enabled on the DB server.

Usage:
    conn = PgVectorConnector.from_env()
    conn.store_embedding("doc-001", [0.1, 0.2, ...], {"data_class": "INFORM", "tenant_id": "t1"})
    results = conn.search_similar([0.1, 0.2, ...], top_k=5, data_class_filter="INFORM")
    conn.delete_by_subject("user@example.com")

Environment variables:
    AXIOM_PGVECTOR_DSN   — full DSN, e.g. postgresql://user:pass@host:5432/db
    AXIOM_PGVECTOR_TABLE — table name (default: axiom_embeddings)
    AXIOM_PGVECTOR_DIM   — embedding dimension (default: 1536)

The table is created automatically on first use.  Requires the pgvector
extension to be pre-installed on the server:
    CREATE EXTENSION IF NOT EXISTS vector;
"""
from __future__ import annotations

import json
import os
from typing import Optional

_HAS_PSYCOPG2 = False
try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    pass


class PgVectorConnector:
    """Governed vector store backed by PostgreSQL + pgvector."""

    def __init__(
        self,
        dsn: str,
        table: str = "axiom_embeddings",
        dim: int = 1536,
    ):
        if not _HAS_PSYCOPG2:
            raise ImportError(
                "psycopg2-binary is required for PgVectorConnector. "
                "Install: pip install psycopg2-binary"
            )
        self._dsn = dsn
        self._table = table
        self._dim = dim
        self._ensure_table()

    @classmethod
    def from_env(cls) -> "PgVectorConnector":
        dsn = os.environ.get("AXIOM_PGVECTOR_DSN")
        if not dsn:
            raise EnvironmentError(
                "AXIOM_PGVECTOR_DSN is not set. "
                "Set it to a PostgreSQL DSN string."
            )
        return cls(
            dsn=dsn,
            table=os.environ.get("AXIOM_PGVECTOR_TABLE", "axiom_embeddings"),
            dim=int(os.environ.get("AXIOM_PGVECTOR_DIM", "1536")),
        )

    def _conn(self):
        return psycopg2.connect(self._dsn)

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        id          TEXT PRIMARY KEY,
                        tenant_id   TEXT NOT NULL,
                        subject_id  TEXT,
                        data_class  TEXT NOT NULL DEFAULT 'INFORM',
                        metadata    JSONB,
                        embedding   vector({self._dim}),
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self._table}_tenant
                    ON {self._table}(tenant_id)
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self._table}_subj
                    ON {self._table}(subject_id)
                    WHERE subject_id IS NOT NULL
                """)
                # IVFFlat index for cosine similarity search
                # Requires at least ~1000 rows to be useful.
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self._table}_emb
                    ON {self._table} USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100)
                """)
            conn.commit()

    def store_embedding(
        self,
        doc_id: str,
        embedding: list[float],
        metadata: Optional[dict] = None,
    ) -> None:
        """Store or replace an embedding with governance metadata.

        metadata keys:
          tenant_id   (required)
          subject_id  — data subject for right-to-erasure
          data_class  — e.g. "INFORM", "PCI", "GDPR-9"
        """
        meta = metadata or {}
        tenant_id = meta.get("tenant_id", "")
        subject_id = meta.get("subject_id")
        data_class = meta.get("data_class", "INFORM")
        extra = {k: v for k, v in meta.items()
                 if k not in ("tenant_id", "subject_id", "data_class")}

        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {self._table}
                        (id, tenant_id, subject_id, data_class, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (id) DO UPDATE SET
                        tenant_id  = EXCLUDED.tenant_id,
                        subject_id = EXCLUDED.subject_id,
                        data_class = EXCLUDED.data_class,
                        metadata   = EXCLUDED.metadata,
                        embedding  = EXCLUDED.embedding
                """, (
                    doc_id,
                    tenant_id,
                    subject_id,
                    data_class,
                    json.dumps(extra),
                    vec_str,
                ))
            conn.commit()

    def search_similar(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        tenant_id: Optional[str] = None,
        data_class_filter: Optional[str] = None,
    ) -> list[dict]:
        """Find the top_k most similar embeddings by cosine distance.

        Returns list of {id, tenant_id, subject_id, data_class, metadata,
        similarity, created_at}.
        """
        vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        clauses: list[str] = []
        params: list = []
        if tenant_id:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        if data_class_filter:
            clauses.append("data_class = %s")
            params.append(data_class_filter)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params_full = params + [vec_str, top_k]

        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT id, tenant_id, subject_id, data_class, metadata,
                           1 - (embedding <=> %s::vector) AS similarity,
                           created_at
                    FROM {self._table}
                    {where}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    params + [vec_str, vec_str, top_k],
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def delete_by_subject(self, subject_id: str, tenant_id: Optional[str] = None) -> int:
        """Delete all embeddings for a data subject (right-to-erasure).

        Returns count of deleted rows.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                if tenant_id:
                    cur.execute(
                        f"DELETE FROM {self._table} "
                        "WHERE subject_id = %s AND tenant_id = %s",
                        (subject_id, tenant_id),
                    )
                else:
                    cur.execute(
                        f"DELETE FROM {self._table} WHERE subject_id = %s",
                        (subject_id,),
                    )
                count = cur.rowcount
            conn.commit()
        return count

    def delete_by_tenant(self, tenant_id: str) -> int:
        """Delete all embeddings for a tenant (tenant right-to-erasure)."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self._table} WHERE tenant_id = %s",
                    (tenant_id,),
                )
                count = cur.rowcount
            conn.commit()
        return count
