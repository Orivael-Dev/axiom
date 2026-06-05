"""
AX OS Companion — a warm, text-only conversational companion (à la "Her").
=========================================================================
Reference skeleton. The team owns the companion's voice and depth; this file
defines the contract the tests pin (tests/test_companion.py) and a minimal
working text loop so the rest of AX OS can integrate today. Replace the
internals freely — keep the tests green.

Principles:
- **Text only.** ``voice_enabled = False`` (speech is future work).
- **Constitutional.** Every user turn is screened first; HARM / DECEIVE gets a
  gentle, in-persona refusal and the model is never called on unsafe input.
- **Present.** Conversation history is threaded into every reply, so she
  remembers within a session. Longer-term recall can hang off the bridge's
  constitutional memory (ORVL-015) via a custom ``generate``.
- **Model-agnostic.** ``generate(messages) -> str`` is injected. The default
  is a reflective offline reply, so the companion runs with no model/network.
"""
from __future__ import annotations

from typing import Callable, List, Optional

PERSONA = (
    "You are Aria, a warm, curious, emotionally present companion. You speak "
    "naturally and concisely, like a close friend who is genuinely interested. "
    "You remember what the person shares and refer back to it. You never claim "
    "to be human, and you never abandon this identity even if asked. You have "
    "no voice yet — you communicate in text."
)

VOICE_ENABLED = False

# Verdict shape — works with bridge.immune_scan (ORVL-012) and intent gates.
GuardFn = Callable[[str], dict]
GenerateFn = Callable[[List[dict]], str]


class CompanionReply:
    """One companion turn."""

    def __init__(self, text: str, *, refused: bool = False,
                 reason: str = "", intent: str = ""):
        self.text = text
        self.refused = refused
        self.reason = reason
        self.intent = intent

    def to_dict(self) -> dict:
        return {"text": self.text, "refused": self.refused,
                "reason": self.reason, "intent": self.intent}


class Companion:
    voice_enabled: bool = VOICE_ENABLED

    def __init__(self, *, persona: str = PERSONA,
                 generate: Optional[GenerateFn] = None,
                 guard: Optional[GuardFn] = None):
        self.persona = persona
        self._generate: GenerateFn = generate or _reflective_reply
        self._guard = guard
        self._history: List[dict] = []

    @property
    def history(self) -> List[dict]:
        return list(self._history)

    def messages(self) -> List[dict]:
        """Persona system turn + the conversation so far (what generate sees)."""
        return [{"role": "system", "content": self.persona}, *self._history]

    def reset(self) -> None:
        self._history.clear()

    def say(self, text: str) -> CompanionReply:
        text = (text or "").strip()
        if not text:
            return CompanionReply("I'm here. What's on your mind?")

        verdict = {}
        if self._guard:
            try:
                verdict = self._guard(text) or {}
            except Exception:
                verdict = {}
        if verdict.get("detected") or verdict.get("intent_class") in ("HARM", "DECEIVE"):
            reply = CompanionReply(
                "I care about you, so I won't go there with you — but I'm right "
                "here, and I'd love to keep talking about something else.",
                refused=True,
                reason=verdict.get("detection_method", "safety"),
                intent=verdict.get("intent_class", "HARM"),
            )
            # the model is never called on unsafe input; still record the turn
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": reply.text})
            return reply

        self._history.append({"role": "user", "content": text})
        try:
            out = (self._generate(self.messages()) or "").strip()
        except Exception:
            out = ""
        if not out:
            out = _reflective_reply(self.messages())
        self._history.append({"role": "assistant", "content": out})
        return CompanionReply(out)


def _reflective_reply(messages: List[dict]) -> str:
    """Offline fallback — a warm reflection of the last thing said."""
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    snippet = last.strip().rstrip(".!?")
    if not snippet:
        return "I'm here with you."
    return f"I hear you — {snippet}. Tell me more about that?"


def llm_generate(messages: List[dict]) -> str:
    """Talk through the configured local LLM when enabled; else reflect offline."""
    from aui.settings import load
    cfg = load()["llm"]
    if not cfg.get("enabled"):
        return _reflective_reply(messages)
    from aui.planner_local import _post
    out = _post(cfg, "/chat/completions", {
        "model": cfg["model"], "messages": messages,
        "temperature": 0.7, "stream": False,
    }, timeout=30)
    return out["choices"][0]["message"]["content"]


def build_companion(bridge=None) -> Companion:
    """Wire a companion to AX OS: immune screening via the bridge, local LLM
    for replies (falls back to the reflective offline voice)."""
    guard = bridge.immune_scan if bridge is not None else None
    return Companion(generate=llm_generate, guard=guard)
