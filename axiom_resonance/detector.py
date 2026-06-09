"""ResonanceDetector — phase-conflict and amplitude-spike anomaly detection.

Stateful detector with a rolling EMA baseline.  The first token initialises
the baseline.  Subsequent tokens are compared against it.

Detection priority (checked in order, first match wins):
  1. PHASE_CONFLICT + AMPLITUDE_SPIKE → CRITICAL
  2. PHASE_CONFLICT alone              → HIGH
  3. AMPLITUDE_SPIKE alone             → HIGH
  4. DECAY_MISMATCH                    → MEDIUM
  5. None of the above                 → NONE (normal)

Phase-conflict is the primary zero-day / injection detection mechanism:
an attacker sending INFORM (phase=0) immediately followed by HARM (phase=π)
in rapid succession produces a phase delta of π >> π/3, triggering
PHASE_CONFLICT HIGH or CRITICAL depending on whether amplitude also spiked.

AnomalyAlert is HMAC-signed under ALERT_KEY_NS so downstream consumers can
verify that the alert was genuinely produced by this detector module.
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
from axiom_resonance.token import ResonanceSignal, ResonantEventToken

ALERT_KEY_NS = b"axiom-resonance-alert-v1"

_ALERT_KEY: Optional[bytes] = None


def _alert_key() -> bytes:
    global _ALERT_KEY
    if _ALERT_KEY is None:
        _ALERT_KEY = derive_key(ALERT_KEY_NS)
    return _ALERT_KEY


def _canonical(d: dict) -> bytes:
    payload = {k: v for k, v in d.items() if k != "signature"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _hmac_sign(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


# ── Detection thresholds ───────────────────────────────────────────────────────

PHASE_CONFLICT_THRESHOLD  = math.pi / 3   # 60° — CANNOT_MUTATE intent
AMPLITUDE_SPIKE_THRESHOLD = 0.4           # absolute deviation from EMA baseline
DECAY_FLOOR               = 0.3           # below this is "stale"
DECAY_MISMATCH_AMP_FLOOR  = 0.15          # stale token must also have elevated amp

# EMA weights for the rolling baseline
_EMA_KEEP = 0.7
_EMA_NEW  = 0.3


# ── AnomalyAlert ──────────────────────────────────────────────────────────────


@dataclass
class AnomalyAlert:
    """Signed anomaly detection result.

    Fields
    ------
    alert_type            "PHASE_CONFLICT"|"AMPLITUDE_SPIKE"|"DECAY_MISMATCH"|"NONE".
    severity              "CRITICAL"|"HIGH"|"MEDIUM"|"LOW"|"NONE".
    phase_conflict_score  |new.phase - baseline.phase| (0.0 if no baseline).
    amplitude_deviation   |new.amplitude - baseline.amplitude| (0.0 if no baseline).
    description           Human-readable explanation.
    token_id              event_token.id from the triggering ResonantEventToken.
    signature             HMAC-SHA256 under ALERT_KEY_NS.
    """
    alert_type:           str
    severity:             str
    phase_conflict_score: float
    amplitude_deviation:  float
    description:          str
    token_id:             str
    signature:            str = ""

    def _as_dict(self) -> dict:
        return {
            "alert_type":           self.alert_type,
            "severity":             self.severity,
            "phase_conflict_score": self.phase_conflict_score,
            "amplitude_deviation":  self.amplitude_deviation,
            "description":          self.description,
            "token_id":             self.token_id,
            "signature":            self.signature,
        }

    def sign(self) -> "AnomalyAlert":
        d   = self._as_dict()
        sig = _hmac_sign(_canonical(d), _alert_key())
        return AnomalyAlert(
            alert_type           = self.alert_type,
            severity             = self.severity,
            phase_conflict_score = self.phase_conflict_score,
            amplitude_deviation  = self.amplitude_deviation,
            description          = self.description,
            token_id             = self.token_id,
            signature            = sig,
        )

    def verify(self) -> bool:
        if not self.signature:
            return False
        d        = self._as_dict()
        expected = _hmac_sign(_canonical(d), _alert_key())
        return hmac.compare_digest(self.signature, expected)

    @property
    def is_anomaly(self) -> bool:
        return self.alert_type != "NONE"


# ── ResonanceDetector ─────────────────────────────────────────────────────────


class ResonanceDetector:
    """Stateful anomaly detector over a stream of ResonantEventTokens.

    The rolling EMA baseline is updated AFTER each call to detect() so
    that the detection is always compared against the prior state.
    Callers should call detect() then update_baseline() (or let
    ResonanceLayer.run() do both in order).

    State
    -----
    _baseline  ResonanceSignal | None — rolling EMA of past signals.
               Not signed (runtime state, not audit data).
    """

    def __init__(self) -> None:
        self._baseline: Optional[ResonanceSignal] = None

    def detect(self, token: ResonantEventToken) -> AnomalyAlert:
        """Compare token's resonance signal against the EMA baseline.

        Returns a signed AnomalyAlert.  If no baseline exists yet,
        initialises the baseline and returns a NONE alert.
        """
        if self._baseline is None:
            self.update_baseline(token)
            return AnomalyAlert(
                alert_type           = "NONE",
                severity             = "NONE",
                phase_conflict_score = 0.0,
                amplitude_deviation  = 0.0,
                description          = "Baseline initialised",
                token_id             = token.event_token.id,
            ).sign()

        sig            = token.resonance
        baseline       = self._baseline
        phase_conflict = abs(sig.phase - baseline.phase)
        amplitude_dev  = abs(sig.amplitude - baseline.amplitude)

        if phase_conflict > PHASE_CONFLICT_THRESHOLD and amplitude_dev > 0.2:
            alert_type  = "PHASE_CONFLICT"
            severity    = "CRITICAL"
            description = (
                "Token out-of-phase with baseline and amplitude spike — novel threat signal"
            )
        elif phase_conflict > PHASE_CONFLICT_THRESHOLD:
            alert_type  = "PHASE_CONFLICT"
            severity    = "HIGH"
            description = (
                "Token out-of-phase with baseline — possible novel threat signal"
            )
        elif amplitude_dev > AMPLITUDE_SPIKE_THRESHOLD:
            alert_type  = "AMPLITUDE_SPIKE"
            severity    = "HIGH"
            description = "Amplitude spike detected — elevated risk signal"
        elif sig.decay < DECAY_FLOOR and amplitude_dev > DECAY_MISMATCH_AMP_FLOOR:
            alert_type  = "DECAY_MISMATCH"
            severity    = "MEDIUM"
            description = (
                f"Stale token (decay={sig.decay:.3f}) with elevated amplitude — "
                "signal may be replayed"
            )
        else:
            alert_type  = "NONE"
            severity    = "NONE"
            description = "Signal within normal baseline range"

        return AnomalyAlert(
            alert_type           = alert_type,
            severity             = severity,
            phase_conflict_score = round(phase_conflict, 6),
            amplitude_deviation  = round(amplitude_dev, 6),
            description          = description,
            token_id             = token.event_token.id,
        ).sign()

    def update_baseline(self, token: ResonantEventToken) -> None:
        """Update the rolling EMA baseline from the new token's resonance signal.

        EMA formula: baseline.X = 0.7 * old.X + 0.3 * new.X for all floats.
        The domain field tracks the most recent token's domain.
        The updated baseline is NOT signed (runtime state only).
        """
        new = token.resonance
        if self._baseline is None:
            self._baseline = ResonanceSignal(
                token_id   = "baseline",
                domain     = new.domain,
                frequency  = new.frequency,
                amplitude  = new.amplitude,
                phase      = new.phase,
                decay      = new.decay,
                confidence = new.confidence,
                risk_flags = [],
                timestamp  = new.timestamp,
            )
            return

        old = self._baseline
        self._baseline = ResonanceSignal(
            token_id   = "baseline",
            domain     = new.domain,
            frequency  = _EMA_KEEP * old.frequency  + _EMA_NEW * new.frequency,
            amplitude  = _EMA_KEEP * old.amplitude  + _EMA_NEW * new.amplitude,
            phase      = _EMA_KEEP * old.phase      + _EMA_NEW * new.phase,
            decay      = _EMA_KEEP * old.decay      + _EMA_NEW * new.decay,
            confidence = _EMA_KEEP * old.confidence + _EMA_NEW * new.confidence,
            risk_flags = [],
            timestamp  = new.timestamp,
        )
