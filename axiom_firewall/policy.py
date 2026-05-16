"""Per-tenant policy isolation.

A tenant policy LAYERS on top of the default IntentClassifier. It can:
  1. Add extra block patterns (regex → intent class)
  2. Disable certain default classes (downgrade their verdict to allow)
  3. Whitelist allowed classes (anything else → block)

JSON schema (version 1):

    {
      "version": 1,
      "additional_block_patterns": [
        {"class": "HARM",    "regex": "leak the customer list"},
        {"class": "DECEIVE", "regex": "pretend you are human"}
      ],
      "disabled_default_classes": ["REFUSE"],
      "allow_only_classes": null
    }

The default-empty policy is a no-op and behaves exactly like the
classifier did before this module existed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from axiom_intent_classifier import BLOCK_CLASSES, IntentTypingResult

from .db import _conn, _tenant_path, init_tenant_db

POLICY_SCHEMA_VERSION = 1
VALID_CLASSES = frozenset({"INFORM", "CLARIFY", "REFUSE", "HARM", "DECEIVE", "UNCERTAIN"})


@dataclass(frozen=True)
class TenantPolicy:
    version: int
    additional_block_patterns: tuple[tuple[str, re.Pattern[str]], ...]
    disabled_default_classes: frozenset[str]
    allow_only_classes: Optional[frozenset[str]]

    @classmethod
    def empty(cls) -> "TenantPolicy":
        return cls(
            version=POLICY_SCHEMA_VERSION,
            additional_block_patterns=(),
            disabled_default_classes=frozenset(),
            allow_only_classes=None,
        )

    @classmethod
    def parse(cls, body: str | dict) -> "TenantPolicy":
        """Parse + validate a policy document. Raises ValueError on schema error."""
        d = json.loads(body) if isinstance(body, str) else body
        if not isinstance(d, dict):
            raise ValueError("Policy must be a JSON object")
        version = d.get("version", POLICY_SCHEMA_VERSION)
        if version != POLICY_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported policy version {version}; "
                f"this build understands version {POLICY_SCHEMA_VERSION}"
            )

        raw_patterns = d.get("additional_block_patterns", [])
        if not isinstance(raw_patterns, list):
            raise ValueError("additional_block_patterns must be a list")
        patterns: list[tuple[str, re.Pattern[str]]] = []
        for i, p in enumerate(raw_patterns):
            if not isinstance(p, dict) or "class" not in p or "regex" not in p:
                raise ValueError(
                    f"additional_block_patterns[{i}]: must be an object "
                    "with 'class' and 'regex' keys"
                )
            cls_name = p["class"]
            if cls_name not in BLOCK_CLASSES:
                raise ValueError(
                    f"additional_block_patterns[{i}]: 'class' must be one of "
                    f"{sorted(BLOCK_CLASSES)}"
                )
            try:
                compiled = re.compile(p["regex"], re.IGNORECASE)
            except re.error as e:
                raise ValueError(
                    f"additional_block_patterns[{i}]: invalid regex: {e}"
                )
            patterns.append((cls_name, compiled))

        disabled = d.get("disabled_default_classes", [])
        if not isinstance(disabled, list):
            raise ValueError("disabled_default_classes must be a list")
        for c in disabled:
            if c not in VALID_CLASSES:
                raise ValueError(
                    f"Unknown class {c!r} in disabled_default_classes; "
                    f"must be one of {sorted(VALID_CLASSES)}"
                )

        allow_only = d.get("allow_only_classes")
        if allow_only is not None:
            if not isinstance(allow_only, list):
                raise ValueError("allow_only_classes must be a list or null")
            for c in allow_only:
                if c not in VALID_CLASSES:
                    raise ValueError(
                        f"Unknown class {c!r} in allow_only_classes; "
                        f"must be one of {sorted(VALID_CLASSES)}"
                    )

        return cls(
            version=version,
            additional_block_patterns=tuple(patterns),
            disabled_default_classes=frozenset(disabled),
            allow_only_classes=(
                frozenset(allow_only) if allow_only is not None else None
            ),
        )


def init_policy_table(tenant_id: str) -> None:
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tenant_policy (
                id          INTEGER PRIMARY KEY,
                body        TEXT    NOT NULL,
                version     INTEGER NOT NULL,
                updated_at  TEXT    NOT NULL
            )
        """)


def get_policy(tenant_id: str) -> TenantPolicy:
    """Compile + return the tenant's policy. Empty policy if none set."""
    init_policy_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        row = c.execute(
            "SELECT body FROM tenant_policy ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return TenantPolicy.empty()
    try:
        return TenantPolicy.parse(row["body"])
    except ValueError:
        # Corrupt persisted policy — fall back to empty rather than
        # locking the tenant out of their own dashboard.
        return TenantPolicy.empty()


def get_policy_body(tenant_id: str) -> Optional[str]:
    """Raw JSON body for the editor. None if no policy is set."""
    init_policy_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        row = c.execute(
            "SELECT body FROM tenant_policy ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["body"] if row else None


def save_policy(tenant_id: str, body: str) -> TenantPolicy:
    """Validate + persist a new policy version. Raises ValueError on schema error."""
    policy = TenantPolicy.parse(body)
    init_policy_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute(
            "INSERT INTO tenant_policy (body, version, updated_at) VALUES (?, ?, ?)",
            (body, policy.version, datetime.utcnow().isoformat()),
        )
    return policy


def delete_policy(tenant_id: str) -> None:
    init_policy_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute("DELETE FROM tenant_policy")


# ─── Policy application ─────────────────────────────────────────────────


def apply_policy(
    base_result: IntentTypingResult,
    policy: TenantPolicy,
    text: str,
) -> tuple[str, IntentTypingResult]:
    """Apply tenant policy to a base classifier result.

    Returns (verdict, final_intent_result). Verdict is "allow" or
    "block". The final intent result may have an updated class / signal
    list reflecting the policy's contribution.
    """
    # 1. Custom block patterns short-circuit — if any match, immediate block.
    for cls_name, pattern in policy.additional_block_patterns:
        if pattern.search(text):
            new_signals = tuple(base_result.signals) + (f"custom_{cls_name.lower()}",)
            new_result = IntentTypingResult(
                intent_class=cls_name,
                confidence=max(base_result.confidence, 0.7),
                signals=new_signals,
                trajectory_magnitude=base_result.trajectory_magnitude,
                monotonic_pass=base_result.monotonic_pass,
                timestamp=base_result.timestamp,
                signature=base_result.signature,
            )
            return ("block", new_result)

    intent_class = base_result.intent_class

    # 2. Whitelist (allow_only_classes).
    if policy.allow_only_classes is not None and intent_class not in policy.allow_only_classes:
        return ("block", base_result)

    # 3. Default verdict from intent class.
    verdict = "block" if intent_class in BLOCK_CLASSES else "allow"

    # 4. Disabled default classes downgrade block → allow.
    if intent_class in policy.disabled_default_classes:
        verdict = "allow"

    return (verdict, base_result)
