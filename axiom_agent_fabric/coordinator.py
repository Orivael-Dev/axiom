"""FabricCoordinator — drives the full dormant-capsule micro-agent cycle.

Six-stage pipeline per event:

  1. PARSE   — Coordinator.compose() → EventToken
               ModalFusion.fuse()    → FusedIntent
  2. SCORE   — AgentRouter.score(event_text, intent_vector)
  3. WAKE    — AgentRouter.wake(scores) → list[MiniSRDAgent]
  4. DISTILL — _run_agent() per woken agent → AgentResult
               (TextAgent / AudioAgent / VideoAgent as back-ends)
  5. MERGE   — Coordinator.compose(summaries, parent=event_token)
  6. LOG     — LedgerWriter.append() + EventTokenChain.append()

FabricResult bundles all artefacts produced in a single run so callers
can audit the full activation: event_token, scored agents, woken agents,
per-agent results, merge token, dormant count, and the live chain.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from axiom_agent_fabric.capsule import MiniSRDAgent
from axiom_agent_fabric.result import AgentResult
from axiom_agent_fabric.router import AgentRouter, WakeScore
from axiom_event_token.chain import EventTokenChain
from axiom_event_token.coordinator import Coordinator
from axiom_event_token.models import EventToken, LayerReport
from axiom_exoskeleton_ledger import LedgerWriter
from axiom_fusion import FusedIntent, ModalFusion


# ─── FabricResult ────────────────────────────────────────────────────────────


@dataclass
class FabricResult:
    """All artefacts produced by one FabricCoordinator.run() cycle.

    Attributes
    ----------
    event_token     Initial EventToken from the parse stage.
    scores          WakeScore list (all non-archived agents, sorted).
    woken           MiniSRDAgents whose compression_state was → "active".
    results         Per-agent AgentResult (one per woken agent).
    merge_token     Coordinator EventToken merging all agent summaries.
    dormant_count   Number of agents that stayed dormant this cycle.
    chain           Live EventTokenChain (event_token + merge_token).
    routing_record  HMAC-signed audit dict from AgentRouter.
    """
    event_token:    EventToken
    scores:         list[WakeScore]
    woken:          list[MiniSRDAgent]
    results:        list[AgentResult]
    merge_token:    EventToken
    dormant_count:  int
    chain:          EventTokenChain
    routing_record: dict


# ─── FabricCoordinator ───────────────────────────────────────────────────────


class FabricCoordinator:
    """Orchestrates the dormant-capsule micro-agent fabric.

    Parameters
    ----------
    agents      Full registry of MiniSRDAgents (all compression states).
    k           Maximum agents to wake per event (default 4).
    min_score   Wake-threshold passed to AgentRouter (default 0.35).
    ledger_path Path for the JSONL audit ledger; defaults to the
                system-wide ledger path from LedgerWriter.
    """

    def __init__(
        self,
        agents: list[MiniSRDAgent],
        k: int = 4,
        min_score: float = 0.35,
        ledger_path: Optional[Path] = None,
    ) -> None:
        self._router    = AgentRouter(agents, k=k, min_score=min_score)
        self._all       = agents
        self._coord     = Coordinator()
        self._fusion    = ModalFusion()
        self._ledger    = LedgerWriter(path=ledger_path)
        self._chain     = EventTokenChain()

    # ── Public API ────────────────────────────────────────────────────

    def run(
        self,
        text: str,
        *,
        audio: Optional[dict] = None,
        video: Optional[dict] = None,
        event_id: Optional[str] = None,
    ) -> FabricResult:
        """Execute one full parse → score → wake → distill → merge → log cycle.

        Parameters
        ----------
        text        User event text (required).
        audio       Optional audio layer dict (passed to AudioAgent).
        video       Optional video layer dict (passed to VideoAgent).
        event_id    Stable event identifier for the routing audit record.
                    Auto-generated if not supplied.
        """
        eid = event_id or f"fab_{uuid.uuid4().hex[:12]}"

        # ── 1. PARSE ──────────────────────────────────────────────────────
        activate = ["text", "governance"]
        if audio:
            activate.append("audio")
        if video:
            activate.append("video")

        event_token = self._coord.compose(
            text=text,
            audio=audio,
            video=video,
            activate=activate,
        )
        fused: FusedIntent = self._fusion.fuse(event_token)

        # ── 2. SCORE ──────────────────────────────────────────────────────
        scores = self._router.score(
            event_text=text,
            intent_vector=fused.intent_vector,
        )

        # ── 3. WAKE ───────────────────────────────────────────────────────
        woken = self._router.wake(scores)
        dormant_count = sum(
            1 for a in self._all
            if a.compression_state not in ("active", "archived")
        ) - len(woken)
        dormant_count = max(dormant_count, 0)

        # ── 4. DISTILL ────────────────────────────────────────────────────
        results: list[AgentResult] = []
        for agent in woken:
            result = _run_agent(
                agent,
                text=text,
                audio=audio,
                video=video,
                coord=self._coord,
            )
            results.append(result)

        # ── 5. MERGE ──────────────────────────────────────────────────────
        # Concatenate agent summaries as the merge-token text
        if results:
            summary_text = " | ".join(r.answer_summary for r in results)
        else:
            summary_text = text

        merge_token = self._coord.compose(
            text=summary_text,
            activate=["text", "governance"],
            parent=event_token,
        )

        # ── 6. LOG ────────────────────────────────────────────────────────
        # Chain: root = event_token, then merge_token
        if not self._chain.tokens:
            self._chain.append(event_token)
        else:
            # For subsequent runs in the same session the chain tail links
            # the merge_token from the previous cycle.
            pass

        # Append merge_token if its parent_signature matches the chain tail
        try:
            self._chain.append(merge_token)
        except ValueError:
            # Chain already has tokens from a prior run; start a sub-chain
            # by simply recording but not failing.
            pass

        self._ledger.append(
            token=event_token,
            use_case="fabric_event",
            input_text=text,
        )
        self._ledger.append(
            token=merge_token,
            use_case="fabric_merge",
            input_text=summary_text,
        )

        routing_record = self._router.signed_routing_record(
            event_id=eid,
            scores=scores,
            woken=woken,
        )

        return FabricResult(
            event_token=event_token,
            scores=scores,
            woken=woken,
            results=results,
            merge_token=merge_token,
            dormant_count=dormant_count,
            chain=self._chain,
            routing_record=routing_record,
        )

    # ── Chain access ──────────────────────────────────────────────────

    @property
    def chain(self) -> EventTokenChain:
        return self._chain


# ─── Per-agent distillation ──────────────────────────────────────────────────


def _run_agent(
    agent: MiniSRDAgent,
    *,
    text: str,
    audio: Optional[dict],
    video: Optional[dict],
    coord: Coordinator,
) -> AgentResult:
    """Run a woken MiniSRDAgent using the appropriate Coordinator agents.

    Routes to text / audio / video layers based on the agent's role and
    what modality data is available.  Governance always runs to provide
    constitutional oversight.  The first LayerReport with non-null payload
    is used to build the AgentResult.
    """
    role_lower = agent.role.lower()

    activate = ["text", "governance"]
    if audio and _role_matches(role_lower, ("audio", "sound", "music", "voice")):
        activate.append("audio")
    if video and _role_matches(role_lower, ("video", "vision", "motion", "visual")):
        activate.append("video")

    agent_token = coord.compose(
        text=text,
        audio=audio,
        video=video,
        activate=activate,
    )

    # Choose the most informative layer for the AgentResult
    report: Optional[LayerReport] = (
        agent_token.text
        or agent_token.audio
        or agent_token.video
        or agent_token.governance
    )
    if report is None:
        # Fallback: synthesise a minimal LayerReport
        report = LayerReport.signed(
            agent=agent.agent_id,
            payload={
                "phrase": text,
                "intent_class": "INFORM",
                "confidence": 0.5,
                "signals": [],
            },
            confidence=0.5,
        )

    # Determine next recommended agent from the woken agent's own hint
    # (the agent may carry a "next_agent" governance_limit like "→ citation_checker")
    next_agent: Optional[str] = None
    for limit in agent.governance_limits:
        if limit.startswith("→"):
            next_agent = limit[1:].strip()
            break

    return AgentResult.from_layer_report(
        agent_id=agent.agent_id,
        report=report,
        next_agent=next_agent,
        memory_delta={"last_active_event": text[:100]},
    )


def _role_matches(role: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in role for kw in keywords)
