"""Axiom Event Token — 3D / multimodal token validation prototype.

Per the concept note (uploads/2afd1ba6-axiom_3d_multimodal_tokens.pdf),
a single token represents a layered concept-or-event with sub-reports
from specialist agents (Text, Audio, Video, Physics, Governance) plus
a Coordinator that fuses them.

This package is the 1-day validation prototype: the data container +
signing + selective-activation API + a stub-driven demo. The Text Agent
uses the real `axiom_intent_classifier`; Audio / Video / Physics / Governance
are stubs that return believable shapes so we can test whether the
container abstraction holds together end-to-end.

When the real audio / video engines ship (per the saved audio plan +
video concept doc), their agents replace the stubs without touching
the container format.

PRIVATE — do not push to a public mirror until the patent decision is
made. See `docs/training/kid-guard-strategy.md` and the saved plan
file for the patent-timing reasoning.
"""
__version__ = "0.1.0"

from .models import EventToken, LayerReport
from .coordinator import Coordinator
from .chain import EventTokenChain
from .backends import (
    SLMBackend, BackendResult, BackendError,
    NIMBackend, LocalNanoBackend, ChainedBackend,
    make_backend, default_backend,
)
from .router import DelegateRouter, RoutingDecision
from .delegate_runtime import DelegateAgent

__all__ = [
    "EventToken", "LayerReport", "Coordinator", "EventTokenChain",
    "SLMBackend", "BackendResult", "BackendError",
    "NIMBackend", "LocalNanoBackend", "ChainedBackend",
    "make_backend", "default_backend",
    "DelegateRouter", "RoutingDecision",
    "DelegateAgent",
    "__version__",
]
