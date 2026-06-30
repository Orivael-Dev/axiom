# Orivael — Launch Playbook

Ready-to-post copy for each platform, the sequence, and the launch-day checklist.
Edit the bracketed bits, keep the honesty — it's the differentiator.

Asset: `launch/orivael_guard_demo.gif` (the guard blocking a destructive action, signed).
Generate/edit with `python launch/make_demo_gif.py`.

---

## 1 · Hacker News — `Show HN`

**Title** (≤ 80 chars, no hype, no emoji):
> Show HN: Orivael – block unsafe AI-agent tool calls before they run (self-host)

**Body:**
> Most "AI security" scans prompt/response text. The thing that actually hurts in an
> agentic system isn't the text — it's the *action*: the agent calling `delete_records`,
> `send_email(to=all)`, or wiring money. Orivael is a runtime that intercepts every tool
> call **before it executes**, rules PASS / WARN / BLOCK (with a human-approval path), and
> signs every decision into a hash-chained audit log.
>
> It's not a model — it's a control plane you put in front of one. Self-hostable; the guard
> is stdlib-light and runs in microseconds (no LLM call on the hot path).
>
> Honest about what it does and doesn't do:
> - We benchmark **catch *and* over-block together** — a guard that blocks everything is
>   useless. On our Layer-0 intent gate alone: ~42% catch at **0% over-refusal**; the full
>   stack catches more (we publish the numbers, including the misses).
> - It does **not** make a model "safe." It governs what the model is allowed to *do*, and
>   proves what happened.
>
> Live demo (no install): [firewall.orivael.dev]. Type your own scenario — the model reasons
> live and the guard rules on whatever tools it tries to call.
>
> Repo / docs: [link]. Happy to go deep on the threat model, the over-block tradeoff, the
> audit signing, or where it falls short — I'll be here all day.

*HN rules: post Tue–Thu ~8–10am ET. Do NOT ask for upvotes. Strip every buzzword
("constitutional", "entanglement") from the headline — save the deep architecture for
people who click through. Your credibility is the honesty, not the adjectives.*

---

## 2 · Product Hunt

**Name:** Orivael
**Tagline** (≤ 60 chars):
> The control plane for AI agents — govern before they act

**Description:**
> Orivael sits between your users, your agents, and your tools and enforces policy on every
> action *before it runs*. It blocks unsafe or irreversible tool calls, routes high-risk
> actions to human approval, and signs every decision into a tamper-evident audit trail.
> Built for teams shipping AI agents that can't afford a "the bot deleted prod" incident.
>
> • Intercepts tool calls, not just text • PASS / WARN / BLOCK + approval • Signed audit log
> • Self-hostable, microsecond hot path • Live demo, bring your own scenario

**Maker's first comment:**
> Hi PH 👋 I built Orivael after watching an "authenticated, well-formed" request walk an
> agent straight into a destructive action — the API security stack saw nothing wrong,
> because nothing *was* wrong about the request. The damage was in the **action**.
>
> So Orivael governs the action layer: every tool call is ruled on before it executes, and
> every decision is signed so you can prove what happened. The demo isn't scripted — type
> any situation and watch the guard rule on whatever the model tries to call.
>
> It's free to try and self-hostable. I'd love your hardest "but what about…" — especially
> on the over-blocking tradeoff, which is the part everyone else hides. AMA.

*PH: schedule 00:01 PT. Gallery = the GIF first, then the 3-panel demo, then a benchmark
card. Rally your list in the first 2 hours. Reply to every comment.*

---

## 3 · Indie Hackers (build-in-public story)

**Title:** I built an "AI firewall" for agents — and the honest benchmark surprised me

**Post:**
> Everyone selling AI safety quotes a catch rate. Almost nobody quotes the *over-block*
> rate — the benign requests they wrongly refuse. That asymmetry bugged me, so I built the
> benchmark to report both, then built the runtime to pass it.
>
> What I learned shipping it:
> - The Layer-0 gate (fast, pattern-based) catches ~42% on its own at 0% over-refusal. That
>   number looks bad until you realize it's *supposed* to be the cheap first filter — and
>   publishing it honestly earned more trust than a "99%" claim ever would.
> - I added a feedback loop so every miss becomes a validated rule (catch goes up, over-block
>   stays flat). That loop — not the rules — is the actual moat, because it runs on data a
>   competitor doesn't have.
>
> Live demo, repo, and the full numbers (including the misses): [links]. Building in public —
> ask me anything about the architecture or the business.

*IH rewards transparency + metrics + the journey. Keep posting milestones, not just the
launch.*

---

## 4 · DevHunt

**Tagline:** A drop-in guard for AI agents — block unsafe tool calls, sign every decision.
**Blurb:**
> Self-hostable runtime that intercepts agent tool calls before they execute (PASS/WARN/
> BLOCK + human approval) and writes a signed, hash-chained audit log. MCP-compatible;
> stdlib-light, microsecond hot path. Live demo + repo: [links].

---

## 5 · BetaList (pre-launch, submit ~3 weeks early)

**One-liner:** Governance runtime for AI agents — block unsafe actions, prove what happened.
**Description:**
> Orivael is the control plane between your prompts, models, tools, and actions. It enforces
> policy on every agent action before it runs and signs every decision for audit. Early
> access for teams putting AI agents into production. Join the list for an invite + the
> self-host beta.

---

## 6 · Peerlist (Launchpad)

**Tagline:** Govern AI agents before they act — signed, self-hostable.
**Blurb:**
> A runtime that rules on every AI-agent tool call before execution and records a
> tamper-evident audit trail. Built for production agents where an unsafe action is a real
> incident, not a demo bug. Try the live guard, self-host the rest. [links]

---

## Launch sequence

| When | Do |
|---|---|
| **T-3 wks** | BetaList submission live · landing page + signup form up · start collecting emails |
| **T-1 wk** | Pre-write all posts · line up PH hunter (optional) · warm your list ("we launch Tue") · record the GIF/video |
| **Launch day (Tue)** | **00:01 PT** Product Hunt + DevHunt + Peerlist (shared audience, compounds) · rally list hours 0–2 |
| **Same day or +1** | **Show HN** — only when you can babysit comments 8 hrs. Keep it purely technical. |
| **Launch week** | Indie Hackers story · then keep posting milestones |

**Rule:** never copy-paste the same blurb everywhere. HN/DevHunt = plain + technical.
PH/Peerlist = polished + visual. IH = story + numbers. BetaList = the promise.

## Launch-day checklist

- [ ] Live demo up and load-tested (`firewall.orivael.dev` + governed-agent demo)
- [ ] `orivael_guard_demo.gif` in the PH gallery (first slot) and the HN/PH/X posts
- [ ] 30-second quickstart at the top of the README
- [ ] A clear **free / self-host** path (no "book a demo" wall for devs)
- [ ] Benchmark page live with **catch + over-block + the misses** (the honest card)
- [ ] Founder free all day to answer — especially the over-block and threat-model questions
- [ ] Pricing/beta-mode language consistent (beta = free; no Stripe surprises)
- [ ] Strip "constitutional"/"entanglement" from headlines; keep them for the deep-dive page
