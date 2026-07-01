# -*- coding: utf-8 -*-
"""
AXIOM .axm guest-key delegation — unit tests
=============================================
Master mints a keypair and signs a scoped, expiring guest cert; the guest signs
an .axm attestation; an outside verifier checks the bundle with ONLY the master
public key. Covers the spec test matrix in docs/AXM_GUEST_KEY_DELEGATION.md.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_axm_guest"

from axiom_axm import AXMContainer
from axiom_axm_authority import (
    mint_master, gen_keypair, issue_guest, sign_attestation_as_guest,
    verify_guest_bundle, GuestCert, GuestCertLedger, CRYPTO_MODE,
)
from examples.axm_pack_starter import STARTER_SPEC

ISSUED = "2026-06-01T00:00:00Z"
NOT_AFTER = "2026-07-01T00:00:00Z"
NOW_OK = "2026-06-15T00:00:00Z"


def _pack_with_weights(tmp_path):
    wsrc = tmp_path / "wsrc"
    wsrc.mkdir()
    (wsrc / "model.bin").write_bytes(b"REAL_WEIGHTS_V1")
    return AXMContainer.pack(STARTER_SPEC, str(tmp_path / "m.axm"),
                             weights_source_dir=wsrc)


def _master_guest_cert(scope=None):
    master_priv, master_pub = mint_master()
    guest_priv_b, guest_pub_b = gen_keypair()
    guest_priv, guest_pub = guest_priv_b.hex(), guest_pub_b.hex()
    cert = issue_guest(master_priv, guest_pub,
                       scope or {"containers": ["*"], "ops": ["attest"]},
                       ISSUED, NOT_AFTER)
    return master_priv, master_pub, guest_priv, guest_pub, cert


# ── PASSED ─────────────────────────────────────────────────────────────────────

class TestGuestPassed:

    def test_passed_guest_bundle_verifies_with_master_pubkey_only(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        # Verifier has ONLY the master public key (no private key anywhere).
        result = verify_guest_bundle(bundle, master_pub, NOW_OK, container=c)
        assert result.valid is True, result.reason
        assert result.status == "VERIFIED"

    def test_passed_verifies_without_live_container(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        result = verify_guest_bundle(bundle, master_pub, NOW_OK)  # no container
        assert result.valid is True, result.reason


# ── BLOCKED ─────────────────────────────────────────────────────────────────────

class TestGuestBlocked:

    def test_blocked_expired_cert(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        result = verify_guest_bundle(bundle, master_pub, "2026-08-01T00:00:00Z", container=c)
        assert result.valid is False
        assert result.status == "EXPIRED"

    def test_blocked_not_yet_valid(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        result = verify_guest_bundle(bundle, master_pub, "2026-05-01T00:00:00Z", container=c)
        assert result.valid is False
        assert result.status == "NOT_YET_VALID"

    def test_blocked_scope_denied_wrong_container(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        # Cert scoped to a different container fingerprint.
        _, master_pub, guest_priv, _, cert = _master_guest_cert(
            scope={"containers": ["00000000"], "ops": ["attest"]})
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        result = verify_guest_bundle(bundle, master_pub, NOW_OK, container=c)
        assert result.valid is False
        assert result.status == "SCOPE_DENIED"

    def test_blocked_scope_denied_wrong_op(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert(
            scope={"containers": ["*"], "ops": ["deploy"]})  # no "attest"
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        result = verify_guest_bundle(bundle, master_pub, NOW_OK, container=c)
        assert result.valid is False
        assert result.status == "SCOPE_DENIED"

    def test_blocked_revoked_cert(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        ledger = GuestCertLedger(tmp_path / "revocations.jsonl")
        ledger.revoke(cert.cert_id, actor="security", now_iso=NOW_OK)
        result = verify_guest_bundle(bundle, master_pub, NOW_OK, container=c, revocation=ledger)
        assert result.valid is False
        assert result.status == "REVOKED"

    def test_blocked_self_extended_expiry(self, tmp_path):
        # Guest tries to extend its own not_after — master_signature no longer covers it.
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        bundle["guest_cert"]["not_after"] = "2099-01-01T00:00:00Z"
        result = verify_guest_bundle(bundle, master_pub, NOW_OK, container=c)
        assert result.valid is False
        assert result.status == "INVALID_CERT"

    def test_blocked_widened_scope(self, tmp_path):
        # Guest tries to widen scope — master signature breaks.
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert(
            scope={"containers": ["00000000"], "ops": ["attest"]})
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        bundle["guest_cert"]["scope"] = {"containers": ["*"], "ops": ["attest"]}
        result = verify_guest_bundle(bundle, master_pub, NOW_OK, container=c)
        assert result.valid is False
        assert result.status == "INVALID_CERT"

    def test_blocked_wrong_signer(self, tmp_path):
        # Attestation signed by a key NOT bound in the cert.
        c = _pack_with_weights(tmp_path)
        _, master_pub, _, _, cert = _master_guest_cert()
        other_priv = gen_keypair()[0].hex()
        bundle = sign_attestation_as_guest(other_priv, cert, c.attest())
        result = verify_guest_bundle(bundle, master_pub, NOW_OK, container=c)
        assert result.valid is False
        assert result.status == "INVALID_SIG"

    def test_blocked_wrong_master_pubkey(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        _, _, guest_priv, _, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        _, attacker_pub = mint_master()  # not the issuing master
        result = verify_guest_bundle(bundle, attacker_pub, NOW_OK, container=c)
        assert result.valid is False
        assert result.status == "INVALID_CERT"

    def test_blocked_swapped_weight_under_valid_cert(self, tmp_path):
        # Valid cert + valid guest sig, but the container bytes were tampered.
        c = _pack_with_weights(tmp_path)
        _, master_pub, guest_priv, _, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        (Path(c.path) / "weights" / "model.bin").write_bytes(b"SWAPPED")
        reloaded = AXMContainer.from_path(str(c.path))
        result = verify_guest_bundle(bundle, master_pub, NOW_OK, container=reloaded)
        assert result.valid is False
        # Either the fingerprint still matches (it does — fingerprint is header-only)
        # so verify_proofs catches the swap → INTEGRITY_FAIL.
        assert result.status == "INTEGRITY_FAIL"

    def test_blocked_malformed_bundle(self):
        result = verify_guest_bundle({"nonsense": 1}, "ab" * 32, NOW_OK)
        assert result.valid is False
        assert result.status == "MALFORMED"


# ── INVARIANTS ─────────────────────────────────────────────────────────────────

class TestGuestInvariants:

    def test_invariant_master_private_key_never_in_bundle(self, tmp_path):
        c = _pack_with_weights(tmp_path)
        master_priv, master_pub, guest_priv, guest_pub, cert = _master_guest_cert()
        bundle = sign_attestation_as_guest(guest_priv, cert, c.attest())
        blob = json.dumps(bundle)
        assert master_priv not in blob
        assert guest_priv not in blob
        # The PUBLIC key + fingerprint are expected to be present.
        assert guest_pub in blob

    def test_invariant_ed25519_active_in_this_env(self):
        # The cryptography lib is installed here, so we exercise the real asymmetric
        # path (not the HMAC fallback). Guards against silent degradation.
        assert CRYPTO_MODE == "ed25519"

    def test_invariant_revocation_ledger_is_signed_and_chained(self, tmp_path):
        ledger = GuestCertLedger(tmp_path / "rev.jsonl")
        e1 = ledger.revoke("gc-aaa", "actor1", NOW_OK)
        e2 = ledger.revoke("gc-bbb", "actor2", NOW_OK)
        assert e2["prev_hash"] == e1["entry_hash"]
        assert ledger.is_revoked("gc-aaa") is True
        assert ledger.is_revoked("gc-bbb") is True
        assert ledger.is_revoked("gc-ccc") is False
        # Tamper: flip an action and the signature no longer matches → not revoked.
        lines = (tmp_path / "rev.jsonl").read_text().splitlines()
        rec = json.loads(lines[0]); rec["cert_id"] = "gc-zzz"
        (tmp_path / "rev.jsonl").write_text(json.dumps(rec) + "\n" + lines[1] + "\n")
        assert ledger.is_revoked("gc-zzz") is False  # forged entry rejected
