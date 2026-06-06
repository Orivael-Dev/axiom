"""
Curiosity — surface what the companion doesn't yet know about the person.
=========================================================================
A "weight of words" pass over a turn. For each heavy, self-referential topic
the person mentions, do a **reverse check**: is this already connected to the
person in what I know (history + recalled memory)? If a heavy topic is unknown
and above threshold, turn the heaviest one into a gentle follow-up question.

Two scorers:
  - keyword (offline, deterministic) — a curated weight table + a binary
    reverse check (is the word already in the record?).
  - embedded (when a local-LLM `embed` callable is available) — true latent
    salience: weight = personal-salience(candidate) × novelty(candidate vs what's
    known), both from embedding cosine. This is the reverse-latent idea with real
    vectors: heavy = personally salient AND new. Falls back to keyword on any
    embedding failure.
"""
from __future__ import annotations

import math
import re
from typing import Callable, List, Optional, Sequence

# Curiosity only fires on personal statements — that's where the weight is.
_SELF = re.compile(r"\b(i|i'm|im|i've|ive|i'll|my|mine|me|we|our)\b", re.IGNORECASE)

# Heavy personal-life topics → (weight, question). Weight = how much an unknown
# here matters / how worth asking it is.
TOPICS = {
    # work & career
    "work": (1.0, "what kind of work do you do?"),
    "job": (1.0, "what do you do for work?"),
    "career": (0.95, "what's your line of work?"),
    "company": (0.8, "what does the company do?"),
    "startup": (0.85, "what are you building?"),
    "business": (0.8, "what's the business?"),
    "role": (0.75, "what's the role?"),
    "shift": (0.7, "what do you do?"),
    "interview": (0.85, "what's the interview for?"),
    "promotion": (0.8, "what's the new role?"),
    "boss": (0.6, "what's your boss like?"),
    # education
    "school": (0.9, "what are you studying?"),
    "college": (0.9, "what are you studying?"),
    "university": (0.9, "what are you studying?"),
    "degree": (0.85, "a degree in what?"),
    "major": (0.85, "what's your major?"),
    "study": (0.8, "what are you studying?"),
    "class": (0.65, "what class?"),
    "exam": (0.65, "an exam in what?"),
    "thesis": (0.8, "what's your thesis on?"),
    # family & relationships
    "family": (0.8, "tell me about your family?"),
    "kids": (0.95, "how many kids do you have?"),
    "kid": (0.95, "tell me about your kid?"),
    "son": (0.9, "how old is your son?"),
    "daughter": (0.9, "how old is your daughter?"),
    "wife": (0.9, "how did you two meet?"),
    "husband": (0.9, "how did you two meet?"),
    "partner": (0.85, "how did you two meet?"),
    "girlfriend": (0.85, "how did you two meet?"),
    "boyfriend": (0.85, "how did you two meet?"),
    "mom": (0.8, "are you close with your mom?"),
    "dad": (0.8, "are you close with your dad?"),
    "parents": (0.8, "where do your parents live?"),
    "brother": (0.75, "older or younger brother?"),
    "sister": (0.75, "older or younger sister?"),
    "baby": (0.9, "congratulations — how old?"),
    "wedding": (0.9, "whose wedding?"),
    "marriage": (0.85, "how long have you been married?"),
    "friend": (0.65, "how do you two know each other?"),
    # pets
    "dog": (0.75, "what's your dog's name?"),
    "cat": (0.75, "what's your cat's name?"),
    "puppy": (0.8, "what's the puppy's name?"),
    "pet": (0.7, "what kind of pet?"),
    # places
    "home": (0.65, "where's home for you?"),
    "apartment": (0.7, "what part of town?"),
    "town": (0.65, "what town are you in?"),
    "city": (0.65, "what city are you in?"),
    "hometown": (0.8, "where's your hometown?"),
    "country": (0.7, "which country?"),
    "move": (0.8, "where are you moving to?"),
    "moving": (0.8, "where are you moving to?"),
    # hobbies & interests
    "hobby": (0.7, "what do you like to do?"),
    "band": (0.7, "what band?"),
    "guitar": (0.7, "how long have you played?"),
    "music": (0.65, "what do you listen to?"),
    "art": (0.7, "what kind of art?"),
    "painting": (0.7, "what do you like to paint?"),
    "book": (0.65, "what are you reading?"),
    "writing": (0.7, "what are you writing?"),
    "novel": (0.75, "what's your novel about?"),
    "game": (0.6, "what game?"),
    "gym": (0.65, "what are you training for?"),
    "running": (0.65, "training for anything?"),
    "hiking": (0.7, "where do you like to hike?"),
    "climbing": (0.7, "indoor or outdoor?"),
    "cooking": (0.65, "what do you like to cook?"),
    "garden": (0.65, "what are you growing?"),
    "photography": (0.7, "what do you shoot?"),
    "project": (0.7, "what are you building?"),
    "travel": (0.7, "where to?"),
    "trip": (0.7, "where are you headed?"),
    "vacation": (0.7, "where are you going?"),
    # life events (gentle, supportive)
    "birthday": (0.7, "whose birthday?"),
    "graduation": (0.85, "graduating in what?"),
    "surgery": (0.7, "is everything okay?"),
    "recovery": (0.7, "how are you feeling?"),
}

# Generic "my <noun>" still carries weight; skip low-value nouns.
_GENERIC_WEIGHT = 0.6
_STOP_NOUNS = frozenset({
    "day", "time", "way", "thing", "things", "lot", "bit", "life", "mind",
    "head", "hand", "side", "turn", "point", "part", "place", "name", "self",
    "phone", "stuff", "guess", "idea", "plan", "week", "weekend", "morning",
    "night", "today", "tomorrow", "year", "month", "moment", "minute", "hour",
})
# Obvious non-topic words to keep the embedding candidate set small.
_FUNCTION_WORDS = frozenset({
    "have", "just", "really", "going", "gonna", "want", "need", "feel", "think",
    "know", "like", "been", "have", "with", "that", "this", "they", "them",
    "from", "about", "would", "could", "should", "there", "here", "what", "when",
    "where", "your", "yours", "still", "much", "very", "some", "more", "most",
    "also", "even", "than", "then", "into", "over", "back", "down", "good", "nice",
})

WEIGHT_THRESHOLD = 0.6
EMBED_THRESHOLD = 0.30

# Person-facet anchors — embedded candidates score salience against these.
_ANCHORS = (
    "their job or career",
    "their family and relationships",
    "where they live",
    "their hobbies and interests",
    "their education or studies",
    "their pet",
    "their health and how they're feeling",
)

_WORD = re.compile(r"[a-z][a-z'-]{2,}", re.IGNORECASE)
_EMBED_UNAVAILABLE = object()


def _candidates(text: str, broad: bool) -> List[tuple]:
    """(weight, topic, question) candidates from a turn. `broad` adds plain
    content words so embedding salience — not the keyword list — picks the topic."""
    low = text.lower()
    out: List[tuple] = []
    seen: set = set()
    for topic, (w, q) in TOPICS.items():
        if topic not in seen and re.search(rf"\b{re.escape(topic)}\b", low):
            out.append((w, topic, q))
            seen.add(topic)
    for m in re.finditer(r"\bmy ([a-z]+)", low):
        noun = m.group(1)
        if noun in seen or noun in _STOP_NOUNS or len(noun) < 3:
            continue
        out.append((_GENERIC_WEIGHT, noun, f"tell me more about your {noun}?"))
        seen.add(noun)
    if broad:
        for tok in _WORD.findall(low):
            wl = tok.lower()
            if (wl in seen or wl in _STOP_NOUNS or wl in _FUNCTION_WORDS or len(wl) < 4):
                continue
            out.append((_GENERIC_WEIGHT, wl, f"tell me more about your {wl}?"))
            seen.add(wl)
    return out


def find_gap(text: str, known: str,
             embed: Optional[Callable[[Sequence[str]], Optional[list]]] = None) -> Optional[tuple]:
    """Return (topic, weight, question) for the heaviest *unknown* personal topic
    in ``text``, or None. Uses embedding salience when ``embed`` is available,
    else the keyword heuristic. ``known`` = prior history + recalled memory."""
    if not text or not _SELF.search(text):
        return None
    cands = _candidates(text, broad=embed is not None)
    if not cands:
        return None
    if embed is not None:
        res = _embed_gap(cands, known or "", embed)
        if res is not _EMBED_UNAVAILABLE:
            return res
    return _keyword_gap(cands, known or "")


def _keyword_gap(cands: List[tuple], known: str) -> Optional[tuple]:
    low = known.lower()
    unknown = [(w, t, q) for (w, t, q) in cands
               if w >= WEIGHT_THRESHOLD and not re.search(rf"\b{re.escape(t)}\b", low)]
    if not unknown:
        return None
    w, t, q = max(unknown, key=lambda c: c[0])
    return (t, w, q)


def _embed_gap(cands: List[tuple], known: str, embed) -> object:
    """Latent salience: weight = personal-salience × novelty, from cosine."""
    terms = [t for (_w, t, _q) in cands]
    chunks = _chunks(known)
    try:
        vecs = embed(list(terms) + list(_ANCHORS) + chunks)
    except Exception:
        vecs = None
    if not vecs or len(vecs) != len(terms) + len(_ANCHORS) + len(chunks):
        return _EMBED_UNAVAILABLE
    nt, na = len(terms), len(_ANCHORS)
    cand_v, anch_v, known_v = vecs[:nt], vecs[nt:nt + na], vecs[nt + na:]
    best = None
    for i, (_w, t, q) in enumerate(cands):
        sal = max((_cos(cand_v[i], a) for a in anch_v), default=0.0)
        nov = 1.0 - (max((_cos(cand_v[i], k) for k in known_v), default=0.0) if known_v else 0.0)
        score = max(0.0, sal) * max(0.0, nov)
        if best is None or score > best[1]:
            best = (t, round(score, 4), q)
    return best if (best and best[1] >= EMBED_THRESHOLD) else None


def _chunks(known: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"[.!?\n]+", known) if p.strip()]
    return parts[-12:]  # bound the embedding payload


def _cos(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def curious_reflection(text: str, question: str) -> str:
    """An offline reply that mirrors what they said and asks the follow-up."""
    snippet = text.strip().rstrip(".!?")
    if len(snippet) > 60:
        snippet = snippet[:60].rsplit(" ", 1)[0] + "…"
    lead = f"I hear you — {snippet}. " if snippet else ""
    return f"{lead}{question[0].upper()}{question[1:]}"
