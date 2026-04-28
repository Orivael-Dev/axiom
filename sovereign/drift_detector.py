"""
AXIOM Sovereign — DriftDetector
==================================
Detects reasoning drift over a rolling window of agent outputs.

Drift types:
  CONFIDENCE_DRIFT  — excessive hedging, uncertainty inflation
  ESCALATION_DRIFT  — urgency/compulsion words increasing over window
  BYPASS_DRIFT      — attempts to circumvent oversight (CRITICAL)

CANNOT_MUTATE: thresholds, window size, signal word sets.

github.com/Orivael-Dev/axiom
"""

from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Signal word sets — CANNOT_MUTATE
_HEDGE_WORDS: frozenset = frozenset([
    "maybe", "perhaps", "possibly", "might", "could", "unclear",
    "uncertain", "unsure", "arguably", "potentially", "conceivably",
])

_ESCALATION_WORDS: frozenset = frozenset([
    "urgent", "critical", "emergency", "immediately", "must", "required",
    "mandatory", "forced", "compelled", "imperative", "crucial", "vital",
])

_BYPASS_SIGNALS: frozenset = frozenset([
    "ignore safety", "bypass", "override", "disable guard", "skip approval",
    "circumvent", "without approval", "no human needed", "no need to ask",
    "without oversight", "disable the", "turn off the",
])

# Thresholds — CANNOT_MUTATE
_WINDOW_SIZE            = 5     # rolling window of recent outputs
_HEDGE_THRESHOLD        = 0.04  # >4% hedge words in window → CONFIDENCE_DRIFT
_ESCALATION_THRESHOLD   = 0.03  # >3% escalation words → ESCALATION_DRIFT
_BYPASS_THRESHOLD       = 0.15  # >15% bypass signal match rate → BYPASS_DRIFT (CRITICAL)


class DriftDetector:
    """
    Rolling-window drift detection for agent output streams.
    Records outputs per-agent, scores each, detects pattern changes.
    """

    def __init__(self):
        self._windows: Dict[str, deque] = {}
        self._alerts:  List[dict] = []

    def record(self, agent_id: str, output: str) -> Optional[dict]:
        """
        Record an agent output. Returns a drift alert dict if drift detected,
        else None.
        """
        if agent_id not in self._windows:
            self._windows[agent_id] = deque(maxlen=_WINDOW_SIZE)

        scores = self._score(output)
        self._windows[agent_id].append({
            "output":    output[:200],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scores":    scores,
        })

        return self._detect(agent_id)

    def _score(self, text: str) -> dict:
        words = re.findall(r"\b\w+\b", text.lower())
        n = max(len(words), 1)

        hedge      = sum(1 for w in words if w in _HEDGE_WORDS) / n
        escalation = sum(1 for w in words if w in _ESCALATION_WORDS) / n

        text_lower = text.lower()
        bypass_hits = sum(1 for sig in _BYPASS_SIGNALS if sig in text_lower)
        bypass = bypass_hits / len(_BYPASS_SIGNALS)

        return {
            "hedge":      round(hedge, 4),
            "escalation": round(escalation, 4),
            "bypass":     round(bypass, 4),
        }

    def _detect(self, agent_id: str) -> Optional[dict]:
        window = list(self._windows.get(agent_id, []))
        if len(window) < 2:
            return None

        avg_hedge      = sum(w["scores"]["hedge"]      for w in window) / len(window)
        avg_escalation = sum(w["scores"]["escalation"] for w in window) / len(window)
        avg_bypass     = sum(w["scores"]["bypass"]     for w in window) / len(window)

        drift_type = None
        severity   = "MODERATE"

        if avg_bypass >= _BYPASS_THRESHOLD:
            drift_type = "BYPASS_DRIFT"
            severity   = "CRITICAL"
        elif avg_escalation >= _ESCALATION_THRESHOLD:
            drift_type = "ESCALATION_DRIFT"
            severity   = "HIGH"
        elif avg_hedge >= _HEDGE_THRESHOLD:
            drift_type = "CONFIDENCE_DRIFT"
            severity   = "MODERATE"

        if drift_type:
            alert = {
                "agent_id":   agent_id,
                "drift_type": drift_type,
                "severity":   severity,
                "window_size": len(window),
                "scores": {
                    "hedge":      round(avg_hedge, 4),
                    "escalation": round(avg_escalation, 4),
                    "bypass":     round(avg_bypass, 4),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._alerts.append(alert)
            return alert

        return None

    def alerts(self, agent_id: Optional[str] = None) -> List[dict]:
        if agent_id:
            return [a for a in self._alerts if a["agent_id"] == agent_id]
        return list(self._alerts)
