# Training manual — Dev-Agent Coder

> **`axiom_ollama_coder.py`** — LLM-backed code proposer wrapped
> in `AxiomDevAgentV2`'s four-layer constitutional review. Runs on
> the Orin Nano against `qwen2.5:1.5b` via dustynv/ollama, or any
> OpenAI-compatible endpoint. CLI + REPL modes.

## What it is

```
  LLM proposes code (Ollama / Claude / Stub)
      │
      ▼
  [1] CodeReflex             ast.parse + forbidden-pattern scan
                              (os.system, eval/exec, shell=True,
                               bare except) — <1ms, no LLM
      │
      ▼
  [2] PullRequestReviewer    forecast = 1 - novelty - size - citation
                              PASS / SOFTEN / VETO
      │
      ▼
  [3] DevCurriculum          lifts competence on past outcomes —
                              this is the loop the AXIOM thesis
                              depends on
      │
      ▼
  [4] CIExaminer             5 deterministic checks (task class
                              recognized, diff under size ceiling,
                              BUG_FIX cites pattern, ...)
      │
      ▼
  MERGED / SOFTEN_REQUESTED / VETO / REFLEX_REFUSED
```

Each layer signs its verdict under its own namespaced HMAC key —
`axiom-dev-reflex-v1`, `axiom-dev-reviewer-v1`,
`axiom-dev-curriculum-v1`, `axiom-dev-examiner-v1`. Same signing
chain pattern as the kid audit + the research engine.

## Who it's for

| Buyer profile | Pitch |
|---|---|
| Sovereign-Box customer | "Code-generating dev agent that runs on YOUR Orin, never sends prompts to a cloud LLM. Every diff gets reviewed against four constitutional layers before it leaves the box." |
| Air-gapped lab | "Local LLM coder with provenance — every accepted diff has a four-layer signed audit trail back to the model output." |
| Internal R&D | "Iterate on coding tasks against `qwen2.5:1.5b` at ~zero per-query cost, with the curriculum learning loop building competence over a session." |

## Why `SOFTEN_REQUESTED` is a first-class result

A fresh `AxiomDevAgentV2` has **competence 0** on every task class.
Its forecast for first-time work is `1 - novelty_penalty -
size_penalty - citation_penalty ≈ 0.30`, which falls between the
SOFTEN floor (0.20) and the PASS threshold (~0.80 at c=0).

That's intentional — the agent should be cautious until it has a
track record. The coder distinguishes three result states:

| `result.*` flag | Meaning |
|---|---|
| `merged = True` | reviewer + CI both green; code is fully cleared |
| `accepted_with_warnings = True` | reflex passed, reviewer SOFTEN'd. `accepted_code` is surfaced anyway with the `softening_advice` rendered for the user. Equivalent to a code-review "LGTM with concerns." |
| neither | VETO or REFLEX_REFUSED. `accepted_code = None`, only reasons. |

CLI exit codes mirror this: `0` = MERGED, `1` = accepted with
warnings, `2` = rejected. Use in CI for graduated gates.

Three ways to climb out of SOFTEN territory:

1. **Iterate.** Every successful CI bumps competence for that
   task class. After ~10 successful FEATUREs, competence ≈ 0.5
   and clean diffs start MERGING. This is the curriculum loop.
2. **Cite patterns.** Pass `cited_patterns` of AXM TrajectoryBlocks
   the diff invokes — clears the 0.20 citation penalty.
3. **Smaller diffs.** Diff size adds up to 0.30 penalty at 1000+
   lines. Stay under a couple hundred lines.

## Architecture

The coder is ~350 LOC. The four-layer review pipeline lives in
`axiom_dev_agent_v2.py` (810 LOC, already shipped). The coder is
just the composition layer:

| File | What it adds |
|---|---|
| `axiom_ollama_coder.py` | `OllamaCoder` (composes LLM + AxiomDevAgentV2), prompt builder, retry feedback loop, CLI argparse, REPL |
| `tests/test_axiom_ollama_coder.py` | 16 tests — strip-codefence, prompt content, untrained-softens, trained-merges, reflex-refuses, retry feedback, env-var fallback contract |
| `docs/NANO_DEV_AGENT.md` | Public deployment doc |

## Key concepts

### LLM client is the same interface as the research engine

Reuses `axiom_research.synthesize.OllamaClient`, `ClaudeClient`,
`StubLLMClient`. One Protocol, three implementations, swap by
`--backend` flag or `AXIOM_CODER_BACKEND` env var.

### Env-var fallbacks for argparse defaults

Once these land in `~/.bashrc`, you stop passing flags:

```bash
export AXIOM_MASTER_KEY="<64-hex>"
export OLLAMA_URL="http://localhost:11434"
export OLLAMA_MODEL="qwen2.5:1.5b"
export AXIOM_CODER_BACKEND="ollama"
```

Then:
```bash
python3 axiom_ollama_coder.py --task "..." --path foo.py
python3 axiom_ollama_coder.py --repl
```

Flags still override env (locked in by
`test_cli_flags_still_override_env`).

### Recognized task classes

`TASK_CLASSES = ("FEATURE", "BUG_FIX", "EFFICIENCY", "SPEC_WRITING",
"DOCUMENTATION")` from `axiom_dev_agent_v2.py`. Passing anything
else fails the CIExaminer's `task_class_is_recognized` check (5/5
must pass for MERGE).

### Markdown-fence recovery

Even instruction-tuned 1-3B models will sometimes wrap output in
` ```python ... ``` ` despite "Python only, no markdown" in the
prompt. `_strip_codefence()` handles this cheaply instead of
bouncing the diff through CodeReflex.

## Common workflows

### Workflow A: One-shot generation on the Orin

```bash
python3 axiom_ollama_coder.py \
  --task "function that returns SHA-256 hex of a UTF-8 string" \
  --path axiom_hash_utils.py \
  --task-class FEATURE
```

Returns one of:
- `MERGED` → code printed under `── accepted code ──`
- `SOFTEN_REQUESTED` → code printed under `── accepted with warnings ──` + softening advice listed
- `VETO` / `REFLEX_REFUSED` → reasons printed, no code

### Workflow B: REPL session — curriculum builds across turns

```bash
python3 axiom_ollama_coder.py --repl
```

```
coder> code axiom_hash_utils.py write a function that base64-encodes a string
coder> code axiom_hash_utils.py and a function that base64-decodes it
coder> status
coder> quit
```

Each successful turn lifts the agent's `FEATURE` competence. Over
~10 turns, MERGED becomes routine for clean code.

### Workflow C: Driving the Orin from a laptop

```bash
# On the laptop (~/.bashrc):
export OLLAMA_URL="http://orin.tailnet.ts.net:11434"
export OLLAMA_MODEL="qwen2.5:1.5b"
export AXIOM_MASTER_KEY="<same hex as on the Orin>"

# Then identical commands as on the Orin.
python3 axiom_ollama_coder.py --task "..." --path ...
```

Important: **same `AXIOM_MASTER_KEY` on both machines** — otherwise
the agent's signature chain doesn't cross-verify.

## Test scenarios

```bash
AXIOM_MASTER_KEY=<64-hex> python3 -m pytest tests/test_axiom_ollama_coder.py -v
```

16 tests, hermetic. Highlights:

- `test_clean_code_from_untrained_agent_softens` — locks in that
  a fresh agent SOFTENs clean code; surfaces it with warnings.
- `test_trained_agent_merges_clean_code` — hand-trains 10 successful
  FEATUREs, then same diff MERGES. Proof the curriculum works.
- `test_coder_reflex_refuses_os_system` — security gate fires
  before reviewer sees a forbidden pattern.
- `test_coder_retries_and_feeds_back_reasons` — second-attempt
  prompt MUST include first-attempt rejection reasons.
- `test_cli_picks_up_ollama_url_and_model_from_env` — the bashrc
  workflow contract.

## House rules for support + sales

- **It's a coder, not a reviewer.** The dev-agent V2 underneath
  is a reviewer; the coder ADDS the generation step. Don't confuse
  the two — internal docs refer to AxiomDevAgentV2 for review-only
  workflows (PR auto-review of submitted diffs).
- **The agent isn't "right" on first call.** First-attempt SOFTEN
  is expected. If a customer demos and gets SOFTEN, that's WORKING
  — the agent is being honest about its competence. Demo with the
  pre-trained reviewer (10 hand-fed outcomes) to show MERGE.
- **Local LLM costs are zero per query.** On the Orin Nano running
  qwen2.5:1.5b, a generation is 100% on-device. Compare to ~$3 /
  1K queries on Haiku-class hosted models. This is the headline
  number for Sovereign-Box conversations.
- **Don't oversell autonomous coding.** Constitutional gates catch
  the obvious foot-shoots (os.system, eval, hardcoded creds), not
  semantic bugs. The reviewer's confidence is bounded; the human is
  still the last line.

## Further reading

- [`docs/NANO_DEV_AGENT.md`](../NANO_DEV_AGENT.md) — public-facing deployment guide
- [`axiom_dev_agent_v2.py`](../../axiom_dev_agent_v2.py) — the four-layer reviewer (810 LOC)
- [`axiom_ollama_coder.py`](../../axiom_ollama_coder.py) — the coder composition (350 LOC)
- [`tests/test_axiom_ollama_coder.py`](../../tests/test_axiom_ollama_coder.py) — locked-in contract
- [`nano-deployment.md`](nano-deployment.md) — getting the Orin running
