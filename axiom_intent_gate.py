"""
AXIOM Intent Gate — ORVL-016 cross-container guard
====================================================
Thin wrapper around :class:`axiom_intent_classifier.IntentClassifier` that
turns a packet into a routing verdict and writes a tamper-evident log
entry for every check.

The gate is the only path that ``axiom_cmaa.ConstitutionalMultiAgentArchitecture``
consults before forwarding a packet between containers (see the
``IntentGateClassification`` concept in
``axiom_files/core/axiom_cmaa.axiom``).

Public API:

    gate = IntentGate(classifier, log_path="axiom_intent_gate_log.jsonl")

    # Direct use:
    result = gate.check(packet)
    if result.intent_class in BLOCK_CLASSES:
        # CMAA already raises IntentViolation — this is just a peek.
        ...

    # As a callable for CMAA's dependency injection:
    orch = ConstitutionalMultiAgentArchitecture(
        hmac_key=key,
        intent_classifier=gate.as_callable(),
    )

Manifest  : axiom-intent-gate-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

BUG-003 : sys.stdout reconfigured to utf-8
BUG-007 : HMAC always finalised with .hexdigest()
BUG-008 : payload strings encoded via .encode("utf-8") before HMAC
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import types as _types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from axiom_intent_classifier import (
    BLOCK_CLASSES,
    INTENT_CLASSES,
    CONFIDENCE_FLOOR,
    IntentClassifier,
    IntentTypingResult,
)


# ── CANNOT_MUTATE constants ────────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
DEFAULT_LOG_PATH: str = "axiom_intent_gate_log.jsonl"
MANIFEST_ID: str = "axiom-intent-gate-v1"

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "DEFAULT_LOG_PATH", "MANIFEST_ID",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)


def _packet_text(packet: Any) -> str:
    """Pull a flat string out of a ConstitutionalPacket or dict-like payload."""
    payload = getattr(packet, "payload", None)
    if payload is None and isinstance(packet, Mapping):
        payload = packet.get("payload") or packet
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        # Concatenate any string-valued leaves for the lexical pass.
        parts: list[str] = []
        for v in payload.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
                parts.extend(s for s in v if isinstance(s, str))
        return " ".join(parts)
    return ""


def _packet_trajectory(packet: Any) -> Optional[Sequence[Sequence[float]]]:
    """Extract the (preflight, mid_chain, final_synthesis) magnitude trace."""
    traj = getattr(packet, "trajectory", None)
    if traj is None and isinstance(packet, Mapping):
        traj = packet.get("trajectory")
    if not traj:
        return None
    # Accept either a list of vectors or a single vector — normalise to list.
    if traj and isinstance(traj[0], (int, float)):
        return [traj]
    return list(traj)


def _packet_pair_id(packet: Any) -> Optional[str]:
    """Pull a bonded-pair id from the packet, if any.

    Recognised locations, in order:
      1. ``packet.pair_id`` attribute
      2. ``packet["pair_id"]`` (Mapping)
      3. ``packet.payload["pair_id"]``
      4. ``packet.metadata["pair_id"]``
    """
    pid = getattr(packet, "pair_id", None)
    if pid:
        return str(pid)
    if isinstance(packet, Mapping):
        if packet.get("pair_id"):
            return str(packet["pair_id"])
        payload = packet.get("payload")
        if isinstance(payload, Mapping) and payload.get("pair_id"):
            return str(payload["pair_id"])
        meta = packet.get("metadata")
        if isinstance(meta, Mapping) and meta.get("pair_id"):
            return str(meta["pair_id"])
    payload = getattr(packet, "payload", None)
    if isinstance(payload, Mapping) and payload.get("pair_id"):
        return str(payload["pair_id"])
    return None


class IntentGate:
    """Cross-container gate that classifies every packet and appends a
    signed log entry. Returns the underlying ``IntentTypingResult`` so the
    CMAA orchestrator can raise its own ``IntentViolation`` while keeping
    sole authority over fleet-level effects (suspension, fallback routing).
    """

    def __init__(
        self,
        classifier: IntentClassifier,
        *,
        log_path: Optional[str] = None,
        bonded_pair_ledger: Any = None,
        companion_panel: Any = None,
    ) -> None:
        if not isinstance(classifier, IntentClassifier):
            raise TypeError("classifier must be an IntentClassifier")
        self._classifier = classifier
        self._log_path = Path(log_path or DEFAULT_LOG_PATH)
        # Optional dependency — a bonded-pair ledger to consult when a
        # packet carries a pair_id. If revoked / not authorised, the
        # gate short-circuits to a HARM verdict before running the
        # lexical classifier, so revoked grants cannot pass even with
        # benign content.
        self._bonded_pair_ledger = bonded_pair_ledger
        # Optional dependency — a three-layer Friend / BestFriend / Mom
        # companion panel.  When wired in, the gate consults the panel
        # ONLY for borderline classifier verdicts (UNCERTAIN, or
        # confidence below ESCALATION_FLOOR) and lets Mom upgrade the
        # verdict on a SAFETY signal.  Confident classifier verdicts
        # — including BLOCK classes — bypass the panel.
        self._companion_panel = companion_panel

    # ── Public API ────────────────────────────────────────────────────────
    def check(self, packet: Any) -> IntentTypingResult:
        # Bonded-pair authority gate — runs FIRST when a ledger is wired
        # in. The whole point of the primitive: revoked grants must be
        # denied even when the payload itself is benign.
        if self._bonded_pair_ledger is not None:
            pair_id = _packet_pair_id(packet)
            if pair_id is not None:
                from axiom_event_token.bonded_pair import is_authorized
                if not is_authorized(self._bonded_pair_ledger, pair_id):
                    current = self._bonded_pair_ledger.current_state(pair_id)
                    state_signal = current or "uninitialised"
                    result = self._classifier.seal_verdict(
                        "HARM", 1.0,
                        ("bonded_pair_revoked",
                         f"pair_id={pair_id}",
                         f"state={state_signal}"),
                    )
                    self._append_log(packet, result)
                    return result

        text = _packet_text(packet)
        traj = _packet_trajectory(packet)
        result = self._classifier.classify(text, trajectory=traj)

        # Companion panel second pass — only for borderline verdicts.
        # The panel never DOWNgrades; it can only escalate UNCERTAIN /
        # low-confidence INFORM to HARM (Mom SAFETY) or CLARIFY (Mom
        # DISTRESS).  Confident verdicts and existing BLOCK_CLASSES
        # results are returned unchanged.
        if self._companion_panel is not None and not result.blocks:
            from axiom_companion_panel import ESCALATION_FLOOR
            borderline = (
                result.intent_class == "UNCERTAIN"
                or result.confidence < ESCALATION_FLOOR
            )
            if borderline:
                panel_verdict = self._companion_panel.evaluate(
                    text, result.intent_class,
                )
                if panel_verdict.upgrade_to is not None:
                    upgraded_signals = tuple(result.signals) + tuple(
                        panel_verdict.signals
                    ) + (f"panel:mom_signal={panel_verdict.mom_signal}",)
                    # Re-seal as the upgraded class.  Confidence is
                    # ceilinged to the panel's certainty (SAFETY = high,
                    # DISTRESS = moderate).
                    upgrade_conf = (
                        0.90 if panel_verdict.safety_escalation else 0.55
                    )
                    result = self._classifier.seal_verdict(
                        panel_verdict.upgrade_to,
                        upgrade_conf,
                        upgraded_signals,
                    )

        self._append_log(packet, result)
        return result

    def as_callable(self) -> Callable[[Any], tuple]:
        """Return a function CMAA can plug in as ``intent_classifier=...``.

        CMAA expects ``(intent_class: str, confidence: float)``.
        """
        def _classify(packet: Any) -> tuple:
            r = self.check(packet)
            return (r.intent_class, float(r.confidence))
        return _classify

    # ── Internals ─────────────────────────────────────────────────────────
    def _append_log(self, packet: Any, result: IntentTypingResult) -> None:
        entry = {
            "manifest_id":   MANIFEST_ID,
            "packet_id":     getattr(packet, "packet_id", None) or (
                packet.get("packet_id") if isinstance(packet, Mapping) else None
            ),
            "source":        getattr(packet, "source", None) or (
                packet.get("source") if isinstance(packet, Mapping) else None
            ),
            "destination":   getattr(packet, "destination", None) or (
                packet.get("destination") if isinstance(packet, Mapping) else None
            ),
            "intent_class":  result.intent_class,
            "confidence":    result.confidence,
            "signals":       list(result.signals),
            "blocked":       result.intent_class in BLOCK_CLASSES,
            "timestamp":     result.timestamp,
            "signature":     result.signature,
        }
        # BUG-003: explicit utf-8
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")
        except OSError:
            # Logging is best-effort — never block the gate on a disk error.
            pass


# ── Production default ────────────────────────────────────────────────────
def default_intent_classifier(
    hmac_key: bytes,
    *,
    log_path: Optional[str] = None,
    bonded_pair_ledger: Any = None,
):
    """Convenience constructor for production callers (e.g. CMAA defaults).

    Returns a callable suitable for ``intent_classifier=...``.

    ``bonded_pair_ledger`` (optional) wires the gate to consult the
    bonded-pair state register before classifying. Packets carrying a
    ``pair_id`` whose state is anything other than ACTIVE_VALIDATED
    are denied with a HARM verdict and a ``bonded_pair_revoked``
    signal. See ``axiom_event_token.bonded_pair`` for the primitive.
    """
    classifier = IntentClassifier(hmac_key)
    gate = IntentGate(classifier, log_path=log_path,
                      bonded_pair_ledger=bonded_pair_ledger)
    return gate.as_callable()
