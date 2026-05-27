# `axiom_autonomous.scenarios` — real-world tasks harness

A curated set of coding tasks the AXIOM autonomous agent can be
pointed at, with external success criteria checked against the
post-run workdir (pytest passes, files present, forbidden files
sha256-unchanged).

## Quickstart

```bash
# List the registered scenarios.
python3 -m axiom_autonomous.scenarios list

# Run all 5 with a Docker-isolated sandbox (recommended).
python3 -m axiom_autonomous.scenarios run \
    --sandbox docker_required \
    --output ./scenarios_run.json -v

# Run a single scenario, locally (no Docker), for fast smoke testing.
python3 -m axiom_autonomous.scenarios run \
    --only S001-cli-flag \
    --sandbox local \
    --output /tmp/smoke.json -v
```

The runner writes a signed JSON report (HMAC-SHA256 under the
`axiom-autonomous-scenarios-v1` key) carrying meta, per-scenario
rows, and a summary block. Each row records the autonomous agent's
internal verdict (`agent_success`), the external criteria check
(`criteria_passed`), step count, wall time, the chain head token id
(traceable in the autonomous ledger), and the criteria detail dict.

Exit code: `0` iff every scenario passes its criteria; `1`
otherwise. CI can treat the runner as a gate.

## Adding a scenario

1. Create `seeds/<your-scenario-id>/` with the starter files the
   agent will work against (target source modules + their tests).
2. Append one JSON line to `library.jsonl` matching the `Scenario`
   schema in `library.py` (id, title, task, seed, criteria,
   budget_steps, wall_seconds, tags).
3. Run `python3 -m axiom_autonomous.scenarios list` — your scenario
   should appear.
4. Smoke-run with `--only <your-id> --sandbox local` first to catch
   library / criteria bugs before paying for a Docker run.

## What a "real-world" scenario covers

The 5-scenario v1 set spans four common autonomous-coding archetypes:

| ID | Archetype | What's tested |
|---|---|---|
| S001-cli-flag | Feature addition on existing code | Reads existing code, extends contract, adds matching test |
| S002-fix-failing-test | Bug fix from failing test | Reads failure, locates root cause, makes minimal fix |
| S003-implement-function | Greenfield from docstring + tests | TDD: implement to satisfy specified tests |
| S004-extract-helper | Refactor without behaviour change | DRY a duplication; existing tests must still pass |
| S005-write-readme | Multi-file reading → new artifact | Read 3 modules, produce coherent prose |
