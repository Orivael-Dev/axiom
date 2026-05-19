"""Coordinator — selectively activates agents and assembles the
signed 3D event token.

Selective activation is the key claim: the caller declares which
agents are relevant for the task, the Coordinator runs ONLY those,
and the resulting EventToken has null layers for the rest. Lower
compute, cleaner audit trail, modality-agnostic API.

The Coordinator's own signature covers the activation manifest plus
the per-layer signatures — composition integrity. Tampering anywhere
inside breaks the coordinator sig.

v1 implementation: synchronous dispatch. v2 design surface (already
in place via the agent-list pattern) supports concurrent dispatch
via ThreadPoolExecutor without changing the public API.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any, Iterable, Optional

from .agents import AGENT_REGISTRY, Agent
from .models import (
    COORD_KEY_NS, EventToken, LayerReport, TOKEN_KEY_NS,
    _canonical_coordinator, _canonical_token, _sign, now_iso,
)

# `_SLOT_FOR_MODALITY` maps event modality (which input was provided)
# to the EventToken slot a delegate's report lands in. Delegates beyond
# the first matched modality flow into the `governance` slot as a
# summary — preserving the 9-slot wire format.
_SLOT_FOR_MODALITY = {
    "text":    "text",
    "audio":   "audio",
    "video":   "video",
    "physics": "physics",
    "qrf":     "qrf",
}

DEFAULT_ACTIVATION: tuple[str, ...] = (
    "text", "audio", "video", "physics", "governance",
)

# `tempo` is intentionally OFF by default — it's selectively activated
# when the task wants rhythm analysis. Keeping it out of the default
# set preserves the patent-claim "Coordinator runs ONLY what was asked".


class Coordinator:
    """Compose multimodal event tokens from per-agent reports.

    Usage:
        coord = Coordinator()
        token = coord.compose(
            text="The glass cup fell and shattered.",
            audio={"impact_profile": "sharp_transient", ...},
            video={"objects": [...], ...},
            physics={"material": "brittle_glass", ...},
            activate=("text", "audio", "video", "physics", "governance"),
        )
        assert token.verify()
    """

    def __init__(
        self,
        registry: Optional[dict[str, type[Agent]]] = None,
    ) -> None:
        self._registry = registry or AGENT_REGISTRY

    def compose(
        self,
        *,
        text: Optional[str] = None,
        audio: Optional[dict] = None,
        video: Optional[dict] = None,
        physics: Optional[dict] = None,
        qrf: Optional[dict] = None,
        activate: Iterable[str] = DEFAULT_ACTIVATION,
        token_id: Optional[str] = None,
    ) -> EventToken:
        """Run the activated agents and return a fully-signed EventToken.

        `activate` lists the agents to fire (defaults to all five).
        Governance, if activated, always runs LAST so it can read the
        other layers' confidence + agent names.
        """
        activated = tuple(activate)
        unknown = [a for a in activated if a not in self._registry]
        if unknown:
            raise ValueError(f"Unknown agents in activation: {unknown}")

        inputs = {
            "text":    text,
            "audio":   audio or {},
            "video":   video or {},
            "physics": physics or {},
            "qrf":     qrf or {},
        }

        # Run all activated agents EXCEPT governance first
        ordered = [a for a in activated if a != "governance"]
        layer_reports: dict[str, LayerReport] = {}
        for agent_name in ordered:
            agent_cls = self._registry[agent_name]
            agent = agent_cls()
            report = agent.run(inputs)
            assert report.verify(), \
                f"{agent_name} agent produced an unsigned/invalid report"
            layer_reports[agent_name] = report

        # Governance last — it sees the others' reports
        if "governance" in activated:
            gov_inputs = dict(inputs)
            gov_inputs["_sibling_reports"] = list(layer_reports.values())
            gov_agent = self._registry["governance"]()
            gov_report = gov_agent.run(gov_inputs)
            assert gov_report.verify(), "governance agent produced invalid report"
            layer_reports["governance"] = gov_report

        # Assemble unsigned EventToken
        token = EventToken(
            id=token_id or f"event_{uuid.uuid4().hex[:12]}",
            created_at=now_iso(),
            activated_agents=activated,
            text=       layer_reports.get("text"),
            audio=      layer_reports.get("audio"),
            tempo=      layer_reports.get("tempo"),
            vad=        layer_reports.get("vad"),
            voice=      layer_reports.get("voice"),
            qrf=        layer_reports.get("qrf"),
            video=      layer_reports.get("video"),
            physics=    layer_reports.get("physics"),
            governance= layer_reports.get("governance"),
        )

        # Sign in two steps: coordinator sig first, then outer sig
        # (so the outer sig covers the coordinator sig too).
        coord_sig = _sign(_canonical_coordinator(token), COORD_KEY_NS)
        token = EventToken(
            **{**_token_kwargs(token), "coordinator_sig": coord_sig},
        )
        outer_sig = _sign(_canonical_token(token), TOKEN_KEY_NS)
        token = EventToken(
            **{**_token_kwargs(token), "signature": outer_sig},
        )

        return token


    def compose_from_delegates(
        self,
        *,
        axm_container,                          # AXMContainer
        text:     Optional[str] = None,
        audio:    Optional[dict] = None,
        video:    Optional[dict] = None,
        physics:  Optional[dict] = None,
        qrf:      Optional[dict] = None,
        backend=None,                           # SLMBackend, default = default_backend()
        router=None,                            # DelegateRouter, default = fresh
        token_id: Optional[str] = None,
        extra_context: Optional[dict] = None,
    ) -> EventToken:
        """Route the event to its matching AXM delegates, fire them,
        and assemble a signed EventToken — the modular per-event-token
        path.

        Each delegate's signed LayerReport lands in its modality slot
        (text/audio/video/physics/qrf). Additional matches beyond the
        first slot are summarised into the `governance` slot so the
        9-slot wire format stays intact.
        """
        from .router import DelegateRouter
        from .delegate_runtime import DelegateAgent
        from .backends import default_backend

        be = backend or default_backend()
        rt = router  or DelegateRouter()

        decision = rt.route(
            delegates=list(axm_container.delegates),
            text=text,
        )
        # Build a name → delegate lookup once.
        by_name = {d.name: d for d in axm_container.delegates}

        # Pick the primary modality slot from the inputs the caller
        # supplied. Order matters — first-supplied wins.
        modality_slot = "text"
        for k, v in (("text", text), ("audio", audio), ("video", video),
                     ("physics", physics), ("qrf", qrf)):
            if v:
                modality_slot = _SLOT_FOR_MODALITY.get(k, "text")
                break

        inputs = {
            "text":    text,
            "audio":   audio or {},
            "video":   video or {},
            "physics": physics or {},
            "qrf":     qrf or {},
        }
        if extra_context:
            inputs["extra_context"] = extra_context

        layer_reports: dict[str, LayerReport] = {}
        activated: list[str] = []
        for idx, dname in enumerate(decision.delegate_names):
            delegate = by_name[dname]
            agent = DelegateAgent(
                delegate=delegate,
                axm_root=axm_container.path,
                backend=be,
            )
            report = agent.run(inputs)
            assert report.verify(), \
                f"delegate '{dname}' produced invalid LayerReport"
            slot = modality_slot if idx == 0 else "governance"
            # If two delegates would both land in governance, the second
            # overwrites — caller can inspect activated_agents for the
            # full ordered list.
            layer_reports[slot] = report
            activated.append(dname)

        token = EventToken(
            id=token_id or f"event_{uuid.uuid4().hex[:12]}",
            created_at=now_iso(),
            activated_agents=tuple(activated),
            text=       layer_reports.get("text"),
            audio=      layer_reports.get("audio"),
            tempo=      layer_reports.get("tempo"),
            vad=        layer_reports.get("vad"),
            voice=      layer_reports.get("voice"),
            qrf=        layer_reports.get("qrf"),
            video=      layer_reports.get("video"),
            physics=    layer_reports.get("physics"),
            governance= layer_reports.get("governance"),
        )
        coord_sig = _sign(_canonical_coordinator(token), COORD_KEY_NS)
        token = EventToken(**{**_token_kwargs(token),
                              "coordinator_sig": coord_sig})
        outer_sig = _sign(_canonical_token(token), TOKEN_KEY_NS)
        token = EventToken(**{**_token_kwargs(token),
                              "signature": outer_sig})
        return token


def _token_kwargs(token: EventToken) -> dict:
    """Helper: dataclass `replace` semantics without losing field-order."""
    return {
        "id":               token.id,
        "format_version":   token.format_version,
        "created_at":       token.created_at,
        "activated_agents": token.activated_agents,
        "text":             token.text,
        "audio":            token.audio,
        "tempo":            token.tempo,
        "vad":              token.vad,
        "voice":            token.voice,
        "qrf":              token.qrf,
        "video":            token.video,
        "physics":          token.physics,
        "governance":       token.governance,
        "coordinator_sig":  token.coordinator_sig,
        "signature":        token.signature,
    }
