"""Legal RAG Bench — FTS5 retrieval evaluation against isaacus/legal-rag-bench.

Measures how well our SQLite FTS5 / BM25 pipeline retrieves the correct
legal passage for each of the 100 benchmark questions, without any embedding
model or GPU.

Metrics reported:
  Recall@1, @5, @10  — correct passage in top-k results
  MRR                — Mean Reciprocal Rank of the correct passage
  Hit latency        — FTS5 query time per question

The benchmark uses two dataset configs from `isaacus/legal-rag-bench`:
  corpus  — full passage text; indexed into FTS5 (`text` column as `answer`,
             passage `id` as `cve_id` in the FTS5 table)
  qa      — 100 question / answer / relevant_passage_id rows

Why FTS5 is interesting for legal text:
  Legal documents are dense with exact identifiers — case citations
  (Smith v. Jones, 2024 WL 123456), statute numbers (42 U.S.C. § 1983),
  regulation references (17 CFR 240.10b-5) — that BM25 retrieves with
  exact token matching.  Semantic questions are weaker for pure BM25;
  the benchmark reveals where the threshold is.

Run:
    pip install datasets
    python3 research/legal/legal_rag_bench.py [--db /tmp/legal.db] [--k 10] [--hf-token TOKEN]

Results are written to research/legal/results.json and printed as a table.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Tuple

# ── repo root on sys.path ─────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axiom_cve_retriever import CVERetriever


# ── constants ─────────────────────────────────────────────────────────────────

DATASET_ID     = "isaacus/legal-rag-bench"
CORPUS_CONFIG  = "corpus"
QA_CONFIG      = "qa"
DEFAULT_DB     = Path(__file__).parent / "legal_fts5.db"
RESULTS_PATH   = Path(__file__).parent / "results.json"


# ── FTS5 helpers (legal-schema variant of CVERetriever) ───────────────────────

def build_legal_index(
    db_path: Path,
    corpus,
    *,
    batch: int = 2000,
    progress_every: int = 10000,
) -> int:
    """Index the legal corpus into an FTS5 table.

    Schema mirrors CVERetriever's `cve(cve_id, question, answer)`:
      cve_id   → passage id
      question → empty (corpus rows have no paired question)
      answer   → passage text
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous  = NORMAL")
    conn.execute("PRAGMA cache_size   = -40000")
    conn.execute("DROP TABLE IF EXISTS cve")
    conn.execute(
        "CREATE VIRTUAL TABLE cve USING fts5("
        "cve_id, question, answer, tokenize='unicode61')"
    )

    rows, pending = 0, []
    for row in corpus:
        passage_id = str(row.get("id", "") or row.get("passage_id", "") or f"p{rows}")
        text = row.get("text", "")
        if not text.strip():
            continue
        pending.append((passage_id, "", text))
        rows += 1
        if len(pending) >= batch:
            conn.executemany(
                "INSERT INTO cve(cve_id, question, answer) VALUES (?,?,?)",
                pending,
            )
            pending.clear()
            if rows % progress_every == 0:
                print(f"  indexed {rows:,} passages…")
    if pending:
        conn.executemany(
            "INSERT INTO cve(cve_id, question, answer) VALUES (?,?,?)",
            pending,
        )
    conn.commit()
    conn.execute("INSERT INTO cve(cve) VALUES('optimize')")
    conn.commit()
    conn.close()
    return rows


def retrieve_ids(
    retriever: CVERetriever,
    question: str,
    *,
    k: int = 10,
) -> Tuple[List[str], float]:
    """Return (ranked_passage_ids, latency_ms)."""
    t0 = time.perf_counter()
    hits = retriever.retrieve(question, k=k)
    ms  = (time.perf_counter() - t0) * 1000
    ids = [h.uri.split("/")[-1] if "/" in h.uri else h.title for h in hits]
    return ids, ms


def retrieve_ids_raw(
    conn: sqlite3.Connection,
    retriever: CVERetriever,
    question: str,
    *,
    k: int = 10,
) -> Tuple[List[str], float]:
    """Direct FTS5 query returning cve_id values (passage ids)."""
    match = retriever._match_for(question)
    if not match:
        return [], 0.0
    t0 = time.perf_counter()
    cur = conn.execute(
        "SELECT cve_id FROM cve WHERE cve MATCH ? ORDER BY bm25(cve) LIMIT ?",
        (match, k),
    )
    rows = [r[0] for r in cur.fetchall()]
    ms   = (time.perf_counter() - t0) * 1000
    return rows, ms


# ── metrics ───────────────────────────────────────────────────────────────────

def reciprocal_rank(retrieved: List[str], relevant: str) -> float:
    try:
        rank = retrieved.index(relevant) + 1
        return 1.0 / rank
    except ValueError:
        return 0.0


def recall_at_k(retrieved: List[str], relevant: str, k: int) -> bool:
    return relevant in retrieved[:k]


# ── main benchmark loop ───────────────────────────────────────────────────────

def run_benchmark(
    db_path: Path,
    qa_rows: list,
    *,
    k: int = 10,
) -> dict:
    retriever = CVERetriever(str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")

    results = []
    for row in qa_rows:
        qid         = str(row.get("id", ""))
        question    = row.get("question", "")
        ref_answer  = row.get("answer", "")
        passage_id  = str(row.get("relevant_passage_id", ""))

        retrieved_ids, latency_ms = retrieve_ids_raw(
            conn, retriever, question, k=k
        )
        rr = reciprocal_rank(retrieved_ids, passage_id)
        results.append({
            "id":            qid,
            "question":      question,
            "relevant_id":   passage_id,
            "retrieved_ids": retrieved_ids[:5],
            "rr":            rr,
            "recall@1":      recall_at_k(retrieved_ids, passage_id, 1),
            "recall@5":      recall_at_k(retrieved_ids, passage_id, 5),
            "recall@10":     recall_at_k(retrieved_ids, passage_id, 10),
            "latency_ms":    round(latency_ms, 2),
        })

    conn.close()
    n = len(results)
    if n == 0:
        return {}

    mrr      = sum(r["rr"]        for r in results) / n
    r_at_1   = sum(r["recall@1"]  for r in results) / n
    r_at_5   = sum(r["recall@5"]  for r in results) / n
    r_at_10  = sum(r["recall@10"] for r in results) / n
    avg_lat  = sum(r["latency_ms"] for r in results) / n
    p50_lat  = sorted(r["latency_ms"] for r in results)[n // 2]
    p95_lat  = sorted(r["latency_ms"] for r in results)[int(n * 0.95)]

    return {
        "dataset":    DATASET_ID,
        "n_questions": n,
        "retriever":  "FTS5-BM25 (SQLite, no GPU)",
        "metrics": {
            "MRR":         round(mrr,    4),
            "Recall@1":    round(r_at_1, 4),
            "Recall@5":    round(r_at_5, 4),
            "Recall@10":   round(r_at_10, 4),
        },
        "latency_ms": {
            "mean": round(avg_lat, 2),
            "p50":  round(p50_lat, 2),
            "p95":  round(p95_lat, 2),
        },
        "per_question": results,
    }


# ── pretty print ──────────────────────────────────────────────────────────────

def print_table(summary: dict) -> None:
    m  = summary["metrics"]
    lm = summary["latency_ms"]
    n  = summary["n_questions"]

    print()
    print("┌─────────────────────────────────────────────────────────┐")
    print(f"│  Legal RAG Bench — FTS5 / BM25 ({n} questions)          │")
    print("├──────────────────────┬──────────────────────────────────┤")
    print(f"│  MRR                 │  {m['MRR']:.4f}                          │")
    print(f"│  Recall@1            │  {m['Recall@1']:.4f}                          │")
    print(f"│  Recall@5            │  {m['Recall@5']:.4f}                          │")
    print(f"│  Recall@10           │  {m['Recall@10']:.4f}                          │")
    print("├──────────────────────┼──────────────────────────────────┤")
    print(f"│  Latency mean        │  {lm['mean']:.2f} ms                         │")
    print(f"│  Latency p50         │  {lm['p50']:.2f} ms                         │")
    print(f"│  Latency p95         │  {lm['p95']:.2f} ms                         │")
    print("└──────────────────────┴──────────────────────────────────┘")

    # Show failures
    fails = [r for r in summary["per_question"] if not r["recall@5"]]
    if fails:
        print(f"\n  {len(fails)} questions not in top-5 (sample):")
        for r in fails[:5]:
            print(f"    [{r['id']}] {r['question'][:80]}")
            print(f"           relevant: {r['relevant_id']}")
            print(f"           retrieved: {r['retrieved_ids'][:3]}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Benchmark FTS5/BM25 retrieval on isaacus/legal-rag-bench"
    )
    ap.add_argument("--db",       type=Path, default=DEFAULT_DB,
                    help="FTS5 database path (created if missing)")
    ap.add_argument("--k",        type=int,  default=10,
                    help="Retrieve top-k candidates per question")
    ap.add_argument("--hf-token", default=None,
                    help="HuggingFace token (or set HF_TOKEN env var)")
    ap.add_argument("--skip-build", action="store_true",
                    help="Skip corpus indexing (use existing db)")
    ap.add_argument("--results",  type=Path, default=RESULTS_PATH,
                    help="JSON output path")
    args = ap.parse_args(argv)

    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: run `pip install datasets` first")
        return 1

    hf_token = args.hf_token or __import__("os").environ.get("HF_TOKEN")

    # ── 1. Build corpus index ─────────────────────────────────────────────────
    if not args.skip_build:
        print(f"Loading corpus from {DATASET_ID}…")
        corpus = load_dataset(
            DATASET_ID, CORPUS_CONFIG, split="test", token=hf_token
        )
        print(f"  {len(corpus):,} passages")
        print(f"  Building FTS5 index → {args.db}")
        t0 = time.perf_counter()
        n_indexed = build_legal_index(args.db, corpus)
        elapsed   = time.perf_counter() - t0
        print(f"  Done: {n_indexed:,} passages in {elapsed:.1f}s")
    else:
        print(f"Skipping build — using existing db at {args.db}")

    # ── 2. Load QA pairs ──────────────────────────────────────────────────────
    print(f"\nLoading QA pairs from {DATASET_ID}…")
    qa = load_dataset(DATASET_ID, QA_CONFIG, split="test", token=hf_token)
    print(f"  {len(qa)} questions")

    # ── 3. Run benchmark ──────────────────────────────────────────────────────
    print(f"\nRunning FTS5 retrieval (k={args.k})…")
    summary = run_benchmark(args.db, list(qa), k=args.k)

    # ── 4. Print and save results ─────────────────────────────────────────────
    print_table(summary)

    args.results.parent.mkdir(parents=True, exist_ok=True)
    per_q = summary.pop("per_question")
    args.results.write_text(json.dumps({**summary, "per_question": per_q}, indent=2))
    print(f"\n  Full results → {args.results}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
