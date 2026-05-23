# Archived prompts — pre pegging-fix (2026-05-23)

Every per-task directory in this archive was created while the
Evaluator was emitting inflated scores. Symptoms on disk:

- `299885cde39a9bc4/worker.json` — 13 iterations, all scoring 9.0
- `8b754c3221e825a8/worker.json` — pegged at 8.5 for 6 of 7 iterations
- `fe75010ce20136a0/worker.json` — climbed 8.0 → 10.0 in 7 iterations
- Every other directory's first iteration scored ≥8.0 — under a
  rigorous Evaluator that's not plausible from a seed Worker prompt.

Three failure modes diagnosed and fixed in commit **eb112e5**:

1. `axiom_constitutional/agents/evaluator.py` — strict scoring bands
   echoed in the user message + verbatim-evidence requirement for any
   score ≥8.0 + post-call demoter that knocks empty-evidence high
   scores down to 7.5.
2. `axiom_constitutional/rubric.py` — `format_for_prompt` injects the
   strict bands into the rubric body so they hit the Evaluator on
   every call regardless of the auto-generated scoring_guide.
3. `axiom_constitutional/evolution.py` — new `detect_score_pegging()`
   that aborts the loop when the same score appears in N consecutive
   iterations.

## Why these were archived rather than deleted

`axiom_constitutional/agents/base.py:36` loads task-scoped saved
prompts as the highest-priority seed for the Worker. Leaving these
files at their original `prompts/{task_hash}/` paths would mean the
next evolution run re-seeds from a Worker prompt that the broken
Evaluator falsely rewarded — re-poisoning the next set of saved
prompts.

Archiving (instead of deleting) preserves them in case:
- You want to grep for a specific phrase the old Worker emitted.
- You want to diff what the new (rigorous) Evaluator scores the same
  Worker prompt at — useful for validating the fix lands.
- A specific high-score prompt happens to actually be good and you
  want to manually re-promote it after eyeballing the output.

## Restoring a specific run

```
git mv prompts/_archive_pre_pegging_fix_2026-05-23/<task_hash> \
       prompts/<task_hash>
```

Don't restore wholesale — the whole archive is suspect.
