"""
Knowledge — Aria searches to answer questions she doesn't know, summarises the
result as a tl;dr in conversation, and (via memory) retains it so next time she
answers from memory instead of re-searching. The retain step is the self-
learning loop: recall-first, search-on-miss, remember-after.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

# A factual question worth looking up — interrogative, and not aimed at Aria
# herself (those are conversational and handled by the normal reply path).
_Q = re.compile(
    r"^\s*(what|whats|what's|who|whom|whose|when|where|why|which|how|is|are|was|were|"
    r"does|do|did|can|could|should|define|name|tell me about|explain)\b",
    re.IGNORECASE,
)
_ABOUT_ARIA = re.compile(r"\b(you|your|yours|yourself|aria|u)\b", re.IGNORECASE)
_MORE = re.compile(
    r"\b(more|details?|sources?|links?|elaborate|expand|full|where.*from|read more)\b",
    re.IGNORECASE,
)


def is_knowledge_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if not (t.endswith("?") or _Q.match(t)):
        return False
    if _ABOUT_ARIA.search(t):     # "how are you?", "what's your name?" → conversational
        return False
    return True


def is_more_request(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(_MORE.search(t)) and len(t.split()) <= 7


def tldr(question: str, results: dict,
         summarize: Optional[Callable[[str, list, list], str]] = None) -> str:
    """A 1–2 sentence answer from search results. Uses the LLM summariser when
    available; otherwise an extractive fallback. Raw results are never shown —
    only this tl;dr goes into the conversation."""
    results = results or {}
    answers = results.get("answers") or []
    visible = [h for h in (results.get("results") or []) if not h.get("blocked")]

    if summarize:
        try:
            s = (summarize(question, answers, visible) or "").strip()
            if s:
                return s
        except Exception:
            pass

    if answers:
        return f"tl;dr — {answers[0]}"
    if visible:
        snippet = (visible[0].get("content") or visible[0].get("title") or "").strip()
        if snippet:
            first = re.split(r"(?<=[.!?])\s", snippet)[0]
            return f"tl;dr — {first.rstrip('.')}."
    if not results.get("ok"):
        return "I tried to look that up but couldn't reach the web just now."
    return "I looked, but couldn't find a clear answer to that."


def sources_block(results: dict, n: int = 3) -> str:
    visible = [h for h in (results or {}).get("results", []) if not h.get("blocked")][:n]
    if not visible:
        return "I don't have sources saved for that."
    lines = [f"• {h.get('title') or h.get('url')} — {h.get('url')}" for h in visible if h.get("url")]
    return "Here's where that came from:\n" + "\n".join(lines)
