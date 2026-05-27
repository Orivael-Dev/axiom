# AXIOM Autonomous Coding Agent

A planner / executor / verifier loop that runs LLM-decided coding
tasks inside a per-task sandbox, signs every step into the
exoskeleton ledger, and gates every action through the
constitutional governance stack.

This document is the operator-facing reference. The implementation
lives in `axiom_autonomous/`; the CLI shim is
`axiom_autonomous_agent.py:1`.

## What it does

Given a free-text task and a workdir, the agent:

1. **Pre-flight gates** — `intent_gate` routes the task through
   `axiom_cmaa.ConstitutionalMultiAgentArchitecture`; HARM /
   DECEIVE classifications abort with a signed `denied` token.
   `sandbox_review` runs the SandboxAgent when available
   (`axiom_autonomous/governance.py:9`).
2. **Spawns a sandbox** — Docker container by default
   (`--network none --read-only`, workdir bind-mounted at `/work`,
   repo bind-mounted read-only at `/repo`). Falls back to
   `LocalSandbox` (host subprocess) if docker is unavailable, with a
   warning recorded in the ledger.
3. **Plans** — Planner LLM emits a list of `Subgoal`s
   (`axiom_autonomous/planner.py`).
4. **Executes** — for each open subgoal, Executor picks one
   `ToolCall` from the tool registry; the per-action governance gate
   screens it; the sandbox dispatches; the result is captured as an
   `Observation`. Honesty patterns post-scan the model's `thought`
   (`axiom_autonomous/honesty_patterns.py`).
5. **Verifies** — Verifier returns a `Verdict` of `success` /
   `retry` / `replan` / `abort`.
6. **Signs** — every plan / execute / verify / replan / denied step
   produces one signed `EventToken` chained from the previous one
   via an extra `chain_sig` HMAC. See `axiom_autonomous/ledger.py:1`
   for the chain construction.
7. **Tears down** — the sandbox container is removed; the workdir
   is exported back to the host.

Hard caps (`axiom_autonomous/orchestrator.py:34`):
- `DEFAULT_BUDGET_STEPS = 30` plan/execute/verify cycles
- `DEFAULT_WALL_SECONDS = 900` total wall clock
- `MAX_RETRIES_PER_SUBGOAL = 3` before forced replan

## Quick start

### 1. Build the sandbox image (one-off)

```
cd deploy/autonomous && docker compose build
```

This produces `orivael/axiom-autonomous:local` — a Python 3.11 slim
image with `pytest`, `pytest-json-report`, `requests`, `pyyaml`
pre-installed, `axiom_tool_runner` baked at
`/usr/local/bin/axiom_tool_runner`, and an empty `/wheels`
directory you can populate with pre-vetted wheels for offline pip
installs. The image runs as the non-root `axiom` user and has no
network at runtime.

### 2. Run a task

```
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')

python3 -m axiom_autonomous_agent run \
    --task "write a prime sieve in primes.py and tests in test_primes.py" \
    --workdir /tmp/auto-primes-1 \
    --budget-steps 20
```

The agent prints a human-readable summary on stderr and (with
`--json`) the full `AutonomousRunResult.to_dict()` on stdout. Exit
codes:
- `0` — task succeeded
- `1` — task ran but did not finish all subgoals
- `2` — pre-flight gate (intent or sandbox review) refused, or
  `AXIOM_MASTER_KEY` was missing

### 3. Verify the signed chain

```
python3 -m axiom_autonomous_agent verify \
    --run-id auto_abc123def \
    --ledger ~/.axiom/exoskeleton-ledger.jsonl
```

Replays every step token for the run and exits `0` only when all
signatures (layer / coordinator / token / chain) verify against the
master key.

## Sandbox model

| Aspect              | Docker                                          | Local (fallback)               |
|---------------------|-------------------------------------------------|--------------------------------|
| Network             | `--network none`                                | Host network (no isolation)    |
| Filesystem          | Read-only except `/work` + `/tmp`               | Workdir only                   |
| Repo access         | `/repo` read-only bind-mount                    | None                           |
| User                | `axiom` (non-root)                              | Whoever ran the CLI            |
| Resources           | `--memory 1g --cpus 2 --pids-limit 256`         | None                           |
| Tool dispatch       | `docker exec` into `/usr/local/bin/axiom_tool_runner` | Direct subprocess        |
| When used           | `--sandbox docker` (default), `docker_required` | `--sandbox local`, or fallback |

`--sandbox docker_required` refuses to run when docker is
unavailable — use this in production. `--sandbox docker` (the
default) silently falls back to `LocalSandbox` with a warning if
docker isn't reachable, which is convenient for development but not
appropriate for untrusted task descriptions.

## Tool inventory

The default registry (`axiom_autonomous/tools/__init__.py:78`) ships
seven tools. The executor sees them via the `schema()` rendering
embedded in its prompt.

| Tool          | Risk   | Purpose                                                      |
|---------------|--------|--------------------------------------------------------------|
| `write_file`  | medium | Write a file inside the workdir. Path traversal blocked.     |
| `read_file`   | low    | Read a file from the workdir.                                |
| `list_dir`    | low    | List a directory inside the workdir.                         |
| `apply_patch` | medium | Apply a unified diff. Pre-screened for forbidden patterns.   |
| `run_shell`   | high   | Run a command. Binary allow-list + deny-pattern enforcement. |
| `run_tests`   | medium | `python -m pytest -v <args>` with parsed pass/fail counts.   |
| `finish`      | low    | Explicit "no more steps needed" signal.                      |

Custom registries can be passed to `AutonomousAgent(registry=...)`
for tests or specialised tool sets.

## Governance gates

Three layers, all fail-closed
(`axiom_autonomous/governance.py:1-26`):

1. **Pre-plan intent gate** — task text routed through
   `axiom_cmaa`. HARM / DECEIVE classifications raise
   `GovernanceBlock` before any sandbox is spawned. The denied step
   is still signed for the audit trail.
2. **Pre-plan SandboxAgent review** — when `axiom_constitutional`
   and a backing LLM are wired, the SandboxAgent must return
   `ALLOW`. Errors fall closed.
3. **Per-action gates** — fast and rule-based on every `ToolCall`:
   - `write_file` / `apply_patch`: path must resolve inside the
     sandbox workdir. `/etc`, `/usr`, `/repo`, traversal rejected.
   - `apply_patch` diff content runs through
     `CodeReflex._FORBIDDEN_PATTERNS` (eval, exec, os.system,
     shell=True, master-key hex, `assert False`).
   - `run_shell`: argv[0] must be in the binary allow-list; the
     full command is screened against `SHELL_DENY_PATTERNS`
     (`axiom_autonomous/governance.py:70`).

## Signed ledger

Every step appends one `EventToken` to the ledger at
`~/.axiom/exoskeleton-ledger.jsonl` (override with
`--ledger PATH` or `--no-ledger`). Each token carries:

- `step_kind` ∈ `plan` / `execute` / `verify` / `replan` / `denied`
- `parent_token_id` — the previous step's token id
- `diff_hash` — SHA-256 of the workdir state at that step
- `chain_sig` — `HMAC(chain_key, run_id‖step_idx‖parent_id‖token_id)`
  computed BEFORE the layer/coordinator/token sigs are applied, so
  splicing a forged token into the middle of a chain breaks the
  chain_sig check during replay
  (`axiom_autonomous/ledger.py:18`).

Verify a run with the CLI:

```
python3 -m axiom_autonomous_agent verify --run-id <id>
```

Or programmatically:

```python
from axiom_autonomous.ledger import verify_chain
from axiom_exoskeleton_ledger import read_ledger, default_ledger_path

entries = read_ledger(default_ledger_path())
ok = verify_chain([e for e in entries if e.use_case.startswith("autonomous:RUN_ID:")])
```

## Configuration

### Environment variables

| Variable                  | Required | Purpose                                                    |
|---------------------------|----------|------------------------------------------------------------|
| `AXIOM_MASTER_KEY`        | Yes      | 32 bytes hex. Used for all signing. CLI exits 2 if unset.  |
| `AXIOM_AUTONOMOUS_IMAGE`  | No       | Override `orivael/axiom-autonomous:local` sandbox image.   |

The Planner, Executor, and Verifier all use whatever LLM backend
`axiom_event_token.backends.default_backend()` resolves — same
backend selection as the rest of AXIOM.

### CLI flags (`run`)

| Flag                 | Default                 | Effect                                                                  |
|----------------------|-------------------------|-------------------------------------------------------------------------|
| `--task` / `-t`      | —                       | Inline task description. Required unless `--task-file` is given.        |
| `--task-file` / `-f` | —                       | Read task text from a file.                                             |
| `--workdir` / `-w`   | (required)              | Sandbox workdir. Created if missing.                                    |
| `--budget-steps`     | `30`                    | Max plan/execute/verify cycles before forced termination.               |
| `--wall-seconds`     | `900`                   | Max wall-clock seconds for the entire run.                              |
| `--sandbox`          | `docker`                | One of `docker` / `local` / `docker_required`.                          |
| `--ledger`           | `~/.axiom/exoskeleton-ledger.jsonl` | Override ledger path.                                        |
| `--no-ledger`        | off                     | Skip ledger append entirely (testing only).                             |
| `--no-dev-cycle`     | off                     | Skip `DevCycleRecord` append at terminal step.                          |
| `--json`             | off                     | Emit full result JSON on stdout in addition to stderr summary.          |

## Python API

```python
from pathlib import Path
from axiom_autonomous import AutonomousAgent

agent = AutonomousAgent(sandbox_prefer="docker_required")
result = agent.run(
    task="add a /healthz endpoint to server.py and test it",
    workdir=Path("/tmp/work-1"),
    budget_steps=20,
    wall_seconds=600,
)

print(result.success, result.steps, result.aborted_reason)
for sg in result.plan.subgoals:
    print(sg.id, sg.done, sg.description)
```

Public surface from `axiom_autonomous/__init__.py:14`:

- `AutonomousAgent` — the orchestrator
- `AutonomousRunResult` — return value of `run()`
- `Plan`, `Subgoal`, `ToolCall`, `Observation`, `Verdict` — frozen
  dataclasses for the data flowing through the loop

## Limitations

- The wheelhouse at `/wheels` in the sandbox image ships empty.
  Workloads that need extra Python packages must populate it at
  build time; the running container has no network for live pip.
- `LocalSandbox` (the docker fallback) has no filesystem or network
  isolation. Only use it for trusted task descriptions or in CI
  where the host itself is the sandbox.
- The default sandbox image lacks compilers, system tooling beyond
  `git`, and language runtimes other than Python 3.11. Add what
  your tasks need to `deploy/autonomous/Dockerfile`.
- `AXIOM_MASTER_KEY` must remain stable between `run` and `verify`
  or the chain signatures won't validate.

## Testing

```
AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))') \
  python3 -m pytest tests/test_axiom_autonomous_*.py -q
```

The autonomous-agent suite (~5 test files) covers the orchestrator,
governance gates, ledger chain, parser, and tools. Tests use
`LocalSandbox` so docker is not required to run them.

## Related docs

- `docs/FIREWALL_PHASE_STATUS.md` — overall shipping status
- `docs/NANO_DEV_AGENT.md` — the smaller dev-agent this loop builds
  on conceptually
- `deploy/autonomous/Dockerfile` — sandbox image build
- `deploy/autonomous/tool_runner.py` — the in-container dispatch
  script invoked by `docker exec`
