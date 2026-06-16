# High-Precision Edge RAG — Deployment Guide

**Orivael Inc. · Confidential · June 2026**

---

## What this system is

Axiom's Edge RAG pipeline answers technical identifier queries — CVE numbers,
error codes, OBD faults, ICD codes, regulatory references — with **exact,
verifiable answers** sourced from a local SQLite FTS5 index. No GPU, no
embedding model, no cloud egress required.

The architecture is three layers:

| Layer | What it does | Latency |
|---|---|---|
| **Constitutional Answer Cache** | Frozen verified answers; bypasses retrieval entirely | ~0 ms |
| **FTS5 / BM25 index** | Lexical search over your knowledge corpus | ~3 ms |
| **LLM grounding** (optional) | Small model reads retrieved context; no hallucination on identifiers | ~2–6 s |

The cache warms up over time. High-frequency queries (top CVEs, common faults)
get instant responses; rare identifiers still hit FTS5 at 3 ms. Both paths
return the same answer — the cache just removes the FTS5 round-trip after
the answer has been verified N times.

**The correct framing for regulated markets:** this is not a fast search
engine. It is a **constitutional truth oracle**. FTS5 with column-scoped
matching either returns the exact row for `CVE-2021-44228` or returns nothing.
It cannot return a semantically similar CVE the way a vector model might.
The grounding score improvement (0.062 → 0.806) and correctness improvement
(0.000 → 1.000) measured in the technical brief are a qualitative difference
in reliability, not just a speed difference. For air-gapped, safety-critical,
or regulated deployments, "close enough" is not acceptable.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10 + | CPython's `sqlite3` ships with FTS5 enabled |
| `axiom-constitutional` | 1.8.8 + | `pip install axiom-constitutional` |
| `AXIOM_MASTER_KEY` | 64 hex chars | Derives all HMAC signing keys |
| Disk | ≥ 2× corpus size | FTS5 index ≈ 1.2× raw text; WAL overhead ≈ 0.2× |
| RAM | 256 MB + | No HNSW graph; only active query rows in memory |

Generate and export your master key once per deployment:

```bash
export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
# Add to /etc/environment or your secrets manager — never commit it.
```

Install:

```bash
pip install axiom-constitutional
# or in an air-gapped environment:
pip install axiom-constitutional --no-index --find-links /mnt/packages/
```

---

## Quick start — single CVE shard (5 minutes)

### 1. Build the FTS5 index

```bash
# Build from a JSONL file of CVE Q&A pairs.
# Row format: {"User": "what is CVE-2021-44228?", "Assistant": "Log4Shell..."}
python3 -m axiom_cve_retriever build \
  --jsonl /data/all_cve_database.jsonl \
  --db    /data/cve_fts5.db
```

This is a one-time operation (~2 min for 300k CVEs). The resulting `.db` file
is self-contained — no server process needed to serve queries.

### 2. Start the research server

```bash
export AXIOM_MASTER_KEY=<your-64-hex-key>
export AXIOM_CVE_DB_PATH=/data/cve_fts5.db          # activates CVE shard

python3 -m axiom_research_server
# → http://127.0.0.1:8765
```

### 3. Test a query

```bash
curl -s http://127.0.0.1:8765/api/research \
  -H "Content-Type: application/json" \
  -d '{"query": "what is CVE-2021-44228 and how do I fix it?", "domain": "security"}' \
  | python3 -m json.tool
```

Expected response includes `"provider": "cve-fts5"` on the first call and
`"provider": "cve-cache"` after N verified hits (default N = 5).

---

## Tier 1 — Constitutional Answer Cache

The cache (ORVL-028) sits on top of the FTS5 index. After a query is answered
correctly N times, the answer is frozen in a SQLite cache table with an HMAC
signature. Future calls return from cache in ~0 ms; the FTS5 index and LLM
are not consulted.

**Env vars:**

```bash
AXIOM_CVE_DB_PATH=/data/cve_fts5.db         # FTS5 index
AXIOM_CVE_CACHE_PATH=/data/cve.cache.db     # verified answer cache
                                              # (default: <db>.cache.db)
```

**How to verify an answer and promote it to cache:**

```python
from axiom_cve_retriever import CVERetriever, CachedCVERetriever
from axiom_verified_answer_cache import VerifiedAnswerCache

cache = VerifiedAnswerCache(db_path="/data/cve.cache.db")
r = CachedCVERetriever(CVERetriever("/data/cve_fts5.db"), cache)

answer, from_cache = r.answer("what is CVE-2021-44228?")
if not from_cache and answer:
    r.verify("what is CVE-2021-44228?")   # call 5× → promotes to hot path
```

**Live ingestion without rebuild:**

NVD publishes ~50 new CVEs/day. Use the background watcher to keep the index
current with zero maintenance windows:

```python
from pathlib import Path
from axiom_cve_retriever import CVERetriever
from axiom_nvd_ingester import NVDIngester

r = CVERetriever("/data/cve_fts5.db")
ing = NVDIngester(r, poll_s=30)
ing.tail_jsonl(Path("/data/nvd_updates.jsonl"))   # background daemon thread
# New rows from NVD RSS → append to JSONL → picked up within 30 s
# Concurrent queries never block — WAL mode keeps readers and writers separate
```

---

## Tier 2 — Multi-domain shards

The identical FTS5 schema works for any identifier-heavy knowledge domain. Each
domain is a separate `.db` file; the shard router dispatches by pattern.

| Domain | Identifier pattern | Example use case |
|---|---|---|
| `cve` | `CVE-YEAR-N` | Security vulnerability database |
| `bugs` | `BUG-N` | Internal engineering bug reports |
| `errors` | `ERR-*`, `FAULT-*` | Industrial / OT error codes |
| `obd` | `P0000` | Automotive OBD-II diagnostic codes |
| `medical` | `ICD-*` | Clinical decision support |
| `regulatory` | `FINRA`, `GDPR`, `ISO` | Compliance agent |
| `runbooks` | `ECONNRESET`, `SIGABRT` | Software ops runbooks |
| `tsb` | `TSB-NN-NNN` | Technical service bulletins |

**Build a shard for any domain:**

```bash
# Same build command — just point at the right JSONL and a new db path.
python3 -m axiom_cve_retriever build \
  --jsonl /data/obd_codes.jsonl \
  --db    /data/obd_fts5.db

python3 -m axiom_cve_retriever build \
  --jsonl /data/runbooks.jsonl \
  --db    /data/runbooks_fts5.db
```

**Start with multiple shards:**

```bash
export AXIOM_SHARD_CVE=/data/cve_fts5.db
export AXIOM_SHARD_OBD=/data/obd_fts5.db
export AXIOM_SHARD_RUNBOOKS=/data/runbooks_fts5.db
# Optional: override cache paths per shard
export AXIOM_SHARD_CVE_CACHE=/data/cve.cache.db

python3 -m axiom_research_server
```

The router detects identifiers automatically:
- `"what is CVE-2021-44228?"` → CVE shard (exact hit or nothing)
- `"P0301 misfire cylinder 1"` → OBD shard
- `"ECONNRESET retry backoff"` → runbooks shard
- `"what causes high CPU?"` → parallel fan-out across all shards, results merged by BM25 rank

---

## Tier 3 — ShardRouter, RAG bundle, SPLADE

### Federated shard routing

The `ShardRouter` is wired automatically when shard env vars are set.
For programmatic use:

```python
from axiom_cve_retriever import CVERetriever, CachedCVERetriever
from axiom_verified_answer_cache import VerifiedAnswerCache
from axiom_shard_router import ShardRouter, ShardConfig, DEFAULT_SHARD_PATTERNS

cve = CachedCVERetriever(CVERetriever("/data/cve.db"),
                          VerifiedAnswerCache("/data/cve.cache.db"))
obd = CachedCVERetriever(CVERetriever("/data/obd.db"),
                          VerifiedAnswerCache("/data/obd.cache.db"))

router = ShardRouter([
    ShardConfig("cve", DEFAULT_SHARD_PATTERNS["cve"], cve),
    ShardConfig("obd", DEFAULT_SHARD_PATTERNS["obd"], obd),
])

# Identifier query → single shard, cache-aware
hits = router.query("CVE-2021-44228 log4j")

# Free-text query → all shards in parallel, merged by BM25 rank
hits = router.query("remote code execution via deserialization")

# Optional SPLADE second-pass reranker (CPU, ~20 ms, no GPU)
from axiom_splade_reranker import SPLADEReranker
reranker = SPLADEReranker()   # loads naver/splade-v3 on first call
hits = router.query("JNDI injection architecture", reranker=reranker)
```

### Packaging for appliance deployment (RAG Bundle)

Package shards, caches, and their supply-chain proof into a single signed
artifact that ships to any edge device:

```bash
# Pack shards into a signed bundle
axm index-pack \
  --shard cve:/data/cve_fts5.db \
  --shard bugs:/data/bugs_fts5.db \
  --shard runbooks:/data/runbooks_fts5.db \
  --output /artifacts/orivael_rag_v1.rag.axm

# Output:
# {
#   "fingerprint": "a3f8...",
#   "output": "/artifacts/orivael_rag_v1.rag.axm",
#   "size_bytes": 42000000,
#   "shards": ["cve", "bugs", "runbooks"]
# }
```

**Verify before deployment** (on the receiving device):

```bash
axm index-verify /artifacts/orivael_rag_v1.rag.axm

# Output on success:
# {"verified": true, "fingerprint": "a3f8...", "shards": ["cve", "bugs", "runbooks"]}

# Output on tampered file:
# {"verified": false, "error": "file hash mismatch", "details": ["shards/cve.db: sha256 mismatch..."]}
```

**Deploy to edge device:**

```bash
# Unpack and verify in one step
axm index-unpack /artifacts/orivael_rag_v1.rag.axm --dest /opt/axiom/shards

# Then start the server pointing at the bundle
export AXIOM_MASTER_KEY=<key>
export AXIOM_RAG_BUNDLE=/artifacts/orivael_rag_v1.rag.axm
python3 -m axiom_research_server
```

The bundle format is a zip archive containing:
- `rag_manifest.json` — HMAC-SHA256 signed shard manifest
- `shards/<domain>.db` — FTS5 index for each domain
- `shards/<domain>.cache.db` — verified answer cache (if included at pack time)

Any bit-flip in any db file, or any change to the manifest, breaks verification
and the server refuses to start. This is the cryptographic provenance story:
the fingerprint is a public commitment to exactly these weights and indexes.

### SPLADE semantic re-ranker (optional)

SPLADE runs on CPU with no GPU and requires no dense embedding model. It is a
sparse transformer that expands query and document representations, then
re-ranks the FTS5 top-20 by dot product. Install the extra:

```bash
pip install transformers torch
```

The reranker degrades transparently to identity (original FTS5 order) if
`transformers` or the model is unavailable — no code changes needed.

```bash
# Override the model path for offline appliances
export AXIOM_SPLADE_LOCAL_PATH=/opt/models/splade-v3

# Or override the model name
export AXIOM_SPLADE_MODEL=naver/splade-v3
```

---

## Research console

The server ships a web console at `http://127.0.0.1:8765/`. It shows:

- Live retrieval sources (FTS5 or cache hit, latency, shard name)
- QRF branch reasoning for supported domains
- Exoskeleton ledger (last N calls, latency, verified status)

To expose to your network:

```bash
export AXIOM_RESEARCH_HOST=0.0.0.0
export AXIOM_RESEARCH_PORT=8765
export AXIOM_RESEARCH_TOKEN=<random-token>   # enables Bearer auth
python3 -m axiom_research_server
```

---

## Environment variable reference

| Variable | Default | Description |
|---|---|---|
| `AXIOM_MASTER_KEY` | *(required)* | 64-hex HMAC master key — derives all signing keys |
| `AXIOM_CVE_DB_PATH` | *(unset)* | Path to `cve_fts5.db` — activates CVE shard (legacy single-shard) |
| `AXIOM_CVE_CACHE_PATH` | `<db>.cache.db` | Override cache path for legacy single-CVE mode |
| `AXIOM_SHARD_<DOMAIN>` | *(unset)* | Path to FTS5 db for `domain` (CVE, BUGS, ERRORS, OBD, MEDICAL, RUNBOOKS, REGULATORY, TSB) |
| `AXIOM_SHARD_<DOMAIN>_CACHE` | `<db>.cache.db` | Override cache path per shard |
| `AXIOM_RAG_BUNDLE` | *(unset)* | Path to a `.rag.axm` bundle — loads all shards from bundle (takes priority over `AXIOM_SHARD_*`) |
| `AXIOM_RESEARCH_HOST` | `127.0.0.1` | Bind address |
| `AXIOM_RESEARCH_PORT` | `8765` | Bind port |
| `AXIOM_RESEARCH_TOKEN` | *(unset)* | Bearer token — when set, all API calls require `Authorization: Bearer <token>` |
| `AXIOM_RESEARCH_CORS_ORIGINS` | *(unset)* | Comma-separated CORS origins, or `*` |
| `AXIOM_EXTERNAL_RETRIEVAL` | `1` | Set `0` to disable PubMed / ClinicalTrials / Wikipedia fan-out (air-gapped mode) |
| `AXIOM_SPLADE_MODEL` | `naver/splade-v3` | SPLADE model id or local path |
| `AXIOM_SPLADE_LOCAL_PATH` | *(unset)* | Local directory for offline SPLADE model |
| `AXIOM_EXOSKELETON_LEDGER` | `~/.axiom/ledger.jsonl` | Path to the HMAC-signed call ledger |

---

## Hardware requirements

| Deployment | RAM | Disk | Notes |
|---|---|---|---|
| Single CVE shard (300k CVEs) | 256 MB | 1.5 GB | FTS5 db ~1.1 GB; index on NVMe recommended |
| 5-shard deployment | 512 MB | 8 GB | One connection per shard; WAL files add ~200 MB |
| Jetson Orin Nano 8 GB | 8 GB | 16 GB NVMe | Full multi-shard + Qwen 0.5B inference |
| Raspberry Pi 5 (8 GB) | 8 GB | 32 GB SD/NVMe | Single shard + small model; disable SPLADE |
| Air-gapped rack server | 16 GB + | 500 GB + | Full corpus + SPLADE + multi-model routing |

FTS5 stores only the inverted index plus the original text. It does **not**
require the corpus in RAM — the OS page cache handles hot rows automatically.
A 300k-CVE index at peak load uses ~80 MB RSS.

---

## Security notes

**AXIOM_MASTER_KEY** — treat as a root credential. It derives every HMAC key
in the system including the RAG bundle signature key, the answer cache signing
key, and the exoskeleton ledger key. Rotate it by:
1. Re-packing the RAG bundle with the new key (`axm index-pack`)
2. Clearing the verified answer cache (old HMAC signatures won't verify)
3. Restarting the server with the new key

**Zero egress** — set `AXIOM_EXTERNAL_RETRIEVAL=0` to disable all outbound
HTTP. In this mode the server returns only from local FTS5 shards and the
verified answer cache. The research console still functions; the provider
column will show `cve-fts5` or `cve-cache` only.

**Bearer token** — always set `AXIOM_RESEARCH_TOKEN` when `AXIOM_RESEARCH_HOST`
is not `127.0.0.1`. The server logs a warning at startup if it binds to a
non-loopback address without a token.

**Bundle verification** — run `axm index-verify` as a pre-flight check before
starting the server in production. Consider adding it to your systemd unit's
`ExecStartPre` or your container's healthcheck.

---

## Systemd unit example

```ini
[Unit]
Description=Orivael Edge RAG Server
After=network.target

[Service]
Type=simple
User=axiom
EnvironmentFile=/etc/axiom/env
ExecStartPre=/usr/local/bin/axm index-verify /opt/axiom/rag_bundle.rag.axm
ExecStart=/usr/local/bin/python3 -m axiom_research_server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/axiom/env`:

```
AXIOM_MASTER_KEY=<64-hex-key>
AXIOM_RAG_BUNDLE=/opt/axiom/rag_bundle.rag.axm
AXIOM_RESEARCH_HOST=0.0.0.0
AXIOM_RESEARCH_PORT=8765
AXIOM_RESEARCH_TOKEN=<random-bearer-token>
AXIOM_EXTERNAL_RETRIEVAL=0
```

---

## Updating the corpus

**Adding new CVEs (online appliance):**

```bash
# Download today's NVD feed as JSONL, append to the update file.
# The NVDIngester background thread picks it up within poll_s seconds.
cat /data/nvd_today.jsonl >> /data/nvd_updates.jsonl
```

**Replacing a shard (offline appliance):**

```bash
# Build the new index on your build server
python3 -m axiom_cve_retriever build --jsonl /data/cve_2026_q2.jsonl --db /data/cve_v2.db

# Pack a new bundle
axm index-pack --shard cve:/data/cve_v2.db --shard bugs:/data/bugs.db \
               --output /artifacts/rag_v2.rag.axm

# Verify, then ship to edge device and swap
axm index-verify /artifacts/rag_v2.rag.axm
scp /artifacts/rag_v2.rag.axm edge-device:/opt/axiom/
ssh edge-device systemctl restart axiom-edge-rag
```

**Invalidating a cached answer:**

```python
from axiom_cve_retriever import CVERetriever, CachedCVERetriever
from axiom_verified_answer_cache import VerifiedAnswerCache

r = CachedCVERetriever(CVERetriever("/data/cve.db"),
                        VerifiedAnswerCache("/data/cve.cache.db"))
r.invalidate("what is CVE-2021-44228?")
# Next query hits FTS5 fresh; re-verified answers re-promote after N calls
```

---

## CLI reference

```
axm index-pack  --shard DOMAIN:PATH [--shard ...] --output FILE
                Pack FTS5 shards into a signed .rag.axm bundle.

axm index-verify FILE
                Verify HMAC signature and per-shard SHA-256.
                Exit 0 = clean, exit 1 = tampered.

axm index-unpack FILE --dest DIR [--no-verify]
                Verify and extract bundle to a directory.

python3 -m axiom_cve_retriever build --jsonl JSONL --db DB
                Build FTS5 index from a JSONL Q&A corpus.

python3 -m axiom_cve_retriever query --db DB "QUERY"
                Run a single FTS5 query and print results.

python3 -m axiom_cve_retriever stats --db DB
                Print index row count, size, and db path.

python3 -m axiom_datasheet_ingester ingest --folder DIR
                Ingest PDFs and text files into a BM25 local index.

python3 -m axiom_datasheet_ingester watch --folder DIR --poll 30
                Poll a folder and ingest new files in the background.

python3 -m axiom_research_server
                Start the research console HTTP server.
```
