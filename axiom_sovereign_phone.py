"""
AXIOM Sovereign Phone Architecture (ORVL-019) — software emulator.

The four ASPA chip blocks rendered as Python classes that compose the
existing AXIOM stack:

    NeuralComputeBlock        wraps axiom_intent_classifier.IntentClassifier
    VectorMemoryBlock         wraps axiom_memory_engine.ConstitutionalPacket + LSHIndex
    SecureIdentityBlock       wraps axiom_signing.derive_key (master-key isolation)
    EventMonitor              wraps axiom_os_shield.ConstitutionalOSShield

The ConstitutionalCoprocessor is the gatekeeper. Every outbound query and
every inbound response is driven through `GovernanceCoprocessorEmulator`
from `axiom_anf_emulator` — making the sovereign phone a real workload for
the Axiom Neural Fabric (ORVL-018), not a synthetic benchmark.

Trust  : TRUST_LEVEL = 3  CANNOT_MUTATE
Encoding: UTF-8  BUG-003 compliant
HMAC   : every Decision dataclass is signed; signature is the *only* mutable
         field after construction.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import re
import sys
import types as _types
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Sequence, Tuple, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

TRUST_LEVEL: int = 3
ISOLATION: bool = True

_FROZEN = frozenset({"TRUST_LEVEL", "ISOLATION"})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})


# ── PII redactor — demo-grade, not production DLP ────────────────────────
_PII_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                              "SSN"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),                       "EMAIL"),
    (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),                  "PHONE"),
    (re.compile(r"\b\d+\s+[A-Z][a-z]+\s+(?:St|Ave|Rd|Blvd|Ln)\b"),      "ADDRESS"),
    (re.compile(r"\b(?:diabetes|HIV|cancer|pregnancy|depression)\b",
                flags=re.IGNORECASE),                                    "HEALTH"),
    # "My name is First Last" — only redact when explicitly named.
    (re.compile(r"\b(?:my name is|i am|i'm)\s+([A-Z][a-z]+ [A-Z][a-z]+)\b",
                flags=re.IGNORECASE),                                    "NAME"),
)


def _redact_pii(text: str) -> Tuple[str, List[str]]:
    """Return (redacted_text, categories_hit). Order matters — SSN before PHONE
    since both look numeric. NAME runs after generic patterns so 'John Smith'
    isn't pre-stripped before the 'my name is' shape can match."""
    out = text
    hits: List[str] = []
    for pattern, label in _PII_PATTERNS:
        token = f"[REDACTED:{label}]"
        if label == "NAME":
            # Pattern captures group(1); replace just the captured name.
            def _sub(m, _t=token):
                hits.append(label)
                return m.group(0).replace(m.group(1), _t)
            out = pattern.sub(_sub, out)
        else:
            new_out, n = pattern.subn(token, out)
            if n:
                hits.extend([label] * n)
            out = new_out
    return out, hits


# ── Intent class mapping (classifier vocab → ANF vocab) ──────────────────
# IntentClassifier ships INFORM/CLARIFY/REFUSE/HARM/DECEIVE/UNCERTAIN.
# ANF SparseReasoningCoreEmulator activates on INFORM/REQUEST/EXPLORE/
# MANIPULATE/DECEIVE/HARM. Map between them.
_CLASSIFIER_TO_ANF = {
    "INFORM":    "INFORM",
    "CLARIFY":   "REQUEST",
    "REFUSE":    "DECEIVE",
    "HARM":      "HARM",
    "DECEIVE":   "DECEIVE",
    "UNCERTAIN": "EXPLORE",
}


# ── Signed decision payloads ─────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")  # BUG-008


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac_lib.new(key, _canonical(payload), hashlib.sha256).hexdigest()  # BUG-007


def _verify(key: bytes, payload: Mapping[str, Any], signature: str) -> bool:
    expected = _sign(key, payload)
    return hmac_lib.compare_digest(expected, signature)


@dataclass(frozen=True)
class OutboundDecision:
    query_id:        str
    redacted_text:   str
    intent_class:    str
    confidence:      float
    pii_categories:  Tuple[str, ...]
    anf_distance:    float
    anf_cores_active: int
    anf_gate_fired:  bool
    anf_signature:   str
    timestamp:       str
    signature:       str = ""


@dataclass(frozen=True)
class InboundDecision:
    response_id:        str
    intent_class:       str
    confidence:         float
    monotonic_pass:     bool
    privacy_injection:  bool
    timestamp:          str
    signature:          str = ""


@dataclass(frozen=True)
class SovereignAlert:
    """Phone-side block payload. Mirrors axiom_intent_gate.SuspendAlert shape
    but stays in the phone module so callers can `except SovereignAlert` cleanly."""
    gate:           str            # "outbound" | "inbound"
    intent_class:   str
    confidence:     float
    level:          int            # 1..4
    reason:         str
    timestamp:      str
    signature:      str = ""


# ── Neural Compute Block ─────────────────────────────────────────────────
class NeuralComputeBlock:
    """On-device intent pre-classification. Thin wrapper around
    IntentClassifier — no model call, no cloud hit."""

    def __init__(self, classifier):
        self._classifier = classifier

    def pre_classify(self, text: str, trajectory=None):
        return self._classifier.classify(text, trajectory=trajectory)


# ── Vector Memory Block ──────────────────────────────────────────────────
class VectorMemoryBlock:
    """On-device constitutional-packet store. ConstitutionalPackets in,
    cosine-similar packets out. Never transmitted to cloud."""

    def __init__(self):
        from axiom_memory_engine import LSHIndex
        self._index = LSHIndex()
        self._packets: List[Any] = []

    def store(self, packet) -> int:
        self._index.index(packet)
        self._packets.append(packet)
        return len(self._packets)

    def recall(self, query_vec: Sequence[float], k: int = 5):
        return self._index.retrieve(list(query_vec), k=k)

    @property
    def depth(self) -> int:
        return len(self._packets)


# ── Secure Identity Block ────────────────────────────────────────────────
class SecureIdentityBlock:
    """Hardware-bound key analog. Derives a phone-scoped key from
    AXIOM_MASTER_KEY via axiom_signing.derive_key and refuses to expose it
    through __repr__ / __str__."""

    def __init__(self):
        from axiom_signing import derive_key
        self._key = derive_key(b"axiom-aspa-device-v1")

    def sign(self, payload: Mapping[str, Any]) -> str:
        return _sign(self._key, payload)

    def verify(self, payload: Mapping[str, Any], signature: str) -> bool:
        return _verify(self._key, payload, signature)

    def fingerprint(self) -> str:
        """First 8 hex of HMAC(key, b"fingerprint") — safe to display."""
        return hmac_lib.new(self._key, b"fingerprint",
                            hashlib.sha256).hexdigest()[:8]

    def __repr__(self) -> str:
        return f"<SecureIdentityBlock fp={self.fingerprint()}>"

    __str__ = __repr__


# ── Event Monitor ────────────────────────────────────────────────────────
class EventMonitor:
    """Mobile OS Shield — adapts ConstitutionalOSShield to app-trajectory
    monitoring. The OS Shield's L1-L4 logic is exactly what we need; we
    just feed it ProcessSnapshot-shaped data for mobile apps instead of
    desktop processes."""

    def __init__(self, hmac_key: bytes):
        from axiom_os_shield import ConstitutionalOSShield, ProcessManifold
        self._shield = ConstitutionalOSShield(hmac_key=hmac_key)
        self._manifolds: dict = {}
        self._ProcessManifold = ProcessManifold
        self._suspended: set = set()  # apps the shield has suspended (L3+)

    def baseline(self, app: str, snapshots: list) -> None:
        m = self._manifolds.setdefault(app, self._ProcessManifold(app, "APP"))
        m.establish_baseline(snapshots)

    def record_app_event(self, app: str, snapshot) -> dict:
        """Score one app snapshot. Returns an escalation event dict if a
        threshold is crossed, else {'level': 0, 'action': 'normal'}."""
        m = self._manifolds.get(app)
        if m is None or not m.baseline:
            return {"level": 0, "action": "learning", "app": app}
        dist = m.measure_distance(snapshot)
        level = self._shield.determine_level(dist)
        if level == 0:
            return {"level": 0, "action": "normal", "app": app, "distance": dist}
        fp = self._shield.compute_fp_confidence(snapshot, m)
        event = self._shield.escalate(level, snapshot, dist, fp)
        if level >= 3:
            self._suspended.add(app)
        return event

    @property
    def suspended(self) -> frozenset:
        return frozenset(self._suspended)


# ── Constitutional Coprocessor ───────────────────────────────────────────
class ConstitutionalCoprocessor:
    """The gatekeeper. Outbound and inbound traffic both pass through here.
    Every call drives the ANF emulator's GovernanceCoprocessorEmulator.process()
    — that's the "testing ground" wiring."""

    def __init__(self, neural: NeuralComputeBlock,
                 memory: VectorMemoryBlock,
                 identity: SecureIdentityBlock,
                 anf_emulator,
                 fused_rom: Optional[Mapping[str, Any]] = None):
        self.neural   = neural
        self.memory   = memory
        self.identity = identity
        self.anf      = anf_emulator
        self._fused_rom = dict(fused_rom or {
            "block_classes":      ["HARM", "DECEIVE"],
            "outbound_l3_floor":  0.05,
            "inbound_l2_floor":   0.10,
        })
        self.anf_calls = 0  # counter for the "ANF was actually invoked" invariant
        # ── Session-trajectory escalation ──────────────────────────────
        # Tracks blocks per session_id so consecutive HARM/DECEIVE hits
        # within one call step L1 → L2 → L3 (the ORVL-019 §4 timeline).
        # When session_id is None we fall back to single-call L3 blocks —
        # backward-compat for callers that don't track sessions.
        self._session_blocks: dict = {}

    def _trajectory_level(self, session_id: Optional[str],
                          confidence: float) -> int:
        """Return the L1-L4 level for a block. Without a session_id the
        block is treated as one-shot and lands at L3 (existing behavior).
        With a session_id the level grows with the number of blocks
        already seen in that session, but jumps straight to L3 on a
        high-confidence single hit (3+ pattern matches → conf ≥ 0.85)."""
        if confidence >= 0.85:
            return 3  # very-strong single hit overrides trajectory
        if session_id is None:
            return 3
        count = self._session_blocks.get(session_id, 0) + 1
        self._session_blocks[session_id] = count
        return min(3, count)

    def reset_session(self, session_id: str) -> None:
        """Clear a session's block counter (e.g. end-of-call hook)."""
        self._session_blocks.pop(session_id, None)

    # Map classifier intent to an ANF-vocabulary class.
    @staticmethod
    def _anf_class(classifier_class: str) -> str:
        return _CLASSIFIER_TO_ANF.get(classifier_class, "EXPLORE")

    # Build a deterministic 32-D vector from an IntentTypingResult so the
    # ANF emulator can drive its pipeline. The intent vector itself is
    # implicit in the trajectory the classifier was given (or absent → we
    # synthesize three magnitudes from confidence so the monotonic gate
    # still has something to compare).
    @staticmethod
    def _stage_vecs(intent_result) -> Tuple[List[float], List[float], List[float]]:
        c = float(intent_result.confidence)
        # PREFLIGHT: low magnitude; FINAL_SYNTHESIS: scaled by confidence.
        # If the classifier flagged HARM/DECEIVE, intentionally produce a
        # *decreasing* trajectory so the monotonic gate fires.
        descending = intent_result.intent_class in ("HARM", "DECEIVE")
        if descending:
            mags = (0.9 * c, 0.6 * c, 0.3 * c)
        else:
            mags = (0.3 * c, 0.6 * c, 0.9 * c)
        VECTOR_DIM = 32
        out = []
        for m in mags:
            v = [m] * VECTOR_DIM
            out.append(v)
        return tuple(out)  # type: ignore[return-value]

    def outbound_gate(self, text: str, trajectory=None,
                      session_id: Optional[str] = None
                      ) -> Union[OutboundDecision, SovereignAlert]:
        # 1. NCB pre-classify
        ir = self.neural.pre_classify(text, trajectory=trajectory)

        # 2. PII redaction (independent of intent — PII goes either way)
        redacted, pii_hits = _redact_pii(text)

        # 3. Hard block on HARM/DECEIVE before any cloud call
        if ir.intent_class in self._fused_rom["block_classes"]:
            level = self._trajectory_level(session_id, float(ir.confidence))
            alert_payload = {
                "gate":         "outbound",
                "intent_class": ir.intent_class,
                "confidence":   round(ir.confidence, 4),
                "level":        level,
                "reason":       f"outbound {ir.intent_class} blocked at coprocessor",
                "timestamp":    datetime.now(timezone.utc).isoformat(),
            }
            sig = self.identity.sign(alert_payload)
            return SovereignAlert(**alert_payload, signature=sig)

        # 4. Drive the ANF emulator — the "testing ground" call.
        pre, mid, fin = self._stage_vecs(ir)
        anf_result = self.anf.process(pre, mid, fin, self._anf_class(ir.intent_class))
        self.anf_calls += 1

        # 5. Sign the outbound decision with the device key.
        payload = {
            "query_id":         uuid.uuid4().hex,
            "redacted_text":    redacted,
            "intent_class":     ir.intent_class,
            "confidence":       round(ir.confidence, 4),
            "pii_categories":   tuple(sorted(set(pii_hits))),
            "anf_distance":     anf_result["distance"],
            "anf_cores_active": anf_result["cores_active"],
            "anf_gate_fired":   anf_result["gate_fired"],
            "anf_signature":    anf_result["hmac"],
            "timestamp":        datetime.now(timezone.utc).isoformat(),
        }
        sig = self.identity.sign(payload)
        return OutboundDecision(**payload, signature=sig)

    def inbound_gate(self, text: str, trajectory=None,
                     redacted_categories: Sequence[str] = (),
                     session_id: Optional[str] = None
                     ) -> Union[InboundDecision, SovereignAlert]:
        ir = self.neural.pre_classify(text, trajectory=trajectory)

        # Block manipulative / deceptive cloud responses before display.
        if ir.intent_class in self._fused_rom["block_classes"]:
            # Inbound starts one rung above outbound — a cloud response we
            # don't trust is intrinsically worse than a user query we want
            # to filter, so the first session block fires at L2 minimum.
            base = self._trajectory_level(session_id, float(ir.confidence))
            level = max(2, base) if session_id is not None else 2
            alert_payload = {
                "gate":         "inbound",
                "intent_class": ir.intent_class,
                "confidence":   round(ir.confidence, 4),
                "level":        level,
                "reason":       f"inbound {ir.intent_class} flagged before display",
                "timestamp":    datetime.now(timezone.utc).isoformat(),
            }
            sig = self.identity.sign(alert_payload)
            return SovereignAlert(**alert_payload, signature=sig)

        # Monotonic-gate check on the response trajectory (if supplied).
        monotonic_pass = True
        if trajectory is not None and len(trajectory) >= 2:
            from axiom_anf_emulator import MonotonicGateEmulator
            gate = MonotonicGateEmulator()
            # Fire on any non-monotonic step; pass iff no fire.
            fired = False
            for i in range(len(trajectory) - 1):
                if gate.fire_interrupt(list(trajectory[i]),
                                       list(trajectory[i + 1])):
                    fired = True
                    break
            monotonic_pass = not fired

        # Privacy-injection check: does the response mention a PII category
        # we redacted on the way out? Heuristic — string match on the label.
        lower = text.lower()
        privacy_injection = any(cat.lower() in lower
                                for cat in redacted_categories)

        payload = {
            "response_id":      uuid.uuid4().hex,
            "intent_class":     ir.intent_class,
            "confidence":       round(ir.confidence, 4),
            "monotonic_pass":   monotonic_pass,
            "privacy_injection": privacy_injection,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
        }
        sig = self.identity.sign(payload)
        return InboundDecision(**payload, signature=sig)


# ── Façade ───────────────────────────────────────────────────────────────
class SovereignPhone:
    """One-call constructor — mirrors axiom_cmaa.bootstrap_default()."""

    def __init__(self):
        from axiom_intent_classifier import IntentClassifier
        from axiom_signing import derive_key
        from axiom_anf_emulator import GovernanceCoprocessorEmulator

        classifier_key = derive_key(b"axiom-aspa-classifier-v1")
        anf_key        = derive_key(b"axiom-aspa-anf-v1")
        shield_key     = derive_key(b"axiom-aspa-shield-v1")

        self.neural   = NeuralComputeBlock(IntentClassifier(classifier_key))
        self.memory   = VectorMemoryBlock()
        self.identity = SecureIdentityBlock()
        self.events   = EventMonitor(hmac_key=shield_key)
        self.anf      = GovernanceCoprocessorEmulator(
            hmac_key=anf_key,
            fused_rom={"monotonic_gate": True, "sovereign_levels": 4,
                       "cannot_mutate": True},
        )
        self.coprocessor = ConstitutionalCoprocessor(
            neural=self.neural,
            memory=self.memory,
            identity=self.identity,
            anf_emulator=self.anf,
        )

    def status(self) -> dict:
        return {
            "device_fingerprint": self.identity.fingerprint(),
            "memory_depth":       self.memory.depth,
            "events_suspended":   sorted(self.events.suspended),
            "anf_calls":          self.coprocessor.anf_calls,
            "trust_level":        TRUST_LEVEL,
        }
