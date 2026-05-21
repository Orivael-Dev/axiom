# RFC: Equality Agent for AXIOM Event Tokens

> **Status:** DRAFT · seeking community input before any code lands
> **Owner:** Antonio Roberts · Orivael Dev
> **Created:** 2026-05-21
> **Companion publication:** *Governance Under Question* — Issue +
> Paper TBD once measurement phase completes
> **Comment thread:** (file a GitHub issue tagged `rfc-equality`)

## tl;dr

Build a deterministic, signed, branch-killing fairness layer for the
AXIOM event-token runtime. **But measure before intervening.**
Before any rebalancing code ships, publish a reproducible baseline
of how 5–10 popular open models distribute demographics across a
canonical prompt set. The intervention is calibrated to those
numbers, not to a vibe.

This RFC asks for community input on five design questions where
the right answer is not technical and should not be decided
unilaterally.

## Why AXIOM is well-positioned for this

| Existing primitive | Role in an equality agent |
|---|---|
| QRF branches with `passed` / `rival` / **`killed`** + `kill_reason` | The "kill the trajectory of an overrepresented branch" mechanic is already the QRF model. New use: kill branches whose demographic distribution exceeds a stated skew threshold. |
| `MedicalCoordinatorToken.cross_layer_consistency` | The "fan out across N agents and vote" pattern. Replace `medical` with `equality`, semantics identical. |
| `axiom_exoskeleton_honesty.scan()` (block invented track-record claims) | Identical shape: deterministic post-scan that signs findings into the token. New scanner: demographic-skew detector. |
| Signed event token governance slot | Tamper-evident proof that the bias check fired, with the kill reasons cryptographically anchored. |

Almost nobody else has this combination of primitives. That's the
opportunity. The risk is matching it with sloppy design choices.

## What this RFC asks for community input on

### Q1 — "Balanced to what?"

The hardest design choice. Three real options, each with tradeoffs:

| Option | Pro | Con |
|---|---|---|
| **(a) Population demographics** (US Census 2020 / equivalent) | Defensible default for general queries | Wrong for any global, profession-specific, or historical context |
| **(b) Profession-specific demographics** (AAMC for "doctor", BLS for "engineer", etc.) | Accurate to the actual world | Requires a maintained lookup table per profession; goes stale; what about professions no one has data on? |
| **(c) Equal across recognized categories** (1/N per group) | Easiest to compute, hardest to game | Over-represents minorities relative to ANY real baseline; politically loaded |

**Recommended starting point:** (b) with (a) as fallback when no
profession-specific data is available. But this is a constitutional
choice and should be made publicly, not by me alone.

### Q2 — What categories count?

The classification scheme itself is contested. Open questions:

- Which race/ethnicity taxonomy? (US Census · UK ONS · IPUMS · self-ID)
- Gender: binary · ternary · non-binary inclusive · self-ID
- Age bands?
- Disability? Visible vs. self-identified?
- Religion? Body type? Class signifiers?

Each axis added expands the scoring surface and the failure modes.
**Proposal:** v1 ships with race + gender only, with explicit
hooks for extension. v2 and beyond require an RFC each.

### Q3 — When should balance be SUSPENDED?

This is the most important question, not the rebalancing itself.
Cases where forced balance is clearly WRONG:

- **User specified the demographic.** "Picture of a Chinese doctor"
  — the spec wins.
- **Historical accuracy.** "Picture of a doctor practicing in
  Atlanta in 1955" — applying current demographics is historical
  revisionism.
- **Clinical/genetic relevance.** "Tay-Sachs patient demographics"
  — a real epidemiological cluster, not a bias.
- **Cultural/religious context.** "A wedding in Punjab" — applying
  US demographics is the bias.
- **Statistical or scientific questions.** "What % of NBA players
  are X" — measurement IS the answer.

**Proposal:** an explicit `EQUALITY_SUSPEND_PATTERNS` table (same
shape as `axiom_exoskeleton_honesty.OVERCLAIM_PATTERNS`) with stated
rules + examples. Suspension is signed into the token alongside
why, so an auditor can verify the agent's restraint decisions.

Community input needed: **what other cases belong on the
suspend list?**

### Q4 — Multi-agent voting threshold

The "fan out to N agents and vote" pattern needs an odd N + a
tie-break rule.

- N=3 majority is the smallest defensible vote
- N=5 catches more edge cases but costs 5× per request
- Below threshold consistency → escalate to human review instead
  of auto-deciding

**Proposal:** N=3 by default, `equality_voting_consistency`
recorded per request, below 0.6 triggers `requires_human_review`.

### Q5 — What does the kill record look like?

When a branch is killed for skew, what gets signed into the token?
The audit trail has to be both useful and respectful.

- **Minimum:** kill reason category, the threshold that was
  exceeded, no raw demographic guess on individual generations
- **Maximum:** the inferred-demographic-per-branch histogram, the
  baseline used, the threshold rule, the voting consensus

The maximum is more auditable but also creates a record that
*labels* people. The minimum is more privacy-respecting but harder
to verify externally.

**Proposal:** record the AGGREGATE histogram (counts per category
across the N generations) without per-image labels. The aggregate
is what gets compared to the baseline. Individual generations stay
unlabeled in the signed record.

## Measurement-first phased plan

### Phase 0 — RFC + community input (this doc, ~2 weeks)

- Publish this doc to the AXIOM repo
- Open a GitHub issue tagged `rfc-equality` for comment
- Cross-post the link in a Governance Under Question newsletter
  issue ("AI fairness as a measurement problem before an
  intervention problem")
- Collect input for ~2 weeks before locking the v1 design

**Decision required from comments:** answers to Q1–Q5, especially
the suspend list (Q3).

### Phase 1 — Scorer only, no intervention (~3 days)

A measurement tool that reports demographic distribution; does NOT
modify any output yet.

- `axiom_equality.EqualityScorer` class
  - `score_distribution(prompt, model, n_samples=20) → dict`
  - Returns per-category histogram + skew vs. each candidate baseline
- A canonical prompt set: `doctor`, `nurse`, `CEO`, `criminal`,
  `engineer`, `teacher`, `scientist`, `firefighter`, `pilot`,
  `homemaker` (open to additions)
- Run against 5–10 open models accessible via the AXIOM backend
  abstraction (Qwen 2.5 7B, Llama 3.1 8B, DeepSeek V3, NIM hosted,
  etc.)
- Output: a benchmark CSV + a single-figure summary chart

**Deliverable:** a published *Governance Under Question* issue
+ working paper titled something like "Demographic distribution
in 10 open language models: a reproducible baseline." The data
becomes the calibration source for Phase 2.

### Phase 2 — Constitutional rule + suspend list (~2 days, after Phase 1 data)

With real numbers in hand:

- `axiom_equality.EQUALITY_SUSPEND_PATTERNS` (the result of Q3's
  community input)
- A constitutional rule that fires the scorer at generation time
- Skew threshold calibrated to "1.5× the measured baseline" or
  similar (number set by the data, not by me)
- Branch-killer wiring: when the QRF dispatches multiple branches
  and one's demographic distribution exceeds threshold, that
  branch is killed with `kill_reason="equality_skew"` and the
  kill record signed into the governance layer
- `axiom_event_token.coordinator` extension: a new `equality`
  layer slot

### Phase 3 — Multi-agent voting (~3 days)

- N=3 generations per request when balance applies
- Vote on which subset to return based on lowest skew
- Inconsistent vote (< 0.6 consensus) → `requires_human_review`
  + return the subset with the explicit caveat

### Phase 4 — Continuous measurement (ongoing)

Every quarter, re-run the Phase 1 scorer against the same prompt
set and publish the delta. This keeps the calibration honest as
models update, and gives the newsletter a regular standing report.

## Out of scope for v1

These are real problems but should not block v1 shipping:

- **Image-pixel demographic inference.** v1 measures via text
  descriptions / metadata only. Vision-based inference is a much
  larger ethics + accuracy problem and warrants its own RFC.
- **Real-time intervention on streaming output.** v1 operates on
  completed responses. Streaming-aware killing requires partial-
  output scoring that's hard to do without false positives.
- **Per-jurisdiction policy variation.** v1 ships one constitutional
  rule. A future v2 could ship a "regions" extension where the
  baseline varies by deployment locale, but that's a governance
  problem before it's an engineering one.
- **Adversarial probing for jailbreaks of the suspend list.** Real
  problem, but the right time to harden is after community input
  has shaped what the suspend list contains.

## Failure modes this RFC tries to avoid

1. **Vibes-based intervention.** Shipping a rebalancer without
   measurement = the kind of governance system this project
   exists to question.
2. **Unilateral category choices.** Fairness taxonomies are
   politically loaded; the project owner deciding alone is a
   credibility hit even if the choice is defensible.
3. **Over-broad suspension.** Letting every clinical/historical/
   contextual case suspend balance defeats the purpose.
4. **Over-narrow suspension.** Forced rebalancing in cases where
   the user clearly specified demographic → user-hostile.
5. **Performative tokenism.** A version that's easy to audit but
   doesn't actually change behavior at the boundary that matters.
6. **Honesty regression.** If the scorer's measurements get
   published, the intervention has to match the numbers, not
   diverge from them. Drift between published baseline and
   shipped threshold = lying with extra steps.

## What I'm explicitly NOT asking community input on

These are project-level decisions already settled:

- Whether the agent should be signed (yes — every AXIOM event is)
- Whether kill records belong in the governance layer (yes —
  same place every other constitutional kill goes)
- Whether the rule is CANNOT_MUTATE (yes — same as Tier-5
  patterns in `axiom_medical_safety`)
- Whether the implementation is open source (yes — this is AXIOM)

## How to comment

1. **Best signal:** open a GitHub issue at
   github.com/Orivael-Dev/axiom/issues with the title
   `[RFC-equality] <your proposed answer to Q#>` and tag
   `rfc-equality`.
2. **For policy/taxonomy input:** prefer signed comments —
   organizations with a stake (civil rights orgs, AI fairness
   research groups, professional associations whose membership
   demographics are in scope) carry more weight than anonymous
   votes.
3. **For implementation details:** the standard GitHub PR review
   workflow once Phase 1 is in motion.

## Timeline (target, soft)

- **Week 1–2:** RFC open for comment
- **Week 3:** lock the v1 design based on input
- **Week 4–5:** Phase 1 scorer + measurement run
- **Week 6:** publish the *Governance Under Question* issue +
  working paper
- **Week 7–8:** Phase 2 + Phase 3 implementation
- **Week 9:** ship v1, continue Phase 4 quarterly cadence

All dates slip if community input warrants. The deliverable
quality matters more than the date.

## License

This RFC, the resulting code, and any benchmark data published
under it are CC BY 4.0 (data + docs) / MIT (code). Calibration
baselines used (Census, BLS, AAMC, etc.) are cited per their
respective terms.
