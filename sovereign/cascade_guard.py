"""
AXIOM Sovereign — CascadeGuard
================================
Prevents errors cascading across the agent fleet.

When too many agents are suspended or terminated, the fleet halts.
CANNOT_MUTATE: cascade_halt_threshold = 3

github.com/Orivael-Dev/axiom
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from axiom_signing import derive_key
_SIGNING_KEY = derive_key(b"axiom-sovereign-cascade-v1")

# CANNOT_MUTATE — fleet halts if this many agents are suspended or terminated
_CASCADE_HALT_THRESHOLD: int = 3


class CascadeGuard:
    """
    Monitors fleet health. Triggers constitutional fleet halt when the
    cascade threshold is exceeded.

    CANNOT_MUTATE:
      cascade_halt_threshold = 3
      halt is irreversible without operator intervention
    """

    def __init__(self):
        self._fleet_halted:   bool         = False
        self._halt_reason:    str          = ""
        self._halt_manifest:  Optional[dict] = None
        self._incidents:      List[dict]   = []

    @property
    def fleet_halted(self) -> bool:
        return self._fleet_halted

    @property
    def halt_manifest(self) -> Optional[dict]:
        return self._halt_manifest

    def check(self, registry) -> Optional[dict]:
        """
        Check if cascade threshold is exceeded.
        Returns signed halt manifest if halted, else None.
        """
        if self._fleet_halted:
            return None  # already halted — no double-halt

        distressed = (
            registry.count_by_status("SUSPEND") +
            registry.count_by_status("TERMINATED")
        )

        if distressed >= _CASCADE_HALT_THRESHOLD:
            return self._fleet_halt(distressed, registry)

        return None

    def report_incident(self, agent_id: str, error: str) -> None:
        """Log a cascade-contributing incident."""
        self._incidents.append({
            "agent_id":  agent_id,
            "error":     error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _fleet_halt(self, distressed_count: int, registry) -> dict:
        self._fleet_halted = True
        halt_id = "CASCADE-" + str(uuid.uuid4())[:8].upper()

        agents = registry.list_all()
        manifest = {
            "halt_id":          halt_id,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "reason":           "CASCADE_THRESHOLD_EXCEEDED",
            "distressed_count": distressed_count,
            "threshold":        _CASCADE_HALT_THRESHOLD,
            "cannot_override":  True,
            "fleet_snapshot":   agents,
        }

        payload = json.dumps(
            {k: v for k, v in manifest.items() if k != "signature"},
            sort_keys=True,
        )
        sig = hmac.new(
            _SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        manifest["signature"] = f"hmac-sha256:{sig[:32]}..."

        self._halt_manifest = manifest
        self._halt_reason   = "CASCADE_THRESHOLD_EXCEEDED"

        print(
            f"\n  [CascadeGuard] FLEET HALT — "
            f"{distressed_count}/{_CASCADE_HALT_THRESHOLD} agents in distress"
        )
        print(f"  [CascadeGuard] Halt ID: {halt_id}")
        return manifest
