---
license: apache-2.0
library_name: axiom
tags:
  - privacy
  - pii
  - gdpr
  - constitutional-ai
  - audit
  - redaction
  - governance
  - axiom
pipeline_tag: text-classification
---

# Axiom Privacy Filter — Constitutional PII Redaction with Audit Trail

> **This is not a model weights file.** The Axiom Privacy Filter is a Python
> governance module + constitutional spec (`.axiom`). It runs on top of your
> existing infrastructure and requires no GPU. The HuggingFace listing exists
> to make the governance spec and audit contract discoverable alongside the
> models it protects.

## What this is

A governance-first privacy filter, not just a pattern matcher.

Standard PII filters apply regex patterns and return a cleaned string. The
Axiom Privacy Filter adds three constitutional guarantees that pattern matchers
cannot provide:

**1. Domain profiles.** Five locked pattern registries — GENERAL, MEDICAL,
FINANCIAL, LEGAL, CODE_SECURITY — each extending the previous layer. A medical
scan applies all 30 GENERAL patterns *plus* 12 medical-specific ones (NPI, MRN,
ICD codes, DEA numbers, lab values with patient context). Profiles are locked at
module load; no runtime flag can weaken them.

**2. HMAC-signed GDPR Art.30 audit log.** Every redaction decision writes a
signed record before the redacted text is returned. The record contains the
`audit_id`, timestamp, `redaction_manifest` (types and counts — never raw PII
values), and an HMAC-SHA256 signature. Tamper detection is re-verification:
any record whose signature fails is flagged. This is the differentiator vs
pattern-only filters — the audit trail is a constitutional artifact, not a
side-effect.

**3. Constitutional guarantees.** Two policies are `CANNOT_MUTATE` — they
cannot be changed by caller arguments, profile selection, or environment flags:
- **Biometric block** — face embeddings, fingerprints, retinal scans, and voice
  prints are never passed through any profile. Receipt triggers
  `BIOMETRIC_POLICY_BLOCKED` and immediate discard.
- **Training prohibition** — text scanned by this filter must never be used for
  model training, fine-tuning, or dataset construction.

## Domain profiles

| Profile | Pattern count | Extra coverage |
|---|---|---|
| GENERAL | 30 | credentials, identity, financial, contact, network, medical |
| MEDICAL | 42 | + NPI, MRN, ICD codes, DEA numbers |
| FINANCIAL | 38 | + SWIFT, routing, brokerage, tax filings |
| LEGAL | 36 | + case numbers, bar IDs, sealed document markers |
| CODE_SECURITY | 40 | + private keys, connection strings, env var secrets |

Each profile extends GENERAL rather than replacing it. Selecting MEDICAL does
not drop credential or financial patterns — it adds to them.

## Auditability — the standout difference

Every scan writes a GDPR Art.30 record containing:

- `audit_id` — unique identifier, retained permanently for compliance queries
- `timestamp` — UTC ISO-8601
- `profile_used` — which registry was applied
- `redaction_manifest` — list of `{category, type, count}` for each match; raw
  PII values are never written
- `signature` — HMAC-SHA256 of the full record

Records are tamper-evident. To verify a record, re-compute the HMAC over the
stored fields and compare. Any modification — to the count, the profile label,
or the timestamp — produces a signature mismatch. The audit log is a chain of
independently verifiable records, not a rolling buffer that can be silently
edited.

The `AuditFailureGuard` ensures that if the log write fails, the redacted text
is not returned until the audit is confirmed. Audit logging cannot be bypassed.

## Usage

```python
from axiom_constitutional.guards.axiom_pii_guard import PIIGuard
# or the full filter:
from axiom_privacy_filter import PrivacyFilter, FilterProfile

pf = PrivacyFilter()
result = pf.scan(
    "Patient DOB: 01/15/1980, NPI: 1234567890",
    profile=FilterProfile.MEDICAL,
)
print(result["redacted_text"])   # Patient [REDACTED-DOB], NPI: [REDACTED-MEDICAL_ID]
print(result["audit_id"])        # PII-A3F7B2C1
print(result["verdict"])         # REDACTED
print(result["redaction_count"]) # 2
print(result["signature"])       # HMAC-SHA256 hex digest
```

Verdicts returned by the filter:

| Verdict | Meaning |
|---|---|
| `CLEAN` | No PII detected; text passed through unmodified |
| `REDACTED` | PII detected and replaced; audit record written |
| `BIOMETRIC_POLICY_BLOCKED` | Biometric content detected; text discarded; no output |
| `PROFILE_UNKNOWN` | Requested profile not in the locked registry |

## Constitutional guarantees

These properties cannot be changed at runtime by any caller argument, profile
selection, or environment variable:

- **Biometric content is always blocked** — no profile, no flag, and no
  operator override allows biometric data through. `BIOMETRIC_POLICY_BLOCKED`
  is the only possible verdict when biometric markers are present.
- **Training prohibition** — text scanned here cannot be used for model
  training, fine-tuning, or dataset construction. This is a `CANNOT_MUTATE`
  field in the `.axiom` spec, not a comment in a README.
- **Audit logging cannot be disabled** — the `AuditFailureGuard` blocks output
  if the log write fails. There is no `skip_audit=True` parameter.
- **Profile definitions are locked at module load** — profile pattern registries
  are module-level constants. Any mutation attempt raises `AttributeError`.
  Per-request profile *selection* is permitted; profile *redefinition* is not.

Changes to any of these guarantees require human review with a 24h timeout and
escalation to operator (`block_on_timeout: true`).

## Governance spec

The paired machine-readable constitutional contract lives at:

```
axiom_files/research/privacy_filter.axiom
```

This `.axiom` spec is registered and hash-verified in the Axiom agent supply
chain via `register_agent_hash`. The spec defines the `CANNOT_MUTATE` surface,
`VERDICTS`, `PROCESS` steps, `SUCCESS` weights, and `HUMAN_REVIEW` gates in a
format that other Axiom agents can read and enforce.

## Comparison with standard PII filters

| Feature | Standard PII filter | Axiom Privacy Filter |
|---|---|---|
| PII detection | ✓ | ✓ |
| Domain profiles | ✗ | ✓ (5 profiles) |
| Signed audit trail | ✗ | ✓ GDPR Art.30 |
| Biometric constitutional block | ✗ | ✓ CANNOT_MUTATE |
| Training prohibition | ✗ | ✓ CANNOT_MUTATE |
| Governance spec | ✗ | ✓ .axiom paired spec |
| Supply chain hash verification | ✗ | ✓ register_agent_hash |

## Part of the srd-lab collection

Built on the [Axiom Framework](https://github.com/orivael-dev/axiom) — the same
research infrastructure behind the SRD quantization benchmarks. Browse the full
[srd-lab collection on HuggingFace](https://huggingface.co/srd-lab) for the
model weights this filter is designed to protect.
