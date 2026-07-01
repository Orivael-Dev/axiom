"""ResonanceRouter — frequency-band-based agent scoring and wake selection.

Extends (does NOT replace) AgentRouter.  Both can coexist; ResonanceRouter
wraps AgentRouter.score() for the keyword_score channel and adds two new
channels on top:

  resonance_sim   max(0.0, 1.0 - |query_freq - agent_freq|)
                  Frequency proximity — 1.0 for same-band agents.
                  Note: for 1-D scalars "cosine similarity" degenerates;
                  this proximity metric captures the resonance metaphor.

  phase_align     max(0.0, 1.0 - query_phase / π)
                  1.0 for stable queries, 0.5 for uncertain, 0.0 for opposing.
                  Agents are assumed to prefer stable-phase queries.

  total_score = 0.5 * resonance_sim + 0.3 * phase_align + 0.2 * keyword_score

DOMAIN_BANDS is pre-computed at module load time so it is stable and
deterministic across all runs.  Agents are assigned bands by matching
their role string against DOMAIN_KEYWORDS (imported from encoder.py to
avoid table duplication).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from axiom_signing import derive_key
from axiom_agent_fabric.capsule import MiniSRDAgent, VRAMAgentToken
from axiom_agent_fabric.router import AgentRouter
from axiom_resonance.token import ResonanceSignal, ResonantEventToken, domain_to_frequency
from axiom_resonance.encoder import DOMAIN_KEYWORDS

RESONANCE_ROUTER_KEY_NS = b"axiom-resonance-router-v1"

_RESONANCE_ROUTER_KEY: Optional[bytes] = None


def _router_key() -> bytes:
    global _RESONANCE_ROUTER_KEY
    if _RESONANCE_ROUTER_KEY is None:
        _RESONANCE_ROUTER_KEY = derive_key(RESONANCE_ROUTER_KEY_NS)
    return _RESONANCE_ROUTER_KEY


def _canonical(d: dict) -> bytes:
    payload = {k: v for k, v in d.items() if k != "signature"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _hmac_sign(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


# ── Pre-computed domain band frequencies ──────────────────────────────────────

DOMAIN_BANDS: dict[str, float] = {
    domain: domain_to_frequency(domain)
    for domain in [
        "legal", "medical", "security", "finance", "code",
        "physics", "governance", "memory", "general",
    ]
}


def _agent_band(agent: MiniSRDAgent) -> float:
    """Assign frequency band by matching agent.role against domain keywords.

    Uses the same DOMAIN_KEYWORDS priority ordering as the encoder so
    domain classification is consistent across the resonance stack.
    Returns DOMAIN_BANDS["general"] if no keywords match.
    """
    role_lower = agent.role.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if domain == "general":
            continue
        if any(kw in role_lower for kw in keywords):
            return DOMAIN_BANDS[domain]
    return DOMAIN_BANDS["general"]


# ── ResonanceScore ────────────────────────────────────────────────────────────


@dataclass
class ResonanceScore:
    """Scoring result for one agent under resonance-based routing.

    Fields
    ------
    agent           The MiniSRDAgent that was scored.
    token           Its hot VRAMAgentToken.
    resonance_sim   Frequency proximity to the query signal.
    phase_align     Phase compatibility (1.0 = stable, 0.0 = opposing).
    keyword_score   Legacy keyword score from AgentRouter fallback.
    total_score     Blended: 0.5*resonance_sim + 0.3*phase_align + 0.2*keyword_score.
    """
    agent:          MiniSRDAgent
    token:          VRAMAgentToken
    resonance_sim:  float
    phase_align:    float
    keyword_score:  float
    total_score:    float

    def action(self, min_score: float) -> str:
        return "WAKE" if self.total_score >= min_score else "sleep"


# ── ResonanceRouter ───────────────────────────────────────────────────────────


class ResonanceRouter:
    """Scores dormant SRD capsules by frequency-band similarity.

    Parameters
    ----------
    agents     Full registry of MiniSRDAgents (all states).
    fallback_router  Optional pre-built AgentRouter for keyword_score.
                     Created internally if not provided.
    k          Maximum agents to wake per event (default 4).
    min_score  Minimum total_score for waking (default 0.35).
    """

    def __init__(
        self,
        agents:           list[MiniSRDAgent],
        fallback_router:  Optional[AgentRouter] = None,
        k:                int   = 4,
        min_score:        float = 0.35,
    ) -> None:
        self._agents    = agents
        self._fallback  = fallback_router or AgentRouter(agents, k=k, min_score=min_score)
        self._k         = k
        self._min       = min_score
        # Pre-compute agent frequencies and hot tokens
        self._agent_bands: dict[str, float] = {}
        self._tokens: dict[str, VRAMAgentToken] = {}
        for a in agents:
            if a.compression_state != "archived":
                self._agent_bands[a.agent_id] = _agent_band(a)
                self._tokens[a.agent_id] = a.to_vram_token()

    def score(
        self,
        signal:     ResonanceSignal,
        event_text: str = "",
    ) -> list[ResonanceScore]:
        """Score every non-archived agent against the resonance signal.

        Parameters
        ----------
        signal      ResonanceSignal from ResonanceEncoder.
        event_text  Raw event text forwarded to the fallback AgentRouter
                    for keyword scoring.
        """
        # Get keyword scores from legacy router
        kw_scores: dict[str, float] = {
            ws.agent.agent_id: ws.keyword_score
            for ws in self._fallback.score(event_text)
        }

        phase_align_base = max(0.0, 1.0 - signal.phase / math.pi)

        results: list[ResonanceScore] = []
        for agent in self._agents:
            if agent.compression_state == "archived":
                continue
            token = self._tokens.get(agent.agent_id)
            if token is None:
                continue

            agent_freq    = self._agent_bands.get(agent.agent_id, DOMAIN_BANDS["general"])
            resonance_sim = max(0.0, 1.0 - abs(signal.frequency - agent_freq))
            phase_align   = phase_align_base   # same for all agents (query property)
            keyword_score = kw_scores.get(agent.agent_id, 0.0)
            total         = round(
                0.5 * resonance_sim + 0.3 * phase_align + 0.2 * keyword_score,
                4,
            )
            results.append(ResonanceScore(
                agent         = agent,
                token         = token,
                resonance_sim = round(resonance_sim, 4),
                phase_align   = round(phase_align, 4),
                keyword_score = round(keyword_score, 4),
                total_score   = total,
            ))

        return sorted(results, key=lambda s: s.total_score, reverse=True)

    def wake(self, scores: list[ResonanceScore]) -> list[MiniSRDAgent]:
        """Return top-k agents above min_score as 'active' copies."""
        eligible = [s for s in scores if s.total_score >= self._min]
        top      = eligible[:self._k]
        woken: list[MiniSRDAgent] = []
        for rs in top:
            active = rs.agent.activate()
            woken.append(active)
            self._tokens[active.agent_id] = active.to_vram_token()
        return woken

    def signed_routing_record(
        self,
        event_id: str,
        scores:   list[ResonanceScore],
        woken:    list[MiniSRDAgent],
    ) -> dict:
        """Return an HMAC-signed resonance routing record for audit logs."""
        record = {
            "event_id":  event_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scored": [
                {
                    "agent_id":      s.agent.agent_id,
                    "resonance_sim": s.resonance_sim,
                    "phase_align":   s.phase_align,
                    "keyword_score": s.keyword_score,
                    "total_score":   s.total_score,
                    "action":        s.action(self._min),
                }
                for s in scores
            ],
            "woken_ids": [a.agent_id for a in woken],
            "k":         self._k,
            "min_score": self._min,
            "signature": "",
        }
        sig                = _hmac_sign(_canonical(record), _router_key())
        record["signature"] = sig
        return record
