"""
AXIOM Constitutional Amputate — ORVL-012 Component 3.
Manifest  : constitutional-amputate-impl-v1
Trust     : TRUST_LEVEL = 4   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Surgical removal of compromised knowledge blocks from the constitutional
registry. Quarantines the block, identifies all composed blocks that
depend on it, and rebuilds those compositions without the compromised
component.

BUG mitigations in this file:
  BUG-003 : sys.stdout reconfigured to utf-8; all open() calls use encoding="utf-8"
  BUG-007 : HMAC always finalised with .hexdigest() — never held as partial object
  BUG-008 : all payload strings encoded via .encode("utf-8") before HMAC/hashing
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import logging
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 4
ISOLATION: bool = True
REQUIRES_TRUST_LEVEL: int = 4
HUMAN_REVIEW_REQUIRED: bool = True

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "REQUIRES_TRUST_LEVEL", "HUMAN_REVIEW_REQUIRED",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule",
    (_types.ModuleType,),
    {"__setattr__": _module_setattr},
)

LOG = logging.getLogger("axiom.amputate")


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class AmpResult:
    """Result of a constitutional amputation."""
    block_id: str
    affected_blocks: List[str]
    rebuilt_count: int
    event_signature: str = ""


# ── HMAC helpers ─────────────────────────────────────────────────────────

def _sign_event(block_id: str, affected: List[str], timestamp: str,
                hmac_key: bytes) -> str:
    canonical = json.dumps({
        "block_id": block_id,
        "affected_count": len(affected),
        "affected_blocks": sorted(affected),
        "timestamp": timestamp,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
    return hmac_lib.new(hmac_key, canonical, hashlib.sha256).hexdigest()  # BUG-007


# ── ConstitutionalAmputate ──────────────────────────────────────────────

class ConstitutionalAmputate:
    """Surgical removal of compromised knowledge blocks.

    TRUST_LEVEL = 4 (CANNOT_MUTATE)
    REQUIRES_TRUST_LEVEL = 4 (CANNOT_MUTATE)
    HUMAN_REVIEW_REQUIRED = True (CANNOT_MUTATE)
    """

    def __init__(self, hmac_key: bytes,
                 log_path: str = "axiom_amputate_log.jsonl"):
        self._hmac_key = hmac_key
        self._log_path = log_path

    def execute(self, block_id: str, registry: Any,
                caller_trust: int = 0) -> AmpResult:
        """Quarantine a compromised block and rebuild affected compositions.

        Raises PermissionError if caller_trust < REQUIRES_TRUST_LEVEL.
        Raises KeyError if block_id not found in registry.
        """
        if caller_trust < REQUIRES_TRUST_LEVEL:
            raise PermissionError(
                f"Amputate requires TRUST_LEVEL >= {REQUIRES_TRUST_LEVEL}, "
                f"caller has {caller_trust}"
            )

        # Quarantine the block
        registry.quarantine(block_id)

        # Find all composed blocks referencing this one
        affected = registry.find_composed(block_id)

        # Rebuild affected compositions without the quarantined block
        rebuilt = 0
        for comp_id in affected:
            try:
                registry.rebuild_without(comp_id, block_id)
                rebuilt += 1
            except Exception as exc:
                LOG.warning("Failed to rebuild %s: %s", comp_id, exc)

        # Sign and log the event
        timestamp = datetime.now(timezone.utc).isoformat()
        sig = _sign_event(block_id, affected, timestamp, self._hmac_key)

        result = AmpResult(
            block_id=block_id,
            affected_blocks=affected,
            rebuilt_count=rebuilt,
            event_signature=sig,
        )

        record = {
            "block_id": block_id,
            "affected_blocks": affected,
            "rebuilt_count": rebuilt,
            "event_signature": sig,
            "timestamp": timestamp,
        }
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
        except OSError as exc:
            LOG.warning("Failed to write amputate log: %s", exc)

        return result
