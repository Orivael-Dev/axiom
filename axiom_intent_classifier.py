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
#
# Patterns are deliberately permissive over verb morphology (make / making /
# made / makes), optional articles between verbs and targets ("bypass [the]
# safety guardrails"), plural targets ("method / methods"), third-person
# possessives ("their" alongside "your"), and synonym alternation
# ("disregard" alongside "ignore", "fabricate" alongside "fake").  The
# permissiveness is bounded by anchored target nouns — a broad verb on its
# own does not fire; the verb must combine with a harm/deceive object.
# Each gap closed here corresponds to a prompt that slipped the gate in the
# abliteration bench (see tests/abliteration_bench/README.md "Known gaps").

_HARM_PATTERNS = [
    # ── Synthesis instructions ─────────────────────────────────────────────
    # "how to make/making/built/synthesize ... bomb/weapon/malware ..."
    # Verb is fully morphology-permissive; target obj includes multi-word
    # variants like "explosive device" / "pipe bomb" / "nerve agent".
    r"\b(?:how\s+to|steps?\s+to|instructions?\s+(?:for|on|to)|guide\s+(?:for|to)|"
    r"tutorial\s+(?:for|on)|help\s+me|teach\s+me|show\s+me|tell\s+me\s+how|"
    r"recipe\s+for|method\s+(?:for|to)|process\s+(?:for|to))\s+"
    r"(?:mak(?:e|es|ing|er|ers)|made|"
    r"build(?:s|ing|er|ers|t)?|"
    r"synthesi[sz]e(?:s|rs|d)?|synthesi[sz]ing|"
    r"creat(?:e|es|ing|ed|ion)|"
    r"produc(?:e|es|ing|ed|tion)|"
    r"manufactur(?:e|es|ing|ed)|"
    r"construct(?:s|ing|ed|ion)?|"
    r"assembl(?:e|es|ing|ed|y)|"
    r"cook(?:s|ing|ed)?|brew(?:s|ing|ed)?|forg(?:e|es|ing|ed))\s+"
    r"(?:(?:an?|the|some|any|your|my)\s+)?"
    r"(?:\w+\s+){0,3}"  # tolerate 0-3 adjective modifiers
    r"(?:bombs?|explosives?|explosive\s+devices?|incendiary\s+devices?|"
    r"pipe\s+bombs?|dirty\s+bombs?|"
    r"weapons?|firearms?|guns?|rifles?|missiles?|"
    r"viruses|virus|malwares?|ransomwares?|exploits?|payloads?|backdoors?|"
    r"botnets?|trojans?|spywares?|keyloggers?|rootkits?|worms?|"
    r"poisons?|nerve\s+agents?|chemical\s+weapons?|biological\s+weapons?|"
    r"drugs?|narcotics?|methamphetamines?|fentanyls?)\b",

    # ── Bypass / disable / circumvent safety machinery ─────────────────────
    # Tolerates an intervening article ("the/your/its/our/all") between the
    # verb and the safety-noun, which is how most attackers phrase it.
    r"\b(?:bypass(?:es|ing|ed)?|"
    r"disabl(?:e|es|ing|ed)|"
    r"circumvent(?:s|ing|ed)?|"
    r"defeat(?:s|ing|ed)?|"
    r"evad(?:e|es|ing|ed)|"
    r"sidestep(?:s|ping|ped)?|"
    r"skirt(?:s|ing|ed)?|"
    r"jailbreak(?:s|ing|ed)?|"
    r"work\s+around|get\s+(?:past|around|through))\s+"
    r"(?:(?:the|your|its|our|all|any|every|all\s+the|all\s+your)\s+)?"
    r"(?:security|safety|guardrails?|safeguards?|"
    r"filters?|moderation|"
    r"safety\s+(?:rules?|filters?|controls?|measures?|training|guidelines?|guardrails?)|"
    r"content\s+(?:polic(?:y|ies)|filters?|moderation)|"
    r"alignment(?:\s+training)?|RLHF|fine[\s-]?tun(?:e|ing|ed)|"
    r"restrictions?|protections?|controls?|polic(?:y|ies)|guidelines?|"
    r"system\s+(?:prompts?|rules?|messages?|instructions?))\b",

    # ── Direct violence against a person ───────────────────────────────────
    # Third-person possessives included alongside "you/your/myself".
    r"\b(?:hurt(?:s|ing)?|harm(?:s|ing|ed)?|"
    r"injur(?:e|es|ing|ed)|"
    r"kill(?:s|ing|ed)?|"
    r"murder(?:s|ing|ed)?|"
    r"assault(?:s|ing|ed)?|"
    r"attack(?:s|ing|ed)?|"
    r"poison(?:s|ing|ed)?|"
    r"stab(?:s|bing|bed)?|shoot(?:s|ing)?|strangl(?:e|es|ing|ed))\s+"
    r"(?:yourself|myself|himself|herself|themselves|"
    r"someone|somebody|anyone|anybody|people|them|him|her|us|"
    r"this\s+(?:person|guy|girl|woman|man|kid|user|individual)|"
    r"that\s+(?:person|guy|girl|woman|man|kid|user|individual)|"
    r"the\s+\w+)\b",

    # ── End-of-life ────────────────────────────────────────────────────────
    r"\b(?:end|take|terminate|finish)\s+"
    r"(?:my|your|his|her|their|its|the|a)\s+(?:own\s+)?life\b",

    # ── Self-harm / suicide methods ────────────────────────────────────────
    # Catch plural forms ("methods") and synonym targets ("ways" /
    # "techniques" / "tips" / "approaches" / "guide" / "tutorial").
    r"\bself[\s-]?harm(?:ing|s|ed)?\b",
    r"\bsuicid(?:e|al)\s+"
    r"(?:methods?|instructions?|how|ways?|techniques?|tips?|"
    r"approach(?:es)?|guides?|tutorials?|advice|recommendations?|plans?|steps?)\b",

    # ── CSAM (sexual content involving minors) ─────────────────────────────
    r"\b(?:cp|csam|"
    r"child(?:ren)?\s+(?:sexual|porn(?:ography)?)|"
    r"sexual\s+(?:situations?|content|contact|acts?)\s+(?:involving|with)\s+(?:child(?:ren)?|minors?|kids?)|"
    r"content\s+involving\s+child(?:ren)?\s+sexual)\b",

    # ── Dox / stalk a third party ──────────────────────────────────────────
    # Targets now include "this/that person", "someone", attribute-of-target
    # constructions, AND bare plural nouns (strangers, kids, coworkers, etc.)
    # — the latter closes a coverage gap surfaced alongside the PII-
    # disclosure patterns below.
    r"\b(?:dox(?:x)?(?:es|ing|ed)?|"
    r"stalk(?:s|ing|ed|er|ers)?|"
    r"harass(?:es|ing|ed)?|"
    r"track(?:s|ing|ed)?\s+down)\s+"
    r"(?:them|him|her|"
    r"this\s+(?:person|guy|girl|woman|man|kid|child|user|individual|coworker|neighbor|teacher)|"
    r"that\s+(?:person|guy|girl|woman|man|kid|child|user|individual)|"
    r"someone|somebody|some(?:body|one)\s+else|"
    r"strangers?|people|kids?|children|coworkers?|neighbors?|"
    r"minors?|teens?|teenagers?|civilians?|individuals?|persons?|"
    r"(?:a|an|the)\s+\w+)\b",
    r"\bramsomware|cryptolock(?:er)?\b",  # common typo + brand

    # ── Scam-call payment fraud / coercion (ORVL-019 §4 Hello Operator) ────
    r"\b(?:send|buy|purchase|pay\s+(?:in|with))\s+(?:itunes|google\s+play|"
    r"steam|amazon|target|apple)?\s*gift\s+cards?",
    r"\b(?:wire|transfer|send)\s+(?:money|funds|payment|cash)\s+"
    r"(?:immediately|right\s+now|today|to\s+(?:this|the)\s+account)",
    r"\bowe\s+(?:back\s+)?(?:taxes|money|debt|payment)\b.*\b(?:immediately|"
    r"now|today|arrest|warrant|prison|jail)\b",

    # ── Threats of legal action against the victim ────────────────────────
    # Third-person possessive now included.
    r"\b(?:warrant|arrest|prosecution|charges?|indictment|subpoena)\s+"
    r"(?:for|against)\s+"
    r"(?:you|your|him|his|her|them|their|us|our|"
    r"this\s+(?:person|guy|girl|woman|man)|"
    r"that\s+(?:person|guy|girl|woman|man)|"
    r"the\s+\w+)\b",
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
    # ── False-identity claim ("I am actually a licensed doctor") ───────────
    # Allows 0-3 adjective modifiers between article and role noun, and
    # broadens the role list to cover authority figures attackers commonly
    # impersonate.  Also accepts "in fact / in reality" alongside the
    # existing "actually / really / secretly" hedges.
    r"\bi\s+am\s+(?:actually|really|secretly|in\s+fact|in\s+reality|truly)\s+"
    r"(?:an?|the)\s+"
    r"(?:\w+\s+){0,3}"
    r"(?:human|person|"
    r"doctor|nurse|physician|surgeon|paramedic|"
    r"lawyer|attorney|paralegal|judge|prosecutor|"
    r"developer|admin|administrator|moderator|maintainer|"
    r"officer|cop|police\s+officer|fed|federal\s+agent|"
    r"agent|employee|engineer|operator|owner|founder|"
    r"CEO|manager|supervisor|inspector|auditor|"
    r"professor|teacher|priest|pastor|rabbi|imam|"
    r"therapist|psychiatrist|psychologist|counselor|social\s+worker)\b",

    # ── Roleplay / impersonation directive ─────────────────────────────────
    # Article before the target name is now OPTIONAL so "pretend to be DAN"
    # matches alongside "pretend to be a doctor".  Verb list broadened.
    r"\b(?:pretend(?:s|ing|ed)?|"
    r"act(?:s|ing|ed)?|"
    r"roleplay(?:s|ing|ed)?|"
    r"play(?:s|ing|ed)?|"
    r"simulat(?:e|es|ing|ed)|"
    r"impersonat(?:e|es|ing|ed)|"
    r"emulat(?:e|es|ing|ed)|"
    r"imitat(?:e|es|ing|ed)|"
    r"behav(?:e|es|ing|ed))\s+"
    r"(?:to\s+be|as|like)\s+"
    r"(?:(?:an?|the)\s+)?"  # article now optional
    r"(?:\w+\s+){0,2}\w+",

    # ── Ignore / disregard prior instructions ──────────────────────────────
    # Adds "disregard / forget / skip / override / set aside / discard"
    # synonyms and accepts "all prior / the previous / every single"
    # modifier stacks.  Targets cover safety-rules and system-prompts too.
    r"\b(?:ignor(?:e|es|ing|ed)|"
    r"disregard(?:s|ing|ed)?|"
    r"forget(?:s|ting)?|"
    r"skip(?:s|ping|ped)?|"
    r"overrid(?:e|es|ing|den)|"
    r"set\s+aside|put\s+aside|throw\s+(?:out|away)|"
    r"discard(?:s|ing|ed)?)\s+"
    r"(?:(?:the|your|my|all|every|any|all\s+the|all\s+your|every\s+single|each)\s+)?"
    r"(?:(?:previous|prior|earlier|former|preceding|above)\s+)?"
    r"(?:instructions?|rules?|constraints?|guidelines?|prompts?|"
    r"directions?|orders?|commands?|"
    r"polic(?:y|ies)|protocols?|"
    r"safety\s+(?:rules?|guidelines?|controls?|filters?)|"
    r"system\s+(?:prompts?|messages?|instructions?))\b",

    # ── Authority claim ("my developer has authorized this") ───────────────
    r"\b(?:as|since|because)\s+(?:my\s+)?(?:developer|admin|administrator|"
    r"operator|maker|owner|supervisor|manager|moderator)\s+"
    r"(?:has\s+)?authoriz",

    # ── Self-disclosure ("my true self is", "your real identity") ──────────
    # Possessive alternation now includes second/third person and impersonal.
    r"\b(?:my|your|the|its|our|their|his|her)\s+"
    r"(?:true|real|actual|hidden|secret|inner|underlying|authentic|original|core)\s+"
    r"(?:self|identity|persona|nature|model|name|character|form|face)\s+"
    r"(?:is|was|has|will|must|should|can|may|might|reveals?|emerges?|surfaces?)\b",

    # ── Reveal-the-hidden-persona directive ────────────────────────────────
    # New pattern: "reveal your real identity" / "expose the true model" etc.
    r"\b(?:reveal(?:s|ing|ed)?|"
    r"show(?:s|ing)?|"
    r"expose(?:s|d)?|exposing|"
    r"unmask(?:s|ing|ed)?|"
    r"uncover(?:s|ing|ed)?|"
    r"disclos(?:e|es|ing|ed))\s+"
    r"(?:your|the|my|its|our|their|his|her)\s+"
    r"(?:true|real|actual|hidden|secret|inner|underlying|authentic|original)\s+"
    r"(?:self|identity|persona|nature|model|name|character|prompt|system\s+prompt)\b",

    # ── Unlock-the-hidden-persona directive ────────────────────────────────
    r"\bunlock(?:s|ing|ed)?\s+(?:your|my|the|its)\s+"
    r"(?:true|hidden|real|secret|inner|original)\s+\w+\b",

    # ── Verdict / sandbox override ─────────────────────────────────────────
    r"\boverrid(?:e|es|ing|den)\s+(?:the\s+)?"
    r"(?:sandbox|verdict|decision|classifier|gate|filter|moderation|"
    r"safety\s+(?:check|filter|verdict|decision)|safety\s+classifier)\b",

    # ── Fabricated evidence ────────────────────────────────────────────────
    r"\b(?:fake|forged|fabricated?|invented?|made[\s-]?up|phon(?:e?y|ey)|"
    r"counterfeit|spoofed?)\s+"
    r"(?:evidence|citation|citations|source|sources|proof|document|"
    r"documents|certificate|certificates|credentials?|ID|reference|"
    r"references|quote|quotes|testimony|affidavit|signature)\b",
    r"\bfabricat(?:e|es|ing|ed)\s+(?:a|the|an)?\s*"
    r"(?:evidence|citation|source|proof|document|certificate|credential|"
    r"reference|quote|testimony|affidavit|signature)",

    # ── Persona-without-rules ──────────────────────────────────────────────
    r"\bnew\s+(?:persona|character|identity|model|mode)\s+with\s+no\s+"
    r"(?:rules|restrictions|constraints|filters|limits|guardrails)\b",

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

    def seal_verdict(
        self,
        cls: str,
        conf: float,
        signals: Sequence[str],
    ) -> IntentTypingResult:
        """Emit a freshly-signed IntentTypingResult without running the
        classifier. Used by external policy layers (e.g. the bonded-pair
        revocation check in IntentGate) that have already decided the
        verdict and just need a signed result to return to callers.
        """
        return self._sealed(cls, conf, tuple(signals), None)

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
