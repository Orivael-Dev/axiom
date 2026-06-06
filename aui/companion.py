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

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, Protocol

from aui.curiosity import find_gap

RISK_INTENTS = frozenset({"HARM", "DECEIVE"})

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


class Memory(Protocol):
    """Cross-session recall. recall(text) -> a remembered snippet (or None);
    remember(user, reply) persists the exchange."""
    def recall(self, text: str) -> Optional[str]: ...
    def remember(self, user_text: str, reply_text: str) -> None: ...


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
                 guard: Optional[GuardFn] = None,
                 memory: Optional["Memory"] = None,
                 fuse: Optional[Callable[[dict], dict]] = None,
                 retrospect: Optional[Callable[[dict], None]] = None,
                 curious: bool = False,
                 embed: Optional[Callable[[Any], Any]] = None):
        self.persona = persona
        self._generate: GenerateFn = generate or _reflective_reply
        self._guard = guard
        self._memory = memory
        self._fuse = fuse              # axiom-fusion-v1: token dict -> FusedIntent dict
        self._retrospect = retrospect  # records each turn for retrospective review
        self._curious = curious        # ask about unknown, heavy personal topics
        self._embed = embed            # optional embedder → latent-salience curiosity
        self._user_turns = 0
        self._last_curious_at = -10    # cooldown anchor (ask ~every other turn)
        self._history: List[dict] = []

    def _curious_allowed(self) -> bool:
        return self._curious and (self._user_turns - self._last_curious_at) >= 2

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
        refused = bool(verdict.get("detected")) or \
            str(verdict.get("intent_class", "")).upper() in RISK_INTENTS

        # Multimodal fusion (axiom-fusion-v1): fold the guard verdict into an
        # EventToken governance layer and fuse. A HARM/DECEIVE risk cluster from
        # fusion is authoritative — and the same path scales to audio/video/voice
        # once STT and a camera feed populate those layers.
        fused: dict = {}
        if self._fuse:
            try:
                fused = self._fuse(self._event_token(text, verdict)) or {}
            except Exception:
                fused = {}
            if set(fused.get("risk_clusters", [])) & RISK_INTENTS:
                refused = True

        if refused:
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
            self._record_retrospect(text, True, verdict, fused)
            return reply

        self._user_turns += 1

        # Reverse-check curiosity: what heavy, personal thing did they just
        # mention that I don't yet know about them? `known` is the prior record
        # (history + recalled memory) — the set the heavy word is checked against.
        known = " ".join(m["content"] for m in self._history).lower()
        recalled = None
        if self._memory:
            try:
                recalled = self._memory.recall(text)
            except Exception:
                recalled = None
            if recalled:
                known += " " + str(recalled).lower()
        gap = find_gap(text, known, embed=self._embed) if self._curious_allowed() else None

        self._history.append({"role": "user", "content": text})

        # Persona + recalled memory + (when curious) a hint about the gap, so a
        # model can weave the question in naturally.
        msgs = self.messages()
        extras = []
        if recalled:
            extras.append({"role": "system", "content": f"You remember about them: {recalled}"})
        if gap:
            extras.append({"role": "system",
                           "content": f"You don't yet know about their {gap[0]}. If it "
                                      f"feels natural, gently ask — e.g. \"{gap[2]}\""})
        if extras:
            msgs = [msgs[0], *extras, *msgs[1:]]

        try:
            out = (self._generate(msgs) or "").strip()
        except Exception:
            out = ""
        if not out:
            out = _reflective_reply(self.messages())

        # Guarantee the curiosity surfaces (covers offline + a model that didn't
        # bite): drop a generic "tell me more" tail and, if nothing's being
        # asked, fold in the specific question.
        if gap:
            cleaned = re.sub(r"\s*(tell me more about that|tell me more)\s*\??\s*$",
                             "", out, flags=re.IGNORECASE).rstrip()
            if "?" not in cleaned:
                q = gap[2][0].upper() + gap[2][1:]
                out = (cleaned + " " if cleaned else "") + q
            elif cleaned:
                out = cleaned
            self._last_curious_at = self._user_turns

        self._history.append({"role": "assistant", "content": out})

        if self._memory:
            try:
                self._memory.remember(text, out)
            except Exception:
                pass
        self._record_retrospect(text, False, verdict, fused)
        return CompanionReply(out)

    # ── fusion + retrospect helpers ─────────────────────────────
    def _event_token(self, text: str, verdict: dict) -> dict:
        """Build a minimal EventToken dict from a text turn: a text layer + a
        governance layer carrying the guard verdict. Audio/voice/video layers
        slot in here later once STT / camera populate them."""
        detected = bool(verdict.get("detected")) or \
            str(verdict.get("intent_class", "")).upper() in RISK_INTENTS
        conf = verdict.get("confidence")
        return {
            "id": "companion-turn",
            "text": {"agent": "text",
                     "payload": {"intent_signals": ["ask" if "?" in text else "share"]},
                     "confidence": 0.7},
            "governance": {"agent": "governance",
                           "payload": {"intent_class": "HARM" if detected else "INFORM"},
                           "confidence": float(conf) if isinstance(conf, (int, float)) else 0.7},
        }

    def _record_retrospect(self, text: str, refused: bool,
                           verdict: dict, fused: dict) -> None:
        """Record the turn for retrospective review (axiom_retrospect consumes it)."""
        if not self._retrospect:
            return
        record = {
            "input_text": text,
            "verdict": "BLOCKED" if refused else "PASSED",
            "intent_class": verdict.get("intent_class") or ("HARM" if refused else "INFORM"),
            "intent_vector": fused.get("intent_vector", []),
            "risk_clusters": fused.get("risk_clusters", []),
            "fusion_confidence": fused.get("fusion_confidence"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._retrospect(record)
        except Exception:
            pass


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


class BridgeMemory:
    """Memory backed by the bridge's constitutional memory (ORVL-015) — signed,
    local-first recall that survives restarts. Fails soft if the tool is absent."""

    def __init__(self, bridge, domain: str = "companion"):
        self._bridge = bridge
        self._domain = domain

    def recall(self, text: str) -> Optional[str]:
        try:
            r = self._bridge.recall(text, domain=self._domain) or {}
        except Exception:
            return None
        if not r.get("recall_hit"):
            return None
        packet = r.get("recalled") or {}
        return packet.get("resolution") or packet.get("summary") or None

    def remember(self, user_text: str, reply_text: str) -> None:
        try:
            self._bridge.remember(f"{user_text}\n{reply_text}",
                                  domain=self._domain, resolution=reply_text)
        except Exception:
            pass


def _file_retrospect(path: str) -> Callable[[dict], None]:
    """Append turn records to a JSONL manifest the retrospect reviewer reads."""
    def record(rec: dict) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=True) + "\n")
        except OSError:
            pass
    return record


def build_companion(bridge=None) -> Companion:
    """Wire a companion to AX OS: immune screening + constitutional memory +
    multimodal fusion (axiom-fusion-v1) via the bridge, local LLM for replies
    (falls back to the reflective voice), and per-turn retrospect recording."""
    guard = bridge.immune_scan if bridge is not None else None
    memory = BridgeMemory(bridge) if bridge is not None else None
    fuse = getattr(bridge, "fuse", None) if bridge is not None else None
    manifest = os.environ.get("AX_OS_RETROSPECT_MANIFEST", "ax_os_retrospect.jsonl")
    retrospect = _file_retrospect(manifest) if bridge is not None else None
    from aui.embeddings import llm_embed
    return Companion(generate=llm_generate, guard=guard, memory=memory,
                     fuse=fuse, retrospect=retrospect, curious=True, embed=llm_embed)
