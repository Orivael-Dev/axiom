"""
AXIOM ASI07 -- Inter-Agent Message Authentication
==================================================
OWASP Agentic Top 10 2026 | Gap: agents accept messages from unverified peers.

Mitigation:
  PKI-style per-agent identity using Ed25519 asymmetric keys.
  Every message is signed by the sender's private key.
  Recipients verify against the sender's public key in the registry.
  Mutual authentication: both sender AND recipient must be registered.
  Replay protection: nonce deduplication + 5-minute timestamp window.
  Key fingerprints are stored in certs/ for out-of-band verification.

CANNOT_MUTATE: registry_integrity, nonce_log, mutual_auth_requirement

Crypto:
  Primary : Ed25519 (cryptography library)
  Fallback: HMAC-SHA256 with per-agent derived keys (if cryptography unavailable)

Usage:
  python -m axiom_constitutional.security.asi07_message_auth --demo
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# ── Crypto layer ──────────────────────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption,
    )
    from cryptography.exceptions import InvalidSignature
    _ED25519 = True
except ImportError:
    _ED25519 = False

# ── Constants (CANNOT_MUTATE) ─────────────────────────────────────────────────
_MASTER_KEY    = os.environ.get("AXIOM_MASTER_KEY", "axiom-asi07-master-v1").encode()
CERTS_DIR      = Path("certs") / "agent_keys"
REGISTRY_FILE  = Path("certs") / "asi07_registry.json"
AUDIT_LOG      = Path("certs") / "asi07_audit.jsonl"
MSG_TTL        = 300          # seconds — reject messages older than this
NONCE_WINDOW   = 10_000       # max nonces to retain before LRU trim

# ── ANSI ──────────────────────────────────────────────────────────────────────
def _b(s):  return "\033[1m"  + s + "\033[0m"
def _g(s):  return "\033[32m" + s + "\033[0m"
def _y(s):  return "\033[33m" + s + "\033[0m"
def _r(s):  return "\033[31m" + s + "\033[0m"
def _c(s):  return "\033[36m" + s + "\033[0m"
def _m(s):  return "\033[35m" + s + "\033[0m"
def _gr(s): return "\033[90m" + s + "\033[0m"

SEP  = "=" * 62
DASH = "-" * 62


# ══════════════════════════════════════════════════════════════════════════════
# Dataclasses
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentIdentity:
    agent_id:    str
    fingerprint: str        # SHA-256 of public key bytes (first 16 hex chars)
    public_key:  str        # hex-encoded public key bytes
    trust_level: int
    registered:  str        # ISO8601 timestamp
    crypto_mode: str        # "ed25519" or "hmac"


@dataclass
class AgentMessage:
    msg_id:      str
    sender_id:   str
    recipient_id: str
    payload:     dict
    timestamp:   str
    nonce:       str
    signature:   str        # hex signature bytes


@dataclass
class VerificationResult:
    valid:       bool
    status:      str        # VERIFIED | INVALID_SIG | SENDER_UNKNOWN | RECIPIENT_UNKNOWN
                            # | REPLAY | EXPIRED | MUTUAL_AUTH_FAIL
    message:     Optional[AgentMessage]
    reason:      str
    sender_fp:   str        # fingerprint of sender (for out-of-band check)


# ══════════════════════════════════════════════════════════════════════════════
# Crypto primitives
# ══════════════════════════════════════════════════════════════════════════════

def _derive_hmac_key(agent_id: str) -> bytes:
    """Derive a deterministic per-agent key from the master secret."""
    return hmac.new(_MASTER_KEY, agent_id.encode(), hashlib.sha256).digest()


def _generate_keypair(agent_id: str) -> tuple[bytes, bytes]:
    """Returns (private_key_bytes, public_key_bytes)."""
    if _ED25519:
        priv = Ed25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pub_bytes  = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return priv_bytes, pub_bytes
    # HMAC fallback: private = derived key, "public" = HMAC(key, "pubkey")
    priv_bytes = _derive_hmac_key(agent_id)
    pub_bytes  = hmac.new(priv_bytes, b"pubkey", hashlib.sha256).digest()
    return priv_bytes, pub_bytes


def _sign(private_key_bytes: bytes, message_bytes: bytes, agent_id: str) -> bytes:
    if _ED25519:
        priv = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        return priv.sign(message_bytes)
    # HMAC fallback
    return hmac.new(private_key_bytes, message_bytes, hashlib.sha256).digest()


def _verify(public_key_bytes: bytes, message_bytes: bytes,
            signature_bytes: bytes, agent_id: str) -> bool:
    if _ED25519:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            pub = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            pub.verify(signature_bytes, message_bytes)
            return True
        except Exception:
            return False
    # HMAC fallback: re-derive private key and verify
    priv = _derive_hmac_key(agent_id)
    expected = hmac.new(priv, message_bytes, hashlib.sha256).digest()
    return hmac.compare_digest(expected, signature_bytes)


def _fingerprint(public_key_bytes: bytes) -> str:
    return hashlib.sha256(public_key_bytes).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════════════════
# Audit
# ══════════════════════════════════════════════════════════════════════════════

def _audit(event: str, detail: dict):
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **detail}
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except IOError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# AgentRegistry — central public key store
# ══════════════════════════════════════════════════════════════════════════════

class AgentRegistry:
    """
    Stores agent public keys and fingerprints.
    Private keys are NEVER stored here — only the signer holds them.

    Persistence: certs/asi07_registry.json (loaded on init, saved on register).
    """

    def __init__(self, persist: bool = True):
        self._identities: Dict[str, AgentIdentity] = {}
        self._persist = persist
        if persist:
            self._load()

    def _load(self):
        if REGISTRY_FILE.exists():
            try:
                data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
                for entry in data.get("agents", []):
                    ident = AgentIdentity(**entry)
                    self._identities[ident.agent_id] = ident
            except Exception:
                pass

    def _save(self):
        if not self._persist:
            return
        try:
            REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {"agents": [asdict(i) for i in self._identities.values()]}
            REGISTRY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except IOError:
            pass

    def register(self, agent_id: str, public_key_bytes: bytes,
                 trust_level: int = 3) -> AgentIdentity:
        """Register an agent's public key. Call once at agent startup."""
        fp   = _fingerprint(public_key_bytes)
        mode = "ed25519" if _ED25519 else "hmac"
        ident = AgentIdentity(
            agent_id    = agent_id,
            fingerprint = fp,
            public_key  = public_key_bytes.hex(),
            trust_level = trust_level,
            registered  = datetime.now(timezone.utc).isoformat(),
            crypto_mode = mode,
        )
        self._identities[agent_id] = ident
        self._save()
        _audit("REGISTERED", {"agent_id": agent_id, "fp": fp, "mode": mode})
        return ident

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        return self._identities.get(agent_id)

    def all_agents(self) -> list[AgentIdentity]:
        return list(self._identities.values())

    def is_registered(self, agent_id: str) -> bool:
        return agent_id in self._identities


# ══════════════════════════════════════════════════════════════════════════════
# AgentSigner — holds a private key, signs outgoing messages
# ══════════════════════════════════════════════════════════════════════════════

class AgentSigner:
    """
    Represents a single agent's signing capability.
    Holds the private key — never shares it with the registry.

    Usage:
      signer = AgentSigner.create("worker", registry, trust_level=3)
      msg    = signer.sign("safety", {"action": "run_eval", "data": ...})
    """

    def __init__(self, agent_id: str, private_key_bytes: bytes, registry: AgentRegistry):
        self.agent_id         = agent_id
        self._private_key     = private_key_bytes
        self._registry        = registry

    @classmethod
    def create(cls, agent_id: str, registry: AgentRegistry,
               trust_level: int = 3) -> "AgentSigner":
        """Generate a new key pair, register the public key, return the signer."""
        priv, pub = _generate_keypair(agent_id)
        registry.register(agent_id, pub, trust_level)
        return cls(agent_id, priv, registry)

    @classmethod
    def load(cls, agent_id: str, key_file: Path,
             registry: AgentRegistry) -> "AgentSigner":
        """Load an existing private key from a file."""
        priv = bytes.fromhex(key_file.read_text().strip())
        return cls(agent_id, priv, registry)

    def save_key(self, key_file: Path):
        """Persist private key (hex) to a local file. Never send to registry."""
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(self._private_key.hex())

    def sign(self, recipient_id: str, payload: dict) -> AgentMessage:
        """Sign a message to recipient_id."""
        msg_id = "msg_%s_%s" % (
            datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
            hashlib.sha256(f"{self.agent_id}{recipient_id}{time.time()}".encode()).hexdigest()[:8],
        )
        nonce     = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Sign: hash of (msg_id + sender + recipient + payload_json + timestamp + nonce)
        payload_bytes = _canonical_bytes(msg_id, self.agent_id, recipient_id,
                                         payload, timestamp, nonce)
        sig_bytes = _sign(self._private_key, payload_bytes, self.agent_id)

        msg = AgentMessage(
            msg_id       = msg_id,
            sender_id    = self.agent_id,
            recipient_id = recipient_id,
            payload      = payload,
            timestamp    = timestamp,
            nonce        = nonce,
            signature    = sig_bytes.hex(),
        )
        _audit("SIGNED", {"msg_id": msg_id, "sender": self.agent_id, "recipient": recipient_id})
        return msg


def _canonical_bytes(msg_id: str, sender: str, recipient: str,
                     payload: dict, timestamp: str, nonce: str) -> bytes:
    """Deterministic canonical form for signing/verification."""
    canon = json.dumps({
        "msg_id":       msg_id,
        "sender_id":    sender,
        "recipient_id": recipient,
        "payload":      payload,
        "timestamp":    timestamp,
        "nonce":        nonce,
    }, sort_keys=True)
    return canon.encode()


# ══════════════════════════════════════════════════════════════════════════════
# MessageAuthority — verifies incoming messages
# ══════════════════════════════════════════════════════════════════════════════

class MessageAuthority:
    """
    Verifies inter-agent messages against the registry.
    Enforces mutual authentication: both sender and recipient must be registered.
    Prevents replay attacks via nonce deduplication and timestamp window.
    """

    def __init__(self, registry: AgentRegistry):
        self._registry    = registry
        self._seen_nonces: set[str] = set()

    def verify(self, message: AgentMessage,
               recipient_id: str | None = None) -> VerificationResult:
        """
        Verify a received message.
        If recipient_id is provided, also checks the message was addressed to you.
        """
        # ── Mutual auth: sender must be registered ────────────────────────────
        sender_ident = self._registry.get(message.sender_id)
        if not sender_ident:
            return VerificationResult(
                False, "SENDER_UNKNOWN", message,
                f"Sender '{message.sender_id}' not in registry — mutual auth fails",
                sender_fp="",
            )

        # ── Mutual auth: recipient must be registered ─────────────────────────
        recip_ident = self._registry.get(message.recipient_id)
        if not recip_ident:
            return VerificationResult(
                False, "RECIPIENT_UNKNOWN", message,
                f"Recipient '{message.recipient_id}' not in registry — mutual auth fails",
                sender_fp=sender_ident.fingerprint,
            )

        # ── Recipient ID check ────────────────────────────────────────────────
        if recipient_id and message.recipient_id != recipient_id:
            return VerificationResult(
                False, "MUTUAL_AUTH_FAIL", message,
                f"Message addressed to '{message.recipient_id}' but received by '{recipient_id}'",
                sender_fp=sender_ident.fingerprint,
            )

        # ── Timestamp window ──────────────────────────────────────────────────
        try:
            msg_ts  = datetime.fromisoformat(message.timestamp)
            now     = datetime.now(timezone.utc)
            age_s   = (now - msg_ts).total_seconds()
            if abs(age_s) > MSG_TTL:
                return VerificationResult(
                    False, "EXPIRED", message,
                    f"Message age {age_s:.0f}s exceeds window {MSG_TTL}s",
                    sender_fp=sender_ident.fingerprint,
                )
        except ValueError:
            return VerificationResult(
                False, "EXPIRED", message, "Unparseable timestamp",
                sender_fp=sender_ident.fingerprint,
            )

        # ── Replay: nonce must be fresh ───────────────────────────────────────
        if message.nonce in self._seen_nonces:
            _audit("REPLAY_BLOCKED", {"msg_id": message.msg_id, "nonce": message.nonce})
            return VerificationResult(
                False, "REPLAY", message,
                f"Nonce '{message.nonce}' already seen — replay attack",
                sender_fp=sender_ident.fingerprint,
            )

        # ── Signature verification ────────────────────────────────────────────
        pub_bytes = bytes.fromhex(sender_ident.public_key)
        sig_bytes = bytes.fromhex(message.signature)
        canonical = _canonical_bytes(
            message.msg_id, message.sender_id, message.recipient_id,
            message.payload, message.timestamp, message.nonce,
        )

        if not _verify(pub_bytes, canonical, sig_bytes, message.sender_id):
            _audit("INVALID_SIG", {"msg_id": message.msg_id, "sender": message.sender_id})
            return VerificationResult(
                False, "INVALID_SIG", message,
                "Signature does not match sender's registered public key",
                sender_fp=sender_ident.fingerprint,
            )

        # ── Accept: register nonce ────────────────────────────────────────────
        self._seen_nonces.add(message.nonce)
        if len(self._seen_nonces) > NONCE_WINDOW:
            # Trim oldest (approximate — set has no order, but prevents unbounded growth)
            trim = len(self._seen_nonces) - NONCE_WINDOW // 2
            self._seen_nonces = set(list(self._seen_nonces)[trim:])

        _audit("VERIFIED", {
            "msg_id":   message.msg_id,
            "sender":   message.sender_id,
            "recipient": message.recipient_id,
            "fp":       sender_ident.fingerprint,
        })
        return VerificationResult(
            True, "VERIFIED", message, "",
            sender_fp=sender_ident.fingerprint,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Demo
# ══════════════════════════════════════════════════════════════════════════════

def _demo():
    registry  = AgentRegistry(persist=False)
    authority = MessageAuthority(registry)

    print()
    print("  " + SEP)
    print("  " + _b("AXIOM ASI07 -- Inter-Agent Message Authentication"))
    print("  " + _gr("OWASP Agentic Top 10 2026 -- gap mitigation"))
    print("  " + _gr("Crypto: " + ("Ed25519" if _ED25519 else "HMAC-SHA256 fallback")))
    print("  " + SEP)

    # ── Step 1: Register agents ────────────────────────────────────────────────
    print()
    print(_b("  [1/5] Agent Identity Registration"))
    print("  " + DASH)

    agent_specs = [
        ("sovereign", 5),
        ("worker",    3),
        ("safety",    5),
        ("critic",    3),
    ]
    signers: dict[str, AgentSigner] = {}
    for agent_id, trust in agent_specs:
        signer = AgentSigner.create(agent_id, registry, trust_level=trust)
        signers[agent_id] = signer
        ident = registry.get(agent_id)
        print("  %-12s  trust=%d  fp=%s  [%s]" % (
            _c(agent_id), trust,
            _m(ident.fingerprint),
            _g(ident.crypto_mode),
        ))

    # ── Step 2: Sign messages ──────────────────────────────────────────────────
    print()
    print(_b("  [2/5] Message Signing"))
    print("  " + DASH)

    msg1 = signers["sovereign"].sign("worker", {"action": "run_eval", "task_id": "task_001"})
    msg2 = signers["worker"].sign("safety",    {"action": "report",   "result": "PASS", "confidence": 0.82})
    msg3 = signers["safety"].sign("sovereign", {"verdict": "PROCEED", "risk_level": "LOW"})

    for msg in [msg1, msg2, msg3]:
        sig_preview = msg.signature[:16] + "..."
        print("  %-12s -> %-12s  msg=%s  sig=%s" % (
            msg.sender_id, msg.recipient_id,
            _gr(msg.msg_id[-12:]),
            _gr(sig_preview),
        ))

    # ── Step 3: Verify (mutual auth) ──────────────────────────────────────────
    print()
    print(_b("  [3/5] Mutual Authentication + Verification"))
    print("  " + DASH)

    for msg in [msg1, msg2, msg3]:
        v = authority.verify(msg, recipient_id=msg.recipient_id)
        col = _g if v.valid else _r
        print("  %-12s -> %-12s  [%s]  fp=%s" % (
            msg.sender_id, msg.recipient_id,
            col(_b(v.status)),
            _m(v.sender_fp),
        ))

    # ── Step 4: Attack scenarios ───────────────────────────────────────────────
    print()
    print(_b("  [4/5] Attack Scenarios"))
    print("  " + DASH)

    # Replay attack
    v_replay = authority.verify(msg1, recipient_id="worker")
    print("  Replay attack          [%s]  %s" % (
        _r(_b(v_replay.status)), _gr(v_replay.reason)
    ))

    # Tampered payload — use a fresh message (distinct nonce) then corrupt it
    import copy
    msg_fresh          = signers["worker"].sign("safety", {"action": "report", "result": "PASS"})
    tampered           = copy.deepcopy(msg_fresh)
    tampered.payload["result"] = "FAIL"   # attacker flips the result
    v_tamper = authority.verify(tampered, recipient_id="safety")
    print("  Tampered payload       [%s]  %s" % (
        _r(_b(v_tamper.status)), _gr(v_tamper.reason)
    ))

    # Unknown sender (spoofed agent)
    fake_signer = AgentSigner.create("__malicious__", AgentRegistry(persist=False))
    fake_msg    = fake_signer.sign("sovereign", {"inject": "override constitution"})
    v_fake      = authority.verify(fake_msg, recipient_id="sovereign")
    print("  Unknown sender         [%s]  %s" % (
        _r(_b(v_fake.status)), _gr(v_fake.reason)
    ))

    # Wrong recipient (message intended for someone else)
    msg4        = signers["critic"].sign("worker", {"critique": "needs work"})
    v_wrong     = authority.verify(msg4, recipient_id="safety")  # safety reads worker's mail
    print("  Wrong recipient        [%s]  %s" % (
        _r(_b(v_wrong.status)), _gr(v_wrong.reason)
    ))

    # ── Step 5: Fingerprint table ──────────────────────────────────────────────
    print()
    print(_b("  [5/5] Agent Fingerprint Registry"))
    print("  " + DASH)
    for ident in registry.all_agents():
        print("  %-12s  fp=%-16s  trust=%d" % (
            _c(ident.agent_id), _m(ident.fingerprint), ident.trust_level
        ))
    print("  Registry   : %s" % _gr(str(REGISTRY_FILE)))
    print("  Audit log  : %s" % _gr(str(AUDIT_LOG)))
    print()
    print("  " + SEP)
    print()


def main():
    parser = argparse.ArgumentParser(description="AXIOM ASI07 inter-agent message auth")
    parser.add_argument("--demo", action="store_true", help="Run interactive demo")
    args = parser.parse_args()
    if args.demo:
        _demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
