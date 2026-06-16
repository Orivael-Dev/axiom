"""Per-agent data access policy engine.

Answers: is agent_id allowed to perform action on data_class?

Example policy record:
  {
    "agent_id": "callguard",
    "allowed_data_classes": ["INFORM", "CLARIFY"],
    "blocked_data_classes": ["PAN", "CVV", "SSN", "GENETIC_DATA"],
    "allowed_actions": ["read", "summarise"],
    "blocked_actions": ["store", "forward", "log"]
  }

Data classes map to the category codes from axiom_redact.py
(e.g. "HIPAA-7" covers SSN, "PCI" covers card data, "GDPR-9" covers
special-category data).  You may use individual codes ("SSN") or
category prefixes ("HIPAA", "PCI", "GDPR-9", "CRED").

Action vocabulary is free-form strings — the engine normalises to
lowercase before comparison.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .db import _conn, _tenant_path, init_tenant_db


# ── PolicyVerdict ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PolicyVerdict:
    allowed: bool
    agent_id: str
    action: str
    data_class: str
    reason: str


# ── AgentAccessRule ───────────────────────────────────────────────────────

@dataclass
class AgentAccessRule:
    rule_id: str
    agent_id: str
    allowed_data_classes: list[str]       # empty = allow all
    blocked_data_classes: list[str]       # checked first; empty = block none
    allowed_actions: list[str]            # empty = allow all
    blocked_actions: list[str]            # checked first; empty = block none
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "agent_id": self.agent_id,
            "allowed_data_classes": self.allowed_data_classes,
            "blocked_data_classes": self.blocked_data_classes,
            "allowed_actions": self.allowed_actions,
            "blocked_actions": self.blocked_actions,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentAccessRule":
        return cls(
            rule_id=d["rule_id"],
            agent_id=d["agent_id"],
            allowed_data_classes=d.get("allowed_data_classes", []),
            blocked_data_classes=d.get("blocked_data_classes", []),
            allowed_actions=d.get("allowed_actions", []),
            blocked_actions=d.get("blocked_actions", []),
            created_at=d.get("created_at", datetime.utcnow().isoformat()),
        )


# ── DB helpers ────────────────────────────────────────────────────────────

def _init_agent_policy_table(tenant_id: str) -> None:
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_access_rules (
                rule_id    TEXT PRIMARY KEY,
                agent_id   TEXT NOT NULL,
                body       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_aar_agent "
            "ON agent_access_rules(agent_id)"
        )


def save_agent_rule(tenant_id: str, rule: AgentAccessRule) -> AgentAccessRule:
    """Persist or replace a rule for the given agent."""
    _init_agent_policy_table(tenant_id)
    rule.rule_id = rule.rule_id or str(uuid.uuid4())
    rule.created_at = datetime.utcnow().isoformat()
    with _conn(_tenant_path(tenant_id)) as c:
        # Upsert by agent_id — one active rule per agent
        c.execute(
            "DELETE FROM agent_access_rules WHERE agent_id = ?", (rule.agent_id,)
        )
        c.execute(
            "INSERT INTO agent_access_rules (rule_id, agent_id, body, created_at) "
            "VALUES (?, ?, ?, ?)",
            (rule.rule_id, rule.agent_id, json.dumps(rule.to_dict()), rule.created_at),
        )
    return rule


def get_agent_rule(tenant_id: str, agent_id: str) -> Optional[AgentAccessRule]:
    _init_agent_policy_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        row = c.execute(
            "SELECT body FROM agent_access_rules WHERE agent_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
    if not row:
        return None
    return AgentAccessRule.from_dict(json.loads(row["body"]))


def list_agent_rules(tenant_id: str) -> list[AgentAccessRule]:
    _init_agent_policy_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        rows = c.execute(
            "SELECT body FROM agent_access_rules ORDER BY created_at DESC"
        ).fetchall()
    return [AgentAccessRule.from_dict(json.loads(r["body"])) for r in rows]


def delete_agent_rule(tenant_id: str, agent_id: str) -> bool:
    _init_agent_policy_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        cur = c.execute(
            "DELETE FROM agent_access_rules WHERE agent_id = ?", (agent_id,)
        )
        return cur.rowcount > 0


# ── Policy engine ─────────────────────────────────────────────────────────

def _matches(value: str, patterns: list[str]) -> bool:
    """True if value matches any pattern (exact or prefix match)."""
    v = value.upper()
    for p in patterns:
        p = p.upper()
        if v == p or v.startswith(p + "-") or v.startswith(p):
            return True
    return False


def is_allowed(
    tenant_id: str,
    agent_id: str,
    action: str,
    data_class: str,
) -> PolicyVerdict:
    """Decide whether agent_id may perform action on data_class.

    If no rule exists for this agent, defaults to DENY for PCI/GDPR-9
    special-category data and ALLOW otherwise — safe by default for
    sensitive classes.
    """
    action = action.lower().strip()
    rule = get_agent_rule(tenant_id, agent_id)

    # No rule — apply safe defaults
    if rule is None:
        sensitive = _matches(data_class, ["PCI", "GDPR-9", "GENETIC_DATA",
                                           "BIOMETRIC", "CRIMINAL_RECORD",
                                           "SEXUAL_ORIENTATION"])
        if sensitive:
            return PolicyVerdict(
                allowed=False,
                agent_id=agent_id,
                action=action,
                data_class=data_class,
                reason="no_rule_sensitive_default_deny",
            )
        return PolicyVerdict(
            allowed=True,
            agent_id=agent_id,
            action=action,
            data_class=data_class,
            reason="no_rule_default_allow",
        )

    # Explicit block check — data class
    if rule.blocked_data_classes and _matches(data_class, rule.blocked_data_classes):
        return PolicyVerdict(
            allowed=False,
            agent_id=agent_id,
            action=action,
            data_class=data_class,
            reason="blocked_data_class",
        )

    # Explicit block check — action
    if rule.blocked_actions and action in [a.lower() for a in rule.blocked_actions]:
        return PolicyVerdict(
            allowed=False,
            agent_id=agent_id,
            action=action,
            data_class=data_class,
            reason="blocked_action",
        )

    # Allow list check — data class
    if rule.allowed_data_classes and not _matches(data_class, rule.allowed_data_classes):
        return PolicyVerdict(
            allowed=False,
            agent_id=agent_id,
            action=action,
            data_class=data_class,
            reason="data_class_not_in_allowlist",
        )

    # Allow list check — action
    if rule.allowed_actions and action not in [a.lower() for a in rule.allowed_actions]:
        return PolicyVerdict(
            allowed=False,
            agent_id=agent_id,
            action=action,
            data_class=data_class,
            reason="action_not_in_allowlist",
        )

    return PolicyVerdict(
        allowed=True,
        agent_id=agent_id,
        action=action,
        data_class=data_class,
        reason="rule_allow",
    )
