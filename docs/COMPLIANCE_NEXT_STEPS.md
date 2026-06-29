# Compliance work — paused, pick up here

**Status as of this note:** EU AI Act + ISO 42001 enabler work is merged to `main` and
green. We paused intentionally; this file is the resume point. Nothing below is blocking.

## What's DONE (on `main`)

EU AI Act alignment + gap-closers (`EU_AI_ACT_ALIGNMENT.md` is the source of truth):

| Module | Closes | Tests |
|---|---|---|
| `axiom_risk_register.py` | Art. 9 risk management (also ISO 42001 A.5 / Clause 6.1) | 10 |
| `axiom_annex_iv.py` | Art. 11 / Annex IV technical documentation | 9 |
| `axiom_content_provenance.py` | Art. 50 synthetic-content marking | 10 |
| `axiom_data_subject.py` | Art. 10 / GDPR data-subject rights (access/erasure/portability) | 8 |

ISO/IEC 42001 readiness pack: `docs/iso42001/` (SoA + AI policy + risk/impact procedures).

Articles substantiated as **enabler** (not "certified"): 9, 10, 11, 12, 13, 14, 15, 25, 27, 50.
Framing held throughout: Axiom supplies controls + documentation skeletons; deployer +
counsel complete risk-class, FRIA sign-off, and conformity assessment. No blanket
"compliant" claims.

## What's LEFT (resume backlog)

1. **Art. 10(3) semantic fairness scoring** — current fairness signal is length +
   disparagement patterns; upgrade to cosine-similarity semantic scoring. Smallest open
   code item. Touches `axiom/integrity_check.py` + `AXIOM_DATA_GOVERNANCE.md`.
2. **Wire Art. 50 marker into the server** — `axiom_content_provenance.mark()` exists but
   is not applied in the response path. Add as opt-in (`AXIOM_MARK_OUTPUTS=1`) after
   `OutputShaper` in `axiom_server.py`. Deployer-policy decision; left off deliberately.
3. **ISO 42001 — make the readiness pack live** — fill the `[ORG]` placeholders (scope,
   roles, retention, review cadence) in `docs/iso42001/`; verify Annex A control titles
   against the purchased ISO/IEC 42001:2023 text; then it's ready for a lead implementer.
4. **Deployer/counsel track (not code)** — risk-class determination, FRIA sign-off,
   accredited certification body for 42001 (Stage 1 → Stage 2), conformity assessment for
   any high-risk system.

## Notes for whoever resumes

- **Single source of truth** for EU AI Act status: `EU_AI_ACT_ALIGNMENT.md` (per-article
  matrix + honest gap backlog). Keep it updated in the same change as any control change.
- **All four compliance modules use only stdlib `hmac`/`hashlib`** — no `cryptography`
  dependency — so they run anywhere. Each emits **signed** output (provenance tags, Annex IV
  packs, risk registers, data-subject receipts) and ships with `verify`.
- **Env caveat:** `tests/test_axm_guest_key.py` needs the `cryptography` lib (Ed25519). In
  a fresh shell here it panicked on import because `_cffi_backend` was missing — that's an
  environment gap (`pip install cffi cryptography`), not a code failure. The guest-key code
  is fine and on `main`.
- **Dual-use reminder:** the risk register satisfies *both* EU AI Act Art. 9 and ISO 42001
  A.5/Clause 6.1 — the two regimes are the same controls through two lenses, so close gaps
  once and map twice.
