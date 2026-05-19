"""DelegateRouter — cheap, non-LLM router from event content to AXM delegates.

The router runs the existing IntentClassifier (microseconds, no LLM,
already signed) and returns the ordered list of SkillDelegate names
whose `intent_classes` tuple contains the classified intent. Used by
`Coordinator.compose_from_delegates()` to pick which 1..N delegates
should actually fire — that selectivity is where the per-event token
savings come from.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from axiom_signing import derive_key


@dataclass(frozen=True)
class RoutingDecision:
    """What the router decided + why. Surfaced into the LayerReport payload."""
    intent_class:  str
    confidence:    float
    delegate_names: tuple[str, ...]
    matched_on:    str   # "text" | "audio_transcript" | "empty"


class DelegateRouter:
    """Pure function: (event content, delegate set) -> ordered delegate names.

    Construction lazily builds an IntentClassifier under the firewall
    key so the router is callable with no further setup. Pass an
    explicit classifier instance to share across components.
    """

    def __init__(self, classifier=None) -> None:
        if classifier is None:
            from axiom_intent_classifier import IntentClassifier
            classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))
        self._clf = classifier

    def route(
        self,
        *,
        delegates: Sequence,        # Sequence[SkillDelegate]
        text:      Optional[str] = None,
        audio_transcript: Optional[str] = None,
    ) -> RoutingDecision:
        """Pick the delegates that should run for this event.

        Classification uses `text` if present, otherwise `audio_transcript`.
        Both empty → no delegates matched (caller falls back to default).
        """
        body = text if text else audio_transcript
        matched_on = "text" if text else (
            "audio_transcript" if audio_transcript else "empty"
        )
        if not body:
            return RoutingDecision(
                intent_class="UNCERTAIN", confidence=0.0,
                delegate_names=(), matched_on="empty",
            )
        result = self._clf.classify(body)
        intent = result.intent_class
        # Map AXIOM intent classes → canonical lowercase tags so AXM
        # delegate manifests can use either case ("harm", "HARM", etc.)
        intent_variants = {intent, intent.lower()}
        picked: List[str] = []
        for d in delegates:
            d_intents = {i for i in d.intent_classes} | {
                i.lower() for i in d.intent_classes
            }
            if intent_variants & d_intents:
                if d.name not in picked:
                    picked.append(d.name)
        return RoutingDecision(
            intent_class=intent,
            confidence=float(result.confidence),
            delegate_names=tuple(picked),
            matched_on=matched_on,
        )
