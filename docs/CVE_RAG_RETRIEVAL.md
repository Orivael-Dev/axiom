# Scalable BM25 retrieval + CVE RAG

This change set adds two retrieval backends and an end-to-end RAG demo that
grounds a local quantized model on a 297k-row CVE corpus.

## What changed

| File | Status | Purpose |
|------|--------|---------|
| `axiom_research_retriever.py` | modified | Added `LocalRetriever.add_documents()` + `merge_delta()` — append docs to the live in-memory BM25 index without a full rebuild. |
| `axiom_datasheet_ingester.py` | new | Incremental file ingester (PDF/txt/md/rst → chunked → cached → `add_documents`). Idempotent by mtime. CLI: `ingest` / `watch` / `stats`. |
| `axiom_cve_retriever.py` | new | **SQLite FTS5** retriever — on-disk inverted index + BM25 ranking. Drop-in for `LocalRetriever` (same `retrieve()` / `RetrievedSource`). Scales to 297k+ docs. |
| `research/rag_demo_cve.py` | new | End-to-end: CVE query → FTS5 retrieve → Qwen 0.5B GGUF grounded answer. |
| `research/rag_demo_qwen05b.py` | new | RAG demo over the deduped 14-row knowledge base distilled from the coding-assistant dataset. |
| `tests/test_axiom_datasheet_ingester.py` | new | 20 tests covering the ingester + `add_documents`. |

## Why FTS5

The pure-Python `LocalRetriever` scores **every** document per query (O(N)),
which is fine for the datasheet use case (≤10k docs) but does not scale. A
feasibility probe on 20k of the 297k CVEs, extrapolated to full scale:

| Metric | Pure-Python BM25 (297k) | **FTS5 (297k, measured)** | Gain |
|--------|-------------------------|---------------------------|------|
| Build time | ~43 min | **24.2 s** | ~107× |
| Resident RAM | ~2.45 GB | **8 MB** | ~300× |
| Query — CVE-ID | ~2,375 ms | **~1 ms** | ~2,000× |
| Query — free-text | ~2,375 ms | **~50 ms** | ~47× |
| Disk | — | 669 MB | — |

Query latency is the deciding factor: O(N) scan made the in-memory backend
unusable at this scale. FTS5's inverted index makes it interactive.

### Query optimizations in `axiom_cve_retriever.py`

1. **CVE-ID routing** — a query naming a CVE (`CVE-2021-44228`) is matched
   exact, column-scoped (`cve_id:2021 AND cve_id:44228`) → sub-millisecond.
2. **Common-token pruning** — tokens appearing in >30% of docs (e.g. `cve`,
   `affected`, digits) are dropped from free-text queries via an
   `fts5vocab` shadow table, so FTS5 ranks a small candidate set. Cuts
   free-text latency roughly in half. Average warm latency: 146 ms → **35 ms**.

## End-to-end RAG result (measured)

Query: *"what is the log4j CVE-2021-44228 vulnerability and how do I fix it"*

- Retrieved CVE-2021-44228 (score 1.000) in **3.18 ms**
- Qwen2.5-Coder-0.5B SRD-4 Q4_K_M loaded in 0.5 s
- Generated 185 tokens in 5.9 s (31.3 tok/s, CPU)
- Answer correct on every detail (affected versions 2.0-beta9–2.15.0 excluding
  2.12.2/2.12.3/2.3.1, JNDI/LDAP RCE, removed in 2.15.0) — grounded on the
  retrieved record, not parametric memory.

## Dataset triage (context)

Three datasets were analyzed before choosing the RAG target:

| Dataset | Rows | Unique outputs | Verdict |
|---------|------|----------------|---------|
| `dataset.jsonl` (coding assistant) | 1,100,000 | **14** | Templated; distilled to 14-row KB. |
| `claude_mythos_distilled_25k.jsonl` | 25,000 | **214** | ~117× redundant + uniform boilerplate preamble. |
| `all_cve_database.jsonl` | 297,441 | **297,441** | Genuinely diverse, grounded — the RAG target. |

## Usage

```bash
# Build the CVE index once (~24 s)
python -m axiom_cve_retriever build \
    --jsonl I:/Orivael/dataset/all_cve_database.jsonl \
    --db    I:/Orivael/dataset/cve_fts5.db

# Query
python -m axiom_cve_retriever query --db I:/Orivael/dataset/cve_fts5.db \
    "log4j remote code execution"

# Full RAG with the model
python research/rag_demo_cve.py "what is CVE-2021-44228"
```

The 669 MB `cve_fts5.db` lives alongside the dataset, outside the repo.
`llama-cpp-python` (0.3.30) is required for the generation step.
