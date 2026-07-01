"""Axiom Resonance Layer — Resonant Event Tokens (RET).

Two capabilities built additively over the existing Axiom fabric:

  1. Resonance-based mini-agent routing (ResonanceRouter)
     Agents are assigned frequency bands from their role description.
     Routing uses frequency proximity instead of brittle keyword matching.

  2. Phase-conflict anomaly detection (ResonanceDetector)
     A rolling EMA baseline tracks the expected signal state.
     Phase differences > π/3 between consecutive tokens trigger
     PHASE_CONFLICT alerts — detecting zero-day / injection patterns
     that produce coherent intent signals but contradictory directives.

Usage:
    from axiom_resonance import ResonanceLayer, DOMAIN_BANDS

    layer = ResonanceLayer(agents=my_agents)
    result = layer.run("medical research query about patient data")
    for alert in result.alerts:
        if alert.is_anomaly:
            print(alert.alert_type, alert.severity)
"""

from axiom_resonance.token import (
    ResonanceSignal,
    ResonantEventToken,
    domain_to_frequency,
    PHASE_STABLE,
    PHASE_UNCERTAIN,
    PHASE_OPPOSING,
)
from axiom_resonance.encoder import ResonanceEncoder, DOMAIN_KEYWORDS
from axiom_resonance.router import ResonanceRouter, ResonanceScore, DOMAIN_BANDS
from axiom_resonance.detector import (
    ResonanceDetector,
    AnomalyAlert,
    PHASE_CONFLICT_THRESHOLD,
    AMPLITUDE_SPIKE_THRESHOLD,
)
from axiom_resonance.layer import ResonanceLayer, ResonanceLayerResult

__all__ = [
    "ResonanceSignal",
    "ResonantEventToken",
    "domain_to_frequency",
    "PHASE_STABLE",
    "PHASE_UNCERTAIN",
    "PHASE_OPPOSING",
    "ResonanceEncoder",
    "DOMAIN_KEYWORDS",
    "ResonanceRouter",
    "ResonanceScore",
    "DOMAIN_BANDS",
    "ResonanceDetector",
    "AnomalyAlert",
    "PHASE_CONFLICT_THRESHOLD",
    "AMPLITUDE_SPIKE_THRESHOLD",
    "ResonanceLayer",
    "ResonanceLayerResult",
]
