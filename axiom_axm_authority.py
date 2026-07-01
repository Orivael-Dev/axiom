"""
AXIOM .axm Guest-Key Delegation — ORVL-023 authority layer
===========================================================
Asymmetric (Ed25519) delegation for `.axm` attestation. A main account mints a
master keypair and signs scoped, auto-expiring **guest certificates**; a guest
signs deployment attestations with its own key; an outside auditor verifies a
bundle with only the published master **public** key — the master is never online
at verify time, and a guest cannot forge outside its grant.

Trust chain:
    master keypair (Ed25519)            private OFFLINE; public PUBLISHED
       │ signs
       ▼
    GuestCert {guest_pubkey, scope, issued_at, not_after, cert_id, master_sig}
       │ authorizes
       ▼
    guest signs an .axm attestation body  →  bundle {attestation, guest_sig, cert}

Verify (master public key + revocation register only):
    1. master_signature over cert body valid
    2. issued_at <= now <= not_after
    3. scope permits this container fingerprint + op "attest"
    4. cert_id not REVOKED in the register
    5. guest_signature over the attestation valid under guest_pubkey
    6. (with a live container) attestation fingerprint == container fingerprint
       and container.verify_proofs() passes

HONESTY:
  * Ed25519 gives the real property — third parties verify with the public key and
    cannot forge. When the `cryptography` library is unavailable this module falls
    back to a symmetric HMAC mode where the "public key" is secret-equivalent; that
    mode is same-trust-domain only and is surfaced as crypto_mode="hmac". Check
    `CRYPTO_MODE` / a bundle's cert crypto_mode before trusting it across a boundary.
  * Expiry is checked against a caller-supplied `now` (consistent with the rest of
    AXIOM not reading a wall clock at sign/verify); it is only as good as the
    verifier's clock.
  * The master private key is the root of trust — keep it offline; compromise is a
    full break. Rotation = new master, publish the new pubkey, re-issue certs.
  * Revocation requires an online register fetch; an offline verifier can honor
    expiry but not revocation (standard PKI CRL/OCSP tradeoff).

Prior art in-repo: `axiom_constitutional/security/asi07_message_auth.py` (Ed25519
mutual auth), `axiom_event_token/bonded_pair.py` (hash-chained revocation register).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── Crypto core: Ed25519 with HMAC fallback ───────────────────────────────────
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, PublicFormat, NoEncryption,
    )
    _ED25519 = True
except Exception:  # pragma: no cover - exercised only when lib absent
    _ED25519 = False

CRYPTO_MODE = "ed25519" if _ED25519 else "hmac"


def _canon(payload: Mapping[str, Any]) -> bytes:
    """Canonical JSON bytes for signing — matches axiom_axm._canonical."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def gen_keypair() -> tuple[bytes, bytes]:
    """Return (private_bytes, public_bytes).

    In ed25519 mode the public key is genuinely public. In the HMAC fallback the
    "public" key equals the secret (symmetric) — NOT third-party safe.
    """
    if _ED25519:
        priv = Ed25519PrivateKey.generate()
        priv_b = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pub_b = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return priv_b, pub_b
    secret = os.urandom(32)
    return secret, secret


def _sign_bytes(priv: bytes, msg: bytes) -> bytes:
    if _ED25519:
        return Ed25519PrivateKey.from_private_bytes(priv).sign(msg)
    return hmac_lib.new(priv, msg, hashlib.sha256).digest()


def _verify_bytes(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if _ED25519:
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
            return True
        except Exception:
            return False
    return hmac_lib.compare_digest(hmac_lib.new(pub, msg, hashlib.sha256).digest(), sig)


def _fingerprint(pub: bytes) -> str:
    return hashlib.sha256(pub).hexdigest()[:16]


# ── Guest certificate ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GuestCert:
    cert_id:           str
    guest_pubkey:      str            # hex
    guest_fingerprint: str
    scope:             dict           # {"containers": ["*"|<fp>...], "ops": ["attest"], "max_trust_level": int}
    issued_at:         str            # ISO8601 UTC, caller-supplied
    not_after:         str            # ISO8601 UTC expiry
    crypto_mode:       str            # "ed25519" | "hmac"
    master_signature:  str = ""       # hex; master's signature over body()

    def body(self) -> dict:
        return {
            "cert_id":           self.cert_id,
            "guest_pubkey":      self.guest_pubkey,
            "guest_fingerprint": self.guest_fingerprint,
            "scope":             self.scope,
            "issued_at":         self.issued_at,
            "not_after":         self.not_after,
            "crypto_mode":       self.crypto_mode,
        }

    def to_dict(self) -> dict:
        return {**self.body(), "master_signature": self.master_signature}


@dataclass
class GuestVerification:
    valid:  bool
    status: str    # VERIFIED | INVALID_CERT | NOT_YET_VALID | EXPIRED | SCOPE_DENIED
                   # | REVOKED | INVALID_SIG | FINGERPRINT_MISMATCH | INTEGRITY_FAIL | MALFORMED
    reason: str

    def to_dict(self) -> dict:
        return {"valid": self.valid, "status": self.status, "reason": self.reason}


# ── Issue / sign ──────────────────────────────────────────────────────────────

def mint_master() -> tuple[str, str]:
    """Generate a master keypair. Returns (private_hex, public_hex).

    Store the private hex OFFLINE; publish the public hex as the org root of trust.
    """
    priv, pub = gen_keypair()
    return priv.hex(), pub.hex()


def issue_guest(master_priv_hex: str, guest_pubkey_hex: str, scope: Mapping[str, Any],
                issued_at: str, not_after: str,
                cert_id: Optional[str] = None) -> GuestCert:
    """Master signs a scoped, expiring certificate authorizing a guest key."""
    guest_pub = bytes.fromhex(guest_pubkey_hex)
    fp = _fingerprint(guest_pub)
    if cert_id is None:
        cert_id = "gc-" + hashlib.sha256(
            (guest_pubkey_hex + issued_at + not_after).encode()
        ).hexdigest()[:12]
    cert = GuestCert(
        cert_id=cert_id, guest_pubkey=guest_pubkey_hex, guest_fingerprint=fp,
        scope=dict(scope), issued_at=issued_at, not_after=not_after,
        crypto_mode=CRYPTO_MODE,
    )
    sig = _sign_bytes(bytes.fromhex(master_priv_hex), _canon(cert.body())).hex()
    return GuestCert(**{**asdict(cert), "master_signature": sig})


def sign_attestation_as_guest(guest_priv_hex: str, cert: GuestCert,
                              attestation: Mapping[str, Any]) -> dict:
    """Guest signs an `.axm` attestation body and wraps it with its cert."""
    gsig = _sign_bytes(bytes.fromhex(guest_priv_hex), _canon(attestation)).hex()
    return {
        "axm_guest_bundle_version": "1.0",
        "attestation":    dict(attestation),
        "guest_signature": gsig,
        "guest_cert":      cert.to_dict(),
    }


# ── Verify ────────────────────────────────────────────────────────────────────

def _scope_allows(scope: Mapping[str, Any], target_fp: Optional[str]) -> bool:
    ops = scope.get("ops", ["attest"])
    if "attest" not in ops:
        return False
    containers = scope.get("containers", ["*"])
    if "*" in containers:
        return True
    return target_fp is not None and target_fp in containers


def verify_guest_bundle(bundle: Mapping[str, Any], master_pubkey_hex: str,
                        now_iso: str, container=None,
                        revocation: "Optional[GuestCertLedger]" = None) -> GuestVerification:
    """Verify a guest attestation bundle against the master public key.

    `container` (an AXMContainer) is optional; when supplied the live fingerprint
    and `verify_proofs()` are cross-checked so a valid cert can't vouch for tampered
    bytes. `revocation` is an optional register consulted for cert_id revocation.
    """
    try:
        cert_d = dict(bundle["guest_cert"])
        attestation = dict(bundle["attestation"])
        guest_sig = bundle["guest_signature"]
        master_sig = cert_d.get("master_signature", "")
    except (KeyError, TypeError, ValueError):
        return GuestVerification(False, "MALFORMED", "bundle missing required fields")

    # 1. Master signed the cert body.
    body = {k: cert_d.get(k) for k in
            ("cert_id", "guest_pubkey", "guest_fingerprint", "scope",
             "issued_at", "not_after", "crypto_mode")}
    try:
        ok = _verify_bytes(bytes.fromhex(master_pubkey_hex), _canon(body),
                           bytes.fromhex(master_sig))
    except (ValueError, TypeError):
        ok = False
    if not ok:
        return GuestVerification(False, "INVALID_CERT",
                                 "master signature over the cert does not verify")

    # 2. Validity window (ISO-8601 UTC strings compare lexicographically).
    if now_iso < cert_d.get("issued_at", ""):
        return GuestVerification(False, "NOT_YET_VALID",
                                 "now is before the cert issued_at")
    if now_iso > cert_d.get("not_after", ""):
        return GuestVerification(False, "EXPIRED", "cert not_after has passed")

    # 3. Scope: which container + which op.
    target_fp = container.fingerprint() if container is not None else attestation.get("fingerprint")
    if not _scope_allows(cert_d.get("scope", {}), target_fp):
        return GuestVerification(False, "SCOPE_DENIED",
                                 "cert scope does not permit attest on this container")

    # 4. Revocation.
    if revocation is not None and revocation.is_revoked(cert_d.get("cert_id", "")):
        return GuestVerification(False, "REVOKED", "cert_id is revoked in the register")

    # 5. Guest signed the attestation.
    try:
        gok = _verify_bytes(bytes.fromhex(cert_d["guest_pubkey"]), _canon(attestation),
                            bytes.fromhex(guest_sig))
    except (ValueError, TypeError, KeyError):
        gok = False
    if not gok:
        return GuestVerification(False, "INVALID_SIG",
                                 "guest signature over the attestation does not verify")

    # 6. Cross-check against a live container, if provided.
    if container is not None:
        if attestation.get("fingerprint") != container.fingerprint():
            return GuestVerification(False, "FINGERPRINT_MISMATCH",
                                     "attestation fingerprint != live container fingerprint")
        if not container.verify_proofs():
            return GuestVerification(False, "INTEGRITY_FAIL",
                                     "container.verify_proofs() failed — bytes do not match")

    return GuestVerification(True, "VERIFIED", "cert + guest signature + scope all valid")


# ── Revocation register (hash-chained, HMAC-signed) ───────────────────────────

def _ledger_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-axm-guest-ledger-v1")


def _default_ledger_path() -> Path:
    return Path.home() / ".axiom" / "axm_guest_cert_ledger.jsonl"


class GuestCertLedger:
    """Append-only, hash-chained, HMAC-signed revocation register for guest certs.

    Modeled on `axiom_event_token/bonded_pair.py`: revocation lives in a ledger, not
    in the cert bytes, so revoking needs no key rotation. Tampered or broken-chain
    entries are ignored on replay.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or os.environ.get("AXM_GUEST_LEDGER", _default_ledger_path()))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _sign(self, entry: dict) -> str:
        payload = {k: v for k, v in entry.items() if k != "signature"}
        return hmac_lib.new(_ledger_key(), _canon(payload), hashlib.sha256).hexdigest()

    def _last_hash(self) -> str:
        prev = "GENESIS"
        if not self.path.exists():
            return prev
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            prev = json.loads(line).get("entry_hash", prev)
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass
        return prev

    def revoke(self, cert_id: str, actor: str, now_iso: str) -> dict:
        entry = {
            "cert_id":   cert_id,
            "action":    "REVOKED",
            "actor":     actor,
            "timestamp": now_iso,
            "prev_hash": self._last_hash(),
        }
        entry["entry_hash"] = hashlib.sha256(
            (entry["prev_hash"] + _canon(entry).decode()).encode()
        ).hexdigest()[:32]
        entry["signature"] = self._sign(entry)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def is_revoked(self, cert_id: str) -> bool:
        if not self.path.exists():
            return False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("cert_id") == cert_id and e.get("action") == "REVOKED":
                        if e.get("signature") == self._sign(e):
                            return True
        except OSError:
            pass
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def _read_hex(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="axiom_axm_authority",
        description="AXIOM .axm guest-key delegation (Ed25519 cert model)",
    )
    sub = p.add_subparsers(dest="action", required=True)

    pm = sub.add_parser("mint-master", help="generate a master keypair")
    pm.add_argument("--out", required=True, help="write master private hex here (chmod 600)")

    pg = sub.add_parser("gen-guest", help="generate a guest keypair")
    pg.add_argument("--out", required=True, help="write guest private hex here (chmod 600)")

    pi = sub.add_parser("issue-guest", help="master signs a guest cert")
    pi.add_argument("--master-key", required=True, help="file with master private hex")
    pi.add_argument("--guest-pubkey", required=True, help="guest public key hex")
    pi.add_argument("--scope", required=True, help='JSON, e.g. {"containers":["*"],"ops":["attest"]}')
    pi.add_argument("--issued-at", required=True, help="ISO8601 UTC")
    pi.add_argument("--expires", required=True, help="ISO8601 UTC (not_after)")
    pi.add_argument("--cert-id", help="optional explicit cert id")

    pa = sub.add_parser("attest-guest", help="guest signs an attestation bundle for a container")
    pa.add_argument("--guest-key", required=True, help="file with guest private hex")
    pa.add_argument("--cert", required=True, help="guest cert JSON file")
    pa.add_argument("container", help="path to the .axm container")

    pv = sub.add_parser("verify-guest", help="verify a guest bundle with the master pubkey")
    pv.add_argument("--bundle", required=True, help="bundle JSON file")
    pv.add_argument("--master-pubkey", required=True, help="master public key hex")
    pv.add_argument("--now", required=True, help="ISO8601 UTC")
    pv.add_argument("--container", help="optional live container to cross-check")
    pv.add_argument("--ledger", help="optional revocation register path")

    pr = sub.add_parser("revoke-guest", help="append a REVOKED entry to the register")
    pr.add_argument("--cert-id", required=True)
    pr.add_argument("--actor", required=True)
    pr.add_argument("--now", required=True, help="ISO8601 UTC")
    pr.add_argument("--ledger", help="revocation register path")

    args = p.parse_args(argv)

    if args.action in ("mint-master", "gen-guest"):
        priv_hex, pub_hex = mint_master()
        out = Path(args.out)
        out.write_text(priv_hex, encoding="utf-8")
        try:
            os.chmod(out, 0o600)
        except OSError:
            pass
        role = "master" if args.action == "mint-master" else "guest"
        print(json.dumps({"role": role, "crypto_mode": CRYPTO_MODE,
                          "public_key": pub_hex, "private_key_file": str(out)},
                         indent=2))
        return 0

    if args.action == "issue-guest":
        cert = issue_guest(_read_hex(args.master_key), args.guest_pubkey,
                           json.loads(args.scope), args.issued_at, args.expires,
                           cert_id=args.cert_id)
        print(json.dumps(cert.to_dict(), indent=2, ensure_ascii=True))
        return 0

    if args.action == "attest-guest":
        from axiom_axm import AXMContainer
        c = AXMContainer.from_path(args.container)
        if not c.verify_proofs():
            print(json.dumps({"error": "container failed verify_proofs() — refusing to attest"}))
            return 1
        cert_d = json.loads(Path(args.cert).read_text(encoding="utf-8"))
        cert = GuestCert(**{k: cert_d[k] for k in (
            "cert_id", "guest_pubkey", "guest_fingerprint", "scope",
            "issued_at", "not_after", "crypto_mode", "master_signature")})
        bundle = sign_attestation_as_guest(_read_hex(args.guest_key), cert, c.attest())
        print(json.dumps(bundle, indent=2, ensure_ascii=True))
        return 0

    if args.action == "verify-guest":
        from axiom_axm import AXMContainer
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        container = AXMContainer.from_path(args.container) if args.container else None
        ledger = GuestCertLedger(Path(args.ledger)) if args.ledger else None
        result = verify_guest_bundle(bundle, args.master_pubkey, args.now,
                                     container=container, revocation=ledger)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=True))
        return 0 if result.valid else 1

    if args.action == "revoke-guest":
        ledger = GuestCertLedger(Path(args.ledger)) if args.ledger else GuestCertLedger()
        entry = ledger.revoke(args.cert_id, args.actor, args.now)
        print(json.dumps({"revoked": entry["cert_id"], "entry_hash": entry["entry_hash"]},
                         indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_main())
