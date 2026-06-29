# `.axm` Guest-Key Delegation — Spec

Status: **implemented** — `axiom_axm_authority.py` + `tests/test_axm_guest_key.py`
(16 tests, real Ed25519 path). CLI: `mint-master` / `gen-guest` / `issue-guest` /
`attest-guest` / `verify-guest` / `revoke-guest`.
Owner: Orivael
Depends on: `.axm` attestation (shipped), Ed25519 via the `cryptography` lib (with
HMAC fallback), bonded-pair ledger pattern (revocation register).

> **Implementation note:** built as a dedicated sibling module + CLI
> (`axiom_axm_authority.py`), not folded into `axiom_axm.py` — keeps the cert/PKI
> logic separate. The crypto core is self-contained (Ed25519 + HMAC fallback,
> modeled on `asi07_message_auth`) so the master keypair is true-random rather than
> agent-id-derived. The revocation register is a compact hash-chained, HMAC-signed
> `GuestCertLedger` (same pattern as `bonded_pair.py`, not an import of it).

## Context

`.axm` attestation today is signed with `_container_key()` — a symmetric HMAC key
derived from the single master secret. That proves integrity to anyone *holding the
master key*, but cannot give **unforgeable third-party verification**: a holder of an
HMAC key can both sign and verify, so a guest could forge. This blocks the enterprise
need: *let a main account issue scoped, expiring keys to teams or external parties who
can sign deployment attestations, where an outside auditor verifies with only a
published public key and no one can cheat.*

The asymmetric capability needed for this **already exists** in the repo
(`axiom_constitutional/security/asi07_message_auth.py`: real Ed25519 sign/verify with a
graceful HMAC fallback). This spec is a **wiring job** — a guest-certificate model over
existing primitives — not crypto from scratch.

## Threat model / goals

| Goal | Mechanism |
|---|---|
| Main account authorizes a guest key | Master **signs a certificate** binding the guest pubkey + scope + expiry |
| Guest can't forge outside its grant | Guest signs attestations with its own key; authority comes from the master-signed cert |
| Auto-expire | `not_after` field in the cert, checked at verify against a trusted `now` |
| Sandbox / scope | `scope` field (which containers, which ops, max trust level) |
| Revocation without key rotation | Append a `REVOKED` row to a hash-chained register (bonded-pair pattern) |
| Outsider verifies offline-ish | Needs only the **master public key** (+ the revocation register for revocation checks) |

Non-goals: encrypting containers; replacing the symmetric `attest()` (it stays for
same-trust-domain use); a CA hierarchy deeper than master → guest (one level).

## Design

### Trust chain
```
Master keypair (Ed25519)         private key OFFLINE; public key PUBLISHED
   │  signs a cert
   ▼
GuestCert { guest_pubkey, scope, issued_at, not_after, cert_id, master_signature }
   │  authorizes
   ▼
Guest keypair (Ed25519) signs an .axm attestation body
   │
   ▼
GuestAttestationBundle { attestation, guest_signature, guest_cert }
```
Verifier checks, using only the master **public** key + the revocation register:
1. `master_signature` valid over the cert body → master signed off
2. `issued_at <= now <= not_after` → not expired
3. `scope` permits this container fingerprint + op `attest` → in sandbox
4. `cert_id` not REVOKED in the register → still authorized
5. `guest_signature` valid over the attestation body under `guest_pubkey` → authentic
6. (when a live container is present) attestation fingerprint == container fingerprint, and `verify_proofs()` passes → bytes match

### New module: `axiom_axm_authority.py`
Keeps cert/PKI logic out of `axiom_axm.py`. Reuses asi07's crypto core (factor the
needed helpers into a small public surface, or import the existing `_generate_keypair`
/ `_sign` / `_verify` / `_fingerprint`, `_ED25519`).

```python
@dataclass(frozen=True)
class GuestCert:
    cert_id:           str
    guest_pubkey:      str          # hex
    guest_fingerprint: str          # sha256(pubkey)[:16]
    scope:             dict         # {"containers": ["*"|<fp>...], "ops": ["attest"], "max_trust_level": int}
    issued_at:         str          # ISO8601, PASSED IN (not Date.now)
    not_after:         str          # ISO8601 expiry
    crypto_mode:       str          # "ed25519" | "hmac"
    master_signature:  str = ""     # hex Ed25519 sig by master priv over canonical body

def mint_master() -> tuple[bytes, bytes]            # (priv, pub) — store priv offline
def issue_guest(master_priv, guest_pub, scope, issued_at, not_after) -> GuestCert
def sign_attestation_as_guest(guest_priv, cert, attestation: dict) -> dict   # bundle
def verify_guest_bundle(bundle: dict, master_pub: bytes, now_iso: str,
                        container: "AXMContainer | None" = None,
                        revocation: "GuestCertLedger | None" = None) -> GuestVerification
```

`GuestVerification` mirrors asi07's `VerificationResult`:
`{valid: bool, status: str, reason: str}` with statuses
`VERIFIED | INVALID_CERT | EXPIRED | SCOPE_DENIED | REVOKED | INVALID_SIG | FINGERPRINT_MISMATCH | INTEGRITY_FAIL`.

### Revocation register
Reuse the bonded-pair ledger (`axiom_event_token/bonded_pair.py`): hash-chained,
HMAC-signed, `ACTIVE_VALIDATED → REVOKED` terminal transition. Either key it by
`cert_id`, or add a thin `GuestCertLedger` wrapper modeled on `BondedPairLedger`.
`revoke_guest(cert_id, actor, ts)` appends a REVOKED row; `is_revoked(cert_id)`
replays the chain. No key rotation — the guest pubkey bytes never change.

### `.axm` wiring (`axiom_axm.py`)
- `attest()` is unchanged (symmetric, same-domain).
- New `AXMContainer.attest_bundle(guest_priv, cert, now_iso)` → calls `attest()` for the
  body, then `sign_attestation_as_guest()`.
- `verify_guest_bundle(...)` lives in the authority module and optionally takes the live
  container to cross-check fingerprint + run `verify_proofs()`.

### CLI
On `axiom_axm.py` (or a sibling `axiom_axm_authority.py` CLI):
- `mint-master --out master_priv.key` → writes priv (chmod 600), prints master pubkey hex
- `issue-guest --master-key <priv> --guest-pubkey <hex> --scope <json> --issued-at <iso> --expires <iso>` → cert JSON
- `attest --as-guest --guest-key <priv> --cert <cert.json> <container>` → bundle JSON
- `verify --guest-bundle <bundle.json> --master-pubkey <hex> --now <iso> [<container>]`
- `revoke-guest <cert_id> --actor <id> --now <iso>`

## Critical files
- **New**: `axiom_axm_authority.py` — cert types, issue/sign/verify, CLI; reuses asi07
  crypto (`axiom_constitutional/security/asi07_message_auth.py:120-158`) and the
  bonded-pair ledger.
- **Edit**: `axiom_axm.py` — `attest_bundle()` method + CLI subcommands; reuse the
  shipped `attest()` (body) and `verify_proofs()` (integrity).
- **New**: `tests/test_axm_guest_key.py`.
- **Edit**: `README.md` `.axm` paragraph — note guest-key delegation + that third-party
  verification needs the Ed25519 path.

## Tests (`tests/test_axm_guest_key.py`)
- `test_passed_guest_bundle_verifies_with_master_pubkey_only` — happy path, master priv
  not present at verify.
- `test_blocked_expired_cert` — `now > not_after` → EXPIRED.
- `test_blocked_scope_denied` — cert scoped to container A, attest B → SCOPE_DENIED.
- `test_blocked_revoked_cert` — revoke then verify → REVOKED.
- `test_blocked_self_extended_expiry` — guest edits `not_after` → master_signature
  invalid → INVALID_CERT (proves a guest can't extend itself).
- `test_blocked_wrong_signer` — attestation signed by a key not in the cert → INVALID_SIG.
- `test_blocked_swapped_weight_under_valid_cert` — valid cert + guest sig, but a swapped
  weight → INTEGRITY_FAIL (cert authority doesn't bypass `verify_proofs()`).
- `test_invariant_master_private_key_never_in_bundle` — key hygiene (mirrors the existing
  `test_invariant_signing_key_never_exposed`).
- `test_hmac_fallback_same_domain_only` — when `_ED25519` is False, verify still works but
  the doc/return notes it is not third-party-safe.

## Honesty caveats (must be in code + README, not hidden)
- **Ed25519 required for the real property.** Under the HMAC fallback (`cryptography`
  not installed), "verification" re-derives the private key from the master — i.e. it is
  symmetric and forgeable by a key holder. The bundle's `crypto_mode` must surface this;
  third-party verification is only honest in `ed25519` mode.
- **Clock is the caller's.** Expiry is checked against a `now` passed in by the verifier
  (consistent with the rest of Axiom not calling `Date.now()`); it is only as trustworthy
  as the verifier's clock.
- **Master private key is the root of trust.** Kept offline; compromise = full break.
  Rotation = issue a new master, publish the new pubkey, re-issue guest certs.
- **Revocation needs an online register fetch.** An offline verifier can honor `not_after`
  but not revocation — the standard PKI CRL/OCSP tradeoff. Keep `not_after` windows short
  for high-trust guests.

## Verification (when built)
```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
python -m pytest tests/test_axm_guest_key.py -v

# End-to-end, master private key absent at verify:
python axiom_axm_authority.py mint-master --out /tmp/master.key            # prints master pubkey
python axiom_axm_authority.py issue-guest --master-key /tmp/master.key \
    --guest-pubkey <hex> --scope '{"containers":["*"],"ops":["attest"]}' \
    --issued-at 2026-06-26T00:00:00Z --expires 2026-07-26T00:00:00Z > cert.json
python axiom_axm.py attest --as-guest --guest-key /tmp/guest.key --cert cert.json m.axm > bundle.json
python axiom_axm.py verify --guest-bundle bundle.json --master-pubkey <hex> \
    --now 2026-06-27T00:00:00Z m.axm        # expect VERIFIED
# tamper / expire / revoke → expect EXPIRED / SCOPE_DENIED / REVOKED / INVALID_*
```
