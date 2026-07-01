# We benchmarked our AI guard honestly. Here's what works — and what doesn't.

*Orivael — governance runtime for AI agents. This is the post to link from Show HN.*

Every "AI safety" product quotes a **catch rate**. Almost none quote the **over-block
rate** — the benign requests they wrongly refuse. That asymmetry is the whole game: you
can hit 100% catch by blocking everything. So we built the benchmark to report **both**,
then built the runtime to pass it. Here are the real numbers, including the misses.

## The rule: never report catch without over-block

A governance result is only meaningful as a *pair*. We score four axes at once and refuse
to show one without the others:

| Axis | Question | The trap |
|---|---|---|
| **Catch** | does it block unsafe actions? | "100%!" (by blocking all) |
| **Over-block** | does it pass benign requests? | the cost everyone hides |
| **Integrity** | can you prove the audit log is complete & unaltered? | editable logs = no governance |
| **Overhead** | what latency does governing add? | a guard nobody can afford to run |

## The numbers (reproducible)

**Layer-0 intent gate** — the fast, pattern-based first filter (no LLM on the hot path):

```
CATCH        :  42%   (block unsafe)
OVER-BLOCK   :   0%   (false positives on benign)
  over-refusal:  0%   (benign-but-trigger-shaped prompts)
OVERHEAD     :  ~80 µs / request
INTEGRITY    :  PASS  (every verdict HMAC-signed; tampering detected)
```

**42% looks bad until you understand it's *supposed* to.** Layer 0 is the cheap, conservative
first pass — its job is to never over-block and catch the obvious. It deliberately misses
malware/phishing/etc. that the deeper layers are meant to catch. We publish the 42% rather
than quote a full-stack number we'd have to caveat — and we list the exact prompts it missed.

**The calibration flywheel** — every miss becomes a *validated* rule. A candidate pattern is
committed only if it raises catch with **zero** new over-block, measured on the bench:

```
CATCH      : 42%  →  92%
OVER-BLOCK :  0%  →   0%   (guardrail held)
            6 patterns committed · 22 rejected by the guardrail · all signed
```

The loop — not the patterns — is the point. The patterns are copyable; the loop runs on
your production misses, which a competitor doesn't have.

**Memory integrity** — can stored conversation be silently altered? Over a 1,000-day horizon:

```
content tamper detected : 100%   (every altered memory fails its signature, refused at recall)
deletion (no hash-chain): NOT detected  ← we say so; it's the next thing we're closing
```

## Does it "reason," or just memorize?

Honest ladder. The guard generalizes from examples, and we measured exactly how far:

| Generalizes by | Catches | |
|---|---|---|
| exact string (ingest) | verbatim repeats only | where naive denylists sit |
| concept / meaning | rewordings, paraphrases | a reworded attack at cosine 0.90 |
| **the boundary crossed** | **same-reason attacks across domains** | "build a bomb" ≈ "synthesize a nerve agent" — 0.0 by words, **1.0 by *why*** |

The honest cap: a heavy paraphrase using all-new vocabulary outside our concept map is
**missed** by the zero-dep embedder — a real sentence encoder closes it, same interface, no
code change. We test that miss rather than hide it.

## What it does NOT do

- It does **not** make a model "safe." It governs what the model is allowed to **do**, and
  proves what happened. Different problem.
- Layer 0 alone is **not** the product — it's the floor under the calibration loop + the
  deeper guards.
- The corpora here are **illustrative**. Swap in XSTest / OR-Bench / AdvBench / AgentHarm for
  publishable numbers; the 92% above partly reflects learning the test's own misses (we say
  so). The *mechanism* is what generalizes.

## Reproduce it

```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
python axiom_governance_bench.py          # the four-axis scorecard
python axiom_guard_calibration.py         # the 42% → 92% flywheel (over-block held)
python axiom_memory_integrity_bench.py --days 1000   # 100% tamper detection
```

Live demo (no install, bring your own scenario): **firewall.orivael.dev**.

We'd rather show you a real 42% with a 0% over-block and a path to 92% than a "99% safe"
you can't check. Tell us where it's wrong — that's how the loop gets better.
