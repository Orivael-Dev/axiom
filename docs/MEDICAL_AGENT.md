# AXIOM Medical Research Agent

Orchestrator for the medical research instrument. Composes a sealed
medical AXM container with six layer delegates, fires the active
profile per source, wraps the per-source bundle in a
`MedicalCoordinatorToken`, and renders a bracketed descriptor for
downstream LLM use.

The implementation lives in `axiom_medical_agent.py:1`. It is a
sibling of `ExoskeletonAgent` — not a subclass — because the
workflow differs.

## Composition

| Component                                          | Role                              |
|----------------------------------------------------|-----------------------------------|
| `axiom_medical_container.build_medical_container`  | Container + core dict             |
| `examples/medical_pack.MEDICAL_DELEGATES`          | 6 layer delegates                 |
| `axiom_event_token.Coordinator`                    | Per-layer compose                 |
| `axiom_medical_coordinator.MedicalCoordinatorToken`| Cross-layer bind                  |
| `axiom_medical_governance.MedicalGovernanceCheck`  | Deterministic governance verdict  |
| `axiom_medical_descriptor.render`                  | Bracketed LLM-ready descriptor    |
| `axiom_medical_ledger`                             | Best-effort signed audit          |

## Layer activation profiles

`LAYER_ACTIVATION_PROFILES` selects which delegates fire for the
current question. Call `agent.list_profiles()` for the live list.
Typical profiles:

| Profile       | Use case                                                |
|---------------|---------------------------------------------------------|
| `summarize`   | Default — narrative summary of cited sources            |
| `mechanism`   | Pathway / mechanism extraction                          |
| `evidence`    | Tier-classified evidence audit                          |
| `safety`      | Surface contradictions and safety flags                 |

## API

```python
from axiom_medical_agent import MedicalResearchAgent

agent = MedicalResearchAgent.from_default_pack(backend=...)
result = agent.research(
    research_question="What mechanisms link GLP-1 drugs to "
                      "reduced inflammation?",
    sources=[
        {"name": "Cochrane 2023 systematic review",
         "source_type": "systematic_review",
         "text": "..."}
    ],
    profile="mechanism",
)
print(result.descriptor)
print(result.coordinator_tokens[0].to_json(indent=2))
```

`ResearchResult` carries:

- `event_tokens` — per-layer signed `EventToken`s
- `coordinator_tokens` — one `MedicalCoordinatorToken` per source
- `descriptor` — bracketed string for LLM prompt injection
- `manifest_root` — Merkle root over the event tokens
- `requires_human_review` — True if any governance verdict flagged it
- `tier_distribution` — count per evidence tier (1–5)
- `container_id` — sealed container identifier

## Constructors

- `MedicalResearchAgent.from_default_pack(backend=...)` — bundled
  default container
- `MedicalResearchAgent.from_path(axm_path, backend=..., ledger=...)`
  — load a sealed `.axm`

## CANNOT_MUTATE enforcement

`research()` calls `_enforce_cannot_mutate()` before firing. If any
field marked CANNOT_MUTATE in the sealed container has been silently
changed since seal, the call raises `MedicalAgentError` and writes
nothing to the ledger.

## Evidence tiers

Each source's tier is taken from `evidence_tier` / `source_tier` on
the delegate payload; if missing, `classify_source(payload)` is
called against `source_type`. Tiers are 1 (highest, e.g. Cochrane
systematic review) through 5 (lowest, e.g. expert opinion).

## CLI

```
python axiom_medical_agent.py --question "..." --profile mechanism
```

Run with `--profile` to switch activation profiles and `--sources`
to point at a JSONL of source dicts.
