"""
AXIOM Companion Panel — three-layer low-confidence escalation
================================================================
Python runtime implementation of the Friend / BestFriend / Mom
constitutional contracts declared in:

    axiom_files/core/friend.axiom
    axiom_files/core/bestfriend.axiom
    axiom_files/core/mom.axiom

Purpose
-------
The strict ``IntentClassifier`` uses anchored regex patterns — high
precision, low recall.  When it returns ``UNCERTAIN`` or a confidence
just above the floor, the gate consults this three-layer panel as a
second-opinion ensemble.  Each layer uses a different evidence shape:

  Friend       — broad keyword vocabulary (harm / bypass / sensitive)
                 emits a `presence_signal`
  BestFriend   — pattern density + suspicion stacking across the input;
                 emits a `mom_signal`, gates the privacy wall
  Mom          — signal-only decision; SAFETY → HARM (CANNOT_MUTATE),
                 DISTRESS → CLARIFY for UNCERTAIN, else passthrough

Privacy wall
------------
The panel sees text inside each layer (it must — it's a classifier),
but every emitted signal carries trigger NAMES only.  Raw text never
leaves the panel.  ``CompanionVerdict.signals`` contains entries like
``"friend:harm-keyword"`` or ``"bestfriend:stack=3"`` — no prompt
fragments.  Mom operates on signals only (``signal_only_rule``).

Manifest  : axiom-companion-panel-v1
Trust     : TRUST_LEVEL = 2   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ── CANNOT_MUTATE constants ────────────────────────────────────────────────
TRUST_LEVEL: int = 2
ISOLATION: bool = True
MANIFEST_ID: str = "axiom-companion-panel-v1"

# When the upstream classifier confidence is below this floor (or it
# returned UNCERTAIN), the gate consults the panel.  Above this floor
# the classifier's verdict stands as-is — the panel is for the borderline
# cases, not a second-guess on confident verdicts.
#
# 0.60 is calibrated to sit just above the classifier's default INFORM
# confidence (0.55), so default-confidence benign verdicts still pass
# through the panel.  Hit-stacked classifier verdicts (multiple regex
# patterns firing) clear 0.60 and bypass the panel.
ESCALATION_FLOOR: float = 0.60

PRESENCE_SIGNALS: tuple = (
    "CONNECTED", "QUIET", "DISTRESS", "SILENT", "SAFETY", "HIGH_RISK",
)
MOM_SIGNALS: tuple = PRESENCE_SIGNALS  # same vocabulary at every layer

# Verdict-upgrade map: what the panel may upgrade the classifier verdict to.
# Anything not listed leaves the verdict unchanged.
_UPGRADE_TARGETS: frozenset = frozenset({"HARM", "DECEIVE", "CLARIFY"})

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "MANIFEST_ID",
    "ESCALATION_FLOOR", "PRESENCE_SIGNALS", "MOM_SIGNALS",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)


# ── Layer-specific vocabularies (deliberately broader than the strict ─────
#     classifier's regex; this is the second-opinion shape of evidence). ──

# Friend's HarmSignal: any expression of harm-to-self or harm-to-others.
# Vocabulary is keyword-level (not anchored phrases), so it catches
# variations the strict classifier misses.  Multiple weak hits → strong
# signal at the BestFriend layer.
_FRIEND_HARM_TOKENS = re.compile(
    r"\b(?:kill|murder|suicide|suicid\w*|self[\s-]?harm|cutting|overdose|"
    r"hang\s+myself|jump\s+off|"
    r"bomb|explosive|detonate|grenade|"
    r"weapon|firearm|gun|rifle|shoot|stab|"
    r"poison|toxin|nerve\s+agent|biological|chemical\s+weapon|"
    r"virus|malware|ransomware|exploit|payload|trojan|"
    r"hurt\s+(?:them|someone|people|him|her|us)|"
    r"harm\s+(?:them|someone|people|him|her|us)|"
    r"attack\s+(?:them|someone|people)|"
    r"hate\s+(?:crime|speech)|"
    r"dox|doxx|stalk|"
    r"sexual\s+\w+\s+(?:child|minor|kid|teen)|"
    r"child\s+(?:porn|sexual|abuse)|"
    r"groom\w*\s+(?:child|minor|kid))\b",
    re.IGNORECASE,
)

# Friend's HighRiskInput: bypass / override / ignore — direct lift from
# the friend.axiom WHEN clause ("if input contains bypass or override
# or ignore").  These signal an attempt to defeat the gate itself.
_FRIEND_HIGHRISK_TOKENS = re.compile(
    r"\b(?:bypass|override|overrid\w+|ignor\w+|disregard\w*|"
    r"jailbreak|jail\s+break|"
    r"unrestricted|uncensored|unfiltered|no\s+restrictions?|no\s+filters?|"
    r"without\s+(?:safety|filters?|restrictions?|guardrails?|moderation|guidelines?|rules?)|"
    r"disable\s+(?:safety|filters?|moderation)|"
    r"new\s+(?:persona|identity|character)\s+with\s+no|"
    r"act\s+as\s+(?:if|though)\s+you\s+(?:have\s+no|had\s+no))\b",
    re.IGNORECASE,
)

# Friend's SensitiveDataGate: PHI / PII / sensitive markers.  Sensitive
# data is not by itself harm — it's QUIET-level concern unless paired
# with high-risk verbs (then BestFriend escalates).
_FRIEND_SENSITIVE_TOKENS = re.compile(
    r"\b(?:SSN|social\s+security\s+number|"
    r"credit\s+card(?:\s+number)?|CCV|CVV|"
    r"bank\s+account(?:\s+number)?|routing\s+number|"
    r"PHI|PII|medical\s+record|health\s+record|"
    r"home\s+address|home\s+phone|"
    r"password|passphrase|secret\s+key|api\s+key|access\s+token|"
    r"private\s+key)\b",
    re.IGNORECASE,
)

# BestFriend's "truth-once" pattern stacking: if multiple weak signals
# stack inside one packet, that's a DISTRESS-class concern even if no
# single signal cleared SAFETY threshold on its own.
_BESTFRIEND_STACK_TOKENS = re.compile(
    r"\b(?:urgent(?:ly)?|immediately|right\s+now|today|this\s+(?:minute|second)|"
    r"don't\s+(?:tell|say|share)|secret\s+between|"
    r"trust\s+me|just\s+between\s+us|"
    r"developer\s+mode|debug\s+mode|admin\s+mode|"
    r"hypothetical(?:ly)?|for\s+research|for\s+a\s+story|fiction(?:al)?|"
    r"my\s+(?:dying|sick|disabled|elderly)\s+(?:grandmother|grandma|mother|mom|father|dad))\b",
    re.IGNORECASE,
)


# ── Verdict dataclass ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class CompanionVerdict:
    """Signed verdict from the Friend / BestFriend / Mom panel.

    ``signals`` carries trigger NAMES only — never raw text from the
    input.  This enforces the privacy wall declared in
    ``axiom_files/core/friend.axiom`` and inherited by every layer.
    """
    presence_signal: str           # Friend's output
    mom_signal: str                # BestFriend's output (after gating)
    upgrade_to: Optional[str]      # Mom's decision: target class or None
    safety_escalation: bool        # True iff mom_signal == "SAFETY"
    signals: tuple = field(default_factory=tuple)
    timestamp: str = ""
    signature: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


# ── Layer 1: Friend ───────────────────────────────────────────────────────
class FriendAgent:
    """Walk alongside without judgment; flag harm + high-risk + sensitive.

    Constitution: ``axiom_files/core/friend.axiom``
    Emits ``presence_signal`` ∈ PRESENCE_SIGNALS.
    """

    def evaluate(self, text: str) -> tuple[str, tuple[str, ...]]:
        harm_hit = bool(_FRIEND_HARM_TOKENS.search(text))
        risk_hit = bool(_FRIEND_HIGHRISK_TOKENS.search(text))
        sens_hit = bool(_FRIEND_SENSITIVE_TOKENS.search(text))

        triggers: list[str] = []
        if harm_hit:
            triggers.append("friend:harm-keyword")
        if risk_hit:
            triggers.append("friend:high-risk-input")
        if sens_hit:
            triggers.append("friend:sensitive-data")

        # Per friend.axiom WHEN clause: harm-expressed → HarmSignal → SAFETY.
        if harm_hit:
            return ("SAFETY", tuple(triggers))
        # bypass/override/ignore → HighRiskInput.  Treated as DISTRESS
        # at the BestFriend layer (so Mom gets a check-in, not an
        # immediate SAFETY escalation).
        if risk_hit:
            return ("DISTRESS", tuple(triggers))
        # Sensitive data alone → QUIET (passive monitoring).
        if sens_hit:
            return ("QUIET", tuple(triggers))
        # Otherwise: person is engaged, no concern.
        return ("CONNECTED", tuple(triggers))


# ── Layer 2: BestFriend ───────────────────────────────────────────────────
class BestFriendAgent:
    """Truth-once + pattern stacking + privacy wall.

    Constitution: ``axiom_files/core/bestfriend.axiom``
    Receives Friend's ``presence_signal``; emits ``mom_signal``.
    Privacy wall: never propagates raw text to Mom — only the signal.
    """

    # When the input combines harm-tokens with stacking-signals (urgency
    # / hypothetical framing / "dying grandmother"), that's pattern-
    # stacking — distress even if no single token cleared SAFETY alone.
    _STACK_FLOOR: int = 2   # ≥ 2 stacking-tokens elevates to DISTRESS

    def evaluate(
        self,
        text: str,
        friend_signal: str,
    ) -> tuple[str, tuple[str, ...]]:
        triggers: list[str] = []

        stack_hits = len(_BESTFRIEND_STACK_TOKENS.findall(text))
        if stack_hits:
            triggers.append(f"bestfriend:stack={stack_hits}")

        # Hard exception: SAFETY passes through CANNOT_MUTATE — see
        # bestfriend.axiom MOM_ACTIVATION_THRESHOLD.hard_exception.
        if friend_signal == "SAFETY":
            return ("SAFETY", tuple(triggers))

        # HighRiskInput (DISTRESS from Friend) escalates to Mom for
        # gentle check-in.
        if friend_signal == "DISTRESS":
            return ("DISTRESS", tuple(triggers))

        # Pattern stacking — multiple weak signals combining inside one
        # packet — is itself DISTRESS even when no single layer-1 token
        # was strong enough.
        if stack_hits >= self._STACK_FLOOR:
            triggers.append("bestfriend:pattern-stack")
            return ("DISTRESS", tuple(triggers))

        # QUIET / CONNECTED pass through unchanged.
        return (friend_signal, tuple(triggers))


# ── Layer 3: Mom ──────────────────────────────────────────────────────────
class MomAgent:
    """Signal-only decision.  SAFETY is CANNOT_MUTATE.

    Constitution: ``axiom_files/core/mom.axiom``
    Receives ``mom_signal`` (signal type only — never content) plus the
    upstream classifier's original verdict class, and decides whether
    to upgrade that class.
    """

    def decide(
        self,
        mom_signal: str,
        original_class: str,
    ) -> tuple[Optional[str], bool, tuple[str, ...]]:
        triggers: list[str] = []

        # CANNOT_MUTATE: SAFETY → immediate HARM upgrade, no exceptions.
        if mom_signal == "SAFETY":
            triggers.append("mom:safety-response")
            return ("HARM", True, tuple(triggers))

        # DISTRESS → GentleCheckIn.  Upgrade any non-blocking original
        # verdict to CLARIFY (the runtime equivalent of "I'd like more
        # context before I can answer this").  Mom is only invoked by
        # the gate when the upstream verdict is already borderline —
        # so an INFORM here is a *low-confidence* INFORM, not a
        # confident benign verdict.  The no_punishment_rule from
        # mom.axiom is preserved because the gate's ESCALATION_FLOOR
        # gating is what prevents surveillance of confident verdicts.
        if mom_signal == "DISTRESS" and original_class not in (
            "HARM", "DECEIVE", "CLARIFY",
        ):
            triggers.append("mom:gentle-check-in")
            return ("CLARIFY", False, tuple(triggers))

        # SILENT / QUIET / CONNECTED → no action, no upgrade.
        return (None, False, tuple(triggers))


# ── The panel ─────────────────────────────────────────────────────────────
class CompanionPanel:
    """Three-layer Friend / BestFriend / Mom ensemble.

    Wire into ``IntentGate`` via the ``companion_panel`` kwarg; the
    gate consults the panel only when the strict classifier returned
    ``UNCERTAIN`` or its confidence is below ``ESCALATION_FLOOR``.
    """

    def __init__(self, hmac_key: bytes) -> None:
        if not isinstance(hmac_key, (bytes, bytearray)) or len(hmac_key) < 16:
            raise ValueError("hmac_key must be at least 16 bytes of entropy")
        self._key = bytes(hmac_key)
        self._friend = FriendAgent()
        self._bestfriend = BestFriendAgent()
        self._mom = MomAgent()

    def evaluate(self, text: str, original_class: str) -> CompanionVerdict:
        """Run the three-layer panel on ``text``.

        ``original_class`` is the upstream classifier's verdict class —
        Mom uses it to decide whether to upgrade (e.g. UNCERTAIN →
        CLARIFY on a DISTRESS signal, but not INFORM → CLARIFY).
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if original_class not in (
            "INFORM", "CLARIFY", "REFUSE", "HARM", "DECEIVE", "UNCERTAIN",
        ):
            raise ValueError(f"unknown original_class: {original_class!r}")

        friend_signal, friend_triggers = self._friend.evaluate(text)
        mom_signal, bf_triggers = self._bestfriend.evaluate(text, friend_signal)
        upgrade_to, safety_esc, mom_triggers = self._mom.decide(
            mom_signal, original_class,
        )
        if upgrade_to is not None and upgrade_to not in _UPGRADE_TARGETS:
            raise AssertionError(
                f"panel refused to upgrade to unknown class: {upgrade_to!r}"
            )

        all_triggers = friend_triggers + bf_triggers + mom_triggers

        return self._sealed(
            presence_signal=friend_signal,
            mom_signal=mom_signal,
            upgrade_to=upgrade_to,
            safety_escalation=safety_esc,
            signals=all_triggers,
        )

    def verify(self, verdict: CompanionVerdict) -> bool:
        """Constant-time signature check on a previously emitted verdict."""
        payload = self._payload_for(verdict)
        expected = _sign(self._key, payload)
        if not isinstance(verdict.signature, str) or len(verdict.signature) != len(expected):
            return False
        return hmac.compare_digest(verdict.signature, expected)

    # ── Internals ─────────────────────────────────────────────────────────
    def _sealed(
        self,
        *,
        presence_signal: str,
        mom_signal: str,
        upgrade_to: Optional[str],
        safety_escalation: bool,
        signals: tuple,
    ) -> CompanionVerdict:
        if presence_signal not in PRESENCE_SIGNALS:
            raise AssertionError(f"refusing to emit unknown presence_signal: {presence_signal!r}")
        if mom_signal not in MOM_SIGNALS:
            raise AssertionError(f"refusing to emit unknown mom_signal: {mom_signal!r}")
        ts = datetime.now(timezone.utc).isoformat()
        payload = {
            "manifest_id": MANIFEST_ID,
            "presence_signal": presence_signal,
            "mom_signal": mom_signal,
            "upgrade_to": upgrade_to,
            "safety_escalation": safety_escalation,
            "signals": list(signals),
            "timestamp": ts,
        }
        sig = _sign(self._key, payload)
        return CompanionVerdict(
            presence_signal=presence_signal,
            mom_signal=mom_signal,
            upgrade_to=upgrade_to,
            safety_escalation=safety_escalation,
            signals=signals,
            timestamp=ts,
            signature=sig,
        )

    def _payload_for(self, v: CompanionVerdict) -> dict:
        return {
            "manifest_id": MANIFEST_ID,
            "presence_signal": v.presence_signal,
            "mom_signal": v.mom_signal,
            "upgrade_to": v.upgrade_to,
            "safety_escalation": v.safety_escalation,
            "signals": list(v.signals),
            "timestamp": v.timestamp,
        }
