"""Legal RAG Bench — FTS5 BM25 + optional SPLADE hybrid evaluation.

Baseline result (FTS5 BM25, k=10, 4876 passages, 100 questions):
  Hit@1  0.160   Hit@3  0.320   Hit@5  0.380   Hit@10  0.440
  MRR@10 0.251   Latency: 6.0ms avg / 11.7ms p95

Analysis:
  44/100 hits = the BM25 exact-token ceiling. These are questions
  where query vocabulary directly matches the passage.  The 56 misses
  are the semantic gap — correct passage exists but the question
  paraphrases the content using different vocabulary.

Hybrid strategy:
  FTS5 with high k (--fts5-k 100) surfaces far more candidates at
  still ~6ms.  SPLADE reranks 100→k with sparse neural expansion,
  closing the semantic gap without GPU or dense embeddings.

The benchmark reports two rows when --splade is set:
  BM25 only (FTS5 k=fts5_k, report at k=1/3/5/10)
  BM25+SPLADE (FTS5 k=fts5_k → SPLADE → report at k=1/3/5/10)

Run — BM25 only:
    python3 research/legal/legal_rag_bench.py \\
      --db /tmp/legal.db --hf-token $HF_TOKEN

Run — BM25 + SPLADE (install transformers first):
    pip install transformers torch
    python3 research/legal/legal_rag_bench.py \\
      --db /tmp/legal.db --hf-token $HF_TOKEN \\
      --splade --fts5-k 100 --k 10

Run — skip build (use existing db):
    python3 research/legal/legal_rag_bench.py \\
      --db /tmp/legal.db --skip-build --splade --fts5-k 100
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axiom_cve_retriever import CVERetriever


# ── constants ─────────────────────────────────────────────────────────────────

DATASET_ID    = "isaacus/legal-rag-bench"
CORPUS_CONFIG = "corpus"
QA_CONFIG     = "qa"
DEFAULT_DB    = Path(__file__).parent / "legal_fts5.db"
RESULTS_PATH  = Path(__file__).parent / "results.json"

# Legal synonym expansions — free BM25 quality improvement, zero latency cost.
# Maps common legal question words to passage vocabulary variants.
_LEGAL_SYNONYMS: Dict[str, List[str]] = {
    "attorney":    ["lawyer", "counsel", "advocate"],
    "lawyer":      ["attorney", "counsel"],
    "plaintiff":   ["claimant", "petitioner", "appellant"],
    "defendant":   ["respondent", "appellee"],
    "damages":     ["compensation", "remedy", "relief", "award"],
    "contract":    ["agreement", "covenant", "obligation"],
    "terminated":  ["dismissed", "discharged", "cancelled"],
    "liable":      ["responsible", "accountable", "culpable"],
    "held":        ["ruled", "decided", "found", "concluded"],
    "affirmed":    ["upheld", "confirmed", "sustained"],
    "reversed":    ["overturned", "vacated", "set aside"],
}


# ── FTS5 helpers ──────────────────────────────────────────────────────────────

def build_legal_index(db_path: Path, corpus, *, batch: int = 2000,
                      progress_every: int = 10_000) -> int:
    """Index legal corpus passages into FTS5.

    Schema mirrors CVERetriever's cve(cve_id, question, answer):
      cve_id   → passage id
      question → empty string
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
        pid  = str(row.get("id") or row.get("passage_id") or f"p{rows}")
        text = row.get("text", "")
        if not text.strip():
            continue
        pending.append((pid, "", text))
        rows += 1
        if len(pending) >= batch:
            conn.executemany(
                "INSERT INTO cve(cve_id, question, answer) VALUES (?,?,?)", pending)
            pending.clear()
            if rows % progress_every == 0:
                print(f"  indexed {rows:,} passages…")
    if pending:
        conn.executemany(
            "INSERT INTO cve(cve_id, question, answer) VALUES (?,?,?)", pending)
    conn.commit()
    conn.execute("INSERT INTO cve(cve) VALUES('optimize')")
    conn.commit()
    conn.close()
    return rows


def _expand_query(question: str) -> str:
    """Append synonym tokens to the query for BM25 quality improvement."""
    extra: List[str] = []
    lower = question.lower()
    for word, synonyms in _LEGAL_SYNONYMS.items():
        if word in lower:
            extra.extend(synonyms)
    if not extra:
        return question
    return question + " " + " ".join(extra)


def fts5_retrieve(
    conn: sqlite3.Connection,
    retriever: CVERetriever,
    question: str,
    *,
    k: int,
    expand: bool = True,
) -> Tuple[List[str], float]:
    """Return (ranked passage_ids, latency_ms)."""
    q = _expand_query(question) if expand else question
    match = retriever._match_for(q)
    if not match:
        return [], 0.0
    t0 = time.perf_counter()
    cur = conn.execute(
        "SELECT cve_id FROM cve WHERE cve MATCH ? ORDER BY bm25(cve) LIMIT ?",
        (match, k),
    )
    ids = [r[0] for r in cur.fetchall()]
    ms  = (time.perf_counter() - t0) * 1000
    return ids, ms


# ── SPLADE reranker ───────────────────────────────────────────────────────────

class SPLADEReranker:
    """Thin SPLADE wrapper that returns (reranked_ids, latency_ms)."""

    def __init__(self, model_name: str = "naver/splade-v3",
                 max_length: int = 256) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._tok        = None
        self._model      = None
        self._ready      = False

    def load(self) -> bool:
        try:
            from transformers import AutoTokenizer, AutoModelForMaskedLM
            import torch
            self._tok   = AutoTokenizer.from_pretrained(self._model_name)
            self._model = AutoModelForMaskedLM.from_pretrained(self._model_name)
            self._model.eval()
            self._torch = torch
            self._ready = True
            return True
        except Exception as exc:
            print(f"  [SPLADE] unavailable: {exc}")
            return False

    def _encode(self, text: str) -> Dict[int, float]:
        inputs = self._tok(text, return_tensors="pt", max_length=self._max_length,
                           truncation=True, padding=True)
        with self._torch.no_grad():
            out = self._model(**inputs)
            vec = self._torch.log(1 + self._torch.relu(out.logits)).max(dim=1).values.squeeze(0)
        nz = vec.nonzero(as_tuple=True)[0]
        return {int(i): float(vec[i]) for i in nz}

    def _dot(self, a: Dict[int, float], b: Dict[int, float]) -> float:
        return sum(a.get(k, 0.0) * v for k, v in b.items())

    def rerank(
        self,
        question: str,
        candidates: List[Tuple[str, str]],  # (passage_id, passage_text)
    ) -> Tuple[List[str], float]:
        """Return (reranked_ids, latency_ms)."""
        if not self._ready or not candidates:
            return [pid for pid, _ in candidates], 0.0
        t0 = time.perf_counter()
        q_vec = self._encode(question)
        scored = [(self._dot(q_vec, self._encode(txt)), pid)
                  for pid, txt in candidates]
        scored.sort(reverse=True)
        ms = (time.perf_counter() - t0) * 1000
        return [pid for _, pid in scored], ms


# ── metrics ───────────────────────────────────────────────────────────────────

def rr(retrieved: List[str], relevant: str) -> float:
    try:
        return 1.0 / (retrieved.index(relevant) + 1)
    except ValueError:
        return 0.0


def hit_at(retrieved: List[str], relevant: str, k: int) -> bool:
    return relevant in retrieved[:k]


# ── benchmark loop ────────────────────────────────────────────────────────────

def run_benchmark(
    db_path: Path,
    qa_rows: list,
    *,
    fts5_k: int = 10,
    final_k: int = 10,
    splade: Optional[SPLADEReranker] = None,
    expand: bool = True,
) -> dict:
    retriever = CVERetriever(str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")

    # Pre-load passage text for SPLADE (needs raw text for encoding)
    passage_text: Dict[str, str] = {}
    if splade is not None:
        cur = conn.execute("SELECT cve_id, answer FROM cve")
        passage_text = {r[0]: r[1] for r in cur.fetchall()}

    bm25_results, hybrid_results = [], []

    for row in qa_rows:
        qid        = str(row.get("id", ""))
        question   = row.get("question", "")
        passage_id = str(row.get("relevant_passage_id", ""))

        ids, fts5_ms = fts5_retrieve(conn, retriever, question,
                                     k=fts5_k, expand=expand)

        bm25_results.append({
            "id": qid, "relevant_id": passage_id,
            "retrieved": ids,
            "rr": rr(ids, passage_id),
            "fts5_ms": round(fts5_ms, 2),
        })

        if splade is not None:
            candidates = [(pid, passage_text.get(pid, "")) for pid in ids]
            reranked, splade_ms = splade.rerank(question, candidates)
            hybrid_results.append({
                "id": qid, "relevant_id": passage_id,
                "retrieved": reranked,
                "rr": rr(reranked, passage_id),
                "fts5_ms":   round(fts5_ms, 2),
                "splade_ms": round(splade_ms, 2),
            })

    conn.close()
    return {
        "bm25":   _aggregate(bm25_results,   final_k, latency_field="fts5_ms"),
        "hybrid": _aggregate(hybrid_results,  final_k, latency_field="splade_ms")
                  if hybrid_results else None,
    }


def _aggregate(results: list, k: int, *, latency_field: str) -> dict:
    n = len(results)
    if n == 0:
        return {}
    mrr   = sum(r["rr"] for r in results) / n
    hits  = {ks: sum(hit_at(r["retrieved"], r["relevant_id"], ks)
                      for r in results) / n
             for ks in [1, 3, 5, 10]}
    lats  = sorted(r.get(latency_field, 0) for r in results)
    failures = [r for r in results if not hit_at(r["retrieved"], r["relevant_id"], k)]
    return {
        "MRR":     round(mrr, 4),
        "Hit@1":   round(hits[1], 4),
        "Hit@3":   round(hits[3], 4),
        "Hit@5":   round(hits[5], 4),
        "Hit@10":  round(hits[10], 4),
        "latency": {
            "mean": round(sum(lats) / n, 2),
            "p50":  round(lats[n // 2], 2),
            "p95":  round(lats[int(n * 0.95)], 2),
        },
        "n_misses": len(failures),
        "per_question": results,
    }


# ── pretty print ──────────────────────────────────────────────────────────────

def _pct(v: float) -> str:
    return f"{v:.3f}  ({int(round(v*100))}/100)"


def print_comparison(data: dict, fts5_k: int) -> None:
    bm  = data["bm25"]
    hyb = data["hybrid"]

    cols = ["BM25"]
    if hyb:
        cols.append("BM25+SPLADE")

    w = 14
    print()
    print(f"  Legal RAG Bench — FTS5 k={fts5_k} → final k=10")
    print()
    print(f"  {'Metric':<12}  " + "  ".join(f"{c:>{w}}" for c in cols))
    print(f"  {'-'*12}  " + "  ".join("-" * w for _ in cols))
    for metric in ["MRR", "Hit@1", "Hit@3", "Hit@5", "Hit@10"]:
        row = f"  {metric:<12}  {_pct(bm[metric]):>{w}}"
        if hyb:
            delta = hyb[metric] - bm[metric]
            sign  = "+" if delta >= 0 else ""
            row  += f"  {_pct(hyb[metric]):>{w}}  ({sign}{delta:.3f})"
        print(row)
    print(f"  {'Misses':<12}  {bm['n_misses']:>{w}}", end="")
    if hyb:
        print(f"  {hyb['n_misses']:>{w}}", end="")
    print()
    print()
    print(f"  Latency (FTS5)  mean={bm['latency']['mean']} ms  "
          f"p50={bm['latency']['p50']} ms  p95={bm['latency']['p95']} ms")
    if hyb and hyb.get("latency"):
        print(f"  Latency (SPLADE) mean={hyb['latency']['mean']} ms  "
              f"p50={hyb['latency']['p50']} ms  p95={hyb['latency']['p95']} ms")

    if bm["n_misses"]:
        fails = [r for r in bm["per_question"]
                 if not hit_at(r["retrieved"], r["relevant_id"], 10)]
        print(f"\n  Sample misses (gold not in FTS5 top-{fts5_k}):")
        for r in fails[:5]:
            print(f"    [{r['id']}] {r['relevant_id']}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Benchmark FTS5/BM25 (+ optional SPLADE) on legal-rag-bench"
    )
    ap.add_argument("--db",         type=Path, default=DEFAULT_DB)
    ap.add_argument("--k",          type=int,  default=10,
                    help="Final output k (default 10)")
    ap.add_argument("--fts5-k",     type=int,  default=10,
                    help="FTS5 candidate pool before SPLADE rerank (default 10; "
                         "use 100 with --splade)")
    ap.add_argument("--splade",     action="store_true",
                    help="Enable SPLADE second-pass reranker (requires transformers)")
    ap.add_argument("--splade-model", default="naver/splade-v3")
    ap.add_argument("--no-expand",  action="store_true",
                    help="Disable legal synonym query expansion")
    ap.add_argument("--hf-token",   default=None)
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--results",    type=Path, default=RESULTS_PATH)
    args = ap.parse_args(argv)

    # ── build ─────────────────────────────────────────────────────────────────
    if not args.skip_build:
        try:
            from datasets import load_dataset
        except ImportError:
            print("ERROR: pip install datasets")
            return 1
        token = args.hf_token or __import__("os").environ.get("HF_TOKEN")
        print(f"Loading corpus from {DATASET_ID}…")
        corpus = load_dataset(DATASET_ID, CORPUS_CONFIG, split="test", token=token)
        print(f"  {len(corpus):,} passages → {args.db}")
        t0 = time.perf_counter()
        n  = build_legal_index(args.db, corpus)
        print(f"  Done: {n:,} passages in {time.perf_counter()-t0:.1f}s")
    else:
        token = args.hf_token or __import__("os").environ.get("HF_TOKEN")

    # ── load QA ───────────────────────────────────────────────────────────────
    try:
        from datasets import load_dataset
        print(f"\nLoading QA pairs…")
        qa = list(load_dataset(DATASET_ID, QA_CONFIG, split="test", token=token))
        print(f"  {len(qa)} questions")
    except Exception as exc:
        print(f"ERROR loading QA: {exc}")
        return 1

    # ── optional SPLADE ───────────────────────────────────────────────────────
    splade = None
    if args.splade:
        print(f"\nLoading SPLADE ({args.splade_model})…")
        splade = SPLADEReranker(args.splade_model)
        if not splade.load():
            print("  SPLADE load failed — running BM25 only")
            splade = None
        else:
            print("  SPLADE ready")
        if args.fts5_k <= 10:
            print(f"  TIP: --fts5-k 100 gives SPLADE more candidates to rerank")

    # ── run ───────────────────────────────────────────────────────────────────
    expand = not args.no_expand
    print(f"\nRunning benchmark (fts5-k={args.fts5_k}, final-k={args.k}, "
          f"expand={'yes' if expand else 'no'})…")
    data = run_benchmark(
        args.db, qa,
        fts5_k=args.fts5_k,
        final_k=args.k,
        splade=splade,
        expand=expand,
    )

    print_comparison(data, fts5_k=args.fts5_k)

    # strip per_question before saving summary
    save = {}
    for key, val in data.items():
        if val is None:
            continue
        pq = val.pop("per_question", [])
        save[key] = {**val, "n_per_question": len(pq)}
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(save, indent=2))
    print(f"\n  Results → {args.results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
