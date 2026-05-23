# Re:Search Engine — instructions

The AXIOM Re:Search console turns a question into a **signed,
replayable research report**. Every Run produces a fresh
`EventToken` with a per-event HMAC signature, recorded in a
tamper-evident ledger. The page itself looks like any other
research UI — the difference is that everything you see is
cryptographically attached to the run that produced it.

This page covers: what the console actually does, how to read a
result, how to wire your own LLM (BYO), and where the data lives.

---

## 30-second quickstart

1. Open the console (the URL of whichever machine is running
   `axiom_research_server`).
2. Type a question in the search box.
3. Hit **Run**.
4. Read the **green ✓ SIGNED · VERIFIED ribbon** at the top of the
   report — that's the live signed event token for *this* run.
   Refresh the page, run again with the same question: the token
   ID changes. The signature is real.

That's the whole loop. The rest of this page explains what each
piece means.

---

## What the console actually does

For every Run, the server does five things:

1. **Retrieve** relevant sources from a local corpus
   (`axiom_research_retriever.LocalRetriever`). If no corpus is
   indexed, sources show as `No local matches`.
2. **Branch** the question across the configured QRF (Quantum
   Reasoning Field) trajectories — passed / rival / killed,
   each with a probability and a constitutional distance.
3. **Synthesize** an answer via the chosen Exoskeleton delegate
   running against your configured LLM backend.
4. **Sign** the resulting `EventToken` with three layered HMAC
   signatures (per-layer · coordinator · outer).
5. **Append** a record to `~/.axiom/exoskeleton-ledger.jsonl`
   under the `axiom-exoskeleton-ledger-v1` namespace.

Steps 1–3 are observable in the page's SSE progress stream
(`retrieve → branch → synthesize`). Steps 4–5 happen
automatically and are surfaced in the receipt card.

---

## How to read a result

After a successful Run, the page renders five sections.

### 1. The verification ribbon (top of report)

```
✓ SIGNED · VERIFIED   exo_a1b2c3d4e5f6   ·   342ms   ·   local · llama3.2:3b   ·   ledger: ~/.axiom/exoskeleton-ledger.jsonl (+1 entry)
```

- `✓ SIGNED · VERIFIED` (green border-left) means
  `EventToken.verify()` returned True — every per-layer signature
  matched, the coordinator signature matched, the outer signature
  matched. Tamper-evidence is intact.
- `✗ SIGNATURE FAILED` (red) means one of those checks failed —
  someone or something edited the token after signing.
- `DEMO · UNSIGNED` (muted) means the result is from the page's
  mock fallback (e.g. backend unreachable). The data shown is
  illustrative and **not** signed.

### 2. The synthesized report

- **TL;DR** — one-line summary.
- **Key Findings** — the structured claims the delegate extracted.
- **Open Questions** — what the system explicitly flagged as
  unresolved.

### 3. Sources

Per-source cards: title, URI, kind (`internal-doc` /
`external-web` / etc.), retrieval score (0–1), and a snippet.
When the retriever finds no matches, you'll see `No local
matches` — the model still synthesizes, but it's working from
training data only.

### 4. Reasoning branches

Each branch is one trajectory the QRF considered:
- `passed` — accepted into the synthesis
- `rival` — competing interpretation kept visible for honesty
- `killed` — rejected with a stated reason
- Per-branch: probability, constitutional distance, citation count

### 5. Receipt card

The full `EventToken` audit trail: `token_id`, workflow,
backend, signed_at, verified flag, ledger path. The signature
itself isn't shown (it's HMAC, not for human reading) — but
anything in this card can be re-verified with
`EventToken.from_json(...).verify()`.

---

## Domain and workflow pickers

**Domain** narrows the retrieval scope to a subject area
(`general` / `medical` / `finance` / `security` / `hr` /
`supply_chain`). If you pick `medical`, the console routes to the
dedicated medical research instrument (`/api/medical/research`),
which produces a different shape of output: per-layer signed
event tokens, a Coordinator Token, a bracketed Token Descriptor,
and a Tier 1–5 distribution badge.

**Workflow** maps to one of the Exoskeleton delegates
(`outreach_personalization`, `competitive_analysis`,
`customer_discovery`, etc.). Each delegate has its own scoped
system prompt and budget.

---

## Bring your own LLM

The Re:Search engine works with any **OpenAI-compatible** endpoint.
That means out of the box it speaks to:

- **NVIDIA NIM** (default if you set `NVIDIA_NIM_API_KEY`)
- **DeepSeek** (set `DEEPSEEK_API_KEY`)
- **Ollama** running locally (`OLLAMA_URL=http://localhost:11434`)
- **vLLM** / **LM Studio** / **Text Generation WebUI** — anything
  serving an OpenAI-style `/v1/chat/completions` endpoint
- **OpenRouter** / **Together** / **Fireworks** — hosted proxies
- **Your own self-hosted endpoint**

> **Where to configure:** BYOLLM is wired via **environment
> variables on the research server**, not a client-side toggle.
> `web/research_console.html` has no Backend dropdown — the
> backend is chosen once when `axiom_research_server.py` starts
> and serves every request. Change a var → restart the server.
>
> A separate Streamlit prompt-evolution UI (`ui.py`) exists for
> the prompt-evolution workflow; it has its own sidebar. The
> instructions here apply only to the research console.

### Four env vars, one custom endpoint

```bash
AXIOM_BACKEND=custom                       # picks the CustomBackend
AXIOM_BASE_URL=https://your-endpoint/v1    # OpenAI-compatible base URL
AXIOM_API_KEY=sk-...                       # whatever your endpoint needs
AXIOM_MODEL=your-model-name                # passed as the `model` field
```

Put these in `.env` (or your container's env block), restart
`axiom_research_server`, and `/api/health` will report the new
backend label. Every signed token's receipt records which actual
backend + model served the request — `receipt.backend` in the
`/api/research` response.

### Different LLM per domain

The console's domain selector (Medical / Finance / Security / HR /
Supply Chain / General) can route to a different backend per
domain. Use a medically-tuned model for medical queries, a
coding-tuned model for security queries, etc.

Configure with `AXIOM_*_<DOMAIN>` env vars alongside the bare
defaults. Anything not overridden falls through to the default
backend.

```bash
# Default (any domain without its own override)
AXIOM_BACKEND=custom
AXIOM_BASE_URL=https://router/v1
AXIOM_API_KEY=sk-or-v1-...
AXIOM_MODEL=anthropic/claude-3.5-sonnet

# Medical-tuned LLM for the Medical domain button
AXIOM_BACKEND_MEDICAL=custom
AXIOM_BASE_URL_MEDICAL=https://your-medical-llm/v1
AXIOM_API_KEY_MEDICAL=...
AXIOM_MODEL_MEDICAL=meditron-70b

# Coding-tuned LLM for the Security domain button
AXIOM_BACKEND_SECURITY=custom
AXIOM_BASE_URL_SECURITY=https://your-coder-llm/v1
AXIOM_API_KEY_SECURITY=...
AXIOM_MODEL_SECURITY=qwen2.5-coder-32b
```

Supported domain suffixes: `GENERAL`, `MEDICAL`, `FINANCE`,
`SECURITY`, `HR`, `SUPPLY_CHAIN` (uppercase the lowercase form
the console sends).

When any `AXIOM_BACKEND_<DOMAIN>` is set, the server auto-wraps
everything in a `DomainRoutedBackend`. `/api/health` then reports
the routing summary, e.g.:

```
"backend": "domain-routed · default=claude-3.5-sonnet ·
            medical=meditron-70b · security=qwen2.5-coder-32b"
```

Each request still gets one signed token whose `receipt.backend`
names the actual model that served it — so per-domain routing
is fully auditable in the ledger.

Each per-domain block needs its own backend (`AXIOM_BACKEND_<DOM>`)
plus the corresponding `AXIOM_BASE_URL_<DOM>` / `AXIOM_API_KEY_<DOM>`
/ `AXIOM_MODEL_<DOM>`. Any of those three that's missing falls back
to the bare `AXIOM_BASE_URL` / `AXIOM_API_KEY` / `AXIOM_MODEL` —
handy when you only want to swap the model identifier and reuse the
same upstream key.

#### Local Ollama, different model per domain (most common BYO setup)

If you're running Ollama locally with multiple models pulled
(`ollama pull meditron:70b`, `ollama pull qwen2.5-coder:32b`, etc.),
use `local` as the backend and `OLLAMA_MODEL_<DOMAIN>` /
`OLLAMA_URL_<DOMAIN>` for the per-domain overrides — `local`
reads those env names, not the `AXIOM_*` ones.

```bash
# Default — light model on the local box
AXIOM_BACKEND=local
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# Medical queries — bigger medically-tuned model on the same Ollama
AXIOM_BACKEND_MEDICAL=local
OLLAMA_MODEL_MEDICAL=meditron:70b

# Security queries — coder-tuned model on the same Ollama
AXIOM_BACKEND_SECURITY=local
OLLAMA_MODEL_SECURITY=qwen2.5-coder:32b

# Optional — if some domains run on a different Ollama host
# (e.g. medical on a separate GPU box):
# OLLAMA_URL_MEDICAL=http://gpu-box:11434
```

All four models must be `ollama pull`'d before the first request to
that domain or the call fails. Switching the default model is just an
`OLLAMA_MODEL=` edit + server restart; nothing per-domain has to
change.

### Different corpus per domain

Independent of the LLM-per-domain wiring above, the retrieval
corpus is also routable per domain. By default the
`LocalRetriever` indexes the repo's `docs/`, `README.md`, and
`patents/` — fine for the demo, useless for production. Point
each domain at its own corpus with `AXIOM_RETRIEVAL_DIR_<DOMAIN>`:

```bash
AXIOM_RETRIEVAL_DIR_MEDICAL=/data/corpora/pubmed,/data/corpora/guidelines
AXIOM_RETRIEVAL_DIR_SECURITY=/data/corpora/cve,/data/corpora/owasp
AXIOM_RETRIEVAL_DIR_FINANCE=/data/corpora/sec-edgar
AXIOM_RETRIEVAL_DIR_HR=/data/corpora/employment-law
AXIOM_RETRIEVAL_DIR_SUPPLY_CHAIN=/data/corpora/sap-logs
```

- Comma-separated paths become multi-root corpora for the same
  domain.
- Any path that doesn't exist is logged at boot and skipped (the
  domain falls back to the default corpus). Operators can spot
  typos in the warning line without taking the server down.
- `LocalRetriever` indexes plain text (`.md`, `.txt`, `.py`,
  `.js`, `.ts`, etc.) up to ~4000 files per corpus. Drop your
  per-domain `.md` exports into the directory and restart.

When any `AXIOM_RETRIEVAL_DIR_<DOMAIN>` is set, the default
retriever auto-wraps in a `DomainRoutedRetriever` so the right
corpus serves the right domain. Source citations in the
`/api/research` response come from the per-domain corpus — easy
to verify by clicking a citation URI in the console.

You can mix and match: route only the LLM (keep the shared
corpus), only the corpus (keep the shared LLM), or both. They
read separate env vars and don't depend on each other.

### Examples

**OpenRouter** (hosts dozens of models including open-weight):
```bash
AXIOM_BACKEND=custom
AXIOM_BASE_URL=https://openrouter.ai/api/v1
AXIOM_API_KEY=sk-or-v1-...
AXIOM_MODEL=anthropic/claude-3.5-sonnet
```

**Together AI**:
```bash
AXIOM_BACKEND=custom
AXIOM_BASE_URL=https://api.together.xyz/v1
AXIOM_API_KEY=...
AXIOM_MODEL=Qwen/Qwen2.5-72B-Instruct-Turbo
```

**Self-hosted vLLM**:
```bash
AXIOM_BACKEND=custom
AXIOM_BASE_URL=http://your-vllm-host:8000/v1
AXIOM_API_KEY=anything-nonempty
AXIOM_MODEL=meta-llama/Llama-3.1-70B-Instruct
```

**LM Studio** (Mac desktop):
```bash
AXIOM_BACKEND=custom
AXIOM_BASE_URL=http://localhost:1234/v1
AXIOM_API_KEY=lm-studio
AXIOM_MODEL=qwen2.5-7b-instruct
```

If the response shape doesn't match OpenAI's standard
(`{choices: [{message: {content: ...}}], usage: {...}}`) the
client will error with a clear message — most providers don't
deviate.

---

## Privacy and data flow

- **Local backend (Ollama, LM Studio, vLLM on your machine):**
  your prompts and outputs never leave the box. Signed tokens
  live in `~/.axiom/exoskeleton-ledger.jsonl`. No telemetry.
- **Cloud backend (DeepSeek, NIM, OpenRouter, etc.):** your
  prompt goes to the provider per their terms of service.
  Signed tokens are still created and stored locally; the cloud
  provider doesn't see the signature.
- **The console does not phone home.** There are no analytics
  scripts, no third-party CDNs, no tracking pixels. Everything
  runs against `axiom_research_server` on whichever host you
  point it at.
- **The ledger is append-only.** Tampering breaks the per-entry
  HMAC and `verify()` returns False. You can rotate the ledger
  file freely (`mv ~/.axiom/exoskeleton-ledger.jsonl
  ~/.axiom/ledger-2026-q2.jsonl`) — old entries stay
  verifiable as long as you keep the same `AXIOM_MASTER_KEY`.

---

## Limitations (be honest about these)

- **The auto-generated rubric is generic.** The Evaluator builds
  its scoring rubric from the task text; vague tasks get vague
  rubrics. Write 2–4 sentences specifying goal, audience,
  format, and 2–3 things that must appear.
- **`requires_human_review` is advisory, not gating.** Medical
  research sessions flag patient-specific or high-risk queries,
  but the page still renders the descriptor — the flag is a
  recommendation to a human, not a hard refusal.
- **Retrieval is whatever you indexed.** The default
  `LocalRetriever` looks at `docs/`, `README.md`, and (if
  present) `patents/` companion `.md` files. If you want it to
  see more, drop those files in or point a configured
  retriever at a different corpus.
- **Probability bands are deterministic placeholders today.**
  Branch probabilities are computed from constitutional
  distance, not from real embedding similarity. Embedding-based
  calibration is on the roadmap.
- **Mock fallback is intentionally kept.** When the live
  backend fails, the page shows the demo payload labeled
  `DEMO · UNSIGNED` rather than erroring out. This keeps a
  partial demo viable; it also means you should check the
  ribbon color before believing what you see.

---

## Troubleshooting

### `Backend not built yet` banner on page load

The pre-flight `GET /api/health` call returned a state where
the backend hasn't initialized. Common causes:

- `AXIOM_MASTER_KEY` not set in the server's environment
- The first request just warms it up — make any Run, the banner
  goes away

### Run hangs / never shows a result

The chosen backend is unreachable. Open browser DevTools
(Network tab), look at the `/api/research/stream` request. If it
errors at the `synthesize` stage, your LLM provider or local
Ollama isn't responding.

### `RELEVANCE REFUSED` (medical / patch-agent flows)

The medical governance check or the patch-agent's content scan
caught something. For medical: a Tier 5 pattern, PHI, clinical
advice phrasing, or an emergency signal. For patch-agent: the
diff's word-overlap with the bug description fell below
`RELEVANCE_FLOOR=0.05`. Both are CANNOT_MUTATE — the only
override is the explicit `--force-irrelevant` flag (recorded in
the signed token so the bypass is auditable).

### Verify a signed token outside the console

```python
from axiom_event_token import EventToken
import json
data = json.loads(open("path/to/saved-token.json").read())
print(EventToken.from_dict(data).verify())   # → True or False
```

Verification needs `AXIOM_MASTER_KEY` to be the same key that
signed it. Rotating the master key invalidates every prior
signature.

### Ledger entries don't show up at `/api/ledger`

`AXIOM_EXOSKELETON_LEDGER` env var probably points elsewhere.
Check with:
```bash
docker exec <container> env | grep LEDGER
ls -la ~/.axiom/
```

---

## Where things live (file paths)

- Console HTML: `web/research_console.html`
- Server: `axiom_research_server.py`
- Local retriever: `axiom_research_retriever.py`
- Backends: `axiom_event_token/backends.py`
  (`NIMBackend`, `LocalNanoBackend`, `DeepSeekBackend`,
  `CustomBackend`, `ChainedBackend`)
- Exoskeleton delegates: `examples/exoskeleton_pack.py`
- Medical instrument: `axiom_medical_agent.py`,
  `axiom_medical_coordinator.py`,
  `axiom_medical_governance.py`
- Honesty post-scan (blocks invented claims):
  `axiom_exoskeleton_honesty.py`
- Ledgers: `~/.axiom/exoskeleton-ledger.jsonl`,
  `~/.axiom/medical-ledger.jsonl`
- This doc: `docs/research_engine.md`
  (also served live at `/help` by `axiom_research_server`).

---

## Contributing a new LLM provider

If your provider doesn't speak OpenAI-compatible chat
completions, you can add it as a new `SLMBackend` subclass:

1. Add a class to `axiom_event_token/backends.py` implementing
   `generate(*, system, prompt, max_output_tokens, timeout_s) ->
   BackendResult`.
2. Register it in `_BACKEND_FACTORIES` so
   `AXIOM_BACKEND=<your_name>` builds an instance.
3. Add a test in `tests/test_event_token_backends.py` (mirror the
   existing `DeepSeekBackend` tests).
4. Document the env vars at the bottom of this file. The research
   server picks the backend at startup via `default_backend()`,
   so no UI wiring is needed — operators set `AXIOM_BACKEND=<your_name>`
   and restart.

PRs welcome.
