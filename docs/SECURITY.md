# Security policy

## Reporting vulnerabilities

Report security issues privately to **<security@orivael.dev>**.

For sensitive details, use the PGP key fingerprint published at
<https://orivael.dev/.well-known/pgp-key> (publish before the soft
launch).

**Please do not** open public GitHub issues for security
vulnerabilities. The triage cycle is:

| Step | Target | What we do |
|---|---|---|
| Acknowledge | within 48 hours | Confirm receipt + assign a tracking ID. |
| Initial assessment | within 5 business days | Reproduce; rate severity; pick a fix window. |
| Fix + verify | severity-driven (see below) | Patch, regression test, re-verify with reporter. |
| Coordinated disclosure | mutually agreed | Public advisory + reporter credit (if desired). |

### Severity-driven fix windows

| Severity | Examples | Target fix |
|---|---|---|
| Critical | Auth bypass, secret leak, RCE | 24-72 hours |
| High | Privilege escalation, tenant data leak | 7 days |
| Medium | DoS without privilege escalation, info leak with no PII | 30 days |
| Low | Information enumeration, weak defaults | 90 days |

## Scope

In scope:

- The Axiom Intent Firewall service at `firewall.orivael.dev`
- The `/v1/guard/*` API endpoints
- The Python and TypeScript SDKs published as `axiom-firewall` and
  `@axiom/firewall`
- The classifier core in `axiom_intent_classifier.py` insofar as it
  affects verdict correctness

Out of scope:

- Denial-of-service via volume — we expect rate limits to block these
  cheaply; report only if you can show a sub-linear amplification.
- Self-XSS where the attacker would already have valid session cookies.
- Reports generated solely by automated scanners with no demonstrated
  impact (e.g. "header X is missing" without a concrete exploit).
- Findings in third-party services we use (Stripe, AWS, GitHub) —
  please report those to the upstream vendor.

## Safe harbor

Good-faith security research is welcome. If you:

- Test only against your own tenant (sign up at
  <https://firewall.orivael.dev/signup>) or with explicit written
  permission,
- Don't disrupt service for other tenants,
- Don't access, modify, or exfiltrate data that isn't yours,
- Don't publish before we've had a reasonable chance to fix,

then we won't pursue legal action and will credit you in the advisory
(at your option).

## Bug bounty

Not yet — funded program planned for Phase 3 / 2026 Q4. For now we
acknowledge reporters publicly + send a thank-you (and, when budget
permits, a small honorarium).

## What we sign and verify

- Every `/v1/guard/check` response includes an HMAC-SHA256
  `signature` over the verdict — you can replay it post-hoc to prove
  the Firewall actually issued that decision.
- Stripe webhooks are verified with the official Stripe SDK's
  signature check. Untrusted POST bodies are rejected before any
  state-changing handler runs.
- The Python SDK is published to PyPI via [Trusted Publishing
  (OIDC)](https://docs.pypi.org/trusted-publishers/) — no long-lived
  API tokens.
- The TypeScript SDK is published with [npm
  provenance](https://docs.npmjs.com/generating-provenance-statements)
  so consumers can verify it came from this repository.

## Cryptographic primitives

- HMAC-SHA256 — verdict signing, session cookies
- PBKDF2-HMAC-SHA256, 200,000 iterations, 16-byte salt — passwords
- TLS 1.3 (TLS 1.2 floor) — all traffic to `firewall.orivael.dev`

Master key (`AXIOM_MASTER_KEY`) is 32 bytes of entropy stored in AWS
Secrets Manager with a CloudWatch alarm on `GetSecretValue` access
patterns.

## Compliance roadmap

| Target | Status |
|---|---|
| HIPAA-eligible AWS account | Available on Enterprise tier |
| SOC 2 Type I | Audit kickoff Phase 1 week 4; report target Phase 2 |
| SOC 2 Type II | 6-month observation window starts post-Type I |
| GDPR DPA | Available on request for Indie+ tiers |
| ISO 27001 | Evaluating — depends on customer demand |
