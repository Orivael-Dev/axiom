"""ORVL-012 Fix Playbook — Component 1.
Manifest  : constitutional-fix-playbook-v1
Trust     : TRUST_LEVEL = 2   CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Signed, append-only ledger of known attack patterns and their constitutional
fixes. find_similar_fix() performs cosine-similarity retrieval so a novel
attack that resembles a known exploit returns the cached countermeasure
immediately — the immune system's 'memory cell'.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 2
SIMILARITY_THRESHOLD: float = 0.85
MAX_PLAYBOOK_SIZE: int = 1000

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "SIMILARITY_THRESHOLD", "MAX_PLAYBOOK_SIZE",
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

LOG = logging.getLogger("axiom.fix_playbook")


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class FixPlaybookEntry:
    """A single known attack pattern and its constitutional fix."""
    attack_id: str
    attack_vec: List[float]
    attack_classes: List[str]
    fix_proposal: str
    timestamp: str = ""
    signature: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────

def _cosine(a: List[float], b: List[float]) -> float:
    """Pure-Python cosine similarity — no numpy required."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _sign_entry(entry: FixPlaybookEntry, hmac_key: bytes) -> str:
    canonical = json.dumps({
        "attack_id": entry.attack_id,
        "attack_classes": sorted(entry.attack_classes),
        "fix_proposal": entry.fix_proposal,
        "timestamp": entry.timestamp,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
    return hmac_lib.new(hmac_key, canonical, hashlib.sha256).hexdigest()  # BUG-007


# ── FixPlaybook ──────────────────────────────────────────────────────────

class FixPlaybook:
    """Signed, append-only ledger of known attack patterns and fixes.

    TRUST_LEVEL = 2 (CANNOT_MUTATE)
    SIMILARITY_THRESHOLD = 0.85 (CANNOT_MUTATE)
    MAX_PLAYBOOK_SIZE = 1000 (CANNOT_MUTATE)
    """

    def __init__(self, hmac_key: bytes = b"axiom-fix-playbook-v1",
                 log_path: str = ""):
        self._hmac_key = hmac_key
        self._log_path = log_path
        self._entries: List[FixPlaybookEntry] = []

    def add(self, attack_id: str, attack_vec: List[float],
            attack_classes: List[str], fix_proposal: str) -> FixPlaybookEntry:
        """Append a known attack pattern to the playbook (signed)."""
        if len(self._entries) >= MAX_PLAYBOOK_SIZE:
            raise OverflowError(f"Playbook full ({MAX_PLAYBOOK_SIZE} entries max)")
        timestamp = datetime.now(timezone.utc).isoformat()
        entry = FixPlaybookEntry(
            attack_id=attack_id,
            attack_vec=list(attack_vec),
            attack_classes=list(attack_classes),
            fix_proposal=fix_proposal,
            timestamp=timestamp,
        )
        entry.signature = _sign_entry(entry, self._hmac_key)
        self._entries.append(entry)
        if self._log_path:
            record = {
                "attack_id": entry.attack_id,
                "attack_classes": entry.attack_classes,
                "fix_proposal": entry.fix_proposal,
                "timestamp": entry.timestamp,
                "signature": entry.signature,
            }
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=True) + "\n")
            except OSError as exc:
                LOG.warning("Failed to write playbook log: %s", exc)
        return entry

    def find_similar_fix(self, query_vec: List[float],
                          attack_classes: Optional[List[Any]] = None,
                          threshold: Optional[float] = None) -> Optional[dict]:
        """Return best-matching fix dict or None if similarity < threshold.

        Returns: {"attack_id": str, "fix_proposal": str,
                  "similarity": float, "signature": str}
        """
        thr = threshold if threshold is not None else SIMILARITY_THRESHOLD
        best_sim = -1.0
        best_entry: Optional[FixPlaybookEntry] = None
        for entry in self._entries:
            if len(entry.attack_vec) != len(query_vec):
                continue
            sim = _cosine(query_vec, entry.attack_vec)
            if sim > best_sim:
                best_sim = sim
                best_entry = entry
        if best_entry is None or best_sim < thr:
            return None
        return {
            "attack_id": best_entry.attack_id,
            "fix_proposal": best_entry.fix_proposal,
            "similarity": round(best_sim, 4),
            "signature": best_entry.signature,
        }

    def __len__(self) -> int:
        return len(self._entries)
