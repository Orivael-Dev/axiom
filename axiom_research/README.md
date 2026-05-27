# axiom_research

Signed multi-branch research engine built on top of the AXIOM Quantum
Reasoning Forecast (QRF).

The differentiator vs Perplexity / OpenAI Deep Research / Claude with
search: those return **one** synthesized answer that hides uncertainty.
This returns the answer **plus** the probability-weighted branches that
led to it, all signed. Epistemically honest, auditable, deterministic
across runs.

## Pipeline

```
query
  │
  ▼
[1] firewall /v1/guard/check         intent classifier — refuses unsafe queries
  │
  ▼
[2] Retriever.retrieve(query)        top-K grounding docs
  │
  ▼
[3] QRFEngine.forecast(query)        N probability-weighted reasoning branches
  │
  ▼
[4] Synthesizer.synthesize(          LLM writes a Markdown report with
       query, docs, branches)        [doc_N] citations
  │
  ▼
[5] firewall /v1/guard/output        output-side classifier — catches hallucinations
  │
  ▼
[6] ResearchReport.signed(payload)   HMAC under namespace axiom-research-v1
```

Steps [2]–[4] + [6] live in this package. Steps [1] and [5] are firewall
calls the caller wires in.

## Quickstart

```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')

# Default: deterministic stub LLM, no network — runs anywhere
python3 examples/research_demo.py

# Local Ollama (Orin Nano or laptop)
AXIOM_RESEARCH_BACKEND=ollama \
OLLAMA_URL=http://localhost:11434 \
python3 examples/research_demo.py

# Anthropic API
ANTHROPIC_API_KEY=sk-ant-... \
AXIOM_RESEARCH_BACKEND=claude \
python3 examples/research_demo.py
```

## Library usage

```python
from axiom_research import (
    ResearchEngine, OllamaClient, LocalFilesRetriever,
)

engine = ResearchEngine(
    llm=OllamaClient(host="http://orin.tailnet.ts.net:11434"),
    retriever=LocalFilesRetriever("./docs"),
    domain="general",          # or financial / medical / supply_chain / hr / security
)

report = engine.run("Does vitamin D improve sleep quality?")
assert report.verify()
print(report.payload["answer_markdown"])
print(report.payload["top_branch"], report.payload["probability_band"])
for b in report.payload["branches"]:
    print(f"  {b['probability_weight']:.2f}  {b['branch_label']}")
```

## Report payload

| field              | type        | meaning                                      |
|--------------------|-------------|----------------------------------------------|
| `query`            | str         | original question                            |
| `answer_markdown`  | str         | LLM-written report with `[doc_N]` citations  |
| `branches`         | list[dict]  | top-6 QRF branches: label + weight + score   |
| `probability_band` | str         | `HIGH` / `MODERATE` / `LOW` / `UNCERTAIN`    |
| `top_branch`       | str         | highest-weight branch label                  |
| `citations`        | list[dict]  | retrieved docs: path, snippet, score, meta   |
| `domain`           | str         | QRF domain used                              |
| `n_branches`       | int         | how many branches QRF generated total        |
| `n_killed`         | int         | how many the monotonic gate killed           |
| `synth_model`      | str         | `ollama/llama3.2:3b` / `anthropic/...`       |
| `created_at`       | str         | ISO-8601 UTC timestamp                       |

`signature` is HMAC-SHA256 over canonical JSON, namespace
`axiom-research-v1`. Verify standalone via `report.verify()`.

## Components

| module          | purpose                                              |
|-----------------|------------------------------------------------------|
| `retrieve.py`   | `Retriever` Protocol + `LocalFilesRetriever`         |
| `synthesize.py` | `LLMClient` Protocol + Ollama / Claude / Stub        |
| `engine.py`     | `ResearchEngine` orchestrator + `run_research()`     |
| `report.py`     | Signed `ResearchReport` dataclass                    |

All three LLM clients implement the same interface, so swapping
backends is one line of code.

## Selective activation via the event-token Coordinator

QRF is also registered as a peer agent in `axiom_event_token`,
alongside text / audio / tempo / vad / voice / video / physics /
governance. Off by default — you opt in via `activate=`:

```python
from axiom_event_token import Coordinator
token = Coordinator().compose(
    qrf={"query": "Will inflation cool?", "domain": "financial"},
    activate=("qrf", "governance"),
)
assert token.verify()
print(token.qrf.payload["top_branch"])           # e.g. "SafetyBranch"
print(token.qrf.payload["probability_band"])     # e.g. "HIGH"
```

## Tests

```bash
AXIOM_MASTER_KEY=<64-hex> python3 -m pytest tests/test_axiom_research.py -v
```

17 tests, hermetic — `StubLLMClient` keeps everything offline. No
network calls, no API keys required.

## Not to be confused with `axiom_research_pipeline.py`

There is a separate `axiom_research_pipeline.py` at the repo root that
implements a 9-agent **scientific** research workflow (Hypothesis →
Literature → Simulation → Critic → Safety → Ethics → Data → Experiment
→ Report) using direct Claude API calls. That file is its own product
surface — it's not wired into this module, and this module doesn't
depend on it. Pick one:

| If you want…                                            | use                          |
|---------------------------------------------------------|------------------------------|
| One-shot research query with QRF + signed report        | `axiom_research/` (this)     |
| 9-agent constitutional scientific pipeline              | `axiom_research_pipeline.py` |

## Web UI

There is **no research page** in the existing consoles
(`docs/axiom_console.html`, `docs/qrf_console.html`,
`docs/axiom_os_shield_console.html`). Today the research engine is
library + CLI only. Adding a research tab to `qrf_console.html` is
straightforward — open an issue if you want it next.
