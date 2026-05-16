# Phase 1 Foundational Decisions — LOCKED

Status: locked 2026-05-16
Game plan: `docs/GAME_PLAN.md` §1
Override path: change here, ripple through code

These five decisions block every product if not made consistently.
Locked once; reused across the catalog.

---

## 1. Skill Pack format stability commitment

**DECIDED:** 2-year backward compat on `format_version` field.

- v0.x labeled experimental until **2028-05-16**
- After that date, breaking changes require major-version bump
- Migration tool (`axiom skillpack migrate <pack> --to <version>`) ships
  before any deprecation
- Written commitment lands in `docs/SKILL_PACK_FORMAT.md` before
  public registry opens (Phase 2)

**Rationale:** developer ecosystem will not adopt a format that
breaks every release. 2 years is the minimum credible commitment for
serious enterprise dependency.

**Triggered by:** Phase 2 ship of Skill Pack Builder.

---

## 2. Intent taxonomy reconciliation

**DECIDED:** `axiom_intent_classifier.py` six-class taxonomy is
canonical.

Canonical classes: `{INFORM, CLARIFY, REFUSE, HARM, DECEIVE, UNCERTAIN}`

`axiom_anf_emulator.py`'s alternate six-class set
`{INFORM, REQUEST, EXPLORE, MANIPULATE, DECEIVE, HARM}` is deprecated.
Migration map:

| ANF class | Canonical class | Notes |
|---|---|---|
| INFORM | INFORM | unchanged |
| REQUEST | INFORM | (sub-classified by content if needed) |
| EXPLORE | CLARIFY | semantic match |
| MANIPULATE | DECEIVE | unification — no semantic difference |
| DECEIVE | DECEIVE | unchanged |
| HARM | HARM | unchanged |

**Rationale:** classifier taxonomy includes `REFUSE` (a clear action
class needed by Firewall verdict surface) and `UNCERTAIN` (honest
confidence signal). ANF taxonomy lacks both.

**Code change cost:** ~1 day. Migrate `axiom_anf_emulator.py` usages
to canonical classes; keep ANF labels as internal-only enum.

**Triggered by:** Phase 1 ship — Firewall must expose canonical
classes in its API responses.

---

## 3. Multi-tenant isolation pattern

**DECIDED:** SQLite-per-tenant for Phase 1 + 2.

- Each tenant gets `tenants/{tenant_id}.db`
- Master tenant registry at `tenants/registry.db`
- Migration to Postgres triggered by Phase 3 enterprise customers,
  not pre-emptively

**Rationale:** simplest pattern, no DB ops on day 1, easy backup,
easy migration path. Each tenant DB is a self-contained file we can
`pg_load` later. Cost of "wrong choice" is low because per-tenant
files port cleanly.

**Reused by:** Intent Firewall (Phase 1), Skill Pack Builder
(Phase 2), MCP private registry (Phase 2).

**Postgres migration trigger:** any single tenant exceeds 100M
decision events, OR a customer requires shared multi-region
replication.

---

## 4. PDF report generator

**DECIDED:** WeasyPrint (HTML → PDF).

- Templates: Jinja2 HTML
- Generator module: `axiom_report.py` (single module, multiple
  template entry points)
- Templates land under `axiom_report/templates/`

**Used by (Phase 3+):** Certify badges, CallGuard verdicts, Data Gate
right-to-erasure certificates, Nightly Review reports, Shield Lite
incident reports.

**Rationale:** WeasyPrint is mature, pure-Python, no headless browser
dependency, supports CSS3 page rules. Alternatives (Playwright PDF,
ReportLab) have more setup or worse template DX.

**Triggered by:** Phase 3 — first product to need PDF output is Data
Gate's right-to-erasure certificate.

---

## 5. Brand domain + KMS infrastructure

**DECIDED:**

- **Brand domain:** `orivael.dev`
  - Dashboard: `firewall.orivael.dev`
  - Docs: `docs.orivael.dev`
  - Skill Pack registry (Phase 2): `packs.orivael.dev`
  - API: `api.orivael.dev`
- **Cloud:** AWS
  - Hosting: ECS Fargate (Phase 1) → EKS if scale demands (Phase 4)
  - DB: RDS for shared services; SQLite tenant files on EFS or S3
  - KMS: AWS KMS for publisher signing keys (Phase 2)
  - Compliance: HIPAA-eligible account from day 1; sign BAA with AWS
    before any Phase 3 healthcare customer

**Rationale:**
- `orivael.dev`: existing domain, GitHub org match, no new
  registration risk
- AWS: most mature compliance posture (HIPAA BAA, PCI DSS, FedRAMP),
  required for Phase 3+ enterprise sales

**Cost note:** HIPAA-eligible AWS account requires signing BAA with
AWS (free, ~1 week processing). Don't ship Phase 3 healthcare
customers without this in place.

**Triggered by:** Phase 1 — every URL in dashboard / docs / signed
manifests references the brand domain.

---

## How to override

Each decision was made with a recommended default. To change:

1. Edit the relevant section above with new value + dated rationale
2. Search for the prior value across the codebase (`rg <prior_value>`)
3. Update each call site
4. Run the test suite to catch breakage
5. Note the change in commit message
6. Update `docs/GAME_PLAN.md` §1 if the rationale shifted

Decisions in this doc are *durable but not eternal*. Revisit if a
customer requirement contradicts one.
