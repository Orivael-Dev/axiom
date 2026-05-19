# CLAUDE.md — orientation for future Claude Code sessions

This file is what new Claude sessions read first. It's a map, not
a manual — follow the pointers, don't try to internalise
everything.

## What this repo is

AXIOM is a constitutional-safety + signed-event-token runtime
plus a fleet of founder-workflow agents that sit on top of it.
The public README at `README.md` is the long-form tour.
Reading order for a cold start:

1. `README.md` — product landscape + CLI examples
2. `docs/FIREWALL_PHASE_STATUS.md` — current shipping status
3. `docs/GAME_PLAN.md` — the public 6-month commercial plan
4. `docs/internal/` — **private working overlay** (gitignored;
   tracked README stub lives at `docs/internal/README.md`)
5. `docs/training/` — training-data conventions

## Operative plan (live state)

The day-to-day "what should we be doing this week" lives in:

- `docs/internal/ROADMAP_TRACKER.md` — gitignored; 5-month
  investor/GTM roadmap with month/week boundaries, asset
  checklists, weekly cadence

To see live state in one command:

```
python3 -m axiom_status
```

It pulls the current month/week, asset-checklist progress,
recent delegate invocations (from the signed exoskeleton
ledger), and recent commits. Use `--json` for machine output;
`--update "<substring>"` to check off a TODO.

## Sales knowledge store

A small hand-curated body of sales data (`companies.jsonl`,
`buyers.jsonl`, `objections.jsonl`, `competitors.jsonl`, plus
`calls/<date>.md` and `notes.md`) lives at
`docs/internal/sales/`. It is **auto-injected** into the 5
sales-related exoskeleton delegates:

- `sales_objection_handling`
- `outreach_personalization`
- `enterprise_targeting`
- `competitive_analysis`
- `customer_discovery`

Inspect or add records:

```
python3 -m axiom_sales_context list buyers
python3 -m axiom_sales_context add objection '{"class":"BUDGET", ...}'
python3 -m axiom_sales_context relevant outreach_personalization \
    --query "Jane Doe at Acme"
```

Opt out per-invocation with `--no-context` on `axiom_exoskeleton`.

## Privacy / publishing

`docs/internal/` is gitignored except for its README stub. A
fresh clone has zero private content. Never commit anything
under that directory besides the README. Same goes for
`axiom_files/.honesty/` and `axiom_files/.reviews/` — see
`.gitignore` at the repo root for the full list.

## Branch convention

Develop on `claude/test-axiom-security-KgUcQ` per the harness
instructions. Push there. Don't open PRs unless explicitly
asked.

## Build / test

```
AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))') \
  python3 -m pytest tests/ -q
```

`AXIOM_MASTER_KEY` must be 32 bytes hex. Every test file that
touches the signing layer sets it via the `isolated` fixture.
