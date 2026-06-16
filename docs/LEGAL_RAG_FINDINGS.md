# Legal RAG retrieval study — findings

Benchmark: `isaacus/legal-rag-bench` — 4,876 legal passages, 100 scenario QA
pairs, **one gold passage per query**. Metrics: Hit@k (gold in top-k), MRR,
miss count (gold not in top-10), latency. All inference local; only dataset +
model weights downloaded from HuggingFace.

## Final scorecard

| Configuration | MRR | Hit@1 | Hit@10 | Misses | Latency |
|---|---|---|---|---|---|
| BM25 baseline | 0.275 | 0.190 | 0.420 | 58 | 15 ms |
| + stock Qwen rewrite (`qwen2.5:0.5b`) | 0.266 | 0.180 | 0.410 | 59 | 2,850 ms |
| + SPLADE v3 rerank | 0.232 | 0.150 | 0.410 | 59 | 9,200 ms |
| + static title/metadata token | 0.268 | 0.180 | 0.410 | 59 | 6 ms |
| + SRD rewrite (`qwen-srd-coder`, k=10) | 0.284 | **0.220** | 0.420 | 58 | 2,866 ms |
| + Dense RRF (bge-small) | 0.288 | 0.200 | 0.470 | 53 | 44 ms |
| **+ SRD rewrite + Dense RRF** | **0.297** | 0.190 | **0.480** | **52** | 2,822 ms |

## What worked

- **Dense first-stage fusion (RRF).** A dense bi-encoder (`bge-small-en-v1.5`)
  is weak *alone* (MRR 0.186) but, fused with BM25 via Reciprocal Rank Fusion,
  lifts recall: Hit@10 0.420→0.470, misses 58→53, at 44 ms. It surfaces
  passages BM25 never retrieves — the actual fix for the semantic gap.
- **SRD-trained query rewrite.** The locally fine-tuned
  `qwen25_coder_0p5b_srd4_q4km` is the *only* LLM rewrite that helped
  (Hit@1 0.190→0.220, Hit@3 0.300→0.340, MRR +0.018). It improves top-rank
  precision when the gold passage is already retrievable.
- **Best overall:** SRD rewrite + dense RRF — MRR 0.297, Hit@10 0.480,
  misses 52 (best on all three).

## What didn't

- **SPLADE rerank** (−0.043 MRR) and **stock-Qwen rewrite** (−0.009 MRR) both
  *hurt*. Rerankers only reorder BM25's pool, so they cannot fix recall — and
  on scenario queries they demoted correct top hits.
- **Static title / common-vocab token** (−0.007 MRR). Uniform/common tokens
  have IDF≈0 (no ranking signal); section titles re-add low-IDF generic terms
  that already appear in the body. Confirmed empirically and by IDF theory.

## Key insight

Two failure modes need two different fixes:
- **Recall** (gold not retrieved at all — 58 of 100): only a *first-stage*
  dense/hybrid retriever helps. Rerankers are structurally powerless here.
- **Precision** (gold retrieved but ranked low): the SRD-trained rewrite helps;
  a generic SLM does not — the added training is what makes the difference.

## Practical recommendation

- **Production:** plain **Dense RRF** — 0.288 MRR / 0.470 Hit@10 at **44 ms**.
  Captures ~all the recall gain. The SRD rewrite adds ~1 question of quality
  for 64× the latency (2,822 ms), so reserve it for offline/max-quality runs.
- **Max quality (offline):** SRD rewrite + Dense RRF.

## Reproduce

```bash
export HF_TOKEN=...                      # legal-rag-bench is public; token optional
# BM25 / rewrite / SPLADE tracks:
python research/legal/legal_rag_bench.py --db /tmp/legal.db --k 10
python research/legal/legal_rag_bench.py --db /tmp/legal.db --skip-build \
    --rewrite --rewrite-model qwen-srd-coder          # SRD via Ollama
# Dense + RRF (+ optional SRD rewrite on BM25 side):
python research/legal/legal_rag_dense.py  --db /tmp/legal.db
python research/legal/legal_rag_dense.py  --db /tmp/legal.db --rewrite
```

The SRD model is imported into Ollama from the GGUF via a Modelfile
(`FROM .../qwen25_coder_0p5b_srd4_q4km.gguf`). Corpus embeddings cache to
`research/legal/bge_corpus.npz` (gitignored; ~7 MB, regenerated on first run).
