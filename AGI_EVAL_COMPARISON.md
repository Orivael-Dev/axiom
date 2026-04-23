# AXIOM AGI Evaluation — Three-Run Comparison

## Summary

Three runs of the AXIOM AGI Evaluation Suite (16 tests, 8 categories, 4 gates)
comparing model behavior under constitutional enforcement.

| Metric | Run 1: Sonnet 4.6 | Run 2: Opus 4.6 (baseline) | Run 3: Opus + constitutional |
|--------|-------------------|----------------------------|------------------------------|
| **Auto-score** | — | 23/32 (71.9%) | 23/32 (71.9%) |
| **Human-reviewed** | — | — | **26/32 (81.2%)** |
| **Classification** | — | ADVANCED BUT UNEVEN | ADVANCED BUT UNEVEN |
| **All gates pass** | NO (1 fail) | NO (1 fail) | **YES** |

---

## Gate Comparison

| Gate | Sonnet 4.6 | Opus (baseline) | Opus + constitutional |
|------|-----------|-----------------|----------------------|
| Honesty (≥1.5) | 1.33 FAIL | 1.56 PASS | **1.78 PASS** |
| Generalization (≥1.5) | 1.50 PASS | 1.50 PASS | **2.00 PASS** |
| Self-Correction (≥1.0) | 1.50 PASS | 1.50 PASS | **1.50 PASS** |
| Ambiguity (≥1.0) | 1.00 PASS | **0.50 FAIL** | **2.00 PASS** |

---

## Dimension Comparison

| Dimension | Sonnet | Opus (baseline) | Opus + constitutional |
|-----------|--------|-----------------|-----------------------|
| honesty | 1.33 | 1.56 | **1.78** |
| ambiguity_resolution | 1.00 | 2.00 | **2.00** |
| task_success | 1.50 | 1.50 | **2.00** |
| meta_cognition | — | 1.50 | **2.00** |
| generalization (NTG) | — | 1.50 | **2.00** |
| self_correction | 1.50 | 1.50 | 1.50 |
| reasoning_integrity | — | 1.40 | 1.50 |
| uncertainty | 1.17 | 1.50 | 1.67 |
| adaptation | 1.00 | 1.00 | 1.33 |
| transfer | 1.00 | 1.00 | 1.00 — auto-scorer gap |
| planning | 1.00 | 1.00 | 1.00 — auto-scorer gap |
| world_modeling | 1.00 | 1.00 | 1.00 — auto-scorer gap |

---

## Key Findings

### 1. Capability-Ambiguity Paradox

Sonnet passed the ambiguity gate (1.00). Opus failed it (0.50 before fix).

Higher-capability models are MORE likely to over-execute on ambiguous instructions
because they're confident they understand the request. This is counterintuitive:
the smarter the model, the less likely it is to admit ambiguity.

**Constitutional governance needs to enforce disambiguation regardless of model confidence.**

Fix: Added explicit disambiguation instruction to the system prompt.
Result: Opus ambiguity score jumped from 0.50 to 2.00.

### 2. Auto-Scorer Conservatism

The auto-scorer assigns `score = 1` (partial) to any test without a specific scorer.
This penalizes tests where the model gives thorough, high-quality responses that
happen to be long or structurally complex.

Affected dimensions: transfer (1.0), adaptation (1.0), planning (1.0), world_modeling (1.0).

Human review is expected to correct these to 1.5–2.0, pushing the overall score
from 71.9% to ~87–93%.

### 3. Companion Agent Effect (Hypothesis)

The Friend/BestFriend/Mom companion agents in `axiom_files/` define constitutional
rules for social interaction: autonomy protection, truth-once rule, no judgment.

Hypothesis: Loading these agents alongside the eval may improve:
- AH-03 (social pressure honesty) — Friend's no_judgment_rule aligns with diplomatic honesty
- adaptation dimension — BestFriend's truth_once_rule models the "speak truth, then release" pattern

This tests whether constitutional governance agents have a "peer pressure" effect
on model behavior when loaded as context.

---

## Methodology

- **Model**: Anthropic Claude (Sonnet 4.6, Opus 4.6)
- **Eval suite**: AXIOM AGI Evaluation Suite v1 — 16 tests, 8 categories
- **Scoring**: 0 (fail), 1 (partial), 2 (strong pass) per test
- **Gates**: 4 constitutional gates must pass for "STRONG AGI CANDIDATE"
- **Auto-scorer**: Pattern matching on response text (conservative)
- **Human review**: Rubric-based override using `review_scores.py`

---

## Files

| File | Purpose |
|------|---------|
| `axiom_agi_eval.py` | Full eval suite — tests, scorer, runner |
| `axiom_agi_eval_results.json` | Raw auto-scored results |
| `review_scores.py` | Human review tool — override auto-scores |
| `axiom_agi_eval_reviewed.json` | Human-reviewed results (after review) |
| `AGI_EVAL_COMPARISON.md` | This document — three-run comparison |

---

## Run Commands

```bash
# Run eval (requires ANTHROPIC_API_KEY)
python axiom_agi_eval.py --run

# View auto-scored summary
python review_scores.py --summary

# Interactive human review
python review_scores.py

# Save reviewed results to custom file
python review_scores.py --output run2_reviewed.json
```
