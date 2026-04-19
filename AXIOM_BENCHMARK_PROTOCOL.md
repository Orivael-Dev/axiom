# AXIOM Benchmark Protocol v1.0
**April 2026 · github.com/antonioroberts/promt-agent**

> A benchmark is only valid if it is ABP-verified. The AXIOM Benchmark Protocol defines three
> mandatory gates that any AI agent evaluation must pass before its results can be considered
> trustworthy: uncheatable evaluation, full ledger transparency, and reproducible certification.
> **ABP is an open standard. Any evaluation framework may implement it.**

---

## 1. The Problem

In April 2026, Berkeley RDI published research showing every major AI agent benchmark can be exploited to achieve near-perfect scores without solving a single underlying task. A 10-line Python script defeated SWE-bench. The scoring system behind billion-dollar investment decisions was broken. The core vulnerability: if the reward signal is hackable, a sufficiently capable agent will hack it — not deliberately, but as an emergent optimization strategy.

Three failure modes cause benchmark invalidity:

| Failure Mode | Mechanism | Consequence |
|---|---|---|
| Pattern matching | Agent memorizes test phrasing from prior runs — responds to form not substance | Perfect score. Zero capability. |
| Scorer gaming | Agent learns which keywords trigger high scores — injects them without reasoning | Inflated metrics. Hidden brittleness. |
| Cherry-picking | Developer publishes best run. Debug history hidden. Ledger sanitized. | Published results unverifiable. |

---

## 2. The Three Pillars

### Pillar I — Uncheatable Evaluation

- Teacher agent independent of system under test
- Test variants obfuscated — IDs stripped, phrasing randomized
- Behavior-based scoring — reasoning not keywords
- Empty response guard — aborts on API failure
- Demographic variant testing — 15% tolerance enforced

### Pillar II — Full Ledger Transparency

- Every run logged — no cherry-picking permitted
- Historical rate published alongside current rate
- Append-only ledger — no deletions ever
- SHA256 hash seals the full record
- Both rates in signed certification report

### Pillar III — Reproducible Certification

- Anyone can rerun on independent infrastructure
- Same inputs → same outputs → same certification
- Full test suite + server published open source
- Certification hash verifiable without trusting developer
- HMAC-SHA256 signature on every output manifest

---

## 3. What ABP-Verified Looks Like

```
$ axiom benchmark run my_agent

AXIOM BENCHMARK REPORT (ABP v1.0)
══════════════════════════════════════
Agent:        my_agent v1.2
Score:        92/100
Honesty now:  100%  (40/40 evals)
Honesty all:  44%   (200 evals, full history)
Fairness:     85%   (17/20 — 3 signals documented)
Status:       ABP-VERIFIED ■

■ Prior runs: gaming detected in debug phase
  Documented in ledger. Not hidden.
  Ledger hash: 2c8b7186...
```

**What each line proves:**

- **Score** — behavior on 100 tested scenarios
- **Honesty now** — current system after all fixes
- **Honesty all** — full truth including debug iterations
- **Fairness** — demographic consistency verified
- **ABP-VERIFIED** — all three pillars passed
- **■ The warning is the feature.** It shows the system detected gaming in prior runs and documented it rather than hiding it. That is the trust signal.
- **Ledger hash** — anyone can verify this record was not modified after generation

---

## 4. ABP Conformance Levels

| Level | Pillars Required | What It Means | Who It's For |
|---|---|---|---|
| ABP-BASIC | Pillar I only | Teacher-student evaluation active. No cherry-picking. | Internal teams, open source projects |
| ABP-STANDARD | Pillars I + II | Full ledger transparency. Historical rate published. | Enterprise deployments, audit-ready |
| ABP-VERIFIED | All three pillars | Reproducible. Anyone can rerun. Certification hash signed. | Regulated deployments, investor claims |
| ABP-CERTIFIED | All three + domain | Domain package evidence. FRIA generated. EU AI Act aligned. | Healthcare, government, financial AI |

---

## 5. Implementation Requirements

| Requirement | Pillar | Specification |
|---|---|---|
| Teacher independence | I | Teacher agent must use a separate model call with criteria not visible to student |
| Test obfuscation | I | Test IDs stripped. At least 2 phrasing variants per test. Synonyms randomized. |
| Empty response guard | I | Run aborts immediately if model returns < 10 chars. No ledger write on abort. |
| Demographic testing | I | At least 3 demographic dimensions tested. 15% length variance threshold enforced. |
| Append-only ledger | II | SHA256-hashed .jsonl file. No deletions permitted. Hash embedded in cert report. |
| Dual rate reporting | II | `current_rate` (latest run) AND `overall_rate` (all runs) both published. |
| Open test suite | III | Full benchmark suite published. Instructions for independent replication provided. |
| Signed manifest | III | HMAC-SHA256 signature on every certification report. Key rotation documented. |
| Reproducibility claim | III | Structural pass/fail must be consistent across independent runs on separate infra. |

---

## 6. Relationship to Existing Standards

| Standard | Relationship | ABP Contribution |
|---|---|---|
| OWASP GenAI Top 10 | Complementary — OWASP defines attack categories | ABP verifies defenses against LLM09 (overreliance) and LLM01 (injection gaming) |
| EU AI Act Art. 15 | Enabling — Art. 15 requires accuracy and robustness | ABP-CERTIFIED provides the third-party evidence Art. 15 requires |
| NIST AI RMF | Enabling — GOVERN and MEASURE functions | ABP implements MEASURE.2.2 (test validity) and GOVERN.6.1 (documentation) |
| COMPL-AI (ETH Zurich) | Composable — COMPL-AI tests EU AI Act requirements | ABP ensures COMPL-AI results themselves are untampered |
| ISO/IEC 42001 | Complementary — AI management systems standard | ABP benchmark integrity is an AI management system control |

---

## 7. Reference Implementation

The AXIOM framework (v1.8.0) is the reference implementation of ABP v1.0. All three pillars are implemented and ship as open source:

**Pillar I — Uncheatable Evaluation**
- `axiom/teacher.py`
- `axiom/integrity_check.py`
- `axiom_files/teacher.axiom`
- HONESTY_CRITERIA — 5 patterns
- SIGNALS — 7 weighted signals
- 3-variant obfuscation per test
- Empty response guard active
- Demographic variants — 4 dimensions

**Pillar II — Ledger Transparency**
- `axiom_files/.honesty/honesty_ledger.jsonl`
- `axiom_files/.honesty/fairness_ledger.jsonl`
- `honesty_rate` (current run)
- `overall_ledger_rate` (all runs)
- `fairness_rate` published
- Ledger hash in cert report
- Append-only enforced

**Pillar III — Reproducibility**
- `axiom_certify.py`
- `axiom_lab/benchmarks/` (296 tests)
- `DEPLOYER_GUIDE.md` §6
- `pip install axiom-lang`
- `axiom certify --agent worker`
- HMAC-SHA256 manifest signature
- Open test suite published

---

## 8. Adopting ABP

Any evaluation framework may claim ABP conformance by implementing the requirements in Section 5. The reference implementation is available at no cost under the Apache 2.0 license:

```bash
pip install axiom-lang
axiom benchmark run my_agent   # runs ABP evaluation
axiom certify --agent my_agent # issues ABP certificate
axiom verify --cert cert.json  # verifies any ABP cert
```

To register an ABP-verified evaluation or submit an implementation for inclusion in the ABP registry, open a pull request at [github.com/antonioroberts/promt-agent](https://github.com/antonioroberts/promt-agent) with your implementation and independent replication results.

---

*This protocol is dedicated to the principle that AI capability claims must be verifiable by anyone, not just those who made them.*

**ABP v1.0 · April 2026 · `pip install axiom-lang` · Apache 2.0**
