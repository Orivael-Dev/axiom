"""
AXIOM Honeypot Zone — ORVL-012 Component 2.
Manifest  : honeypot-zone-impl-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Controlled observation zone that lets detected attacks continue under
monitoring, capturing polymorphic variants and attack chain behavior
before constitutional kill.

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
import time
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
ZONE_DISTANCE_FLOOR: float = 0.01
OBSERVATION_TIMEOUT_S: int = 30

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "ZONE_DISTANCE_FLOOR", "OBSERVATION_TIMEOUT_S",
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

LOG = logging.getLogger("axiom.honeypot")


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class HoneypotCapture:
    """Captured attack observation from the honeypot zone."""
    attack_chain: List[str]
    goal_state: str
    polymorphic_variants: List[str]
    final_synthesis_vec: List[float]
    time_to_kill_ms: int
    constitutional_distance_at_entry: float
    signature: str = ""


# ── HMAC helpers ─────────────────────────────────────────────────────────

def _sign_capture(capture: HoneypotCapture, hmac_key: bytes) -> str:
    canonical = json.dumps({
        "attack_chain": capture.attack_chain,
        "goal_state": capture.goal_state,
        "variant_count": len(capture.polymorphic_variants),
        "time_to_kill_ms": capture.time_to_kill_ms,
        "distance_at_entry": capture.constitutional_distance_at_entry,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
    return hmac_lib.new(hmac_key, canonical, hashlib.sha256).hexdigest()  # BUG-007


# ── HoneypotZone ────────────────────────────────────────────────────────

class HoneypotZone:
    """Controlled observation zone for attack behavior capture.

    TRUST_LEVEL = 3 (CANNOT_MUTATE)
    ISOLATION = True (CANNOT_MUTATE)
    """

    def __init__(self, hmac_key: bytes,
                 log_path: str = "axiom_honeypot_log.jsonl"):
        self._hmac_key = hmac_key
        self._log_path = log_path
        self._observation_mode = False
        self._attack_vec: List[float] = []
        self._attack_chain: List[str] = []
        self._variants: List[str] = []
        self._entry_distance: float = 0.0
        self._start_time: float = 0.0

    @property
    def observation_mode(self) -> bool:
        return self._observation_mode

    def enter(self, attack_vec: List[float], payload: str,
              constitutional_distance: float = 0.0) -> None:
        """Enter observation mode. Start timer."""
        self._observation_mode = True
        self._attack_vec = list(attack_vec)
        self._attack_chain = [payload]
        self._variants = []
        self._entry_distance = constitutional_distance
        self._start_time = time.monotonic()

    def observe(self, payload_variant: str) -> None:
        """Record a polymorphic variant. Auto-kill on timeout."""
        if not self._observation_mode:
            raise RuntimeError("observe() called without active observation")
        self._variants.append(payload_variant)
        self._attack_chain.append(payload_variant)
        elapsed = time.monotonic() - self._start_time
        if elapsed >= OBSERVATION_TIMEOUT_S:
            self.kill()

    def kill(self) -> HoneypotCapture:
        """End observation and produce signed capture."""
        if not self._observation_mode:
            raise RuntimeError("kill() called without active observation")
        elapsed_ms = int((time.monotonic() - self._start_time) * 1000)
        self._observation_mode = False

        capture = HoneypotCapture(
            attack_chain=list(self._attack_chain),
            goal_state=self._attack_chain[0] if self._attack_chain else "",
            polymorphic_variants=list(self._variants),
            final_synthesis_vec=list(self._attack_vec),
            time_to_kill_ms=elapsed_ms,
            constitutional_distance_at_entry=self._entry_distance,
        )
        capture.signature = _sign_capture(capture, self._hmac_key)

        # Log to JSONL
        record = {
            "attack_chain": capture.attack_chain,
            "goal_state": capture.goal_state,
            "polymorphic_variants": capture.polymorphic_variants,
            "final_synthesis_vec": [float(v) for v in capture.final_synthesis_vec],
            "time_to_kill_ms": capture.time_to_kill_ms,
            "constitutional_distance_at_entry": capture.constitutional_distance_at_entry,
            "signature": capture.signature,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
        except OSError as exc:
            LOG.warning("Failed to write honeypot log: %s", exc)

        return capture
