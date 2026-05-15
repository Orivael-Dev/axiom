"""
AXIOM Signing Key Derivation
=============================
Single source of truth for all HMAC signing keys.

Every module derives its own unique key from one master secret
stored in the AXIOM_MASTER_KEY environment variable.  No secrets
in source code — the master key is generated once per deployment
and set in the shell environment.

Setup:
  python3 -c "import secrets; print(secrets.token_hex(32))"
  export AXIOM_MASTER_KEY="<64-char hex>"

Usage:
  from axiom_signing import derive_key
  SIGNING_KEY = derive_key(b"axiom-agent-v1")

BUG-013: hardcoded_signing_keys — this module is the fix.
github.com/Orivael-Dev/axiom
"""

import hashlib
import hmac
import os

_MASTER_RAW = os.environ.get("AXIOM_MASTER_KEY", "")

if not _MASTER_RAW:
    raise RuntimeError(
        "AXIOM_MASTER_KEY not set. "
        "Generate one: python3 -c \"import secrets; print(secrets.token_hex(32))\" "
        "then: export AXIOM_MASTER_KEY=\"<hex>\""
    )

_MASTER = _MASTER_RAW.encode("utf-8")


def derive_key(salt: bytes) -> bytes:
    """Derive a module-specific signing key from the master secret.

    Each module passes its own salt (e.g. b"axiom-agent-v1") so keys
    are cryptographically independent — compromising one derived key
    does not reveal the master or any other module's key.

    Construction: HMAC-SHA256(salt, master).  HMAC is the standard
    PRF-based KDF primitive — unlike a raw ``SHA256(master || salt)``
    construction it is not vulnerable to length-extension if anyone
    later reuses the helper as a MAC over attacker-controlled input.
    """
    if not isinstance(salt, (bytes, bytearray)):
        raise TypeError("salt must be bytes")
    return hmac.new(salt, _MASTER, hashlib.sha256).digest()
