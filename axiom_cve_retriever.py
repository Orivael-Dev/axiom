"""SQLite FTS5-backed CVE retriever — scalable drop-in for LocalRetriever.

The pure-Python BM25 LocalRetriever scores every document per query (O(N)),
which is ~2.4 s/query at the 297k-CVE scale. This backend uses SQLite's
FTS5 extension: an on-disk inverted index with built-in BM25 ranking, giving
sub-millisecond queries at the same scale with near-zero resident memory.

Interface parity: `retrieve(query, *, k=5, domain=None)` returns the same
`RetrievedSource` objects as `axiom_research_retriever.LocalRetriever`, so the
RAG wiring (research/rag_demo_qwen05b.py) is unchanged — just swap the object.

Build once from the CVE jsonl, then query repeatedly:

    from axiom_cve_retriever import CVERetriever
    r = CVERetriever("I:/Orivael/dataset/cve_fts5.db")
    r.build_from_jsonl("I:/Orivael/dataset/all_cve_database.jsonl")   # one-time
    for src in r.retrieve("CVE-2021-44228 log4j rce", k=5):
        print(src.score, src.title)

CLI:
    python -m axiom_cve_retriever build --jsonl <path> --db <path>
    python -m axiom_cve_retriever query --db <path> "log4j remote code execution"
    python -m axiom_cve_retriever stats --db <path>
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from axiom_research_retriever import RetrievedSource

_CVE_RE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _fts_query(text: str) -> str:
    """Turn a free-text query into a safe FTS5 MATCH expression (unscoped).

    FTS5 treats characters like '-', '"', '*', ':' as operators, so a raw
    "CVE-2021-44228" would error or mis-parse. We extract alphanumeric tokens
    and OR them as quoted terms — recall-friendly, with bm25() doing the
    relevance ranking. Used as the fallback path; CVERetriever._match_for()
    adds CVE-ID routing and common-token pruning on top.
    """
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


class CVERetriever:
    """FTS5 BM25 retriever over the CVE corpus."""

    # Tokens appearing in more than this fraction of documents are
    # non-discriminating (e.g. "cve", "vulnerability") and get pruned
    # from free-text queries so FTS5 doesn't rank the whole corpus.
    _COMMON_DF_FRACTION = 0.30

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._common: Optional[frozenset] = None   # high-DF stop tokens (lazy)

    # ── connection ────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
        return self._conn

    @staticmethod
    def _has_fts5(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
            conn.execute("DROP TABLE _fts5_probe")
            return True
        except sqlite3.OperationalError:
            return False

    # ── build ─────────────────────────────────────────────────────────────

    def build_from_jsonl(
        self,
        jsonl_path: str | Path,
        *,
        batch: int = 5000,
        progress_every: int = 50000,
    ) -> int:
        """Create the FTS5 index from the CVE jsonl. Returns rows inserted.

        Idempotent: drops and rebuilds the table each call.
        """
        conn = self._connect()
        if not self._has_fts5(conn):
            raise RuntimeError(
                "This SQLite build lacks the FTS5 extension. "
                "Use a Python with FTS5-enabled sqlite3 (most do)."
            )
        conn.execute("DROP TABLE IF EXISTS cve")
        # `answer` is stored + searchable; cve_id boosts identifier hits.
        conn.execute(
            "CREATE VIRTUAL TABLE cve USING fts5("
            "cve_id, question, answer, tokenize='unicode61')"
        )
        rows, pending = 0, []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                q = row.get("User", "")
                a = row.get("Assistant", "")
                m = _CVE_RE.search(q) or _CVE_RE.search(a)
                cve_id = m.group(0).upper() if m else ""
                pending.append((cve_id, q, a))
                rows += 1
                if len(pending) >= batch:
                    conn.executemany(
                        "INSERT INTO cve(cve_id, question, answer) VALUES (?,?,?)",
                        pending,
                    )
                    pending.clear()
                    if rows % progress_every == 0:
                        print(f"[cve-fts5]   {rows:,} rows...")
        if pending:
            conn.executemany(
                "INSERT INTO cve(cve_id, question, answer) VALUES (?,?,?)",
                pending,
            )
        conn.commit()
        conn.execute("INSERT INTO cve(cve) VALUES('optimize')")  # merge b-trees
        conn.commit()
        return rows

    # ── query building (optimizations) ────────────────────────────────────

    def _common_tokens(self) -> frozenset:
        """Lazily load the set of ultra-common (high doc-frequency) tokens.

        Uses an fts5vocab('row') shadow table to read each term's document
        count without a corpus scan. Cached after first call.
        """
        if self._common is not None:
            return self._common
        conn = self._connect()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS cve_vocab "
                "USING fts5vocab('cve', 'row')"
            )
            n = conn.execute("SELECT count(*) FROM cve").fetchone()[0] or 1
            cutoff = int(self._COMMON_DF_FRACTION * n)
            rows = conn.execute(
                "SELECT term FROM cve_vocab WHERE doc > ?", (cutoff,)
            ).fetchall()
            self._common = frozenset(t[0] for t in rows)
        except sqlite3.OperationalError:
            self._common = frozenset()
        return self._common

    def _match_for(self, query: str) -> str:
        """Build the optimized FTS5 MATCH expression for a query.

        1. If the query names a CVE-ID, route to an exact, column-scoped
           match (`cve_id:2021 AND cve_id:44228`) — selective, microsecond.
        2. Otherwise OR the alphanumeric tokens, dropping ultra-common ones
           so FTS5 ranks a small candidate set instead of the whole corpus.
        """
        m = _CVE_RE.search(query)
        if m:
            id_tokens = _TOKEN_RE.findall(m.group(0))   # ['cve','2021','44228']
            # Year + sequence are enough to pin a single CVE; skip the
            # literal 'cve' token (present in every id).
            sel = [t for t in id_tokens if t.lower() != "cve"]
            if sel:
                return " AND ".join(f'cve_id:"{t}"' for t in sel)
        tokens = _TOKEN_RE.findall(query)
        if not tokens:
            return ""
        common = self._common_tokens()
        pruned = [t for t in tokens if t.lower() not in common]
        use = pruned or tokens                          # never empty the query
        return " OR ".join(f'"{t}"' for t in use)

    # ── query ─────────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        *,
        k: int = 5,
        domain: Optional[str] = None,
    ) -> List[RetrievedSource]:
        del domain  # interface parity with LocalRetriever
        if not query or not query.strip():
            return []
        match = self._match_for(query)
        if not match:
            return []
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT cve_id, question, answer, bm25(cve) AS rank "
                "FROM cve WHERE cve MATCH ? ORDER BY rank LIMIT ?",
                (match, k),
            )
            hits = cur.fetchall()
        except sqlite3.OperationalError:
            return []
        if not hits:
            return []
        # bm25() is lower-is-better (typically negative). Convert to a
        # positive scale and normalise to the top hit, matching LocalRetriever.
        raw = [-h[3] for h in hits]
        top = max(raw) or 1.0
        out: List[RetrievedSource] = []
        for (cve_id, question, answer, _rank), r in zip(hits, raw):
            title = cve_id or (question[:80] if question else "CVE record")
            snippet = (answer or question or "").strip().replace("\n", " ")
            out.append(RetrievedSource(
                title=title,
                uri=cve_id or "cve",
                kind="security · cve",
                score=round(r / top, 4) if top > 0 else 0.0,
                snippet=snippet[:400],
                provider="cve-fts5",
            ))
        return out

    def answer_for(self, query: str) -> Optional[str]:
        """Convenience: full Assistant text of the top hit (RAG context)."""
        match = self._match_for(query)
        if not match:
            return None
        conn = self._connect()
        cur = conn.execute(
            "SELECT answer FROM cve WHERE cve MATCH ? ORDER BY bm25(cve) LIMIT 1",
            (match,),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def stats(self) -> dict:
        conn = self._connect()
        n = conn.execute("SELECT count(*) FROM cve").fetchone()[0]
        with_id = conn.execute(
            "SELECT count(*) FROM cve WHERE cve_id != ''"
        ).fetchone()[0]
        size_mb = Path(self._db_path).stat().st_size / (1024 * 1024) \
            if Path(self._db_path).exists() else 0.0
        return {
            "rows": n,
            "with_cve_id": with_id,
            "db_path": self._db_path,
            "db_size_mb": round(size_mb, 1),
        }


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(prog="axiom-cve-retriever")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="build FTS5 index from CVE jsonl")
    pb.add_argument("--jsonl", required=True)
    pb.add_argument("--db", required=True)

    pq = sub.add_parser("query", help="query the index")
    pq.add_argument("--db", required=True)
    pq.add_argument("--k", type=int, default=5)
    pq.add_argument("text", nargs="+")

    ps = sub.add_parser("stats", help="index statistics")
    ps.add_argument("--db", required=True)

    args = ap.parse_args(argv)
    r = CVERetriever(args.db)

    if args.cmd == "build":
        t0 = time.time()
        n = r.build_from_jsonl(args.jsonl)
        print(f"built {n:,} rows in {time.time()-t0:.1f} s -> {args.db}")
        print(r.stats())
        return 0
    if args.cmd == "stats":
        print(r.stats())
        return 0
    if args.cmd == "query":
        q = " ".join(args.text)
        t0 = time.time()
        hits = r.retrieve(q, k=args.k)
        ms = (time.time() - t0) * 1000
        print(f"query {q!r}  ({ms:.1f} ms, {len(hits)} hits)")
        for h in hits:
            print(f"  {h.score:6.4f}  {h.title}")
            print(f"          {h.snippet[:120]}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
