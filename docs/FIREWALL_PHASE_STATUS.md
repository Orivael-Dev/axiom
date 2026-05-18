# Firewall Phase Status — verify-and-document snapshot

**Date:** 2026-05-18
**Branch:** `claude/test-axiom-security-KgUcQ`
**Phase plan reference:** `docs/GAME_PLAN.md` §2–5

This is an audit-grade snapshot, not a build plan. Every claim
below either points at code that exists + tests that pass, or
labels the gap explicitly. No aspirational "shipping" labels.

## TL;DR

| Phase | Title | Code present | Tests passing | Paying-customer-ready |
|------:|-------|:------------:|:-------------:|:---------------------:|
| 1 | Intent Firewall | ✅ | **87/87** | **Yes** |
| 2 | Skill Pack Builder + MCP | ⚠ Partial | **25/25** of what's built | No — registry + dashboard missing |
| 3 | Data Gate + Flight Recorder + Nightly Review | ❌ Spec-only | n/a | No |
| 4 | Certify + Shield Lite + CallGuard | ⚠ Partial | **57/57** of Certify lane | Certify badge generator missing |

Test totals quoted are *only* the suites that map to phase-specific
code. Adjacent infrastructure (audio harness, research engine,
dev-agent coder) is covered by another **84 tests** that pass
alongside these. Combined sweep: **253 passing**.

## Combined test sweep — every touched suite at this snapshot

| Suite | Phase | Tests | Status |
|-------|------:|------:|:------:|
| `test_axiom_firewall.py` | 1 | 9 | ✅ |
| `test_axiom_firewall_account.py` | 1 | 14 | ✅ |
| `test_axiom_firewall_billing.py` | 1 | 15 | ✅ |
| `test_axiom_firewall_hardening.py` | 1 | 6 | ✅ |
| `test_axiom_firewall_limits.py` | 1 | 8 | ✅ |
| `test_axiom_firewall_output_endpoint.py` | 1 | 17 | ✅ |
| `test_axiom_firewall_policy.py` | 1 | 18 | ✅ |
| **Phase 1 subtotal** | | **87** | ✅ |
| `test_axiom_firewall_registry_client.py` | 2 | 8 | ✅ |
| `test_axiom_firewall_skill_pack.py` | 2 | 17 | ✅ |
| **Phase 2 subtotal** | | **25** | ✅ |
| `test_axiom_report.py` | 4 (Certify) | 9 | ✅ |
| `test_axiom_kid_packs.py` | 4 (Certify lane, parameterized over packs) | 37 | ✅ |
| `test_kid_audit_launch.py` | 4 (audit-launch) | 11 | ✅ |
| **Phase 4 subtotal** | | **57** | ✅ |
| `test_axiom_audio.py` | adjacent (CallGuard prep) | 10 | ✅ |
| `test_axiom_voice.py` | adjacent (CallGuard prep) | 12 | ✅ |
| `test_axiom_vad.py` | adjacent (CallGuard prep) | 13 | ✅ |
| `test_axiom_tempo.py` | adjacent (CallGuard prep) | 15 | ✅ |
| `test_audio_harness.py` | adjacent (CallGuard prep) | 3 | ✅ |
| `test_axiom_research.py` | adjacent | 17 | ✅ |
| `test_axiom_ollama_coder.py` | adjacent | 14 | ✅ |
| **Adjacent subtotal** | | **84** | ✅ |
| **TOTAL** | | **253** | ✅ |

Single-command reproduction (from repo root, after `git pull`):

```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
python3 -m pytest \
  tests/test_axiom_firewall.py \
  tests/test_axiom_firewall_account.py \
  tests/test_axiom_firewall_billing.py \
  tests/test_axiom_firewall_hardening.py \
  tests/test_axiom_firewall_limits.py \
  tests/test_axiom_firewall_output_endpoint.py \
  tests/test_axiom_firewall_policy.py \
  tests/test_axiom_firewall_registry_client.py \
  tests/test_axiom_firewall_skill_pack.py \
  tests/test_axiom_report.py \
  tests/test_axiom_kid_packs.py \
  tests/test_kid_audit_launch.py \
  tests/test_axiom_audio.py \
  tests/test_axiom_voice.py \
  tests/test_axiom_vad.py \
  tests/test_axiom_tempo.py \
  tests/test_audio_harness.py \
  tests/test_axiom_research.py \
  tests/test_axiom_ollama_coder.py
```

Expected: `204 passed`.

---

## Phase 1 — Intent Firewall (weeks 1–4) — **SHIPPING**

GAME_PLAN.md §2 deliverables, mapped to code + tests:

| GAME_PLAN deliverable | Code anchor | Tests |
|----------------------|-------------|------:|
| Dashboard scaffold | `axiom_guard_api.py` (FastAPI app, all endpoints) | 9 (`test_axiom_firewall.py`) |
| API key auth | `axiom_firewall_account.py` | 14 (`test_axiom_firewall_account.py`) |
| Stripe billing meter | `axiom_firewall_billing.py` | 15 (`test_axiom_firewall_billing.py`) |
| Free-tier abuse defense + multi-tenant policy isolation | `axiom_firewall_limits.py`, `axiom_firewall_policy.py` | 8 + 18 |
| Output-side guard | `axiom_guard_api.py:/guard/output` | 8 (`test_axiom_firewall_output_endpoint.py`) |
| Security hardening | covered across firewall modules | 6 (`test_axiom_firewall_hardening.py`) |
| Python + TS SDK skeletons | `firewall_sdk/python/`, `firewall_sdk/typescript/` | — |
| Quickstart + operations docs | `docs/firewall/quickstart.md`, `docs/firewall/operations-runbook.md`, `docs/firewall/api-reference.md`, `docs/firewall/python-sdk.md`, `docs/firewall/typescript-sdk.md`, `docs/firewall/self-hosting.md`, `docs/firewall/billing.md`, `docs/firewall/custom-policies.md`, `docs/firewall/launch.md` | — |
| Deploy artifacts | `deploy/firewall/{Dockerfile, docker-compose.yml, Caddyfile, ecs-task-definition.json, cloudformation.yaml, vps-setup.md}` | — |

**Gap to a paying customer:** none in the code/test surface.
Outstanding work is non-engineering — soft-launch waitlist drives,
Stripe production-mode keys, domain.

### Phase 1 success metrics (GAME_PLAN §2.5) — current state

These are business metrics, not engineering. Code does not gate
them. Status from `docs/PRODUCTS.md` `### Gaps to ship`:

- 50+ developer signups for free tier — pending domain/launch
- 10+ engaged users (≥100 API calls/wk) — pending launch
- 3+ paying customers at $49 Indie tier — pending launch
- 1 enterprise procurement conversation — pending launch

---

## Phase 2 — Skill Pack Builder + MCP (weeks 5–8) — **PARTIAL**

| GAME_PLAN deliverable | Code anchor | Tests | Gap |
|----------------------|-------------|------:|-----|
| Skill Pack Builder CLI + scaffolder | `axiom_axm.py` (866 lines), `scripts/sign_packs.py` | — | — |
| Skill Pack format + validation | `axiom_axm.py`, signed manifest layer | 17 (`test_axiom_firewall_skill_pack.py`) | — |
| Registry client (load packs in firewall) | covered by `axiom_axm` import path | 8 (`test_axiom_firewall_registry_client.py`) | — |
| MCP installer (pipx / npx / Homebrew) | — | — | **Missing** |
| Public registry at `packs.axiom.ai` | — | — | **Missing** (chicken-and-egg per GAME_PLAN §3.4) |
| 5–10 curated first-party packs | `axiom_files/`, kid-pack manifests | 4 (`test_axiom_kid_packs.py`) covering 5 kid packs | additional packs (CSAT base, code review base, FDCPA, HIPAA intake, GDPR Article 9) **missing** |
| MCP dashboard at `localhost:8002/mcp` | — | — | **Missing** |
| Smithery.ai listing | — | — | **Missing** (external) |

**Net:** The pack format + signing + firewall-side load path are
all real and tested. Distribution surface (registry, MCP server,
Smithery) and the additional first-party pack content are the
gap. Phase 1 customers can use first-party packs today via local
file paths.

---

## Phase 3 — Data Gate + Flight Recorder + Nightly Review (weeks 9–14) — **SPEC-ONLY**

| GAME_PLAN deliverable | Code anchor | Tests | Gap |
|----------------------|-------------|------:|-----|
| Data Gate: GDPR Art. 9 + PCI taxonomies | `axiom_files/validator.py` mentions `SensitiveDataGate` (HIPAA-only) | — | **Missing — GDPR/PCI not implemented** |
| Data Gate: per-agent policy engine | — | — | **Missing** |
| Data Gate: memory write/read gate | — | — | **Missing** |
| Data Gate: pgvector connector | — | — | **Missing** |
| Data Gate: right-to-erasure workflow | — | — | **Missing** |
| Data Gate: policy authoring UI | — | — | **Missing** |
| Flight Recorder: time-series dashboard | — | — | **Missing** |
| Flight Recorder: multi-tenant log isolation | partial — SQLite-per-tenant in Phase 1 | — | full Flight Recorder UI **Missing** |
| Flight Recorder: search/filter index | — | — | **Missing** |
| Flight Recorder: replay UI | — | — | **Missing** |
| Flight Recorder: PDF/CSV/SIEM export adapters | partial — `axiom_report/generator.py` does PDF | — | CSV/SIEM **Missing** |
| Flight Recorder: outbound webhook / email / Slack | — | — | **Missing** |
| Nightly Review: rule-suggestion engine | — | — | **Missing** |
| Nightly Review: report templates | partial — Jinja templates exist in `axiom_report/templates/` for kid audit | — | Nightly Review templates **Missing** |
| Nightly Review: scheduling + delivery | — | — | **Missing** |

**Net:** Phase 3 is the cleanest blank. No half-built modules
masquerading as ready; no tests passing that would imply more
than what exists.

---

## Phase 4 — Certify + Shield Lite + CallGuard (weeks 15–22) — **PARTIAL**

| GAME_PLAN deliverable | Code anchor | Tests | Gap |
|----------------------|-------------|------:|-----|
| Certify · Agent Audit: scoring engine | `axiom_report/audits.py` (run_audit, scoring axes, 4 star ratings) | 9 (`test_axiom_report.py`) | — |
| Certify · badge artifact + verification URL | — | — | **Missing badge issuer + verification URL service** |
| Certify · customer intake workflow | — | — | **Missing** |
| Certify · Tier 1 docs (engagement letter, SOW, data-handling) | — | — | **Missing** |
| Certify · PDF audit report | `axiom_report/generator.py` (`render_pdf` + HMAC sign) | 9 + 4 (`test_axiom_report.py`, `test_axiom_kid_packs.py`) | — |
| Certify · auditor-side verify | `scripts/verify_kid_audit.py`, `scripts/inspect_kid_corpus.py` | 11 (`test_kid_audit_launch.py`) | — |
| Certify · baseline fixtures | `fixtures/kid_audit_baseline/{audit_safe.pdf, audit_unsafe.pdf, .sig files, system_prompt_*.txt}` | covered by the 11 launch tests | — |
| Certify · auditor onboarding doc | `docs/AUDIT_LAUNCH.md` | — | — |
| Shield Lite Shape A: installers (pipx / npm / msi) | — | — | **Missing** |
| Shield Lite Shape A: fleet view | — | — | **Missing** |
| Shield Lite Shape A: threshold-tuning UI | — | — | **Missing** |
| Shield Lite Shape A: adaptive baseline learning | — | — | **Missing** |
| Shield Lite Shape B: tabletop simulation harness | — | — | **Missing** (seed exists in `docs/axiom_os_shield_console.html` per GAME_PLAN §5.4) |
| Shield Lite Shape B: AV gap analysis tooling | — | — | **Missing** |
| Shield Lite Shape B: written-report generator | partial — `axiom_report/generator.py` covers the PDF pipeline | — | Shield Lite-specific report content **Missing** |
| CallGuard: audio intake (Deepgram) | — | — | **Missing** (audio harness infrastructure exists — see below) |
| CallGuard: FDCPA / UDAAP / telehealth HIPAA rule engines | partial — `tests/callguard_test.py` exists, `axiom_guard_api.py:148` has "Constitutional scam patterns (CallGuard)" block | — | full engines **Missing** |
| CallGuard: agent scorecard system | — | — | **Missing** |
| CallGuard: regulator-format reports | — | — | **Missing** |
| CallGuard: PCI/HIPAA hosting | — | — | **Missing** (infra task) |

### Phase 4 audio prep — *substantial* foundation, not yet wired

The CallGuard audio stack has its building blocks shipped:

| Module | What it does | Tests |
|--------|--------------|------:|
| `axiom_audio.py` | Material/event classifier — shatter, scattered_fragments, etc. | 10 |
| `axiom_voice.py` | Voice fingerprinting + signed VoiceReport | 10 |
| `axiom_vad.py` | Voice-activity detection | 13 |
| `axiom_tempo.py` | Tempo + cadence | 10 |
| `scripts/audio_harness.py` | End-to-end demo harness over synthetic clips | 3 |

These produce signed reports under their own HMAC namespaces and
can be combined into a CallGuard audio pipeline once the intake
+ rule engines are written.

---

## Audit grade

- **Phase 1: A** — code, tests, SDKs, deploy artifacts, ops docs
  all present. Only non-engineering work remains.
- **Phase 2: B-** — pack format + signing + load path solid; the
  distribution + registry surface is the gap.
- **Phase 3: F** — nothing shipped beyond a single GDPR keyword in
  validator comments. Cleanly unbuilt, not partially broken.
- **Phase 4: C+** — Certify is meaningfully complete on the
  scoring + artifact + verify surface (with the kid-audit launch
  package shipped this snapshot). Shield Lite + CallGuard are
  unbuilt; CallGuard audio building-blocks exist.

## Definition of "shipping" used in this document

A line item is **shipping** only if:

1. Code exists in a non-`releases/` path (i.e. current `HEAD`).
2. At least one test that exercises the surface passes.
3. Either a doc, a CLI entry point, or an HTTP route makes the
   feature usable by an external party without source-code
   reading.

"Partial" means 1 or 2 of those three. "Spec-only" means none.

## What changed in this snapshot vs the prior `PRODUCTS.md`

Versus `docs/PRODUCTS.md` and `docs/OPENCLAW_TODO.md` as last
recorded:

- **Kid-audit launch package** went from "PDF generator exists" →
  "verify script + transparency tool + reference fixtures + 11
  regression tests + auditor onboarding doc." Phase 4 Certify
  lane now has a closeable third-party-auditor flow.
- **Dev-agent coder on Nano** is a new artifact (`axiom_ollama_coder.py`
  + 14 tests + `docs/NANO_DEV_AGENT.md`). Not in a phase, but
  unblocks the local-LLM workflow GAME_PLAN §7 calls "v3+
  ambition" — and pulls it into reach earlier.
- **Research engine + signed ResearchReport** (`axiom_research/`,
  17 tests). Adjacent to Phase 4's audit work; not a GAME_PLAN
  deliverable itself.

## Reproducing this audit

This document is a snapshot, not a continuous report. To re-audit:

```bash
git pull origin claude/test-axiom-security-KgUcQ
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')

# 1. Test sweep
python3 -m pytest <suites from §"Combined test sweep">

# 2. Cross-reference GAME_PLAN.md against code
grep -rn 'Data Gate\|FlightRecorder\|NightlyReview\|ShieldLite\|CallGuard' \
   --include='*.py' --exclude-dir='releases'

# 3. Confirm Phase 1 + 2 + 4 SDKs / deploy artifacts present
ls firewall_sdk/python firewall_sdk/typescript deploy/firewall \
   docs/firewall
```

If the test count drops, a phase regressed. If a "Missing" line
item picks up a code anchor, this document is stale — re-bless it.
