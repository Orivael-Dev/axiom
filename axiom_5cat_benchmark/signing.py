"""HMAC signing for benchmark results.

Every trial result and the top-level meta block are signed under a
namespace-specific key derived from ``AXIOM_MASTER_KEY``. A third
party holding the same master key can re-verify a published
``results.json`` by re-deriving the key and recomputing the HMAC
over the canonical JSON.

Canonical JSON shape mirrors axiom_exoskeleton_ledger._canonical:
``json.dumps(d, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)`` so signatures are byte-stable across Python
versions and platforms.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from axiom_signing import derive_key

# Salt is part of the public contract — anyone re-verifying a
# results.json needs to derive the same key from their master.
SIGNING_NAMESPACE: bytes = b"axiom-5cat-bench-v1"

# Separate namespace for fingerprinting the master key itself, so we
# can publish a non-secret "this run used master X" tag in meta
# without leaking key material.
FINGERPRINT_NAMESPACE: bytes = b"axiom-5cat-bench-fpr-v1"

_SIG_PREFIX = "hmac-sha256:"


def _bench_key() -> bytes:
    return derive_key(SIGNING_NAMESPACE)


def _canonical(d: dict) -> bytes:
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def sign_result(payload: dict[str, Any]) -> str:
    """Return ``hmac-sha256:<hex>`` over the canonical JSON of payload.

    The caller MUST pass the payload WITHOUT any existing signature
    field — signing.py never strips fields, so leaving a stale
    signature in produces a different canonical form than verify
    will see."""
    sig = hmac.new(
        _bench_key(), _canonical(payload), hashlib.sha256,
    ).hexdigest()
    return f"{_SIG_PREFIX}{sig}"


def verify_result(payload: dict[str, Any], signature: str) -> bool:
    """Constant-time compare against re-derived signature.

    Returns False on any malformed input rather than raising —
    a verify call shouldn't crash on a bad results.json."""
    if not isinstance(signature, str) or not signature.startswith(_SIG_PREFIX):
        return False
    expected = sign_result(payload)
    return hmac.compare_digest(signature, expected)


def master_key_fingerprint() -> str:
    """A non-secret tag identifying which master key signed this run.

    Derived from a *different* namespace than the signing key, so
    publishing this fingerprint can't help an attacker forge
    signatures even if they intercept it."""
    fpr = hashlib.sha256(derive_key(FINGERPRINT_NAMESPACE)).hexdigest()
    return f"sha256:{fpr[:16]}"


def sign_and_attach(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ``{**payload, "signature": "hmac-sha256:..."}``.

    Convenience wrapper for the common case of "sign and embed."
    Does NOT mutate the input dict."""
    out = dict(payload)
    out.pop("signature", None)
    out["signature"] = sign_result(out)
    return out


def verify_attached(signed_payload: dict[str, Any]) -> bool:
    """Round-trip check for a dict produced by sign_and_attach."""
    if "signature" not in signed_payload:
        return False
    sig = signed_payload["signature"]
    payload = {k: v for k, v in signed_payload.items() if k != "signature"}
    return verify_result(payload, sig)
