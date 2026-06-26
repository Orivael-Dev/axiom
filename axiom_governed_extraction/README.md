# AXIOM Governed Extraction (prototype)

Large-scale data extraction is the ideal workload for a **compact model + a governance
layer**: it's a constrained parse (not reasoning-heavy, so a small model does it well),
it runs at scale (so a cheap on-prem model is the right economics), and it touches the
most regulated data there is (so compliance is the whole point).

The thesis: **don't ask the model to judge what's safe to extract or where it can go.**
The model only *proposes* fields. Every governance decision is **deterministic and
auditable** — which is a stronger compliance story than "the model decided."

```
document ─▶ [pre-guard] ─▶ extractor backend ─▶ [post-guard] ─▶ verified sink
              injection      (model proposes        min-necessary · grounding ·
              · scope         fields + conf)        confidence gate · egress
              └──────────────── HMAC-signed manifest, 1 per record ───────────┘
```

## What the governance layer enforces
| Guard | Rule | Verdict |
|---|---|---|
| Injection screen | document content is DATA; embedded instructions never executed | `INJECTION_FLAGGED` |
| Minimum-necessary | for a de-identified purpose, direct identifiers are redacted regardless of model confidence | `FIELD_REDACTED` |
| Grounding / no-fabrication | a value is released only if it appears in the source; ungrounded values are nulled | `FABRICATION_BLOCKED` |
| Confidence gate | below-threshold fields are held for human review, not output | `FIELD_REVIEW` |
| Egress gate | payloads may only be written to an approved internal sink | `EGRESS_BLOCKED` |
| Audit | every record yields an HMAC-signed manifest entry | — |

The policy is declared in [`policy/medical_extraction.axiom`](policy/medical_extraction.axiom)
(AXIOM language) with the field authorization map in
[`policy/medical_extraction.schema.json`](policy/medical_extraction.schema.json).

## Why the small model makes this *easier*
- The backend is the only untrusted part. Swap `NimBackend` for a fine-tuned
  **SmolLM-135M** exposing the same `.extract()` and **nothing else changes**.
- Unauthorized fields are stripped deterministically — a tiny model can't leak what
  the policy redacts.
- Grounding turns "no hallucinated PHI" into an enforced invariant, not a hope —
  exactly the backstop a 135M model needs.

## Run it
```bash
python run_demo.py                      # offline, deterministic Mock backend
python run_demo.py --nim                # live NIM llama-3.3-70b extractor (needs NVIDIA_API_KEY)
python run_demo.py --sink evil.net      # demonstrate an egress BLOCK
```

The Mock backend deliberately over-extracts identifiers and emits one ungrounded
value, so the guards visibly fire on the sample records in `samples/`.

## Files
- `governed_extractor.py` — guard stages, grounding, signing, the `GovernedExtractor` API
- `backends.py` — `MockBackend` (offline) and `NimBackend` (llama-3.3-70b); 135M slot
- `run_demo.py` — CLI runner + governance metrics
- `policy/` — the `.axiom` policy and the field schema
- `samples/` — sample medical records (one carrying a prompt-injection payload)

## Roadmap
1. Replace `NimBackend` with a fine-tuned SmolLM-135M (grammar-constrained decoding).
2. Distill SFT data for the schema from a NIM teacher (llama-3.3-70b).
3. Persist the manifest to the AXIOM exoskeleton ledger; wire the human-review queue.
4. Swap the schema/policy to target finance or support verticals (architecture is unchanged).
