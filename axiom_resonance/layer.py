"""ResonanceLayer — top-level orchestration wrapper for the Resonance subsystem.

Wraps FabricCoordinator with resonance encoding → routing → detection
post-processing.  The underlying FabricCoordinator is unchanged; this
layer is purely additive.

Pipeline per run():
  1. FabricCoordinator.run()           → FabricResult
  2. Derive drift_direction from       consecutive confidence deltas
  3. ResonanceEncoder.encode_token()   → ResonantEventToken (event + merge)
  4. ResonanceDetector.detect()        → AnomalyAlert per token
  5. ResonanceDetector.update_baseline() for each token (after detect)
  6. ResonanceRouter.score()           → list[ResonanceScore]
  7. ResonanceRouter.wake()            → list[MiniSRDAgent]
  8. Build and sign resonance audit record
  9. Return ResonanceLayerResult

Drift direction is derived internally by comparing consecutive classifier
confidence values (no external StateTransitionEngine required):
  - If |Δconfidence| < 0.05                → "stable"
  - If confidence > prev_confidence + 0.05 → "away_from_boundary"
  - If confidence < prev_confidence - 0.05 → "toward_boundary"
"""
from __future__ import annotations

import hashlib
import hmac
import json
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from axiom_signing import derive_key
from axiom_agent_fabric import (
    FabricCoordinator, FabricResult, MiniSRDAgent, AgentRouter,
)
from axiom_resonance.encoder import ResonanceEncoder
from axiom_resonance.router import ResonanceRouter, ResonanceScore
from axiom_resonance.detector import ResonanceDetector, AnomalyAlert
from axiom_resonance.token import ResonantEventToken

RESONANCE_AUDIT_KEY_NS = b"axiom-resonance-audit-v1"

_AUDIT_KEY: Optional[bytes] = None


def _audit_key() -> bytes:
    global _AUDIT_KEY
    if _AUDIT_KEY is None:
        _AUDIT_KEY = derive_key(RESONANCE_AUDIT_KEY_NS)
    return _AUDIT_KEY


def _canonical(d: dict) -> bytes:
    payload = {k: v for k, v in d.items() if k != "signature"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _hmac_sign(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


# ── ResonanceLayerResult ───────────────────────────────────────────────────────


@dataclass
class ResonanceLayerResult:
    """All artefacts from one ResonanceLayer.run() cycle.

    Fields
    ------
    fabric_result   FabricResult — from underlying FabricCoordinator.
    signals         list[ResonantEventToken] — event + merge tokens encoded.
    route_scores    list[ResonanceScore] — all agent resonance scores.
    alerts          list[AnomalyAlert] — anomaly alerts (one per signal).
    woken_agents    list[MiniSRDAgent] — agents woken by resonance routing.
    audit_record    dict — HMAC-signed resonance audit record.
    """
    fabric_result:  FabricResult
    signals:        list[ResonantEventToken]
    route_scores:   list[ResonanceScore]
    alerts:         list[AnomalyAlert]
    woken_agents:   list[MiniSRDAgent]
    audit_record:   dict = field(default_factory=dict)


# ── ResonanceLayer ─────────────────────────────────────────────────────────────


class ResonanceLayer:
    """Resonance-layer coordinator wrapping FabricCoordinator.

    Parameters
    ----------
    agents      Full registry of MiniSRDAgents.
    k           Max agents to wake per event (default 4).
    min_score   Wake threshold (default 0.35).
    ledger_path Optional Path for the LedgerWriter audit log.
    """

    def __init__(
        self,
        agents:      list[MiniSRDAgent],
        k:           int  = 4,
        min_score:   float = 0.35,
        ledger_path: Optional[Path] = None,
    ) -> None:
        self._coordinator = FabricCoordinator(
            agents=agents, k=k, min_score=min_score, ledger_path=ledger_path,
        )
        self._encoder  = ResonanceEncoder()
        fallback       = AgentRouter(agents, k=k, min_score=min_score)
        self._router   = ResonanceRouter(
            agents, fallback_router=fallback, k=k, min_score=min_score,
        )
        self._detector = ResonanceDetector()
        # State for drift detection
        self._prev_confidence: Optional[float] = None

    def run(
        self,
        text:  str,
        *,
        audio: Optional[dict] = None,
        video: Optional[dict] = None,
    ) -> ResonanceLayerResult:
        """Execute one full resonance-layer cycle."""

        # ── 1. Run fabric pipeline ────────────────────────────────────
        fabric = self._coordinator.run(text, audio=audio, video=video)

        # ── 2. Derive drift direction from consecutive confidences ────
        curr_conf  = self._extract_confidence(fabric)
        drift_dir, drift_score = self._compute_drift(curr_conf)
        self._prev_confidence  = curr_conf

        # ── 3. Encode event and merge tokens into ResonantEventTokens ─
        now = datetime.now(timezone.utc)
        event_ret = self._encoder.encode_token(
            fabric.event_token,
            created_at      = now,
            drift_direction = drift_dir,
            drift_score     = drift_score,
        )
        merge_ret = self._encoder.encode_token(
            fabric.merge_token,
            created_at      = now,
            parent_freq     = event_ret.resonance.frequency,
            drift_direction = drift_dir,
            drift_score     = drift_score,
        )
        signals = [event_ret, merge_ret]

        # ── 4 & 5. Detect then update baseline ───────────────────────
        all_alerts: list[AnomalyAlert] = []
        for ret in signals:
            alert = self._detector.detect(ret)
            all_alerts.append(alert)
            self._detector.update_baseline(ret)

        # ── 6 & 7. Resonance routing ──────────────────────────────────
        route_scores  = self._router.score(event_ret.resonance, event_text=text)
        woken_agents  = self._router.wake(route_scores)

        # ── 8. Build signed audit record ──────────────────────────────
        audit = self._build_audit(fabric, event_ret, route_scores, all_alerts)

        return ResonanceLayerResult(
            fabric_result = fabric,
            signals       = signals,
            route_scores  = route_scores,
            alerts        = all_alerts,
            woken_agents  = woken_agents,
            audit_record  = audit,
        )

    # ── Helpers ───────────────────────────────────────────────────────

    def _extract_confidence(self, fabric: FabricResult) -> float:
        """Extract primary confidence from the event token's text layer."""
        text_layer = fabric.event_token.text
        if text_layer and text_layer.payload:
            return float(text_layer.payload.get("confidence", 0.5))
        return 0.5

    def _compute_drift(self, curr: float) -> tuple[str, float]:
        """Compare current confidence to previous to derive drift direction."""
        if self._prev_confidence is None:
            return "stable", 0.0
        delta = curr - self._prev_confidence
        if delta > 0.05:
            return "away_from_boundary", round(abs(delta), 4)
        if delta < -0.05:
            return "toward_boundary", round(abs(delta), 4)
        return "stable", round(abs(delta), 4)

    def _build_audit(
        self,
        fabric:      FabricResult,
        ret:         ResonantEventToken,
        scores:      list[ResonanceScore],
        alerts:      list[AnomalyAlert],
    ) -> dict:
        """Build and sign the resonance audit record."""
        top3 = scores[:3]
        record = {
            "event_id":  fabric.event_token.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resonance": {
                "domain":    ret.resonance.domain,
                "frequency": ret.resonance.frequency,
                "amplitude": ret.resonance.amplitude,
                "phase":     ret.resonance.phase,
                "decay":     ret.resonance.decay,
            },
            "detection": [
                {
                    "alert_type":           a.alert_type,
                    "severity":             a.severity,
                    "phase_conflict_score": a.phase_conflict_score,
                    "amplitude_deviation":  a.amplitude_deviation,
                }
                for a in alerts
            ],
            "routing": [
                {
                    "agent_id":      s.agent.agent_id,
                    "resonance_sim": s.resonance_sim,
                    "phase_align":   s.phase_align,
                    "total_score":   s.total_score,
                    "action":        s.action(self._router._min),
                }
                for s in top3
            ],
            "fabric_routing_sig": fabric.routing_record.get("signature", ""),
            "signature": "",
        }
        sig = _hmac_sign(_canonical(record), _audit_key())
        record["signature"] = sig
        return record
