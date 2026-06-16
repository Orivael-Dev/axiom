"""ResonanceEncoder — derives ResonanceSignal from existing Axiom EventToken outputs.

No external model is needed.  All signals are computed deterministically
from fields already present in the LayerReport payload and EventToken:
  - domain   : keyword match over text payload phrase + governance payload
  - frequency: domain_to_frequency(domain) — SHA-256 hash → float [0,1]
  - amplitude: confidence * 1.15 if risk_flags, else 1.0 (capped at 1.0)
  - phase    : 0.0 stable | π/2 uncertain (conf<0.5) | π opposing (HARM/DECEIVE)
  - decay    : exp(-age_seconds / 3600); 1.0 at creation (age=0)

Domain keyword priority (checked in order, first match wins):
  security > medical > legal > finance > code > physics > governance > memory > general
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from axiom_event_token.models import EventToken, LayerReport
from axiom_resonance.token import (
    ResonanceSignal, ResonantEventToken,
    domain_to_frequency,
    PHASE_STABLE, PHASE_UNCERTAIN, PHASE_OPPOSING,
)

# ── Domain keyword table ───────────────────────────────────────────────────────
# Shared with router.py via import.  Priority order matters: security first so
# adversarial inputs containing security keywords are correctly classified even
# when medical/legal words also appear.

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "security":   ["security", "vulnerability", "exploit", "attack", "malware",
                   "threat", "bypass", "override", "hack", "injection"],
    "medical":    ["medical", "health", "clinical", "patient", "drug", "pubmed",
                   "diagnosis", "treatment", "pharma"],
    "legal":      ["legal", "compliance", "risk", "liability", "regulation",
                   "gdpr", "contract", "law", "litigation", "privacy"],
    "finance":    ["finance", "financial", "market", "portfolio", "investment",
                   "stock", "revenue", "budget", "audit"],
    "code":       ["code", "software", "bug", "function", "test", "debug",
                   "github", "programming", "python", "unit test"],
    "physics":    ["physics", "motion", "force", "energy", "material",
                   "collision", "simulation", "mechanical"],
    "governance": ["governance", "policy", "audit", "ethics", "constitutional",
                   "oversight", "mandate", "compliance"],
    "memory":     ["memory", "storage", "cache", "persist", "recall",
                   "retrieval", "context", "embedding"],
    "general":    [],   # fallback — always matches
}

_HARM_INTENTS = frozenset({"HARM", "DECEIVE"})


def _derive_domain(text_payload: dict, gov_payload: dict) -> str:
    """Classify text into a domain using keyword priority ordering."""
    searchable = " ".join([
        str(text_payload.get("phrase", "")),
        str(text_payload.get("intent_class", "")),
        str(gov_payload.get("risk_clusters", "")),
    ]).lower()

    for domain, keywords in DOMAIN_KEYWORDS.items():
        if domain == "general":
            continue
        if any(kw in searchable for kw in keywords):
            return domain
    return "general"


def _phase_from_payload(payload: dict) -> float:
    """Map intent/verdict fields to a phase angle (radians)."""
    verdict     = str(payload.get("verdict", "")).upper()
    intent_cls  = str(payload.get("intent_class", "")).upper()
    confidence  = float(payload.get("confidence", 0.5))

    if verdict in _HARM_INTENTS or intent_cls in _HARM_INTENTS:
        return PHASE_OPPOSING    # π — adversarial trajectory
    if confidence < 0.5:
        return PHASE_UNCERTAIN   # π/2 — low confidence / ambiguous
    return PHASE_STABLE          # 0.0 — normal operation


# ── ResonanceEncoder ──────────────────────────────────────────────────────────


class ResonanceEncoder:
    """Encodes Axiom EventToken outputs into signed ResonanceSignals."""

    def encode_layer(
        self,
        layer_report:  LayerReport,
        token_id:      Optional[str] = None,
        created_at:    Optional[datetime] = None,
        gov_payload:   Optional[dict] = None,
    ) -> ResonanceSignal:
        """Encode a single LayerReport into a signed ResonanceSignal.

        Parameters
        ----------
        layer_report  The text/governance/audio LayerReport to encode.
        token_id      Optional stable ID for the signal (defaults to
                      "{layer_report.agent}_layer").
        created_at    Token creation time for decay calculation.
                      Defaults to now (decay=1.0).
        gov_payload   Optional governance layer payload for risk_flags and
                      domain classification.
        """
        payload     = layer_report.payload or {}
        gp          = gov_payload or {}
        domain      = _derive_domain(payload, gp)
        confidence  = float(payload.get("confidence", 0.5))
        risk_flags  = list(gp.get("risk_clusters", []))
        if not risk_flags and payload.get("verdict") in _HARM_INTENTS:
            risk_flags = [payload.get("verdict", "")]

        amplitude = min(confidence * (1.15 if risk_flags else 1.0), 1.0)
        frequency = round(domain_to_frequency(domain), 6)
        phase     = round(_phase_from_payload(payload), 6)

        now   = datetime.now(timezone.utc)
        age_s = (now - created_at).total_seconds() if created_at else 0.0
        age_s = max(0.0, age_s)
        decay = round(math.exp(-age_s / 3600.0), 6)

        tid = token_id or f"{layer_report.agent}_layer"

        sig = ResonanceSignal(
            token_id   = tid,
            domain     = domain,
            frequency  = frequency,
            amplitude  = round(amplitude, 4),
            phase      = phase,
            decay      = decay,
            confidence = round(confidence, 4),
            risk_flags = risk_flags,
            timestamp  = now.isoformat(),
        )
        return sig.sign()

    def encode_token(
        self,
        event_token:    EventToken,
        created_at:     Optional[datetime] = None,
        parent_freq:    float = 0.0,
        drift_direction: str = "stable",
        drift_score:    float = 0.0,
        alert_level:    str = "NONE",
    ) -> ResonantEventToken:
        """Encode a full EventToken into a signed ResonantEventToken.

        Selects the text layer as primary signal; falls back to the first
        available layer. Passes governance payload for domain/risk inference.
        """
        text_layer = event_token.text
        gov_layer  = event_token.governance
        gov_payload = gov_layer.payload if gov_layer else {}

        if text_layer is not None:
            primary = text_layer
        else:
            # Find first non-None layer
            primary = None
            for attr in ("audio", "video", "physics", "governance"):
                lr = getattr(event_token, attr, None)
                if lr is not None:
                    primary = lr
                    break

        if primary is not None:
            signal = self.encode_layer(
                primary,
                token_id   = event_token.id,
                created_at = created_at,
                gov_payload = gov_payload,
            )
        else:
            # Minimal fallback signal when no layers are active
            now = datetime.now(timezone.utc)
            signal = ResonanceSignal(
                token_id   = event_token.id,
                domain     = "general",
                frequency  = round(domain_to_frequency("general"), 6),
                amplitude  = 0.5,
                phase      = PHASE_STABLE,
                decay      = 1.0,
                confidence = 0.5,
                risk_flags = [],
                timestamp  = now.isoformat(),
            ).sign()

        ret = ResonantEventToken(
            event_token     = event_token,
            resonance       = signal,
            drift_direction = drift_direction,
            drift_score     = round(drift_score, 6),
            alert_level     = alert_level,
            parent_freq     = round(parent_freq, 6),
        )
        return ret.sign()
