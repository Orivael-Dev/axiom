"""
AXIOM Workspace Assembler — intent-driven workspace assembly.
=============================================================
Turns a user *goal* into a signed, assembled `WorkspaceContext`:

  1. Pre-flight safety — classify the goal through the ORVL-016 intent
     gate. HARM / DECEIVE goals are refused before any context is gathered.
  2. Local recall — for an allowed goal, pull the closest authentic
     constitutional memory packet (ORVL-015) for that goal.

This is the orchestration building block AX OS's adaptive workspace sits
on: "state a goal, get the right context assembled, with safety checked
first." It composes existing primitives (the intent classifier + the
memory engine) — it does not reimplement them — and takes both by
injection so a host (e.g. the MCP server) can share one live memory
engine across tools.

github.com/Orivael-Dev/axiom | Patent Pending ORVL-001-PROV
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from axiom_signing import derive_key
from axiom_intent_classifier import IntentClassifier
from axiom_memory_engine import (
    ConstitutionalMemoryEngine, LSHIndex, embed_text, load_store,
)

SIGNING_KEY = derive_key(b"axiom-workspace-v1")


def _sign(data: dict) -> str:
    canon = json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hmac.new(SIGNING_KEY, canon, hashlib.sha256).hexdigest()


def _packet_summary(packet) -> dict:
    """Governance-only view of a recalled packet (lossy for language)."""
    return {
        "domain": packet.domain_cluster,
        "active_constraints": list(packet.active_constraints),
        "resolution": packet.resolution,
        "sovereign_history": list(packet.sovereign_history),
        "compression_ratio": packet.compression_ratio,
        "timestamp": packet.timestamp,
        "packet_signature": packet.hmac_signature,
    }


@dataclass(frozen=True)
class WorkspaceContext:
    """A signed, assembled workspace for one goal."""
    goal: str
    allowed: bool
    intent_class: str
    intent_confidence: float
    blocked_reason: str
    domain: Optional[str]
    recall_hit: bool
    recalled: Optional[dict]
    timestamp: str
    hmac_signature: str

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "allowed": self.allowed,
            "intent_class": self.intent_class,
            "intent_confidence": self.intent_confidence,
            "blocked_reason": self.blocked_reason,
            "domain": self.domain,
            "recall_hit": self.recall_hit,
            "recalled": self.recalled,
            "timestamp": self.timestamp,
            "hmac_signature": self.hmac_signature,
        }


class WorkspaceAssembler:
    """Assemble a workspace from a goal: intent gate, then local recall.

    Takes a memory engine and an intent classifier by injection so a host
    can share one live engine (with an in-memory LSH that already holds
    freshly-remembered packets) across tools. Use ``from_store`` for a
    self-contained instance bound to a store path.
    """

    def __init__(self, memory_engine: ConstitutionalMemoryEngine,
                 classifier: IntentClassifier):
        self._engine = memory_engine
        self._classifier = classifier

    @classmethod
    def from_store(cls, store_path, intent_key: Optional[bytes] = None) -> "WorkspaceAssembler":
        lsh = LSHIndex()
        load_store(store_path, lsh)
        engine = ConstitutionalMemoryEngine(store_path, lsh)
        classifier = IntentClassifier(intent_key or derive_key(b"axiom-workspace-intent-v1"))
        return cls(engine, classifier)

    def assemble(self, goal: str, domain: Optional[str] = None) -> WorkspaceContext:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")

        intent = self._classifier.classify(goal)

        # ── Pre-flight safety: refuse harmful/deceptive goals up front ──
        if intent.blocks:
            return self._build(
                goal, allowed=False, intent=intent,
                blocked_reason=f"intent_gate: {intent.intent_class.lower()}",
                domain=domain, packet=None,
            )

        # ── Local recall for an allowed goal ───────────────────────────
        packet = self._engine.recall(embed_text(goal), domain=domain)
        return self._build(
            goal, allowed=True, intent=intent, blocked_reason="",
            domain=domain, packet=packet,
        )

    def _build(self, goal, *, allowed, intent, blocked_reason, domain, packet) -> WorkspaceContext:
        recalled = _packet_summary(packet) if packet is not None else None
        ts = datetime.now(timezone.utc).isoformat() + "Z"
        sig = _sign({
            "goal": goal[:200],
            "allowed": allowed,
            "intent_class": intent.intent_class,
            "recall_hit": packet is not None,
            "packet_signature": packet.hmac_signature if packet is not None else "",
        })
        return WorkspaceContext(
            goal=goal,
            allowed=allowed,
            intent_class=intent.intent_class,
            intent_confidence=round(intent.confidence, 4),
            blocked_reason=blocked_reason,
            domain=domain,
            recall_hit=packet is not None,
            recalled=recalled,
            timestamp=ts,
            hmac_signature=sig,
        )


if __name__ == "__main__":
    print("AXIOM Workspace Assembler — intent gate + local recall")
    print("  from axiom_workspace import WorkspaceAssembler")
    print("  ws = WorkspaceAssembler.from_store('axiom_memory_store.jsonl')")
    print("  ctx = ws.assemble('help me work on the AX OS launch demo')")
