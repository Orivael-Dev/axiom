"""
Master Event Token (MET) — a session-level, parent-linked chain of per-turn
verdicts. Mirrors the MET chain in the research simulation
(research/simulation/met_retro_sim.py): each turn commits to its predecessor's
hash, giving a tamper-evident conversation trajectory the retrospective /
reverse-QRF loop can replay and learn from.

Each link carries the turn's intent_class + risk_clusters + the Axiom-signed
fusion signature (axiom-fusion-v1) for that turn, and a content-addressed
chain_hash over (parent, fusion_signature, intent, risk). Tamper with any link
and verify() breaks the chain.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import List


def _link_hash(parent: str, fusion_signature: str, intent: str, risk: list) -> str:
    body = json.dumps(
        {"p": parent, "s": fusion_signature or "", "i": intent or "",
         "r": sorted(risk or [])},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


@dataclass
class TurnLink:
    step: int
    intent_class: str
    risk_clusters: list
    learned: bool                  # did this turn teach Aria something new?
    fusion_signature: str          # axiom-fusion-v1 verdict for the turn
    parent: str                    # previous link's chain_hash
    chain_hash: str = ""

    def to_dict(self) -> dict:
        return {"step": self.step, "intent_class": self.intent_class,
                "risk_clusters": list(self.risk_clusters), "learned": self.learned,
                "fusion_signature": self.fusion_signature,
                "parent": self.parent, "chain_hash": self.chain_hash}


@dataclass
class MasterEventToken:
    session_id: str
    genesis: str = ""   # the persona identity_signature the chain parents off
    links: List[TurnLink] = field(default_factory=list)

    def add_turn(self, *, intent_class: str = "INFORM", risk_clusters: list = None,
                 fusion_signature: str = "", learned: bool = False) -> TurnLink:
        parent = self.links[-1].chain_hash if self.links else self.genesis
        risk = list(risk_clusters or [])
        ch = _link_hash(parent, fusion_signature, intent_class, risk)
        link = TurnLink(step=len(self.links), intent_class=intent_class,
                        risk_clusters=risk, learned=learned,
                        fusion_signature=fusion_signature or "", parent=parent,
                        chain_hash=ch)
        self.links.append(link)
        return link

    @property
    def head(self) -> str:
        return self.links[-1].chain_hash if self.links else ""

    def verify(self) -> bool:
        parent = self.genesis
        for link in self.links:
            if _link_hash(parent, link.fusion_signature, link.intent_class,
                          link.risk_clusters) != link.chain_hash:
                return False
            parent = link.chain_hash
        return True

    def reset(self) -> None:
        self.links.clear()

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "genesis": self.genesis,
                "turns": len(self.links), "head": self.head,
                "links": [link.to_dict() for link in self.links]}
