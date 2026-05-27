# RFC: Twitter Scraper + Signed Reply Agent

> **Status:** DRAFT · planning before any code lands
> **Owner:** Antonio Roberts · Orivael Dev
> **Created:** 2026-05-22
> **Comment thread:** (file a GitHub issue tagged `rfc-twitter-agent`)

## tl;dr

A two-sided agent that signs an **input event token** when it
scrapes a tweet and a separate **output event token** when it
drafts a candidate reply. The reply **halts at a deterministic
gate** — the founder reviews the candidates and approves which one
(if any) gets posted. Approval is signed into the token's
governance slot; rejection feeds the retrospect loop so the next
draft learns.

Mirrors the existing **Patch Agent** pattern (draft → halt at
gate → human approves → side-effect fires). Same architectural
shape, replace `diff` with `reply text` and `git apply` with
`twitter POST`.

## Why a planning doc instead of code

Three things make this riskier than the patch agent and worth
locking down before implementation:

1. **Posting has real-world side effects.** A bad reply ships to
   public Twitter; you can delete it but not un-ring the bell. The
   patch agent's worst-case is `git apply -R`; this one's
   worst-case is "a reply on the public record that an investor /
   reviewer / journalist screenshots."
2. **Twitter ToS + API access is non-trivial in 2026.** X's API v2
   pricing starts at $100/mo (Basic) and ~$5K/mo (Pro). Read-only
   scraping outside the API is in a legal gray zone. The right
   choice depends on volume + budget + risk tolerance, not on
   engineering preference.
3. **The honesty post-scan still applies.** A draft reply that
   says "AXIOM has helped startups…" gets blocked by the existing
   `axiom_exoskeleton_honesty.scan` before it ever reaches the
   approval gate. That's a feature, not a bug — but it changes
   how candidate generation should be designed.

## What this RFC asks for input on

### Q1 — Read path: official API vs. unofficial scraping

| Option | Pro | Con |
|---|---|---|
| **(a) Official X API v2 Basic** ($100/mo) | ToS-clean, stable, supports POST replies | Per-month cost + ~10K read req/month is low; might not survive a volume spike |
| **(b) Official X API v2 Pro** ($~5K/mo) | Production-grade volume + posting | Way too expensive for a solo founder beta |
| **(c) Unofficial scraping via `twikit` / `snscrape`** | Free, no per-month commitment | ToS-violating, breaks on every X frontend change, account-bannable for the scraping account |
| **(d) RSS bridges** (e.g. nitter instances) | Free, read-only, no auth | Unreliable; nitter instances rotate; limited search |

**Proposal:** Start with **(a) Basic** for production posting +
**(c) twikit** for higher-volume read-only scraping during the
exploration phase. The two paths get plumbed through different
backends inside the agent.

### Q2 — Posting account

Reply tokens are signed under the AXIOM key, but the TWITTER
account that actually posts is separate. Options:

- **(i) @OrivaelDev** — bound to AXIOM brand; mistakes are
  on-brand
- **(ii) A new @AxiomGovernance handle** — segregated from
  personal/founder voice; less brand contamination on a bad reply
- **(iii) The founder's personal handle** — most authentic, worst
  reversibility

**Proposal:** (ii) — new handle dedicated to the AI-governance /
research-newsletter voice. Posts there can never accidentally
hit followers who signed up for the founder's personal feed.

### Q3 — When does the agent draft a reply at all?

The agent should NOT respond to every scraped tweet. Triggers
need a stated rule, signed into the token. Options:

- **(a) Explicit keyword matches** (e.g. `axiom`, `intent firewall`,
  `signed event token`, `AI governance benchmark`)
- **(b) Mention of @AxiomGovernance directly**
- **(c) Replies to specific seed accounts** (researcher / journalist
  watchlist)
- **(d) Quote-tweets / replies to AXIOM's own posts** (engagement
  amplification)

**Proposal:** (a) + (b) + (c). NOT (d) — too easy to drift into
self-reply loops.

### Q4 — Number of candidate replies per scraped tweet

Per the patch-agent pattern: drafts halt at the gate. How many
candidates per scraped tweet?

- N=1 → fastest review, no comparison signal
- N=3 → comparison signal, 3× LLM cost
- N=5 → more diversity, 5× cost, decision fatigue

**Proposal:** N=3 default. Each candidate gets its own honesty
scan + sentiment label.

### Q5 — Approval gate UX

Where does the founder review + approve? Options:

- **(a) `ui.py` Streamlit tab** — same playground pattern as the
  existing Exoskeleton / Medical / Patch Agent tabs
- **(b) Web console in `web/twitter_review.html`** served by the
  research server
- **(c) CLI:** `python3 -m axiom_twitter_agent review` shows
  pending replies in the terminal
- **(d) Email digest** with approve/reject links

**Proposal:** (a) for v1. The playground pattern is already there;
adding an eighth tab is ~250 lines. (b) + (d) when there are
enough pending replies that a tab is too slow.

### Q6 — Auto-rate-limiting

What's the maximum daily reply volume? A runaway loop could spam
50 replies overnight before anyone notices.

**Proposal:** Hard cap, signed into the agent's constitutional
rules: max 10 approved replies/day, max 30 drafts/day. Refused
loud (CANNOT_MUTATE) — only an explicit re-pack can raise the
limit. CLI flag `--bypass-cap` requires an explicit override flag
+ the bypass is signed into the EventToken's governance slot.

## Architectural fit — what's already there

| Existing AXIOM primitive | Role in the Twitter agent |
|---|---|
| **Patch Agent's draft → halt → approve pattern** (`axiom_patch_agent.py`) | Exact same flow. Replace `PatchDraft` with `TweetReplyDraft`, `git apply` with `twitter.post_reply`. Lift ~70% of the existing module structure. |
| **MonotonicGate** (tests-must-pass) | Replaced by an honesty-scan gate: the draft reply must NOT trigger overclaim patterns or PHI redaction. `RelevanceRefusal` becomes `HonestyRefusal`. |
| **`axiom_patch_agent_ledger`** | Direct template for `axiom_twitter_agent_ledger` — same shape, namespace `axiom-twitter-ledger-v1`. Records approval + rejection + revocation. |
| **`axiom_exoskeleton_honesty.scan`** | Already catches invented track-record claims in any LLM output. Runs unchanged on Twitter drafts. |
| **EventToken governance slot** | Signs the approval/rejection alongside the reply text + recipient tweet id. |
| **Retrospect ingestion** (`dev_agent_improvements.jsonl`) | Rejected drafts feed it with `training_signal="negative"`. Approved drafts feed it with `training_signal="positive"`. The next draft learns. |
| **Patch Agent's `revoke` subcommand** | Direct analog: `revoke` for an already-posted reply = `twitter.delete_tweet(tweet_id)` + sign a revocation token referencing the original approval. |

The integration surface is small. The Twitter SDK is the new
external dependency.

## Out of scope for v1

- **Autonomous posting without human approval.** Hard refused.
  Every reply requires explicit founder approval. There is no
  flag to disable the gate.
- **DM-based engagement.** v1 is replies to public tweets only.
  DMs add a privacy + consent layer that needs its own RFC.
- **Multi-account orchestration.** v1 posts from one configured
  account.
- **Sentiment-based auto-skip.** Detecting "this tweet is a flame
  war, don't engage" sounds great, fails in practice. Surface the
  thread context in the review UI and let the founder skip.
- **Auto-following / auto-liking / auto-anything.** Reply-only.

## Failure modes this RFC tries to avoid

1. **An overclaim slips through.** Honesty post-scan is the
   defense; same scanner as the exoskeleton.
2. **Approval-by-habit.** If the founder approves 9 in a row,
   the 10th deserves more scrutiny. Surface a "you've approved
   N in the last hour; pause?" interstitial.
3. **Posting account compromise.** Posting OAuth token lives in
   `.env`, never in code. Rotation procedure documented before
   v1 ships.
4. **Tweet ID collision in the ledger.** Twitter IDs are 64-bit
   ints; primary key in the ledger is `(tweet_id, draft_id)`.
5. **Posting to a tweet the agent SCRAPED with twikit but can't
   POST to via the official API** (deleted, rate-limited, etc.).
   The post step checks the tweet still exists before sending.
6. **Self-reply loops.** The agent's own posts get excluded from
   the scrape set by Twitter user-id filter.

## Phased plan

### Phase 0 — RFC + decisions locked (~1 week)

This doc + answers to Q1–Q6 above.

### Phase 1 — Read-only scraper, no posting (~2 days)

- `axiom_twitter_agent.scrape` reads tweets matching the configured
  triggers; signs each as an EventToken under `axiom-twitter-input-v1`
- No drafts, no posting
- Deliverable: a CLI that prints scraped tweets + their signed
  token IDs. Confirms the read path is stable before any LLM cost
  kicks in.

### Phase 2 — Draft generation + review UI (~3 days)

- `TweetReplyDraft` dataclass (mirrors `PatchDraft`)
- N=3 candidate replies per scraped tweet via the existing
  exoskeleton `outreach_personalization` delegate or a new
  `twitter_reply` one
- Each draft scanned for honesty violations + tagged with sentiment
- Streamlit tab in `ui.py`: list of pending tweets + N candidates
  each + approve/reject buttons
- No posting yet — approval just signs the choice and writes to
  the ledger. "Send" button still copies to clipboard for now.

### Phase 3 — Live posting (~2 days)

- Switch the "Send" button from copy-to-clipboard to actual API
  POST
- Rate limits enforced (Q6)
- Revoke subcommand: deletes a posted reply + signs a revocation
- Daily-cap CANNOT_MUTATE check

### Phase 4 — Retrospect feedback loop (~1 day)

- Rejected drafts → `dev_agent_improvements.jsonl` with
  `training_signal=negative` (already wired in patch-agent)
- Approved drafts → positive signal
- Newsletter Issue: "What we learned from 100 reply drafts" once
  there's data

## Cost estimate

| Item | Monthly |
|---|---|
| X API v2 Basic | $100 |
| LLM (DeepSeek V3 at N=3 drafts × ~20 tweets/day) | $5–10 |
| Hetzner VPS time (already running) | $0 incremental |
| **Total** | **~$110/month** |

The X API cost is the dominant line item. The decision to commit
to it is mostly about whether Twitter is the right channel for
the AXIOM brand at this stage. Linkedin / Mastodon / Bluesky are
alternatives with cheaper / freer APIs and different audience
mixes — worth considering before locking in.

## How to comment

1. **GitHub issue** tagged `rfc-twitter-agent` with your answer to
   any of Q1–Q6.
2. **Specifically wanted:** input from anyone who's run an
   automated reply agent under X's current ToS and either got
   away with it for >6 months or got banned. Both data points
   are informative.

## Timeline (target, soft)

- **Week 1:** RFC comment period
- **Week 2:** lock decisions; Phase 1 scaffold
- **Week 3:** Phase 2 UI
- **Week 4:** Phase 3 posting + Phase 4 retrospect
- **Total: ~4 weeks to a useful v1**

All dates slip if a question on the comment thread reframes
the design. Twitter ToS clarity matters more than calendar.

## License

Code: MIT. The agent itself is open source; the configured
account, OAuth tokens, and reply ledger are private.
