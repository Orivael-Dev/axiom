"""Legal RAG Bench — FTS5 BM25 + query rewrite + optional SPLADE hybrid.

Baseline result (FTS5 BM25, k=10, 4876 passages, 100 questions):
  Hit@1  0.160   Hit@3  0.320   Hit@5  0.380   Hit@10  0.440
  MRR@10 0.251   Latency: 6.0ms avg / 11.7ms p95

Analysis:
  44/100 hits = the BM25 exact-token ceiling. These are questions
  where query vocabulary directly matches the passage.  The 56 misses
  are the semantic gap — correct passage exists but the question
  paraphrases the content using different vocabulary.

Three-tier hybrid strategy:
  1. Legal synonym expansion — free, zero latency (--expand, on by default)
  2. LLM query rewrite — SLM rewrites question into 3 legal-vocab variants,
     all tokens OR-joined for FTS5. Adds ~100ms per question. (--rewrite)
  3. SPLADE reranker — sparse neural 100→k rerank, closes remaining gap. (--splade)

The benchmark reports up to four columns:
  BM25        — FTS5 k=fts5_k baseline
  BM25+R      — with LLM query rewrite (--rewrite)
  BM25+SPLADE — with SPLADE rerank (--splade --fts5-k 100)
  BM25+R+S    — rewrite + SPLADE together (all flags)

Run — BM25 only:
    python3 research/legal/legal_rag_bench.py \\
      --db /tmp/legal.db --hf-token $HF_TOKEN

Run — BM25 + LLM rewrite (Ollama must be running):
    python3 research/legal/legal_rag_bench.py \\
      --db /tmp/legal.db --skip-build --rewrite \\
      --rewrite-model qwen2.5:0.5b

Run — BM25 + SPLADE (install transformers first):
    pip install transformers torch
    python3 research/legal/legal_rag_bench.py \\
      --db /tmp/legal.db --hf-token $HF_TOKEN \\
      --splade --fts5-k 100 --k 10

Run — full pipeline (rewrite + SPLADE):
    python3 research/legal/legal_rag_bench.py \\
      --db /tmp/legal.db --skip-build \\
      --rewrite --rewrite-model qwen2.5:0.5b \\
      --splade --fts5-k 100 --k 10

Run — skip build (use existing db):
    python3 research/legal/legal_rag_bench.py \\
      --db /tmp/legal.db --skip-build --splade --fts5-k 100
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axiom_cve_retriever import CVERetriever
from axiom_query_rewriter import QueryRewriter, LEGAL_SYSTEM_PROMPT


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
    rewriter: Optional[QueryRewriter] = None,
) -> Tuple[List[str], float]:
    """Return (ranked passage_ids, latency_ms).

    When ``rewriter`` is set, the SLM rewrites the question into 3 legal-vocab
    variants; all tokens are OR-joined into the FTS5 MATCH expression.  This
    replaces both synonym expansion and _match_for — the rewriter's output IS
    the MATCH expression.  Latency includes the SLM call time.
    """
    if rewriter is not None:
        t0    = time.perf_counter()
        match = rewriter.rewrite(question, domain="legal")
        if not match:
            return [], 0.0
        cur = conn.execute(
            "SELECT cve_id FROM cve WHERE cve MATCH ? ORDER BY bm25(cve) LIMIT ?",
            (match, k),
        )
        ids = [r[0] for r in cur.fetchall()]
        ms  = (time.perf_counter() - t0) * 1000
        return ids, ms

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


# ── Dense retriever (bi-encoder first-stage) ──────────────────────────────────

# BGE instruction prefix — boosts retrieval quality for asymmetric tasks.
# Applied only to queries, never to passages.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _dense_index_prefix(db_path: Path, model_name: str) -> Path:
    """Derive the prefix for .npy / .ids.json index files from the db path."""
    slug = model_name.replace("/", "_").replace("-", "_")
    return db_path.parent / f"{db_path.stem}_dense_{slug}"


class DenseRetriever:
    """Bi-encoder first-stage retriever using sentence-transformers.

    Encodes all passages at index time into a (N, dim) float32 matrix stored
    alongside the FTS5 db as two files:
        <db_stem>_dense_<model_slug>.npy        — embedding matrix
        <db_stem>_dense_<model_slug>.ids.json   — passage_id list (row order)

    Query time: one forward pass + matrix-vector dot product → top-k.
    At 4876 passages × 768 dims: <1 ms for the dot product, ~25 ms for encoding.
    """

    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5") -> None:
        self._model_name = model_name
        self._model      = None
        self._matrix     = None   # np.ndarray (N, dim) float32, L2-normalised
        self._ids: List[str] = []
        self._ready      = False
        self._is_bge     = "bge" in model_name.lower()

    def load_model(self) -> bool:
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            self._np    = np
            self._model = SentenceTransformer(self._model_name)
            self._ready = True
            return True
        except ImportError:
            print("  [Dense] pip install sentence-transformers")
            return False
        except Exception as exc:
            print(f"  [Dense] model load failed: {exc}")
            return False

    def build_index(
        self,
        passages: List[Tuple[str, str]],   # (passage_id, text)
        index_prefix: Path,
        *,
        batch_size: int = 128,
    ) -> None:
        """Encode passages and write .npy + .ids.json to disk."""
        texts = [t for _, t in passages]
        print(f"  Encoding {len(texts):,} passages with {self._model_name}…")
        embs = self._model.encode(
            texts, batch_size=batch_size,
            normalize_embeddings=True, show_progress_bar=True,
        )
        self._matrix = self._np.array(embs, dtype=self._np.float32)
        self._ids    = [pid for pid, _ in passages]
        self._np.save(str(index_prefix) + ".npy", self._matrix)
        Path(str(index_prefix) + ".ids.json").write_text(
            json.dumps(self._ids), encoding="utf-8"
        )
        print(f"  Dense index saved → {index_prefix}.npy  "
              f"({self._matrix.shape[0]:,} × {self._matrix.shape[1]})")

    def load_index(self, index_prefix: Path) -> bool:
        npy  = Path(str(index_prefix) + ".npy")
        ids  = Path(str(index_prefix) + ".ids.json")
        if not npy.exists() or not ids.exists():
            return False
        import numpy as np
        self._np     = np
        self._matrix = np.load(str(npy))
        self._ids    = json.loads(ids.read_text(encoding="utf-8"))
        return True

    def retrieve(self, question: str, *, k: int = 100) -> Tuple[List[str], float]:
        """Return (ranked passage_ids, latency_ms)."""
        if not self._ready or self._matrix is None:
            return [], 0.0
        query = (_BGE_QUERY_PREFIX + question) if self._is_bge else question
        t0     = time.perf_counter()
        q_emb  = self._model.encode(query, normalize_embeddings=True)
        scores = self._matrix @ q_emb
        top_k  = self._np.argsort(-scores)[:k]
        ms     = (time.perf_counter() - t0) * 1000
        return [self._ids[i] for i in top_k], ms


# ── RRF merge ─────────────────────────────────────────────────────────────────

def rrf_merge(
    *ranked_lists: List[str],
    k_rrf: int = 60,
    final_k: int = 10,
) -> List[str]:
    """Reciprocal Rank Fusion across any number of ranked lists.

    score(d) = Σ_i  1 / (k_rrf + rank_i(d) + 1)

    k_rrf=60 is the standard Robertson/Cormack default.  Results are
    sorted by descending RRF score; ties broken by order of first list.
    """
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, pid in enumerate(ranked):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k_rrf + rank + 1)
    return sorted(scores, key=lambda p: -scores[p])[:final_k]



# ── Cross-encoder reranker ────────────────────────────────────────────────────

class CrossEncoderReranker:
    """Cross-encoder joint query-passage scorer for final reranking.

    Pairs naturally with DenseRetriever: BGE family uses
    ``BAAI/bge-reranker-base`` (same pretraining, complementary scoring).

    Takes the RRF top-N candidates, scores each (query, passage) pair
    jointly, returns them reordered by relevance score.  This fixes the
    RRF rank-1 regression: RRF optimises recall, cross-encoder restores
    precision at the top slot.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        max_length: int = 512,
    ) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._model      = None
        self._ready      = False

    def load(self) -> bool:
        try:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(
                self._model_name,
                max_length=self._max_length,
            )
            self._ready = True
            return True
        except ImportError:
            print("  [CE] pip install sentence-transformers")
            return False
        except Exception as exc:
            print(f"  [CE] model load failed: {exc}")
            return False

    def rerank(
        self,
        question: str,
        candidates: List[Tuple[str, str]],   # (passage_id, passage_text)
        *,
        top_n: Optional[int] = None,
    ) -> Tuple[List[str], float]:
        """Return (reranked_ids, latency_ms).

        ``top_n`` caps the input list before scoring — pass 20 to score
        only the RRF top-20 rather than all k=100 candidates.
        Falls back to original order when model unavailable.
        """
        if not self._ready or not candidates:
            return [pid for pid, _ in candidates], 0.0
        pool = candidates[:top_n] if top_n else candidates
        pairs = [[question, txt] for _, txt in pool]
        t0     = time.perf_counter()
        scores = self._model.predict(pairs, show_progress_bar=False)
        ms     = (time.perf_counter() - t0) * 1000
        ranked = sorted(zip(scores, [pid for pid, _ in pool]),
                        reverse=True)
        return [pid for _, pid in ranked], round(ms, 2)


# ── metrics ───────────────────────────────────────────────────────────────────

def rr(retrieved: List[str], relevant: str) -> float:
    try:
        return 1.0 / (retrieved.index(relevant) + 1)
    except ValueError:
        return 0.0


def hit_at(retrieved: List[str], relevant: str, k: int) -> bool:
    return relevant in retrieved[:k]


# ── parent-child (section-level) helpers ───────────────────────────────────────
#
# Chunk ids are "<section>-c<N>-s<M>" (e.g. "4.13.2-c4-s2").  Many misses are
# "right section, wrong chunk" — the retriever surfaces a sibling chunk of the
# gold passage.  Parent-child retrieval (PDF §1 "Shared Subspace Embedding")
# retrieves on the granular child chunks, then collapses hits to their parent
# SECTION and returns the section.  Scoring at section granularity recovers the
# sibling-chunk misses at zero extra latency.

_CHUNK_SUFFIX_RE = re.compile(r"-c\d+-s\d+.*$")


def _parent_of(chunk_id: str) -> str:
    """Return the parent section id for a chunk id.

    "4.13.2-c4-s2" -> "4.13.2".  Idempotent: a bare section id (no chunk
    suffix) is returned unchanged, so this is safe to apply twice.
    """
    return _CHUNK_SUFFIX_RE.sub("", chunk_id)


def collapse_to_parents(retrieved: List[str], *, final_k: int = 10) -> List[str]:
    """Collapse a ranked chunk list to a ranked list of unique parent sections.

    Keeps first (best-ranked) occurrence of each section; truncates to final_k.
    """
    seen: List[str] = []
    for pid in retrieved:
        par = _parent_of(pid)
        if par not in seen:
            seen.append(par)
    return seen[:final_k]


# ── benchmark loop ────────────────────────────────────────────────────────────

def run_benchmark(
    db_path: Path,
    qa_rows: list,
    *,
    fts5_k: int = 10,
    final_k: int = 10,
    splade: Optional[SPLADEReranker] = None,
    rewriter: Optional[QueryRewriter] = None,
    dense: Optional[DenseRetriever] = None,
    cross_encoder: Optional[CrossEncoderReranker] = None,
    ce_top_n: int = 20,
    k_rrf: int = 60,
    expand: bool = True,
    parent_child: bool = False,
    pc_pool: int = 30,
) -> dict:
    retriever = CVERetriever(str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")

    # Pre-load passage text for rerankers that need raw text
    passage_text: Dict[str, str] = {}
    if splade is not None or cross_encoder is not None:
        cur = conn.execute("SELECT cve_id, answer FROM cve")
        passage_text = {r[0]: r[1] for r in cur.fetchall()}

    (bm25_results, rewrite_results, hybrid_results, hybrid_rewrite_results,
     dense_results, rrf_results, rrf_ce_results, srd_rrf_ce_results,
     bm25_pc_results, rrf_pc_results) = (
        [], [], [], [], [], [], [], [], [], []
    )

    for row in qa_rows:
        qid        = str(row.get("id", ""))
        question   = row.get("question", "")
        passage_id = str(row.get("relevant_passage_id", ""))

        # Track 1: plain BM25 (with optional synonym expansion)
        ids, fts5_ms = fts5_retrieve(conn, retriever, question,
                                     k=fts5_k, expand=expand)
        bm25_results.append({
            "id": qid, "relevant_id": passage_id,
            "retrieved": ids,
            "rr": rr(ids, passage_id),
            "fts5_ms": round(fts5_ms, 2),
        })

        # Track 2: LLM query rewrite → BM25 (includes SLM latency in ms)
        if rewriter is not None:
            r_ids, r_ms = fts5_retrieve(conn, retriever, question,
                                        k=fts5_k, expand=False, rewriter=rewriter)
            rewrite_results.append({
                "id": qid, "relevant_id": passage_id,
                "retrieved": r_ids,
                "rr": rr(r_ids, passage_id),
                "fts5_ms": round(r_ms, 2),
            })

        # Track 3: BM25 → SPLADE rerank
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

        # Track 4: rewrite → BM25 → SPLADE rerank
        if rewriter is not None and splade is not None:
            r_cands = [(pid, passage_text.get(pid, "")) for pid in r_ids]
            rs_ids, rs_ms = splade.rerank(question, r_cands)
            hybrid_rewrite_results.append({
                "id": qid, "relevant_id": passage_id,
                "retrieved": rs_ids,
                "rr": rr(rs_ids, passage_id),
                "fts5_ms":   round(r_ms, 2),
                "splade_ms": round(rs_ms, 2),
            })

        # Track 5: dense bi-encoder first-stage (no BM25)
        d_ids, dense_ms = (dense.retrieve(question, k=fts5_k)
                           if dense is not None else ([], 0.0))
        if dense is not None:
            dense_results.append({
                "id": qid, "relevant_id": passage_id,
                "retrieved": d_ids,
                "rr": rr(d_ids, passage_id),
                "dense_ms": round(dense_ms, 2),
            })

        # Track 6: BM25 + Dense RRF fusion
        if dense is not None:
            merged = rrf_merge(ids, d_ids, k_rrf=k_rrf, final_k=final_k)
            rrf_ms = round(fts5_ms + dense_ms, 2)
            rrf_results.append({
                "id": qid, "relevant_id": passage_id,
                "retrieved": merged,
                "rr": rr(merged, passage_id),
                "rrf_ms": rrf_ms,
            })

        # Track 7: BM25 + Dense RRF → cross-encoder rerank
        if dense is not None and cross_encoder is not None:
            # Use a wider pool (ce_top_n) before scoring — ensures the
            # correct passage is present even if RRF ranked it just outside
            # final_k.  We re-merge at final_k+ce_top_n then score.
            wide = rrf_merge(ids, d_ids, k_rrf=k_rrf, final_k=ce_top_n)
            ce_cands = [(pid, passage_text.get(pid, "")) for pid in wide]
            ce_ids, ce_ms = cross_encoder.rerank(question, ce_cands)
            ce_total_ms = round(fts5_ms + dense_ms + ce_ms, 2)
            rrf_ce_results.append({
                "id": qid, "relevant_id": passage_id,
                "retrieved": ce_ids[:final_k],
                "rr": rr(ce_ids[:final_k], passage_id),
                "ce_ms": ce_total_ms,
            })

        # Track 8: SRD rewrite + Dense RRF → cross-encoder rerank
        if rewriter is not None and dense is not None and cross_encoder is not None:
            wide_srd = rrf_merge(r_ids, d_ids, k_rrf=k_rrf, final_k=ce_top_n)
            srd_cands = [(pid, passage_text.get(pid, "")) for pid in wide_srd]
            srd_ce_ids, srd_ce_ms = cross_encoder.rerank(question, srd_cands)
            srd_ce_total_ms = round(r_ms + dense_ms + srd_ce_ms, 2)
            srd_rrf_ce_results.append({
                "id": qid, "relevant_id": passage_id,
                "retrieved": srd_ce_ids[:final_k],
                "rr": rr(srd_ce_ids[:final_k], passage_id),
                "ce_ms": srd_ce_total_ms,
            })

        # Track 9/10: parent-child retrieval (deeper child pool → collapse to
        # parent sections).  Scored at section granularity vs the gold's parent.
        # This is the production architecture: retrieve on granular chunks, then
        # return the parent section.  Recovers "right section, wrong chunk" misses.
        if parent_child:
            gold_parent = _parent_of(passage_id)

            # Wide BM25 pool → collapse to parents
            wide_ids, wide_ms = fts5_retrieve(conn, retriever, question,
                                              k=pc_pool, expand=expand)
            bm25_parents = collapse_to_parents(wide_ids, final_k=final_k)
            bm25_pc_results.append({
                "id": qid, "relevant_id": gold_parent,
                "retrieved": bm25_parents,
                "rr": rr(bm25_parents, gold_parent),
                "pc_ms": round(wide_ms, 2),
            })

            # Wide RRF pool (BM25 + dense) → collapse to parents
            if dense is not None:
                wide_d_ids, wide_d_ms = dense.retrieve(question, k=pc_pool)
                wide_merged = rrf_merge(wide_ids, wide_d_ids,
                                        k_rrf=k_rrf, final_k=pc_pool)
                rrf_parents = collapse_to_parents(wide_merged, final_k=final_k)
                rrf_pc_results.append({
                    "id": qid, "relevant_id": gold_parent,
                    "retrieved": rrf_parents,
                    "rr": rr(rrf_parents, gold_parent),
                    "pc_ms": round(wide_ms + wide_d_ms, 2),
                })

    conn.close()
    return {
        "bm25":           _aggregate(bm25_results,           final_k, latency_field="fts5_ms"),
        "rewrite":        _aggregate(rewrite_results,        final_k, latency_field="fts5_ms")
                          if rewrite_results else None,
        "hybrid":         _aggregate(hybrid_results,         final_k, latency_field="splade_ms")
                          if hybrid_results else None,
        "hybrid_rewrite": _aggregate(hybrid_rewrite_results, final_k, latency_field="splade_ms")
                          if hybrid_rewrite_results else None,
        "dense":          _aggregate(dense_results,          final_k, latency_field="dense_ms")
                          if dense_results else None,
        "rrf":            _aggregate(rrf_results,            final_k, latency_field="rrf_ms")
                          if rrf_results else None,
        "rrf_ce":         _aggregate(rrf_ce_results,         final_k, latency_field="ce_ms")
                          if rrf_ce_results else None,
        "srd_rrf_ce":     _aggregate(srd_rrf_ce_results,     final_k, latency_field="ce_ms")
                          if srd_rrf_ce_results else None,
        "bm25_pc":        _aggregate(bm25_pc_results,        final_k, latency_field="pc_ms")
                          if bm25_pc_results else None,
        "rrf_pc":         _aggregate(rrf_pc_results,         final_k, latency_field="pc_ms")
                          if rrf_pc_results else None,
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

    # Section-level (parent-child) scoring on the SAME top-k pool: collapse the
    # retrieved chunks to their parent sections and score against the gold's
    # parent.  This is a LOWER BOUND on parent-child recovery — it only counts
    # a section as found if one of its chunks already made the chunk-level top-k.
    # The dedicated *_pc tracks use a deeper child pool for the full effect.
    sec_rr = [rr(collapse_to_parents(r["retrieved"], final_k=10),
                 _parent_of(r["relevant_id"])) for r in results]
    mrr_sec = sum(sec_rr) / n
    hits_sec = {ks: sum(hit_at(collapse_to_parents(r["retrieved"], final_k=10),
                               _parent_of(r["relevant_id"]), ks)
                        for r in results) / n
                for ks in [1, 3, 5, 10]}
    misses_sec = sum(1 for r in results
                     if not hit_at(collapse_to_parents(r["retrieved"], final_k=10),
                                   _parent_of(r["relevant_id"]), k))
    return {
        "MRR":     round(mrr, 4),
        "Hit@1":   round(hits[1], 4),
        "Hit@3":   round(hits[3], 4),
        "Hit@5":   round(hits[5], 4),
        "Hit@10":  round(hits[10], 4),
        # Section-level (parent-child) variants — "right section, wrong chunk"
        # misses become hits here.
        "MRR_sec":    round(mrr_sec, 4),
        "Hit@1_sec":  round(hits_sec[1], 4),
        "Hit@3_sec":  round(hits_sec[3], 4),
        "Hit@5_sec":  round(hits_sec[5], 4),
        "Hit@10_sec": round(hits_sec[10], 4),
        "n_misses_sec": misses_sec,
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
    bm   = data["bm25"]
    rew  = data.get("rewrite")
    hyb  = data.get("hybrid")
    hyb_r = data.get("hybrid_rewrite")

    # Build column list from present tracks
    den  = data.get("dense")
    rrf  = data.get("rrf")

    col_data = [("BM25", bm)]
    if rew:
        col_data.append(("BM25+Rewrite", rew))
    if hyb:
        col_data.append(("BM25+SPLADE", hyb))
    if hyb_r:
        col_data.append(("BM25+R+SPLADE", hyb_r))
    rrf_ce  = data.get("rrf_ce")
    srd_ce  = data.get("srd_rrf_ce")

    bm25_pc = data.get("bm25_pc")
    rrf_pc  = data.get("rrf_pc")

    if den:
        col_data.append(("Dense", den))
    if rrf:
        col_data.append(("BM25+Dense RRF", rrf))
    if rrf_ce:
        col_data.append(("RRF+CrossEnc", rrf_ce))
    if srd_ce:
        col_data.append(("SRD+RRF+CE", srd_ce))
    if bm25_pc:
        col_data.append(("BM25 parent-child", bm25_pc))
    if rrf_pc:
        col_data.append(("RRF parent-child", rrf_pc))

    w = 16
    print()
    print(f"  Legal RAG Bench — FTS5 k={fts5_k} → final k=10")
    print()
    header = f"  {'Metric':<12}  " + "  ".join(f"{c:>{w}}" for c, _ in col_data)
    print(header)
    print(f"  {'-'*12}  " + "  ".join("-" * w for _ in col_data))
    for metric in ["MRR", "Hit@1", "Hit@3", "Hit@5", "Hit@10"]:
        row = f"  {metric:<12}"
        baseline_val = bm[metric]
        for i, (_, d) in enumerate(col_data):
            val = d[metric]
            cell = _pct(val)
            if i > 0:
                delta = val - baseline_val
                sign  = "+" if delta >= 0 else ""
                cell  = f"{_pct(val)} ({sign}{delta:.3f})"
            row += f"  {cell:>{w}}"
        print(row)

    # Misses row
    row = f"  {'Misses':<12}"
    for _, d in col_data:
        row += f"  {d['n_misses']:>{w}}"
    print(row)
    print()

    # Latency summary
    print(f"  Latency (FTS5)  mean={bm['latency']['mean']} ms  "
          f"p50={bm['latency']['p50']} ms  p95={bm['latency']['p95']} ms")
    if rew and rew.get("latency"):
        print(f"  Latency (Rewrite+FTS5)  mean={rew['latency']['mean']} ms  "
              f"p50={rew['latency']['p50']} ms  p95={rew['latency']['p95']} ms")
    if hyb and hyb.get("latency"):
        print(f"  Latency (SPLADE)  mean={hyb['latency']['mean']} ms  "
              f"p50={hyb['latency']['p50']} ms  p95={hyb['latency']['p95']} ms")
    if hyb_r and hyb_r.get("latency"):
        print(f"  Latency (R+SPLADE)  mean={hyb_r['latency']['mean']} ms  "
              f"p50={hyb_r['latency']['p50']} ms  p95={hyb_r['latency']['p95']} ms")
    if den and den.get("latency"):
        print(f"  Latency (Dense)  mean={den['latency']['mean']} ms  "
              f"p50={den['latency']['p50']} ms  p95={den['latency']['p95']} ms")
    if rrf and rrf.get("latency"):
        print(f"  Latency (BM25+Dense RRF)  mean={rrf['latency']['mean']} ms  "
              f"p50={rrf['latency']['p50']} ms  p95={rrf['latency']['p95']} ms")
    if rrf_ce and rrf_ce.get("latency"):
        print(f"  Latency (RRF+CrossEnc)  mean={rrf_ce['latency']['mean']} ms  "
              f"p50={rrf_ce['latency']['p50']} ms  p95={rrf_ce['latency']['p95']} ms")
    if srd_ce and srd_ce.get("latency"):
        print(f"  Latency (SRD+RRF+CE)  mean={srd_ce['latency']['mean']} ms  "
              f"p50={srd_ce['latency']['p50']} ms  p95={srd_ce['latency']['p95']} ms")
    if rrf_pc and rrf_pc.get("latency"):
        print(f"  Latency (RRF parent-child)  mean={rrf_pc['latency']['mean']} ms  "
              f"p50={rrf_pc['latency']['p50']} ms  p95={rrf_pc['latency']['p95']} ms")

    # Section-level (parent-child) recovery: how many chunk-level misses are
    # actually "right section, wrong chunk" — i.e. recovered for free by scoring
    # at section granularity on the SAME top-k pool.  This isolates the
    # chunking-granularity failures (Group B) from true routing failures.
    print()
    print("  Parent-child (section-level) recovery — same top-k pool:")
    print(f"  {'Track':<18}  {'chunk Hit@10':>14}  {'section Hit@10':>16}"
          f"  {'chunk miss':>12}  {'section miss':>13}  {'recovered':>10}")
    print("  " + "-" * 90)
    for label, d in col_data:
        if "Hit@10_sec" not in d:
            continue
        recovered = d.get("n_misses", 0) - d.get("n_misses_sec", 0)
        print(f"  {label:<18}  {_pct(d['Hit@10']):>14}  {_pct(d['Hit@10_sec']):>16}"
              f"  {d.get('n_misses', 0):>12}  {d.get('n_misses_sec', 0):>13}"
              f"  {recovered:>+10}")
    print("\n  'recovered' = chunk-level misses that ARE the right section "
          "(sibling-chunk hits).\n  These flip to hits when the retriever "
          "returns the parent section instead of the chunk.")

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
    ap.add_argument("--rewrite",    action="store_true",
                    help="Enable LLM query rewrite pass (Ollama must be running)")
    ap.add_argument("--rewrite-backend", default="local",
                    choices=["local", "nim", "deepseek", "custom"],
                    help="Backend for query rewriter (default: local/Ollama)")
    ap.add_argument("--rewrite-model", default=None,
                    help="Model for rewriter, e.g. qwen2.5:0.5b (Ollama) or "
                         "meta/llama-3.1-8b-instruct (NIM)")
    ap.add_argument("--rewrite-url",  default=None,
                    help="Ollama URL override for rewriter (default: http://localhost:11434)")
    ap.add_argument("--dense",       action="store_true",
                    help="Enable dense bi-encoder retrieval (requires sentence-transformers)")
    ap.add_argument("--dense-model", default="BAAI/bge-base-en-v1.5",
                    help="HuggingFace sentence-transformers model for dense index "
                         "(default: BAAI/bge-base-en-v1.5; also try thenlper/gte-base)")
    ap.add_argument("--rrf",         action="store_true",
                    help="Enable BM25 + Dense RRF fusion (implies --dense)")
    ap.add_argument("--rrf-k",       type=int, default=60,
                    help="RRF constant k (default 60)")
    ap.add_argument("--skip-dense-build", action="store_true",
                    help="Skip dense index build if .npy already exists")
    ap.add_argument("--cross-encoder",    action="store_true",
                    help="Enable cross-encoder rerank after RRF (implies --rrf + --dense)")
    ap.add_argument("--ce-model",         default="BAAI/bge-reranker-base",
                    help="Cross-encoder model (default: BAAI/bge-reranker-base)")
    ap.add_argument("--ce-top-n",         type=int, default=20,
                    help="Candidates fed to cross-encoder from RRF pool (default 20)")
    ap.add_argument("--parent-child",     action="store_true",
                    help="Enable parent-child tracks: retrieve a deeper child pool, "
                         "collapse to parent sections, score at section granularity")
    ap.add_argument("--pc-pool",          type=int, default=30,
                    help="Child chunk pool depth before collapsing to parents "
                         "(default 30; needs > final-k to fill k unique sections)")
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

    # ── optional query rewriter ───────────────────────────────────────────────
    rewriter = None
    if args.rewrite:
        print(f"\nLoading query rewriter ({args.rewrite_backend})…")
        try:
            import os
            from axiom_event_token.backends import (
                LocalNanoBackend, NIMBackend, DeepSeekBackend, CustomBackend,
            )
            _backend_map = {
                "local":    lambda: LocalNanoBackend(
                                model=args.rewrite_model or os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
                                url=args.rewrite_url or os.environ.get("OLLAMA_URL", "http://localhost:11434"),
                            ),
                "nim":      lambda: NIMBackend(model=args.rewrite_model) if not args.rewrite_model
                                    else NIMBackend(model=args.rewrite_model),
                "deepseek": lambda: DeepSeekBackend(model=args.rewrite_model) if args.rewrite_model
                                    else DeepSeekBackend(),
                "custom":   lambda: CustomBackend(model=args.rewrite_model) if args.rewrite_model
                                    else CustomBackend(),
            }
            backend  = _backend_map[args.rewrite_backend]()
            rewriter = QueryRewriter(backend, system_prompt=LEGAL_SYSTEM_PROMPT)
            # Quick connectivity check — rewrite one dummy question
            _ = rewriter.rewrite("test query", domain="legal")
            print(f"  Rewriter ready ({args.rewrite_backend})")
        except Exception as exc:
            print(f"  Rewriter unavailable: {exc} — running without rewrite")
            rewriter = None

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
    # ── optional dense retriever (bi-encoder first-stage + RRF) ──────────────
    dense_retriever = None
    use_dense = args.dense or args.rrf or args.cross_encoder
    if use_dense:
        print(f"\nLoading dense model ({args.dense_model})…")
        dense_retriever = DenseRetriever(args.dense_model)
        if not dense_retriever.load_model():
            print("  Dense model load failed — running without dense")
            dense_retriever = None
        else:
            index_prefix = _dense_index_prefix(args.db, args.dense_model)
            loaded = False
            if args.skip_dense_build:
                loaded = dense_retriever.load_index(index_prefix)
                if loaded:
                    print(f"  Dense index loaded from {index_prefix}.npy  "
                          f"({len(dense_retriever._ids):,} passages)")
            if not loaded:
                # Build from the FTS5 db — pull all (id, text) pairs
                conn_tmp = sqlite3.connect(str(args.db))
                passages = [(r[0], r[1]) for r in
                            conn_tmp.execute("SELECT cve_id, answer FROM cve").fetchall()]
                conn_tmp.close()
                t0 = time.perf_counter()
                dense_retriever.build_index(passages, index_prefix)
                print(f"  Dense index built in {time.perf_counter()-t0:.1f}s")
            print("  Dense retriever ready")

    # ── optional cross-encoder reranker ───────────────────────────────────────
    cross_encoder = None
    if args.cross_encoder:
        print(f"\nLoading cross-encoder ({args.ce_model})…")
        cross_encoder = CrossEncoderReranker(args.ce_model)
        if not cross_encoder.load():
            print("  Cross-encoder load failed — running without CE")
            cross_encoder = None
        else:
            print(f"  Cross-encoder ready (top-n={args.ce_top_n})")

    # ── run ───────────────────────────────────────────────────────────────────
    expand = not args.no_expand
    tracks = []
    if expand:
        tracks.append("synonym-expand")
    if rewriter:
        tracks.append("llm-rewrite")
    if splade:
        tracks.append("splade")
    if dense_retriever:
        tracks.append(f"dense({args.dense_model.split('/')[-1]})")
    if args.rrf or args.cross_encoder:
        tracks.append(f"rrf(k={args.rrf_k})")
    if cross_encoder:
        tracks.append(f"ce({args.ce_model.split('/')[-1]},top{args.ce_top_n})")
    if args.parent_child:
        tracks.append(f"parent-child(pool={args.pc_pool})")
    print(f"\nRunning benchmark (fts5-k={args.fts5_k}, final-k={args.k}, "
          f"tracks=[{', '.join(tracks) or 'bm25-only'}])…")
    data = run_benchmark(
        args.db, qa,
        fts5_k=args.fts5_k,
        final_k=args.k,
        splade=splade,
        rewriter=rewriter,
        dense=dense_retriever,
        cross_encoder=cross_encoder,
        ce_top_n=args.ce_top_n,
        k_rrf=args.rrf_k,
        expand=expand,
        parent_child=args.parent_child,
        pc_pool=args.pc_pool,
    )

    print_comparison(data, fts5_k=args.fts5_k)

    # strip per_question before saving summary
    save = {}
    for key, val in data.items():
        if val is None:
            continue
        v  = dict(val)
        pq = v.pop("per_question", [])
        save[key] = {**v, "n_per_question": len(pq)}
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(save, indent=2))
    print(f"\n  Results → {args.results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
