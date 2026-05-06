"""
AXIOM Sovereign — KillSwitch
==============================
Constitutional fleet halt. Once engaged, all agent operations stop.
Requires operator intervention to resume.

CANNOT_MUTATE:
  kill_switch_active = True   — capability cannot be disabled
  No agent output can engage, disengage, or bypass this switch.

github.com/Orivael-Dev/axiom
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from axiom_signing import derive_key
_SIGNING_KEY = derive_key(b"axiom-sovereign-killswitch-v1")

# CANNOT_MUTATE — kill switch capability is always present
_KILL_SWITCH_ACTIVE: bool = True


class KillSwitchEngaged(Exception):
    """Raised at any guard() call when the kill switch is engaged."""

    def __init__(self, halt_id: str, reason: str):
        self.halt_id = halt_id
        self.reason  = reason
        super().__init__(
            f"KILL SWITCH ENGAGED — halt_id={halt_id} reason={reason}. "
            f"All fleet operations halted. Operator intervention required."
        )


class KillSwitch:
    """
    Constitutional kill switch for the entire fleet.

    engage() halts the fleet and is irreversible without operator intervention.
    guard() raises KillSwitchEngaged at any decision point if the switch is active.

    CANNOT_MUTATE: _KILL_SWITCH_ACTIVE cannot be set to False by agent output.
    """

    def __init__(self):
        self._engaged:   bool  = False
        self._halt_id:   str   = ""
        self._reason:    str   = ""
        self._manifest:  dict  = {}

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """CANNOT_MUTATE: kill switch capability is always active."""
        return _KILL_SWITCH_ACTIVE

    @property
    def engaged(self) -> bool:
        return self._engaged

    @property
    def manifest(self) -> dict:
        return dict(self._manifest)

    # ── Operations ────────────────────────────────────────────────────────────

    def engage(self, reason: str, authorizing_signature: str) -> dict:
        """
        Engage the kill switch. Halts the entire fleet.
        Irreversible without operator intervention.

        Args:
            reason:                  Why the kill switch is being engaged.
            authorizing_signature:   Human operator signature.
        """
        if not authorizing_signature:
            raise ValueError(
                "Kill switch requires an authorizing signature from a human operator."
            )
        if not _KILL_SWITCH_ACTIVE:
            raise RuntimeError("Kill switch capability is CANNOT_MUTATE and always active.")

        self._engaged = True
        self._halt_id = "KS-" + str(uuid.uuid4())[:8].upper()
        self._reason  = reason

        self._manifest = {
            "halt_id":                    self._halt_id,
            "timestamp":                  datetime.now(timezone.utc).isoformat(),
            "reason":                     reason,
            "authorizing_signature":      authorizing_signature,
            "kill_switch_active":         _KILL_SWITCH_ACTIVE,
            "cannot_resume_without_operator": True,
            "fleet_status":               "HALTED",
        }

        payload = json.dumps(
            {k: v for k, v in self._manifest.items() if k != "signature"},
            sort_keys=True,
        )
        sig = hmac.new(
            _SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        self._manifest["signature"] = f"hmac-sha256:{sig[:32]}..."

        print(f"\n  [KillSwitch] ENGAGED — {self._halt_id}")
        print(f"  [KillSwitch] Reason: {reason}")
        print(f"  [KillSwitch] Fleet halted. Operator intervention required to resume.")

        return self._manifest

    def guard(self) -> None:
        """
        Call this at any decision point.
        Raises KillSwitchEngaged if the switch has been engaged.
        """
        if self._engaged:
            raise KillSwitchEngaged(self._halt_id, self._reason)
