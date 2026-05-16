"""Authentication + usage tracking for the Firewall.

Password hashing: PBKDF2-HMAC-SHA256, 200k iterations, 16-byte salt.
(stdlib-only; no bcrypt dep)

API key auth: Authorization: Bearer axfw_<token>
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime
from time import perf_counter

from .db import find_tenant_for_secret, insert_usage
from .models import ApiKey, Tenant, UsageRecord

PBKDF2_ITERATIONS = 200_000
PBKDF2_SALT_BYTES = 16

TIER_RATE_LIMITS = {
    "free": 1_000,
    "indie": 50_000,
    "team": 500_000,
    "enterprise": 10_000_000,
}

TIER_PRICE_USD = {
    "free": 0,
    "indie": 49,
    "team": 199,
    "enterprise": None,  # custom
}


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(pw: str, pw_hash: str) -> bool:
    try:
        scheme, iters, salt_hex, digest_hex = pw_hash.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    try:
        iters_int = int(iters)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters_int)
    return hmac.compare_digest(candidate, expected)


def authenticate(secret: str) -> tuple[Tenant, ApiKey] | None:
    """Look up tenant + key for an API secret. None if invalid or revoked."""
    if not secret or not secret.startswith("axfw_"):
        return None
    return find_tenant_for_secret(secret)


def record_call(*, tenant_id: str, key_id: str, endpoint: str,
                verdict: str, intent_class: str, confidence: float,
                started_at: float) -> None:
    insert_usage(UsageRecord(
        record_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        api_key_id=key_id,
        endpoint=endpoint,
        verdict=verdict,
        intent_class=intent_class,
        confidence=confidence,
        latency_ms=round((perf_counter() - started_at) * 1000, 3),
        timestamp=datetime.utcnow(),
    ))
