# AXIOM Dev Agent on Jetson Orin Nano

Running the constitutional dev agent locally on the Orin against
`qwen2.5:1.5b` (via dustynv/ollama). Zero cloud calls, every
generation reviewed by `AxiomDevAgentV2`'s four-layer pipeline.

## Prerequisites

On the Orin:

1. `dustynv/ollama` container running, exposing `:11434`. Setup
   covered in the dustynv migration instructions you already ran.
2. `qwen2.5:1.5b` pulled inside the container:
   ```bash
   docker exec -it ollama ollama pull qwen2.5:1.5b
   ```
3. AXIOM repo cloned + on the working branch.

## One-shot use

Generate a single piece of code:

```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')

python3 axiom_ollama_coder.py \
  --backend     ollama \
  --ollama-url  http://localhost:11434 \
  --model       qwen2.5:1.5b \
  --task        "function that returns the SHA-256 hex digest of a UTF-8 string" \
  --path        axiom_hash_utils.py \
  --task-class  FEATURE
```

Exit codes (use these in scripts):

| code | meaning |
|------|---------|
| `0`  | `MERGED` — reviewer + CI both green, code cleared |
| `1`  | `SOFTEN_REQUESTED` — code is structurally fine but reviewer wants more competence / cited patterns. Surfaced with warnings. |
| `2`  | `VETO` or `REFLEX_REFUSED` — nothing safe to apply |

Recognized `--task-class` values: `FEATURE`, `BUG_FIX`,
`EFFICIENCY`, `SPEC_WRITING`, `DOCUMENTATION`.

## REPL mode

Persistent session — the agent's curriculum updates across turns,
so over time the reviewer's competence on a given task_class grows
and you start seeing `MERGED` instead of `SOFTEN_REQUESTED`:

```bash
python3 axiom_ollama_coder.py --backend ollama --model qwen2.5:1.5b --repl
```

In the REPL:

```
coder> code axiom_hash_utils.py write a function that base64-encodes a string
coder> status
coder> quit
```

## Why the first attempts on a new task class will SOFTEN

`AxiomDevAgentV2` starts with competence 0 on every task class.
Its forecast for first-time work is `1 - novelty_penalty -
size_penalty - citation_penalty ≈ 0.30`, which falls between
the SOFTEN floor (0.20) and the PASS threshold (~0.80 for c=0).

That's intentional — the agent should be cautious until it has
a track record. Three ways to climb out of SOFTEN territory:

1. **Iterate** — every successful CI bumps competence for that
   task class. After ~10 successful FEATUREs, competence ≈ 0.5
   and clean novel diffs start MERGING.
2. **Cite patterns** — pass `cited_patterns` of AXM
   TrajectoryBlocks the diff invokes. Clears the citation
   penalty (worth 0.20).
3. **Smaller diffs** — diff size adds up to 0.30 penalty at 1000+
   lines. Stay under a couple hundred lines and you avoid it.

The CLI doesn't expose `cited_patterns` yet because no AXM
container is wired on the Orin. Add `--patterns` when you stand
that up.

## Health-check before running real tasks

Smoke-test the agent against the StubLLMClient first — proves
your install works without touching Ollama:

```bash
python3 axiom_ollama_coder.py --backend stub --task x --path y.py
# Should exit 2 (REFLEX_REFUSED) — the stub's default response is
# prose, not Python. That's the expected first-time output.
```

Then point at Ollama:

```bash
python3 axiom_ollama_coder.py \
  --backend ollama --model qwen2.5:1.5b \
  --task "trivial function that returns 42" \
  --path axiom_constants.py
```

Expect either `1` (SOFTEN_REQUESTED with warnings) or `2`
(VETO / REFLEX_REFUSED) on the first run. That's the agent doing
its job.

## What gets reviewed

Every generated snippet goes through four constitutional layers:

```
LLM proposes code
       │
       ▼
[1] CodeReflex          ast.parse() + forbidden-pattern scan
                         (os.system, eval/exec, shell=True, bare except)
                         <1 ms; no LLM, no network
       │
       ▼
[2] PullRequestReviewer forecast = 1 - novelty - size - citation penalties
                         PASS / SOFTEN / VETO based on min_safe
       │
       ▼
[3] DevCurriculum       remembers past outcomes, lifts competence on
                         the task_class when CI passes
       │
       ▼
[4] CIExaminer          5 deterministic checks (task class recognized,
                         diff under size ceiling, BUG_FIX cites pattern, ...)
       │
       ▼
DevHandleOutcome  →  MERGED / SOFTEN_REQUESTED / VETO / REFLEX_REFUSED
```

All four layers sign their verdicts under their own
namespaced HMAC keys (`axiom-dev-reflex-v1`,
`axiom-dev-reviewer-v1`, `axiom-dev-curriculum-v1`,
`axiom-dev-examiner-v1`). Same signing chain pattern as the kid
audit and the research engine.

## Tests

```bash
AXIOM_MASTER_KEY=<any 64-hex string> python3 -m pytest tests/test_axiom_ollama_coder.py -v
```

14 tests, hermetic — `StubLLMClient` + deterministic two-attempt
mocks. No Ollama or network access needed to run them.

## Troubleshooting

| symptom | likely cause | fix |
|---------|--------------|-----|
| `AXIOM_MASTER_KEY not set` | env var missing | `export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')` |
| Connection refused on `:11434` | Ollama container down | `docker ps --filter name=ollama` — if not Up, `docker start ollama` |
| All attempts REFLEX_REFUSED | qwen wrapping output in markdown fences | already handled — but if your model emits prose around the code, increase prompt-following with `--temperature 0.1` |
| Every clean attempt SOFTEN'd | fresh agent, no track record | expected — see "Why first attempts SOFTEN" above |
| Out-of-memory mid-generation | qwen2.5:1.5b too big for current load | see `docs/AUDIT_LAUNCH.md` swap setup + drop to a smaller model |
