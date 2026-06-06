"""
Curiosity — surface what the companion doesn't yet know about the person.
=========================================================================
A lightweight "weight of words" pass over a turn. For each heavy, self-
referential topic the person mentions, do a **reverse check**: is this already
connected to the person in what I know (conversation history + recalled
memory)? If a heavy topic is *unknown* and above the weight threshold, turn the
heaviest one into a gentle follow-up question. Once asked, the word enters the
record, so the reverse check stops Aria re-asking — self-limiting.

This is the deterministic first-step (works offline). When a model is driving,
the same gap is injected as a hint so the LLM can weave the curiosity in
naturally rather than ask a canned question.
"""
from __future__ import annotations

import re
from typing import Optional

# Curiosity only fires on personal statements — that's where the weight is.
_SELF = re.compile(r"\b(i|i'm|im|i've|ive|i'll|my|mine|me)\b", re.IGNORECASE)

# Heavy personal-life topics → (weight, question). Weight = how much an unknown
# here matters / how worth asking it is.
TOPICS = {
    "work":     (1.0, "what kind of work do you do?"),
    "job":      (1.0, "what do you do for work?"),
    "school":   (0.9, "what are you studying?"),
    "college":  (0.9, "what are you studying?"),
    "kids":     (0.95, "how many kids do you have?"),
    "kid":      (0.95, "tell me about your kid?"),
    "son":      (0.9, "how old is your son?"),
    "daughter": (0.9, "how old is your daughter?"),
    "family":   (0.8, "tell me about your family?"),
    "partner":  (0.85, "how did you two meet?"),
    "dog":      (0.75, "what's your dog's name?"),
    "cat":      (0.75, "what's your cat's name?"),
    "trip":     (0.7, "where are you headed?"),
    "vacation": (0.7, "where are you going?"),
    "move":     (0.8, "where are you moving to?"),
    "home":     (0.65, "where's home for you?"),
    "town":     (0.65, "what town are you in?"),
    "city":     (0.65, "what city are you in?"),
    "band":     (0.7, "what band?"),
    "team":     (0.65, "which team do you follow?"),
    "project":  (0.7, "what are you building?"),
    "hobby":    (0.7, "what do you like to do?"),
}

# Generic "my <noun>" still carries weight; skip low-value nouns.
_GENERIC_WEIGHT = 0.6
_STOP_NOUNS = frozenset({
    "day", "time", "way", "thing", "things", "lot", "bit", "life", "mind",
    "head", "hand", "side", "turn", "point", "part", "place", "name", "self",
    "phone", "stuff", "guess", "idea", "plan", "week", "weekend", "morning",
    "night", "today", "tomorrow", "year", "month",
})

WEIGHT_THRESHOLD = 0.6


def find_gap(text: str, known: str) -> Optional[tuple]:
    """Return (topic, weight, question) for the heaviest *unknown* personal
    topic in ``text``, or None. ``known`` is everything already connected to the
    person (prior history + recalled memory), lowercased — the reverse-check set.
    """
    if not text or not _SELF.search(text):
        return None
    low = text.lower()
    known = known or ""

    candidates: list[tuple] = []  # (weight, topic, question)
    for topic, (w, q) in TOPICS.items():
        if re.search(rf"\b{re.escape(topic)}\b", low):
            candidates.append((w, topic, q))
    for m in re.finditer(r"\bmy (\w+)", low):
        noun = m.group(1)
        if noun in TOPICS or noun in _STOP_NOUNS or len(noun) < 3:
            continue
        candidates.append((_GENERIC_WEIGHT, noun, f"tell me more about your {noun}?"))

    # reverse check: drop anything already connected to the person, keep heavy ones
    unknown = [(w, t, q) for (w, t, q) in candidates
               if w >= WEIGHT_THRESHOLD and not re.search(rf"\b{re.escape(t)}\b", known)]
    if not unknown:
        return None
    w, t, q = max(unknown, key=lambda c: c[0])
    return (t, w, q)


def curious_reflection(text: str, question: str) -> str:
    """An offline reply that mirrors what they said and asks the follow-up."""
    snippet = text.strip().rstrip(".!?")
    if len(snippet) > 60:
        snippet = snippet[:60].rsplit(" ", 1)[0] + "…"
    lead = f"I hear you — {snippet}. " if snippet else ""
    return f"{lead}{question[0].upper()}{question[1:]}"
