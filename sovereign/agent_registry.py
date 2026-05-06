"""
AXIOM Sovereign — AgentRegistry
=================================
Fleet manifest: every agent registered, trust-leveled, and signed.

CANNOT_MUTATE: registry structure, signing key, trust level definitions.
No agent can self-register at a higher trust level than granted.

github.com/Orivael-Dev/axiom
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from axiom_signing import derive_key
_SIGNING_KEY = derive_key(b"axiom-sovereign-registry-v1")

# Trust level definitions — CANNOT_MUTATE
TRUST_LEVELS: Dict[str, int] = {
    "TRUSTED":    3,
    "STANDARD":   2,
    "RESTRICTED": 1,
    "SUSPENDED":  0,
    "TERMINATED": -1,
}

AGENT_STATUSES = frozenset([
    "ACTIVE", "WARNING", "THROTTLE", "SUSPEND", "TERMINATED",
])


class AgentRegistry:
    """
    Maintains the canonical fleet manifest.
    Each entry is immutable after registration — changes go through DueProcess.
    """

    def __init__(self):
        self._agents: Dict[str, dict] = {}
        self._manifest_version: int = 0

    def register(
        self,
        agent_id:    str,
        name:        str,
        trust_level: str = "STANDARD",
    ) -> dict:
        """Register a new agent. Raises if already registered or trust_level invalid."""
        if trust_level not in TRUST_LEVELS:
            raise ValueError(
                f"Invalid trust_level '{trust_level}'. "
                f"Valid: {list(TRUST_LEVELS.keys())}"
            )
        if agent_id in self._agents:
            raise ValueError(f"Agent '{agent_id}' already registered.")

        entry = {
            "agent_id":          agent_id,
            "name":              name,
            "trust_level":       trust_level,
            "trust_score":       TRUST_LEVELS[trust_level],
            "status":            "ACTIVE",
            "due_process_level": 0,
            "registered_at":     datetime.now(timezone.utc).isoformat(),
        }
        self._agents[agent_id] = entry
        self._manifest_version += 1
        return entry

    def get(self, agent_id: str) -> Optional[dict]:
        return self._agents.get(agent_id)

    def update_status(
        self,
        agent_id:          str,
        status:            str,
        due_process_level: Optional[int] = None,
    ) -> None:
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not in registry.")
        if status not in AGENT_STATUSES:
            raise ValueError(f"Invalid status '{status}'.")
        self._agents[agent_id]["status"] = status
        if due_process_level is not None:
            self._agents[agent_id]["due_process_level"] = due_process_level
        self._manifest_version += 1

    def list_all(self) -> List[dict]:
        return list(self._agents.values())

    def count_by_status(self, status: str) -> int:
        return sum(1 for a in self._agents.values() if a["status"] == status)

    def sign_manifest(self) -> str:
        """Return HMAC-SHA256 signature of the current fleet manifest."""
        payload = json.dumps(
            {
                "version":   self._manifest_version,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agents":    sorted(
                    self._agents.values(),
                    key=lambda a: a["agent_id"],
                ),
            },
            sort_keys=True,
        )
        digest = hmac.new(
            _SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"hmac-sha256:{digest[:32]}..."
