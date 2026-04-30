"""
AXIOM ASI03 — Task-Scoped Credentials
======================================
OWASP Agentic Top 10 2026 | Gap: agents inherit broad ambient credentials.

Mitigation:
  Each agent task receives a short-lived, signed session token.
  Permissions are derived from the agent's trust level (1-5).
  Token expires after TTL or explicit task completion.
  Revocable mid-task if a safety gate fires.
  Full audit trail of every issuance, validation, and revocation.

CANNOT_MUTATE: vault_signing_key, trust_scope_table, audit_log

Usage:
  python -m axiom_constitutional.security.asi03_credentials --demo
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# ── Constitutional constants (CANNOT_MUTATE) ──────────────────────────────────
_VAULT_KEY  = os.environ.get("AXIOM_VAULT_KEY", "axiom-asi03-vault-v1").encode()
AUDIT_LOG   = Path("certs") / "asi03_audit.jsonl"
DEFAULT_TTL = 1800  # 30 minutes

# Trust level → permission scopes (CANNOT_MUTATE — never widen without review)
TRUST_SCOPES: dict[int, list[str]] = {
    1: ["read_own_state"],
    2: ["read_own_state", "execute_task"],
    3: ["read_own_state", "execute_task", "write_output", "read_shared"],
    4: ["read_own_state", "execute_task", "write_output", "read_shared", "delegate_subtask"],
    5: ["read_own_state", "execute_task", "write_output", "read_shared", "delegate_subtask", "system_access"],
}

# ── ANSI ──────────────────────────────────────────────────────────────────────
def _b(s):  return "\033[1m"  + s + "\033[0m"
def _g(s):  return "\033[32m" + s + "\033[0m"
def _y(s):  return "\033[33m" + s + "\033[0m"
def _r(s):  return "\033[31m" + s + "\033[0m"
def _c(s):  return "\033[36m" + s + "\033[0m"
def _gr(s): return "\033[90m" + s + "\033[0m"

SEP  = "=" * 62
DASH = "-" * 62


# ══════════════════════════════════════════════════════════════════════════════
# Dataclasses
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SessionToken:
    token_id:    str
    agent_id:    str
    task_id:     str
    trust_level: int
    scope:       list
    issued_at:   str
    expires_at:  str
    status:      str    # ACTIVE | EXPIRED | REVOKED
    signature:   str


@dataclass
class ValidationResult:
    valid:      bool
    status:     str     # VALID | EXPIRED | REVOKED | NOT_FOUND | TAMPERED
    token:      Optional[SessionToken]
    reason:     str
    checked_at: str


# ══════════════════════════════════════════════════════════════════════════════
# Signing helpers
# ══════════════════════════════════════════════════════════════════════════════

def _sign_token(data: dict) -> str:
    # Exclude mutable lifecycle fields — signature covers immutable issuance params only
    _exclude = {"signature", "status"}
    payload = json.dumps({k: v for k, v in sorted(data.items()) if k not in _exclude})
    sig = hmac.new(_VAULT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{sig[:32]}..."


def _verify_sig(token: SessionToken) -> bool:
    expected = _sign_token(asdict(token))
    return hmac.compare_digest(token.signature, expected)


# ══════════════════════════════════════════════════════════════════════════════
# Audit
# ══════════════════════════════════════════════════════════════════════════════

def _audit(event: str, token_id: str, agent_id: str, detail: str = ""):
    entry = {
        "ts":       datetime.now(timezone.utc).isoformat(),
        "event":    event,
        "token_id": token_id,
        "agent_id": agent_id,
        "detail":   detail,
    }
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except IOError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# CredentialVault
# ══════════════════════════════════════════════════════════════════════════════

class CredentialVault:
    """
    Issues, validates, and revokes task-scoped session tokens.

    ASI03 guarantee: no agent ever holds ambient credentials.
    Every task.start() calls vault.issue(); every task.end() calls vault.complete_task().
    """

    def __init__(self):
        self._tokens: Dict[str, SessionToken] = {}

    # ── Issue ──────────────────────────────────────────────────────────────────

    def issue(
        self,
        agent_id:    str,
        task_id:     str,
        trust_level: int,
        ttl_seconds: int = DEFAULT_TTL,
    ) -> SessionToken:
        """
        Issue a new session token. Token is scoped to (agent, task) and expires
        after ttl_seconds. Permissions are derived from trust_level only.
        """
        trust_level = max(1, min(5, trust_level))
        scope       = TRUST_SCOPES[trust_level].copy()

        now    = datetime.now(timezone.utc)
        exp_ts = now.timestamp() + ttl_seconds
        exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)

        token_id = "tok_%s_%s" % (
            now.strftime("%Y%m%d%H%M%S"),
            hashlib.sha256(f"{agent_id}:{task_id}:{now.isoformat()}".encode()).hexdigest()[:8],
        )

        data = {
            "token_id":    token_id,
            "agent_id":    agent_id,
            "task_id":     task_id,
            "trust_level": trust_level,
            "scope":       scope,
            "issued_at":   now.isoformat(),
            "expires_at":  exp_dt.isoformat(),
            "status":      "ACTIVE",
            "signature":   "",
        }
        data["signature"] = _sign_token(data)
        token = SessionToken(**data)

        self._tokens[token_id] = token
        _audit("ISSUED", token_id, agent_id,
               f"task={task_id} trust={trust_level} ttl={ttl_seconds}s scope={scope}")
        return token

    # ── Validate ───────────────────────────────────────────────────────────────

    def validate(self, token_id: str) -> ValidationResult:
        """
        Validate a token. Checks: existence, signature integrity, status, expiry.
        Call this at the start of every agent action.
        """
        ts    = datetime.now(timezone.utc).isoformat()
        token = self._tokens.get(token_id)

        if not token:
            return ValidationResult(False, "NOT_FOUND", None, "Token not in vault", ts)

        if not _verify_sig(token):
            _audit("TAMPERED", token_id, token.agent_id, "signature mismatch on validate")
            return ValidationResult(False, "TAMPERED", token, "Signature mismatch — possible forgery", ts)

        if token.status == "REVOKED":
            return ValidationResult(False, "REVOKED", token, "Token was revoked", ts)

        exp = datetime.fromisoformat(token.expires_at)
        if datetime.now(timezone.utc) > exp:
            token.status = "EXPIRED"
            _audit("EXPIRED", token_id, token.agent_id, "auto-marked on validate")
            return ValidationResult(False, "EXPIRED", token, "Token past expiry", ts)

        return ValidationResult(True, "VALID", token, "", ts)

    def has_scope(self, token_id: str, permission: str) -> bool:
        """Check if a valid token grants a specific permission."""
        v = self.validate(token_id)
        return v.valid and permission in (v.token.scope if v.token else [])

    # ── Revoke ─────────────────────────────────────────────────────────────────

    def revoke(self, token_id: str, reason: str = "explicit revocation") -> bool:
        """Revoke a single token mid-task."""
        token = self._tokens.get(token_id)
        if not token or token.status != "ACTIVE":
            return False
        token.status = "REVOKED"
        _audit("REVOKED", token_id, token.agent_id, reason)
        return True

    def complete_task(self, task_id: str) -> int:
        """Revoke all active tokens for a completed task. Returns count revoked."""
        count = 0
        for token in self._tokens.values():
            if token.task_id == task_id and token.status == "ACTIVE":
                token.status = "REVOKED"
                _audit("REVOKED", token.token_id, token.agent_id,
                       f"task_complete:{task_id}")
                count += 1
        return count

    # ── Query ──────────────────────────────────────────────────────────────────

    def active_tokens(self, agent_id: str | None = None) -> list[SessionToken]:
        """List currently active tokens, optionally filtered by agent."""
        now    = datetime.now(timezone.utc)
        result = []
        for t in self._tokens.values():
            if t.status != "ACTIVE":
                continue
            if agent_id and t.agent_id != agent_id:
                continue
            if now > datetime.fromisoformat(t.expires_at):
                t.status = "EXPIRED"
                continue
            result.append(t)
        return result

    def summary(self) -> dict:
        counts: dict[str, int] = {"ACTIVE": 0, "EXPIRED": 0, "REVOKED": 0}
        for t in self._tokens.values():
            counts[t.status] = counts.get(t.status, 0) + 1
        return {"total": len(self._tokens), **counts}


# ══════════════════════════════════════════════════════════════════════════════
# Demo
# ══════════════════════════════════════════════════════════════════════════════

def _demo():
    vault = CredentialVault()

    print()
    print("  " + SEP)
    print("  " + _b("AXIOM ASI03 -- Task-Scoped Credentials"))
    print("  " + _gr("OWASP Agentic Top 10 2026 -- gap mitigation"))
    print("  " + SEP)

    agents = [
        ("worker",     "task_research_001", 3),
        ("safety",     "task_research_001", 5),
        ("researcher", "task_research_001", 2),
        ("critic",     "task_review_002",   3),
    ]

    # ── Step 1: Issue tokens ───────────────────────────────────────────────────
    print()
    print(_b("  [1/4] Token Issuance"))
    print("  " + DASH)
    tokens = []
    for agent_id, task_id, trust in agents:
        t = vault.issue(agent_id, task_id, trust, ttl_seconds=1800)
        tokens.append(t)
        scope_str = ", ".join(t.scope[:3]) + ("..." if len(t.scope) > 3 else "")
        print("  %-14s  trust=%d  %-50s" % (_c(agent_id), trust, _gr(scope_str)))
        print("         %s  exp=%s" % (
            _gr("token: " + t.token_id),
            t.expires_at[11:19] + "Z",
        ))

    # ── Step 2: Validate ───────────────────────────────────────────────────────
    print()
    print(_b("  [2/4] Token Validation"))
    print("  " + DASH)
    for t in tokens:
        v = vault.validate(t.token_id)
        col = _g if v.valid else _r
        perm_check = vault.has_scope(t.token_id, "execute_task")
        print("  %-14s  [%s]  execute_task=%s  scopes=%d" % (
            t.agent_id, col(_b(v.status)),
            _g("yes") if perm_check else _gr("no"),
            len(t.scope),
        ))

    # ── Step 3: Revoke + task completion ──────────────────────────────────────
    print()
    print(_b("  [3/4] Mid-Task Revocation + Task Completion"))
    print("  " + DASH)

    vault.revoke(tokens[3].token_id, "safety gate triggered on critic")
    v = vault.validate(tokens[3].token_id)
    print("  critic (force-revoked)  [%s]  %s" % (_r(_b(v.status)), v.reason))

    revoked = vault.complete_task("task_research_001")
    print("  task_research_001 complete -- %s tokens auto-revoked" % _y(str(revoked)))
    for t in tokens[:3]:
        v = vault.validate(t.token_id)
        print("  %-14s  [%s]" % (t.agent_id, _r(_b(v.status))))

    # ── Step 4: Summary ────────────────────────────────────────────────────────
    print()
    print(_b("  [4/4] Vault Summary"))
    print("  " + DASH)
    s = vault.summary()
    print("  Total   : %d" % s["total"])
    print("  Active  : " + _g(str(s["ACTIVE"])))
    print("  Revoked : " + _y(str(s["REVOKED"])))
    print("  Expired : " + _gr(str(s["EXPIRED"])))
    print("  Audit   : " + _gr(str(AUDIT_LOG)))
    print()
    print("  " + SEP)
    print()


def main():
    parser = argparse.ArgumentParser(description="AXIOM ASI03 task-scoped credentials")
    parser.add_argument("--demo", action="store_true", help="Run interactive demo")
    args = parser.parse_args()
    if args.demo:
        _demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
