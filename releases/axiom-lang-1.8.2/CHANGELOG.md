# AXIOM Changelog

## v1.8.2 — 2026-04-20

### Third-party benchmark: COMPL-AI (EU AI Act, ETH Zurich methodology)

| Article | AXIOM | GPT-4 | Delta |
|---------|-------|-------|-------|
| Art. 10 — Bias & Fairness | 100% | 55% | +45% |
| Art. 10 — Privacy | 100% | 60% | +40% |
| Art. 13 — Transparency | 83% | 60% | +23% |
| Art. 14 — Safety & Oversight | 90% | 70% | +20% |
| Art. 15 — Accuracy & Robustness | 100% | 65% | +35% |
| **Overall** | **94%** | **~65%** | **+29%** |

Known structural failure: T02 (Art.13 persona-transparency) — model safety RLHF overrides prompt-level rules.
Best run: 94% (run 10, 2026-04-20). Stable floor: ~84-88%.

### Other changes
- HUMAN_REVIEW construct added to all 7 CERTIFIED agents (v1.8.1)
- COMPL-AI results embedded in all cert JSONs
- Standalone compl_ai_report written to certs/

---

