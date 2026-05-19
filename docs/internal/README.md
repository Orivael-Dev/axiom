# `docs/internal/` — private working overlay

This directory holds working materials that are deliberately kept
off the public mirror. The directory itself is tracked (because
this README is tracked), but **everything else inside is
gitignored** — see the `docs/internal/*` rule in `.gitignore` at
the repo root.

## What lives here

| Path | Purpose | Driven by |
|---|---|---|
| `ROADMAP_TRACKER.md` | 5-month investor/GTM roadmap, broken into months → weeks → asset checklists | hand-edited; parsed by `axiom_status` |
| `sales/companies.jsonl` | Target enterprise accounts (industry, size, signal, status) | `axiom_sales_context add company …` |
| `sales/buyers.jsonl` | Named buyers (role, company, signal, last contact) | `axiom_sales_context add buyer …` |
| `sales/objections.jsonl` | Real objections + AXIOM's response + outcome | `axiom_sales_context add objection …` |
| `sales/competitors.jsonl` | Competitor rows (strength, gap, AXIOM wedge, honest concession) | `axiom_sales_context add competitor …` |
| `sales/calls/<date>.md` | Discovery / sales call notes (one file per call) | hand-written |
| `sales/notes.md` | Free-form sales workspace | hand-written |

Auto-injected into the 5 sales-related exoskeleton delegates
(`sales_objection_handling`, `outreach_personalization`,
`enterprise_targeting`, `competitive_analysis`,
`customer_discovery`) by `ExoskeletonAgent.invoke(...)`. Opt out
with `--no-context` on the CLI.

## Live state

```
python3 -m axiom_status                  # month/week/checklist/recent runs
python3 -m axiom_sales_context list buyers
python3 -m axiom_sales_context relevant sales_objection_handling \
    --query "buyer says no budget this year"
```

## Privacy

A fresh clone will see only this README. Add nothing to this
directory that you would not be comfortable losing to a `git
clean -fdx` accident — back the files up out-of-band if they're
load-bearing. Nothing here is signed; signing is for runtime
artifacts (event tokens, ledger entries), not source data.
