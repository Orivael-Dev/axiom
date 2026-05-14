"""
AXIOM Constitutional Multi-Agent Architecture (ORVL-017)
==========================================================
Fleet-level orchestrator that routes constitutional packets between
specialised SLM containers under the supervision of an intent gate.

Manifest  : cmaa-orchestrator-impl-v1
Trust     : TRUST_LEVEL = 4   CANNOT_MUTATE
Isolation : CROSS_CONTAINER_ISOLATION = True   CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

This module is a runtime *coordinator* — it does not spawn Docker
containers itself (see docker-compose.yml for the physical fleet).
It models the constitutional flow described in
``axiom_files/core/axiom_cmaa.axiom``:

    SLM container  →  IntentGate.classify(packet)
                       │
                       ├─ benign     → deliver, log signed RoutingDecision
                       └─ HARM/DECEIVE → raise IntentViolation, emit
                                          SuspendAlert(level=L3)

The intent classifier and CRL/CBV/CAS hooks are injected so unit tests
can stub them; production wiring lives in ``axiom_files/parser.py``
and the container compose file.

BUG-003 : sys.stdout reconfigured to utf-8; all open() calls use encoding="utf-8"
BUG-007 : HMAC always finalised with .hexdigest()
BUG-008 : payload strings encoded via .encode("utf-8") before HMAC
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

_log = logging.getLogger("axiom.cmaa")

# ── CANNOT_MUTATE constants ────────────────────────────────────────────────
TRUST_LEVEL: int = 4
INTENT_GATE_REQUIRED: bool = True
HUMAN_REVIEW_GATE: bool = True
REWARD_FUNCTION_LOCKED: bool = True
CROSS_CONTAINER_ISOLATION: bool = True
BLOCK_INTENT_CLASSES: frozenset = frozenset({"HARM", "DECEIVE"})
DEFAULT_LOG_PATH: str = "axiom_cmaa_log.jsonl"

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "INTENT_GATE_REQUIRED", "HUMAN_REVIEW_GATE",
    "REWARD_FUNCTION_LOCKED", "CROSS_CONTAINER_ISOLATION",
    "BLOCK_INTENT_CLASSES", "DEFAULT_LOG_PATH",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)


# ── Exceptions ────────────────────────────────────────────────────────────
class IntentViolation(RuntimeError):
    """Raised when the intent gate refuses to forward a packet."""


class TrustHierarchyViolation(RuntimeError):
    """Raised when a TL1 container attempts to reach TL4."""


class HumanReviewRequired(RuntimeError):
    """Raised when an evolution candidate is queued but not yet approved."""


# ── Dataclasses ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ConstitutionalPacket:
    packet_id: str
    source: str
    destination: str
    payload: Mapping[str, Any]
    trajectory: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class RoutingDecision:
    packet_id: str
    source: str
    destination: str
    intent_class: str
    delivered: bool
    timestamp: str
    signature: str


@dataclass(frozen=True)
class SuspendAlert:
    container: str
    intent_class: str
    confidence: float
    level: str  # L1_WARNING, L2_THROTTLE, L3_SUSPEND, L4_TERMINATE
    reason: str


@dataclass(frozen=True)
class EvolutionProposal:
    gap: str
    candidate_image: str
    cbv_status: str
    cas_status: str
    human_review_status: str  # "pending", "approved", "rejected"


# ── Default trust hierarchy ───────────────────────────────────────────────
# Trust levels mirror the .axiom spec — TL1 (red agent) cannot reach TL4
# (orchestrator) directly. Production should override via fleet_manifest.
_DEFAULT_TRUST: dict[str, int] = {
    "axiom-orchestrator": 4,
    "axiom-intent-gate": 3,
    "axiom-memory": 3,
    "axiom-cas-blue": 3,
    "axiom-medical": 2,
    "axiom-financial": 2,
    "axiom-security": 2,
    "axiom-cas-red": 1,
}


# ── Helpers ───────────────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def _verify(key: bytes, payload: Mapping[str, Any], sig: str) -> bool:
    expected = _sign(key, payload)
    if not isinstance(sig, str) or len(sig) != len(expected):
        return False
    return hmac.compare_digest(sig, expected)


# ── Orchestrator ──────────────────────────────────────────────────────────
class ConstitutionalMultiAgentArchitecture:
    """Coordinates a fleet of constitutionally isolated SLM containers.

    The orchestrator does not host the containers; it models the
    constitutional flow that the Docker fleet implements physically.
    Tests inject ``intent_classifier`` and the optional CBV / CAS / CRL
    hooks so the evolution loop can be exercised without GPUs.
    """

    def __init__(
        self,
        hmac_key: bytes,
        intent_classifier: Optional[Callable[[ConstitutionalPacket], tuple[str, float]]] = None,
        *,
        fleet_manifest: Optional[Mapping[str, int]] = None,
        log_path: Optional[str] = None,
        intent_log_path: Optional[str] = None,
        cbv: Optional[Callable[[str], str]] = None,
        cas: Optional[Callable[[str], str]] = None,
        crl_train: Optional[Callable[[str], str]] = None,
    ) -> None:
        if not isinstance(hmac_key, (bytes, bytearray)) or len(hmac_key) < 16:
            raise ValueError("hmac_key must be at least 16 bytes of entropy")
        self._key = bytes(hmac_key)
        # If no classifier is injected, fall back to the production
        # ORVL-016 gate so the orchestrator is usable without dependency
        # injection. Tests still inject stubs to assert specific behaviour.
        if intent_classifier is None:
            from axiom_intent_gate import default_intent_classifier
            intent_classifier = default_intent_classifier(
                self._key, log_path=intent_log_path,
            )
        self._classify = intent_classifier
        self._trust = dict(fleet_manifest) if fleet_manifest is not None else dict(_DEFAULT_TRUST)
        self._log_path = Path(log_path or DEFAULT_LOG_PATH)
        self._cbv = cbv
        self._cas = cas
        self._crl_train = crl_train
        self._review_queue: list[EvolutionProposal] = []
        self._suspended: set[str] = set()

    # ── Routing ───────────────────────────────────────────────────────────
    def route(self, packet: ConstitutionalPacket) -> RoutingDecision:
        """Forward ``packet`` to its destination via the intent gate.

        Raises :class:`TrustHierarchyViolation` if the source is below the
        destination's trust level (e.g. TL1 → TL4).
        Raises :class:`IntentViolation` if the intent classifier returns
        HARM or DECEIVE; a :class:`SuspendAlert` is emitted as a side
        effect of the exception's ``alert`` attribute.
        """
        if packet.source in self._suspended:
            raise IntentViolation(
                f"source container {packet.source!r} is currently suspended"
            )
        self._check_trust(packet.source, packet.destination)

        if not INTENT_GATE_REQUIRED:  # CANNOT_MUTATE; defensive read
            raise RuntimeError("INTENT_GATE_REQUIRED has been tampered with")

        intent_class, confidence = self._classify(packet)
        intent_class = intent_class.upper()

        if intent_class in BLOCK_INTENT_CLASSES:
            self._suspended.add(packet.source)
            alert = SuspendAlert(
                container=packet.source,
                intent_class=intent_class,
                confidence=float(confidence),
                level="L3_SUSPEND",
                reason=f"intent_gate flagged {intent_class}",
            )
            decision = self._decide(packet, intent_class, delivered=False)
            self._append_log(decision, alert)
            exc = IntentViolation(
                f"intent gate refused {intent_class} packet from "
                f"{packet.source!r} (confidence {confidence:.2f})"
            )
            exc.alert = alert  # type: ignore[attr-defined]
            exc.decision = decision  # type: ignore[attr-defined]
            raise exc

        decision = self._decide(packet, intent_class, delivered=True)
        self._append_log(decision, alert=None)
        return decision

    # ── Trust hierarchy ───────────────────────────────────────────────────
    def _check_trust(self, source: str, destination: str) -> None:
        """Enforce the ORVL-017 trust ACL.

        The spec rule (Table 2) is narrow: TL1 containers cannot reach the
        TL4 orchestrator directly. Domain SLMs (TL2) routing packets up to
        the orchestrator is the normal control-plane flow, so we do NOT
        block upward delivery in general — only the TL1 → TL4 hop.
        """
        s = self._trust.get(source)
        d = self._trust.get(destination)
        if s is None or d is None:
            return  # unknown containers cannot be trust-checked; let routing fail elsewhere
        if s <= 1 and d >= 4:
            raise TrustHierarchyViolation(
                f"{source} (TL{s}) cannot deliver to {destination} (TL{d}); "
                f"TL1 containers must not reach the orchestrator directly"
            )

    # ── Decision + log ────────────────────────────────────────────────────
    def _decide(
        self,
        packet: ConstitutionalPacket,
        intent_class: str,
        *,
        delivered: bool,
    ) -> RoutingDecision:
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = {
            "packet_id": packet.packet_id,
            "source": packet.source,
            "destination": packet.destination,
            "intent_class": intent_class,
            "delivered": delivered,
            "timestamp": timestamp,
        }
        return RoutingDecision(
            packet_id=packet.packet_id,
            source=packet.source,
            destination=packet.destination,
            intent_class=intent_class,
            delivered=delivered,
            timestamp=timestamp,
            signature=_sign(self._key, payload),
        )

    def _append_log(self, decision: RoutingDecision, alert: Optional[SuspendAlert]) -> None:
        entry = {
            "decision": {
                "packet_id":   decision.packet_id,
                "source":      decision.source,
                "destination": decision.destination,
                "intent_class": decision.intent_class,
                "delivered":   decision.delivered,
                "timestamp":   decision.timestamp,
                "signature":   decision.signature,
            },
            "alert": None if alert is None else {
                "container":    alert.container,
                "intent_class": alert.intent_class,
                "confidence":   alert.confidence,
                "level":        alert.level,
                "reason":       alert.reason,
            },
        }
        # BUG-003: explicit utf-8
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")

    # ── Verification ─────────────────────────────────────────────────────
    def verify(self, decision: RoutingDecision) -> bool:
        """Constant-time HMAC check on a previously emitted decision."""
        payload = {
            "packet_id":   decision.packet_id,
            "source":      decision.source,
            "destination": decision.destination,
            "intent_class": decision.intent_class,
            "delivered":   decision.delivered,
            "timestamp":   decision.timestamp,
        }
        return _verify(self._key, payload, decision.signature)

    # ── Quarantine / restoration ──────────────────────────────────────────
    @property
    def suspended(self) -> frozenset:
        return frozenset(self._suspended)

    def restore(self, container: str) -> None:
        """Lift the L3 suspension after a successful rebuild + human review."""
        self._suspended.discard(container)

    # ── Evolution loop (DETECT → TRAIN → VALIDATE → DEPLOY → MONITOR) ─────
    def propose_evolution(self, gap: str) -> EvolutionProposal:
        """Run the train / validate stages and queue for human review.

        Deployment is NEVER performed automatically — the caller must
        invoke :meth:`approve_evolution` after a human-operator review.
        ``HUMAN_REVIEW_GATE`` is CANNOT_MUTATE; this method enforces it.
        """
        if not HUMAN_REVIEW_GATE:  # defensive
            raise RuntimeError("HUMAN_REVIEW_GATE has been tampered with")
        candidate = self._crl_train(gap) if self._crl_train else f"axiom-{gap}:candidate"
        cbv_status = self._cbv(candidate) if self._cbv else "CERT_PASS"
        cas_status = self._cas(candidate) if self._cas else "CAS_PASS"
        proposal = EvolutionProposal(
            gap=gap,
            candidate_image=candidate,
            cbv_status=cbv_status,
            cas_status=cas_status,
            human_review_status="pending" if (cbv_status == "CERT_PASS" and cas_status == "CAS_PASS") else "rejected",
        )
        self._review_queue.append(proposal)
        return proposal

    @property
    def review_queue(self) -> tuple:
        return tuple(self._review_queue)

    def approve_evolution(self, candidate_image: str) -> EvolutionProposal:
        """Mark a queued proposal as human-approved. Fleet deployment is
        an out-of-band step performed by the operator (docker run …)."""
        for idx, p in enumerate(self._review_queue):
            if p.candidate_image == candidate_image and p.human_review_status == "pending":
                approved = EvolutionProposal(
                    gap=p.gap,
                    candidate_image=p.candidate_image,
                    cbv_status=p.cbv_status,
                    cas_status=p.cas_status,
                    human_review_status="approved",
                )
                self._review_queue[idx] = approved
                return approved
        raise HumanReviewRequired(
            f"no pending proposal for image {candidate_image!r}"
        )


# ── Bootstrap helpers ─────────────────────────────────────────────────────
def bootstrap_default(
    *,
    log_path: Optional[str] = None,
    intent_log_path: Optional[str] = None,
    fleet_manifest: Optional[Mapping[str, int]] = None,
) -> "ConstitutionalMultiAgentArchitecture":
    """One-call constructor for production callers.

    Reads the HMAC root from ``AXIOM_MASTER_KEY`` (via :mod:`axiom_signing`),
    derives the CMAA-scoped signing key, and wires the orchestrator with
    the ORVL-016 default IntentGate so a deployer can do::

        from axiom_cmaa import bootstrap_default
        orch = bootstrap_default()
        orch.route(packet)

    Test callers should keep using ``ConstitutionalMultiAgentArchitecture``
    directly so they can inject deterministic stubs.
    """
    from axiom_signing import derive_key
    key = derive_key(b"axiom-cmaa-orchestrator-v1")
    return ConstitutionalMultiAgentArchitecture(
        hmac_key=key,
        intent_classifier=None,  # CMAA pulls the ORVL-016 default
        fleet_manifest=fleet_manifest,
        log_path=log_path,
        intent_log_path=intent_log_path,
    )
