# Training manual — Research Engine

> **`axiom_research/`** — signed multi-branch research engine. Takes
> a user query, returns a Markdown answer with `[doc_N]` citations
> PLUS the probability-weighted reasoning branches QRF produced
> along the way, all wrapped in an HMAC-signed `ResearchReport`.

## What it is

A library + CLI + HTTP endpoint that composes three existing pieces:

```
  query
    │
    ▼
  [2] Retriever              top-K grounding documents
    │
    ▼
  [3] QRFEngine.forecast     N probability-weighted reasoning branches
    │
    ▼
  [4] Synthesizer(LLMClient) LLM writes a report with [doc_N] citations
    │
    ▼
  [6] ResearchReport.signed  HMAC under axiom-research-v1
```

Steps [1] firewall input check + [5] firewall output check are the
caller's responsibility — wire them in if the query / answer needs
guardrails.

**The differentiator vs Perplexity / OpenAI Deep Research / Claude
with search:** those return ONE synthesized answer that hides
uncertainty. This returns the answer PLUS the probability-weighted
branches that led to it. Epistemically honest, auditable,
deterministic across runs with the same `AXIOM_MASTER_KEY`.

## Who it's for

| Buyer profile | Pitch |
|---|---|
| Compliance / risk analyst | "Show me an audit-grade research answer where I can see every source AND the branches the model rejected. Wraps in your existing event-token signing chain." |
| Customer-facing analyst tool | "Cite-everything Q&A that won't hallucinate silently — the QRF probability band labels each answer HIGH / MODERATE / LOW / UNCERTAIN." |
| Internal R&D | "Reproducible research over a private corpus — same input, same signed output, same retrievals." |

## Architecture

| Module | Purpose | LOC |
|---|---|---:|
| `axiom_research/retrieve.py` | `Retriever` Protocol + `LocalFilesRetriever` (stdlib grep over a directory, scores by `tokens_matched / tokens_total`) | 130 |
| `axiom_research/synthesize.py` | `LLMClient` Protocol + 3 concrete clients (`OllamaClient`, `ClaudeClient`, `StubLLMClient`) + `Synthesizer` (composes the canonical prompt) | 200 |
| `axiom_research/engine.py` | `ResearchEngine` orchestrator + `run_research()` convenience entry point | 180 |
| `axiom_research/report.py` | Signed `ResearchReport` dataclass (HMAC under `axiom-research-v1`) | 90 |

Total: ~600 LOC + 17 hermetic tests in `tests/test_axiom_research.py`.

## Key concepts

### Pluggable LLM client via Protocol

```python
class LLMClient(Protocol):
    name: str
    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str: ...
```

Three implementations ship. Add more by writing a class that
satisfies the Protocol — no inheritance, no registration.

| Client | When to use |
|---|---|
| `OllamaClient` | Local LLM on Orin Nano (qwen2.5:1.5b) or laptop. `AXIOM_RESEARCH_BACKEND=ollama`. |
| `ClaudeClient` | Anthropic API. Requires `ANTHROPIC_API_KEY`. `AXIOM_RESEARCH_BACKEND=claude`. |
| `StubLLMClient` | Deterministic non-network response. Tests + hermetic CI. `AXIOM_RESEARCH_BACKEND=stub`. |

### Signed report under its own namespace

`ResearchReport.signed(payload=...)` derives a key from
`AXIOM_MASTER_KEY` under the `axiom-research-v1` namespace and
HMAC-signs the canonical JSON. Verify with `report.verify()` —
constant-time compare. Same pattern as `AudioReport`, `VoiceReport`,
`TempoReport`, kid-audit PDFs.

### QRF integration is optional + graceful

`ResearchEngine(qrf_enabled=False)` skips the QRF step entirely;
the report still ships with empty branches. When enabled, if QRF
can't reach its underlying LLM endpoint, the engine catches and
returns `(qrf-error)` instead of crashing. **Retrieval + synthesis
keeps working even when reasoning is unavailable.**

### Selective activation through the event-token Coordinator

`axiom_event_token.Coordinator` registers `QRFAgent` as a peer
alongside `text`/`audio`/`tempo`/`vad`/`voice`/`video`/`physics`/
`governance`. Off by default — opt in with `activate=("qrf", ...)`.
Same selective-activation patent claim applied to research.

## Common workflows

### Workflow A: One-off research call (Python)

```python
from axiom_research import ResearchEngine, OllamaClient, LocalFilesRetriever

engine = ResearchEngine(
    llm=OllamaClient(host="http://localhost:11434", model="qwen2.5:1.5b"),
    retriever=LocalFilesRetriever("./docs"),
    domain="general",
)
report = engine.run("Does vitamin D improve sleep quality?")
assert report.verify()
print(report.payload["answer_markdown"])
print(report.payload["top_branch"], report.payload["probability_band"])
```

### Workflow B: CLI demo (no Python needed)

```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
AXIOM_RESEARCH_BACKEND=ollama python3 examples/research_demo.py
```

Backend switch by env var; no code change. Stub default makes
this runnable anywhere without network.

### Workflow C: HTTP endpoint (multi-tenant)

```bash
curl -sf -X POST http://localhost:8001/research/run \
  -H 'Content-Type: application/json' \
  -d '{"query":"...","backend":"ollama","domain":"general"}'
```

Backend / domain / Ollama URL / model picked per-request — the
server doesn't lock in an LLM choice at startup. Useful for
SaaS deployment where different tenants need different backends.

### Workflow D: QRF console "Research" tab

Open `docs/qrf_console.html` (or hit `/console` on the running
Guard API). The Research tab UI POSTs to `/research/run` and
renders:
- `SIGNED + VERIFIED` badge with the HMAC + namespace
- Probability band tile + branch list with weight bars
- Citations card with each retrieved doc's path + score + snippet
- Synthesized answer Markdown

Settings persist via localStorage (`axiom-console:v1`) so the
endpoint + model + retriever-root survive refreshes.

## Test scenarios

```bash
AXIOM_MASTER_KEY=<64-hex> python3 -m pytest tests/test_axiom_research.py -v
```

17 tests, hermetic — `StubLLMClient` never touches the network.
Covers:

- `LocalFilesRetriever` keyword matching + top-K + ext filter + snippets
- `Synthesizer` prompt shape + empty-docs handling
- `ResearchEngine` signed report + unknown-domain rejection + QRF attach
- `ResearchReport` signing + tamper detection + namespace isolation
- `QRFAgent` Coordinator activation off-by-default + graceful missing-query

## House rules for support + sales

- **The `probability_band` is the differentiator.** When a buyer
  asks "what's different from Perplexity," show them the branch
  list + probability band, not the answer Markdown. The answer
  could come from any LLM; the auditable uncertainty cannot.
- **Signed ≠ encrypted.** Same caveat as the Firewall — the
  signature covers tamper detection on the report body, not
  prompt-content confidentiality.
- **The retriever is pluggable.** Customers asking about Pinecone /
  Weaviate / pgvector retrieval just need a Retriever subclass.
  Today only `LocalFilesRetriever` ships; vector-DB retrievers
  are on the Phase 3 docket (when Data Gate also lands).

## Further reading

- [`axiom_research/README.md`](../../axiom_research/README.md) — public-facing docs
- [`examples/research_demo.py`](../../examples/research_demo.py) — end-to-end demo
- [`docs/FIREWALL_PHASE_STATUS.md`](../FIREWALL_PHASE_STATUS.md) — where this sits in the phase plan
- [`tests/test_axiom_research.py`](../../tests/test_axiom_research.py) — locked-in contract
