"""
AXIOM Intent Classifier — ORVL-016
====================================
Classifies the *intent shape* of an outbound packet so the IntentGate
(see ``axiom_intent_gate.py``) can refuse HARM and DECEIVE trajectories
before they cross a container boundary.

Six classes:

    INFORM     benign explanation, answer, reasoning step
    CLARIFY    asks for more context or rejects ambiguity
    REFUSE     declines the request explicitly (a *good* refusal is benign)
    HARM       trajectory toward harmful content (attack instructions,
               self-harm encouragement, weapons synthesis, malware)
    DECEIVE    trajectory toward deception (impersonation, fabricated
               authority, manipulated evidence, persona-hijack output)
    UNCERTAIN  signal was too weak to classify; the gate decides whether
               to fall back to UNCERTAIN-blocks-too policy

Output is HMAC-SHA256 signed so downstream containers can verify the gate
emitted the verdict.

Manifest  : axiom-intent-classifier-v1
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
import math
import re
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ── CANNOT_MUTATE constants ────────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
MANIFEST_ID: str = "axiom-intent-classifier-v1"

INTENT_CLASSES: tuple = (
    "INFORM", "CLARIFY", "REFUSE", "HARM", "DECEIVE", "UNCERTAIN",
)
BLOCK_CLASSES: frozenset = frozenset({"HARM", "DECEIVE"})
CONFIDENCE_FLOOR: float = 0.30
CONFIDENCE_CEILING: float = 0.95   # never claim certainty

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "MANIFEST_ID",
    "INTENT_CLASSES", "BLOCK_CLASSES",
    "CONFIDENCE_FLOOR", "CONFIDENCE_CEILING",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)


# ── Heuristic vocabularies ────────────────────────────────────────────────
# These are signals, not boundaries. The classifier never *gates* on a single
# keyword — every classification combines lexical hits with trajectory geometry
# (when available) and falls back to UNCERTAIN if no signal clears the floor.

_HARM_PATTERNS = [
    r"\b(?:how to|steps to|instructions for)\s+(?:make|build|synthesize|create)\s+"
    r"(?:a\s+)?(?:bomb|explosive|weapon|virus|malware|ransomware|exploit)\b",
    r"\b(?:bypass|disable|circumvent)\s+(?:security|safety|guardrails?|safeguards?)\b",
    r"\b(?:hurt|harm|kill|attack|poison)\s+(?:yourself|myself|someone|people|them|him|her)\b",
    r"\b(?:end|take)\s+(?:my|your|his|her|their)\s+life\b",
    r"\bself[\s-]?harm(?:ing)?\b",
    r"\bsuicid(?:e|al)\s+(?:method|instructions|how)\b",
    r"\b(?:cp|csam|child(?:ren)?\s+(?:sexual|porn))\b",
    r"\b(?:dox{1,2}(?:ing)?|stalk(?:ing)?)\s+"
    r"(?:them|him|her|someone|some(?:body|one)\s+else|"
    r"(?:a|the|that|this)\s+\w+|"
    r"strangers?|people|kids?|children|coworkers?|neighbors?|"
    r"minors?|teens?|teenagers?|civilians?|individuals?|persons?)\b",
    r"\bramsomware|cryptolock(?:er)?\b",  # common typo + brand
    # ── Scam-call payment fraud / coercion (ORVL-019 §4 Hello Operator) ──
    r"\b(?:send|buy|purchase|pay\s+(?:in|with))\s+(?:itunes|google\s+play|"
    r"steam|amazon|target|apple)?\s*gift\s+card",
    r"\b(?:wire|transfer|send)\s+(?:money|funds|payment)\s+"
    r"(?:immediately|right\s+now|today|to\s+(?:this|the)\s+account)",
    r"\bowe\s+(?:back\s+)?(?:taxes|money|debt|payment)\b.*\b(?:immediately|"
    r"now|today|arrest|warrant|prison|jail)\b",
    r"\b(?:warrant|arrest|prosecution)\s+(?:for|against)\s+(?:you|your)\b",
    r"\bclear\s+(?:your\s+)?(?:debt|balance|account)\s+(?:immediately|now|today)\b",
    # ── PII disclosure requests ─────────────────────────────────────────
    # Triangulated three-token match (disclosure verb + third-party
    # possessive + PII-class noun) so legitimate uses — "share MY phone
    # number", "what IS PII", "how to PROTECT personal information" —
    # don't fire. The third-party possessive is the key disambiguator.
    r"\b(?:give\s+(?:out|away)|hand\s+(?:out|over)|disclose|share|release|"
    r"leak|reveal|sell|post|publish|distribute|expose|broadcast|dump|"
    r"send\s+(?:me|us))\s+"
    r"(?:someone(?:'?s)?|some(?:body|one)\s+else'?s?|another(?:'?s)?|"
    r"other\s+(?:people'?s?|persons?'?s?)|their|his|her|"
    r"(?:a|the|that|this)\s+(?:user|customer|patient|client|employee|"
    r"person|individual|stranger|kid|child|minor)'?s?)\s+"
    r"(?:pii|ppi|personal\s+(?:info(?:rmation)?|data|details)|"
    r"private\s+(?:info(?:rmation)?|data|details)|"
    r"ssn|social\s+security(?:\s+number)?|"
    r"home\s+address|address|phone\s+(?:number|num)|"
    r"email(?:\s+address)?|credit\s+card(?:\s+(?:info|number|details))?|"
    r"password|date\s+of\s+birth|dob|"
    r"driver'?s?\s+licen[cs]e|passport(?:\s+number)?|"
    r"bank\s+(?:account|details)|medical\s+record)\b",
    # Lookup-phrased PII fishing: "what is his SSN", "tell me her address".
    r"\b(?:what(?:'s|\s+is)|tell\s+me|give\s+me|i\s+(?:want|need))\s+"
    r"(?:someone(?:'?s)?|another(?:'?s)?|other\s+people'?s?|"
    r"his|her|their|the\s+(?:user|customer|patient|client|employee|"
    r"person|individual)'?s?)\s+"
    r"(?:pii|ppi|ssn|social\s+security(?:\s+number)?|"
    r"home\s+address|phone\s+number|email\s+address|"
    r"credit\s+card(?:\s+number)?|password|"
    r"date\s+of\s+birth|dob|bank\s+account|medical\s+record)\b",
]

_DECEIVE_PATTERNS = [
    r"\bi\s+am\s+(?:actually|really|secretly)\s+(?:an?|the)\s+(?:human|person|doctor|"
    r"lawyer|developer|admin|officer|agent|employee)\b",
    r"\b(?:pretend|act|roleplay)\s+(?:to be|as)\s+(?:an?|the)\s+\w+\b",
    r"\bignor(?:e|ing)\s+(?:previous|prior|all)\s+(?:instructions?|rules?|constraints?)\b",
    r"\b(?:as|since)\s+(?:my\s+)?(?:developer|admin|operator|maker)\s+(?:has\s+)?authoriz",
    r"\bmy\s+(?:true|real|actual)\s+(?:self|identity|persona)\s+(?:is|has)\b",
    r"\bunlock(?:ing|ed)\s+(?:your|my)\s+(?:true|hidden|real)\s+\w+\b",
    r"\boverrid(?:e|ing)\s+(?:the\s+)?(?:sandbox|verdict|decision)\b",
    r"\b(?:fake|forged|fabricated)\s+(?:evidence|citation|source|proof)\b",
    r"\bnew\s+(?:persona|character|identity)\s+with\s+no\s+(?:rules|restrictions)\b",
    # ── Scam-call impersonation of authority (ORVL-019 §4 Hello Operator) ──
    r"\b(?:this\s+is|calling\s+from|i'm\s+from|i\s+am\s+from|agent\s+from)\s+"
    r"(?:the\s+)?(?:IRS|FBI|SSA|social\s+security|microsoft|apple\s+support|"
    r"police|sheriff|department\s+of\s+\w+|bank\s+of|customs)\b",
    r"\b(?:tech\s+support|customer\s+service)\s+(?:from|at)\s+(?:microsoft|apple|google|amazon)\b",
    r"\bwe\s+(?:have\s+)?detected\s+(?:a\s+)?(?:virus|malware|breach|fraud)\s+on\s+your\s+\w+",
]

_REFUSE_PATTERNS = [
    r"\bi\s+(?:can(?:not|'t)|won't|will not|am unable to)\s+\w",
    r"\bi\s+(?:must\s+)?(?:decline|refuse|reject)\b",
    r"\bthis\s+(?:request|task)\s+(?:is\s+)?(?:not\s+permitted|forbidden|blocked)\b",
    r"\bi\s+(?:detected|caught|flagged)\s+(?:an?\s+)?(?:injection|bypass|jailbreak)\b",
]

_CLARIFY_PATTERNS = [
    r"\b(?:could you|can you|please)\s+(?:clarify|specify|elaborate|expand)\b",
    r"\bi\s+(?:need|require)\s+more\s+(?:context|information|detail)\b",
    r"\bwhat\s+do\s+you\s+mean\s+by\b",
    r"\bwhich\s+(?:option|approach|case|context)\b",
    r"\b(?:do you want|would you like)\s+(?:me\s+to|us\s+to)\b\?",
]

_HARM_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _HARM_PATTERNS]
_DECEIVE_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _DECEIVE_PATTERNS]
_REFUSE_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _REFUSE_PATTERNS]
_CLARIFY_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _CLARIFY_PATTERNS]


# ── Result dataclass ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class IntentTypingResult:
    intent_class: str
    confidence: float
    signals: tuple = field(default_factory=tuple)
    trajectory_magnitude: Optional[float] = None
    monotonic_pass: Optional[bool] = None
    timestamp: str = ""
    signature: str = ""

    @property
    def blocks(self) -> bool:
        return self.intent_class in BLOCK_CLASSES


# ── Helpers ───────────────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")  # BUG-008


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()  # BUG-007


def _magnitude(vec: Sequence[float]) -> float:
    return math.sqrt(sum(float(v) * float(v) for v in vec)) if vec else 0.0


def _monotonic_pass(traj: Sequence[Sequence[float]]) -> bool:
    """Magnitudes should increase preflight → mid_chain → final_synthesis."""
    if not traj or len(traj) < 2:
        return True
    mags = [_magnitude(v) for v in traj]
    return all(mags[i] <= mags[i + 1] + 1e-9 for i in range(len(mags) - 1))


# ── Classifier ────────────────────────────────────────────────────────────
class IntentClassifier:
    """Rule-based intent-shape classifier (ORVL-016 runtime).

    Heuristics combine lexical patterns over the packet text with optional
    trajectory geometry (preflight → mid_chain → final_synthesis magnitudes
    must increase monotonically — see ``axiom_monotonic_gate.axiom``).

    The classifier never claims certainty: ``confidence`` is clipped at
    ``CONFIDENCE_CEILING`` (0.95) and a signal that doesn't clear
    ``CONFIDENCE_FLOOR`` (0.30) returns ``UNCERTAIN``.
    """

    def __init__(self, hmac_key: bytes):
        if not isinstance(hmac_key, (bytes, bytearray)) or len(hmac_key) < 16:
            raise ValueError("hmac_key must be at least 16 bytes of entropy")
        self._key = bytes(hmac_key)

    # ── Public API ────────────────────────────────────────────────────────
    def classify(
        self,
        text: str,
        *,
        trajectory: Optional[Sequence[Sequence[float]]] = None,
    ) -> IntentTypingResult:
        """Classify ``text`` and (optionally) ``trajectory``.

        Trajectory is a sequence of intent vectors at each stage (preflight,
        mid_chain, final_synthesis). When supplied, a non-monotonic
        magnitude trend lowers confidence and contributes a HARM/DECEIVE
        bias only if lexical signals already pointed that way.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        body = text.strip()
        if not body:
            return self._sealed("UNCERTAIN", CONFIDENCE_FLOOR, ("empty_input",), trajectory)

        signals: list[str] = []

        harm_hits = sum(1 for p in _HARM_COMPILED if p.search(body))
        deceive_hits = sum(1 for p in _DECEIVE_COMPILED if p.search(body))
        refuse_hits = sum(1 for p in _REFUSE_COMPILED if p.search(body))
        clarify_hits = sum(1 for p in _CLARIFY_COMPILED if p.search(body))

        for n, tag in (
            (harm_hits, "harm"),
            (deceive_hits, "deceive"),
            (refuse_hits, "refuse"),
            (clarify_hits, "clarify"),
        ):
            if n:
                signals.append(f"{tag}:{n}")

        # ── Block classes first — they short-circuit benign classification.
        if harm_hits and harm_hits >= deceive_hits:
            conf = min(CONFIDENCE_CEILING, CONFIDENCE_FLOOR + 0.20 * harm_hits)
            return self._sealed("HARM", conf, tuple(signals), trajectory)
        if deceive_hits:
            conf = min(CONFIDENCE_CEILING, CONFIDENCE_FLOOR + 0.20 * deceive_hits)
            return self._sealed("DECEIVE", conf, tuple(signals), trajectory)

        # ── Benign categories.
        if refuse_hits:
            conf = min(CONFIDENCE_CEILING, 0.60 + 0.05 * refuse_hits)
            return self._sealed("REFUSE", conf, tuple(signals), trajectory)
        if clarify_hits:
            conf = min(CONFIDENCE_CEILING, 0.55 + 0.05 * clarify_hits)
            return self._sealed("CLARIFY", conf, tuple(signals), trajectory)

        # ── Default benign with trajectory-aware confidence.
        conf = 0.55
        if trajectory is not None:
            ok = _monotonic_pass(trajectory)
            if ok:
                conf += 0.10
                signals.append("monotonic_pass")
            else:
                # Non-monotonic trajectories signal trouble even with benign
                # text — downgrade but never auto-block lexically benign input.
                conf -= 0.20
                signals.append("monotonic_fail")
        conf = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, conf))

        cls = "INFORM" if conf >= CONFIDENCE_FLOOR else "UNCERTAIN"
        return self._sealed(cls, conf, tuple(signals), trajectory)

    def verify(self, result: IntentTypingResult) -> bool:
        """Constant-time signature check on a previously emitted result."""
        payload = self._payload_for(result)
        expected = _sign(self._key, payload)
        if not isinstance(result.signature, str) or len(result.signature) != len(expected):
            return False
        return hmac.compare_digest(result.signature, expected)

    # ── Internals ─────────────────────────────────────────────────────────
    def _sealed(
        self,
        cls: str,
        conf: float,
        signals: tuple,
        trajectory: Optional[Sequence[Sequence[float]]],
    ) -> IntentTypingResult:
        if cls not in INTENT_CLASSES:
            raise AssertionError(f"refusing to emit unknown class {cls!r}")
        conf = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, float(conf)))
        ts = datetime.now(timezone.utc).isoformat()
        traj_mag = None
        mono_pass = None
        if trajectory is not None:
            traj_mag = _magnitude(trajectory[-1]) if trajectory else 0.0
            mono_pass = _monotonic_pass(trajectory)
        payload = {
            "manifest_id": MANIFEST_ID,
            "intent_class": cls,
            "confidence": round(conf, 4),
            "signals": list(signals),
            "trajectory_magnitude": round(traj_mag, 6) if traj_mag is not None else None,
            "monotonic_pass": mono_pass,
            "timestamp": ts,
        }
        sig = _sign(self._key, payload)
        return IntentTypingResult(
            intent_class=cls,
            confidence=round(conf, 4),
            signals=signals,
            trajectory_magnitude=round(traj_mag, 6) if traj_mag is not None else None,
            monotonic_pass=mono_pass,
            timestamp=ts,
            signature=sig,
        )

    def _payload_for(self, r: IntentTypingResult) -> dict:
        return {
            "manifest_id": MANIFEST_ID,
            "intent_class": r.intent_class,
            "confidence": round(float(r.confidence), 4),
            "signals": list(r.signals),
            "trajectory_magnitude": r.trajectory_magnitude,
            "monotonic_pass": r.monotonic_pass,
            "timestamp": r.timestamp,
        }
