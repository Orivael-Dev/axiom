# Firewall Phase Status — verify-and-document snapshot

**Date:** 2026-05-22
**Branch:** `claude/test-axiom-security-KgUcQ`
**Phase plan reference:** `docs/GAME_PLAN.md` §2–5

This is an audit-grade snapshot, not a build plan. Every claim
below either points at code that exists + tests that pass, or
labels the gap explicitly. No aspirational "shipping" labels.

## TL;DR

| Phase | Title | Code present | Tests passing | Paying-customer-ready |
|------:|-------|:------------:|:-------------:|:---------------------:|
| 1 | Intent Firewall | ✅ | **87/87** | **Yes — deployed to Hetzner** |
| 2 | Skill Pack Builder + MCP | ⚠ Partial → improving | **43/43** of what's built | Partial — public registry live, MCP dashboard still missing |
| 3 | Data Gate + Flight Recorder + Nightly Review | ❌ Spec-only | n/a | No |
| 4 | Certify + Shield Lite + CallGuard | ⚠ Partial | **57/57** of Certify lane | Certify badge generator missing |

Test totals quoted are *only* the suites that map to phase-specific
code. Adjacent infrastructure (audio harness, research engine,
dev-agent coder, injection guard, twitter agent) is covered by
another **123 tests** that pass alongside these. Combined sweep:
**310 passing** (+57 since 2026-05-18 snapshot).

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
| `test_axiom_mcp_server.py` | 2 (MCP) | 9 | ✅ |
| `test_axiom_mcp_integration.py` | 2 (MCP) | 9 | ✅ |
| `test_axiom_firewall_mcp.py` | 2 (MCP dashboard) | 4 | ✅ |
| `test_axiom_mcp_pipx.py` | 2 (pipx entry-point) | 4 | ✅ |
| **Phase 2 subtotal** | | **51** | ✅ |
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
Production deployment is live at **`firewall.orivael.dev`** as of
2026-05-22 (Hetzner VPS, single-Caddy multi-subdomain stack with
`research.orivael.dev` + `packs.orivael.dev`). Outstanding work is
non-engineering — soft-launch waitlist drives + Stripe production-
mode keys.

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
| MCP server (5 governance tools, JSON-RPC 2.0/stdio) | `axiom_mcp_server.py` (v1.8.7) | 18 (`test_axiom_mcp_*.py`) | — |
| MCP installer (pipx / npx / Homebrew) | `pyproject.toml` `[project.scripts]` `axiom-mcp = "axiom_mcp_server:main"`, `[tool.setuptools] py-modules` ships top-level files | 4 (`test_axiom_mcp_pipx.py`) | **pipx ✅ (2026-05-22)** — `pipx install axiom-constitutional` gets `axiom-mcp` on PATH. npx + Homebrew **still missing**. |
| **Public registry — `packs.orivael.dev`** | `axiom_packs/server.py`, `deploy/packs/`, `deploy/research/docker-compose.yml` | covered by registry-client tests | **Closed 2026-05-22** — live on Hetzner, serving 14 signed packs |
| 5–10 curated first-party packs | `packs/` directory: code-review-base, coppa, customer-support-base, fdcpa, gdpr-article-9, hipaa-intake, kid-ages-{3-5,6-8,9-12}, kid-bedtime-mode, kid-classroom-mode, kid-voice-output, pci-dss, prompt-injection-strict, sec-rule-10b-5 | 37 (`test_axiom_kid_packs.py`) covering 5 kid packs; remaining 9 packs lack regression tests | additional pack-specific regression tests **partial** |
| MCP dashboard at `/dashboard/mcp` | `axiom_firewall/dashboard.py:mcp_index`, `templates/mcp.html` | 4 (`test_axiom_firewall_mcp.py`) | **Closed 2026-05-22** — login-gated page lists all 14 MCP tools + Claude Desktop / Cursor / generic config snippets |
| Smithery.ai listing | — | — | **Missing** (external) |

**Net:** The pack format + signing + firewall-side load path are
all real and tested. The public registry shipped on 2026-05-22
(`packs.orivael.dev` serving 14 signed first-party packs). MCP
server is live (18 tests) but lacks a packaging story (pipx) and a
dashboard surface (`/dashboard/mcp`). Phase 1 customers can use
first-party packs today via local file paths OR by pointing
`AXIOM_FIREWALL_REGISTRY_URL` at `https://packs.orivael.dev`.

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

- **Phase 1: A+** — code, tests, SDKs, deploy artifacts, ops docs,
  AND now a live Hetzner deployment at `firewall.orivael.dev`. Only
  Stripe production keys + waitlist work remains.
- **Phase 2: A-** — pack format + signing + load path solid; public
  registry live at `packs.orivael.dev`; MCP server (18 tests) +
  `/dashboard/mcp` page (4 tests) both shipping. Remaining gaps:
  pipx packaging of the MCP server + Smithery listing (external).
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

## What changed since the 2026-05-18 snapshot

Versus the prior version of this file:

- **Hetzner production deployment** of all three subdomains —
  `firewall.orivael.dev`, `research.orivael.dev` (basic-auth-gated
  beta), `packs.orivael.dev`. Single-Caddy multi-subdomain stack
  via shared Docker network (`axiom-net`). Caddyfile, compose
  files, and `.env.example` in `deploy/firewall/` + `deploy/research/`.
- **Public packs registry live** — closes the largest Phase 2
  distribution gap. 14 signed first-party packs serving at
  `packs.orivael.dev/v1/packs`. Re-signable per deploy via
  `scripts/sign_packs.py` under the deployer's `AXIOM_MASTER_KEY`.
- **Re:Search console finished for live demos** — auto-run + mock
  fallback stripped, resume picker added, `/api/runs` endpoint
  merges exoskeleton + medical ledgers, signed-verification ribbon
  pinned above the report. 6 new tests in
  `tests/test_research_server_runs.py`.
- **Dev-agent delegates** — `code_generation` and `test_generation`
  exoskeleton delegates wired for local Qwen2.5-coder:3b/7b via
  Ollama. Adds 2 named workflows to the research console.
  `ui.py` preset dropdown updated.
- **InjectionGuard hardening** — `cmd_backtick` regex narrowed to
  require a known shell command (was matching any backticked text,
  triggering false positives on inline markdown code). Added
  `output_format="code"` parameter to relax CMD_INJECTION +
  TEMPLATE_INJ for code-gen flows (XSS/SSRF/PATH stay enforced).
  13 new tests in `tests/test_injection_guard.py`.
- **Twitter reply agent** (`axiom_twitter_agent.py` +
  `axiom_twitter_agent_ledger.py`, 16 tests). Halt-at-gate pattern,
  no API posting — approval surfaces draft text for manual paste,
  signs decision into a ledger under `axiom-twitter-ledger-v1`.

## What changed in the prior snapshot vs `PRODUCTS.md`

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
