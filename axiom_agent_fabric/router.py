"""AgentRouter — scores dormant MiniSRDAgents and wakes the top-k.

Scoring formula (per agent):
  keyword_score = (matched_wake_conditions) / len(wake_conditions)
  intent_boost  = 0.30 if fused.intent_vector[0] keyword overlaps agent role
  total_score   = keyword_score + intent_boost

Agents with compression_state == "archived" are never scored.
Only agents with total_score > min_score are eligible for waking.
The top-k by total_score are returned; their compression_state is
transitioned to "active" on the returned copies.

Each routing decision is recorded as an HMAC-signed dict so the full
activation history can be audited alongside the MET chain.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from axiom_signing import derive_key
from axiom_agent_fabric.capsule import MiniSRDAgent, VRAMAgentToken

ROUTER_KEY_NS = b"axiom-agent-router-v1"

_ROUTER_KEY: Optional[bytes] = None


def _router_key() -> bytes:
    global _ROUTER_KEY
    if _ROUTER_KEY is None:
        _ROUTER_KEY = derive_key(ROUTER_KEY_NS)
    return _ROUTER_KEY


def _canonical(d: dict) -> bytes:
    payload = {k: v for k, v in d.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _hmac_sign(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


# ─── WakeScore ────────────────────────────────────────────────────────────────


@dataclass
class WakeScore:
    """Scoring result for one candidate agent.

    Attributes
    ----------
    agent          The MiniSRDAgent that was scored.
    token          Its hot VRAMAgentToken (pre-computed by AgentRouter).
    keyword_hits   Number of wake_conditions matched in the event text.
    keyword_score  keyword_hits / len(wake_conditions).
    intent_boost   +0.30 when the primary fused intent overlaps agent role.
    total_score    keyword_score + intent_boost.
    """
    agent:         MiniSRDAgent
    token:         VRAMAgentToken
    keyword_hits:  int
    keyword_score: float
    intent_boost:  float
    total_score:   float

    def action(self, min_score: float) -> str:
        return "WAKE" if self.total_score >= min_score else "sleep"


# ─── AgentRouter ──────────────────────────────────────────────────────────────


class AgentRouter:
    """Scores dormant SRD capsules and returns the top-k to wake.

    Parameters
    ----------
    agents     Full registry of MiniSRDAgents (all states).
    k          Maximum number of agents to wake per event.
    min_score  Minimum total_score threshold for waking (default 0.35 to
               allow low-keyword-count agents to wake via intent_boost).
    """

    def __init__(
        self,
        agents: list[MiniSRDAgent],
        k: int = 4,
        min_score: float = 0.35,
    ) -> None:
        self._agents    = agents
        self._k         = k
        self._min_score = min_score
        # Pre-compute hot VRAMAgentTokens for all non-archived agents
        self._tokens: dict[str, VRAMAgentToken] = {
            a.agent_id: a.to_vram_token()
            for a in agents
            if a.compression_state != "archived"
        }

    # ── Scoring ───────────────────────────────────────────────────────

    def score(
        self,
        event_text: str,
        intent_vector: Optional[list[str]] = None,
    ) -> list[WakeScore]:
        """Score every non-archived agent against the event text.

        Parameters
        ----------
        event_text      Raw text of the user event (or MET phrase).
        intent_vector   Primary fused intent signals from ModalFusion
                        (e.g. ["INFORM", "ask_general"]).  Used for
                        intent_boost calculation.
        """
        text_lower   = event_text.lower()
        primary_int  = (intent_vector[0].lower() if intent_vector else "").split("_")[0]
        scores: list[WakeScore] = []

        for agent in self._agents:
            if agent.compression_state == "archived":
                continue

            token = self._tokens.get(agent.agent_id)
            if token is None:
                continue

            # Keyword scoring
            hits = sum(
                1 for kw in agent.wake_conditions
                if kw.lower() in text_lower
            )
            total_kw      = max(1, len(agent.wake_conditions))
            keyword_score = hits / total_kw

            # Intent boost: check if any word in agent role or agent_id
            # matches the primary intent keyword
            role_words = set(
                agent.role.lower().replace("-", " ").replace("_", " ").split()
                + agent.agent_id.lower().replace("-", " ").replace("_", " ").split()
            )
            intent_boost = 0.30 if (primary_int and primary_int in role_words) else 0.0

            total = round(keyword_score + intent_boost, 4)
            scores.append(WakeScore(
                agent         = agent,
                token         = token,
                keyword_hits  = hits,
                keyword_score = round(keyword_score, 4),
                intent_boost  = intent_boost,
                total_score   = total,
            ))

        return sorted(scores, key=lambda s: s.total_score, reverse=True)

    # ── Wake ─────────────────────────────────────────────────────────

    def wake(self, scores: list[WakeScore]) -> list[MiniSRDAgent]:
        """Return the top-k agents that exceed min_score, as 'active' copies."""
        eligible = [s for s in scores if s.total_score >= self._min_score]
        top      = eligible[: self._k]
        woken    = []
        for ws in top:
            active = ws.agent.activate()
            woken.append(active)
            # Update cached token state
            self._tokens[active.agent_id] = active.to_vram_token()
        return woken

    # ── Audit record ─────────────────────────────────────────────────

    def signed_routing_record(
        self,
        event_id:  str,
        scores:    list[WakeScore],
        woken:     list[MiniSRDAgent],
    ) -> dict:
        """Return an HMAC-signed routing record for the MET audit chain."""
        record = {
            "event_id":   event_id,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "scored":     [
                {
                    "agent_id":     s.agent.agent_id,
                    "keyword_hits": s.keyword_hits,
                    "keyword_score": s.keyword_score,
                    "intent_boost": s.intent_boost,
                    "total_score":  s.total_score,
                    "action":       s.action(self._min_score),
                }
                for s in scores
            ],
            "woken_ids":  [a.agent_id for a in woken],
            "k":          self._k,
            "min_score":  self._min_score,
            "signature":  "",
        }
        sig              = _hmac_sign(_canonical(record), _router_key())
        record["signature"] = sig
        return record
