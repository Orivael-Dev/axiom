"""Dense bi-encoder + RRF fusion experiment on isaacus/legal-rag-bench.

Unlike SPLADE (a reranker that only reorders BM25's pool), a dense bi-encoder
is a FIRST-STAGE retriever: it can surface passages BM25 never retrieves —
the actual fix for the 58 semantic-gap misses.

Three tracks compared (top-100 candidates, reported at k=1/3/5/10):
  BM25   — FTS5/BM25 over /tmp/legal.db (via CVERetriever)
  Dense  — BAAI/bge-small-en-v1.5 cosine over the full corpus
  RRF    — Reciprocal Rank Fusion of BM25 + Dense

Corpus embeddings are cached to disk (bge_corpus.npz) so reruns are instant.

Run:
    python research/legal/legal_rag_dense.py --db /tmp/legal.db --hf-token $HF_TOKEN
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from axiom_cve_retriever import CVERetriever

DATASET_ID = "isaacus/legal-rag-bench"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
CACHE_NPZ  = Path(__file__).parent / "bge_corpus.npz"
RESULTS    = Path(__file__).parent / "results_dense.json"
# bge recommends a query instruction for retrieval; passages get none.
QUERY_INSTR = "Represent this sentence for searching relevant passages: "


# ── dense encoder (bge: CLS pooling + L2 normalize) ──────────────────────────

class BGEEncoder:
    def __init__(self, model_name=MODEL_NAME, max_length=512):
        from transformers import AutoTokenizer, AutoModel
        import torch
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModel.from_pretrained(model_name).eval()
        self._max_length = max_length

    def encode(self, texts, batch_size=32, show_every=0):
        import torch
        vecs = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            inp = self._tok(chunk, padding=True, truncation=True,
                            max_length=self._max_length, return_tensors="pt")
            with torch.no_grad():
                out = self._model(**inp)
                emb = out.last_hidden_state[:, 0]          # CLS token
                emb = torch.nn.functional.normalize(emb, p=2, dim=1)
            vecs.append(emb)
            if show_every and (i // batch_size) % show_every == 0:
                print(f"    encoded {min(i+batch_size, len(texts))}/{len(texts)}")
        return torch.cat(vecs).numpy()


# ── metrics + fusion ─────────────────────────────────────────────────────────

def rr(retrieved, gold):
    try:
        return 1.0 / (retrieved.index(gold) + 1)
    except ValueError:
        return 0.0


def aggregate(per_q, latency):
    n = len(per_q)
    hits = {c: sum(g in r[:c] for r, g in per_q) / n for c in (1, 3, 5, 10)}
    mrr = sum(rr(r, g) for r, g in per_q) / n
    lat = sorted(latency)
    return {
        "MRR": round(mrr, 4),
        "Hit@1": round(hits[1], 4), "Hit@3": round(hits[3], 4),
        "Hit@5": round(hits[5], 4), "Hit@10": round(hits[10], 4),
        "misses": sum(g not in r[:10] for r, g in per_q),
        "lat_ms_mean": round(sum(lat) / n, 2),
        "lat_ms_p95": round(lat[int(0.95 * n) - 1], 2),
    }


def rrf(rank_lists, k=60, depth=100):
    scores = {}
    for lst in rank_lists:
        for rank, pid in enumerate(lst[:depth]):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
    return [pid for pid, _ in sorted(scores.items(), key=lambda x: -x[1])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--hf-token", default=os.environ.get("HF_TOKEN"))
    ap.add_argument("--k", type=int, default=100, help="candidate depth per retriever")
    ap.add_argument("--rebuild-emb", action="store_true")
    ap.add_argument("--rewrite", action="store_true",
                    help="use SRD SLM to rewrite the BM25 query (top-rank precision)")
    ap.add_argument("--rewrite-model", default="qwen-srd-coder",
                    help="Ollama model for the rewriter")
    args = ap.parse_args()

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from datasets import load_dataset
    import numpy as np

    print("loading dataset...")
    corpus = list(load_dataset(DATASET_ID, "corpus", split="test", token=args.hf_token))
    qa = list(load_dataset(DATASET_ID, "qa", split="test", token=args.hf_token))
    ids = [str(r["id"]) for r in corpus]
    texts = [r.get("text") or "" for r in corpus]

    # ── corpus embeddings (cached) ──────────────────────────────────────────
    enc = None
    if CACHE_NPZ.exists() and not args.rebuild_emb:
        cached = np.load(CACHE_NPZ, allow_pickle=True)
        emb = cached["emb"]
        cached_ids = list(cached["ids"])
        if cached_ids == ids:
            print(f"loaded cached embeddings {emb.shape}")
        else:
            emb = None
    else:
        emb = None
    if emb is None:
        print(f"embedding {len(texts)} passages with {MODEL_NAME} (CPU, one-time)...")
        enc = BGEEncoder()
        t0 = time.time()
        emb = enc.encode(texts, batch_size=32, show_every=20)
        print(f"  embedded in {time.time()-t0:.1f}s -> caching")
        np.savez(CACHE_NPZ, emb=emb, ids=np.array(ids, dtype=object))

    if enc is None:
        enc = BGEEncoder()

    # ── retrievers ──────────────────────────────────────────────────────────
    bm25 = CVERetriever(args.db)
    K = args.k

    # Optional SRD query rewriter on the BM25 side (dense keeps the raw query).
    rewriter, rconn = None, None
    if args.rewrite:
        from axiom_event_token.backends import LocalNanoBackend
        from axiom_query_rewriter import QueryRewriter, LEGAL_SYSTEM_PROMPT
        rewriter = QueryRewriter(LocalNanoBackend(model=args.rewrite_model),
                                 system_prompt=LEGAL_SYSTEM_PROMPT)
        rconn = sqlite3.connect(args.db)
        print(f"BM25 query rewrite ON via {args.rewrite_model}")

    def bm25_ids(q):
        if rewriter is None:
            return [h.uri for h in bm25.retrieve(q, k=K)]
        match = rewriter.rewrite(q, domain="legal")
        if not match:
            return [h.uri for h in bm25.retrieve(q, k=K)]
        cur = rconn.execute(
            "SELECT cve_id FROM cve WHERE cve MATCH ? ORDER BY bm25(cve) LIMIT ?",
            (match, K),
        )
        return [r[0] for r in cur.fetchall()]

    bm_pq, dn_pq, rrf_pq = [], [], []
    bm_lat, dn_lat, rrf_lat = [], [], []

    for row in qa:
        q = row["question"]
        gold = str(row["relevant_passage_id"])

        # BM25 (optionally SRD-rewritten query)
        t = time.time()
        bm_ids = bm25_ids(q)
        bm_lat.append((time.time() - t) * 1000)

        # Dense
        t = time.time()
        qv = enc.encode([QUERY_INSTR + q])[0]
        sims = emb @ qv
        top = np.argsort(-sims)[:K]
        dn_ids = [ids[i] for i in top]
        dn_lat.append((time.time() - t) * 1000)

        # RRF
        t = time.time()
        fused = rrf([bm_ids, dn_ids], depth=K)
        rrf_lat.append((time.time() - t) * 1000 + bm_lat[-1] + dn_lat[-1])

        bm_pq.append((bm_ids, gold))
        dn_pq.append((dn_ids, gold))
        rrf_pq.append((fused, gold))

    out = {
        "BM25":  aggregate(bm_pq, bm_lat),
        "Dense": aggregate(dn_pq, dn_lat),
        "RRF":   aggregate(rrf_pq, rrf_lat),
    }
    RESULTS.write_text(json.dumps(out, indent=2), encoding="utf-8")

    # ── print ───────────────────────────────────────────────────────────────
    cols = ["BM25", "Dense", "RRF"]
    w = 12
    print("\n" + "=" * 56)
    print(f"  Dense bi-encoder + RRF — legal-rag-bench (k={K})")
    print("=" * 56)
    print(f"  {'Metric':<10}" + "".join(f"{c:>{w}}" for c in cols))
    print(f"  {'-'*10}" + "".join("-" * w for _ in cols))
    for m in ["MRR", "Hit@1", "Hit@3", "Hit@5", "Hit@10"]:
        print(f"  {m:<10}" + "".join(f"{out[c][m]:>{w}.3f}" for c in cols))
    print(f"  {'misses':<10}" + "".join(f"{out[c]['misses']:>{w}}" for c in cols))
    print(f"  {'lat ms':<10}" + "".join(f"{out[c]['lat_ms_mean']:>{w}.1f}" for c in cols))
    print(f"\nsaved -> {RESULTS}")


if __name__ == "__main__":
    main()
