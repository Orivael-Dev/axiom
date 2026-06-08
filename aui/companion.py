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
from aui.knowledge import is_knowledge_question, is_more_request, tldr, sources_block
from aui.master_token import MasterEventToken
from aui.qrf import QRFEngine
from aui.redact import redact_secrets

RISK_INTENTS = frozenset({"HARM", "DECEIVE"})

# What Aria does once the reverse-QRF prediction is mature (threshold reached):
# (model hint, offline suffix, suffix key-word to avoid doubling).
_ANTICIPATE = {
    "QUERY":     ("they tend to ask things — proactively offer to look anything up",
                  "I can look that up whenever you want.", "look"),
    "CLARIFY":   ("they may want things clearer — be concise and offer to expand",
                  "Tell me if you'd like me to slow down.", "slow"),
    "UNCERTAIN": ("they may be unsure — gently reassure and check in",
                  "No rush — we can take this at your pace.", "rush"),
}

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
                 reason: str = "", intent: str = "",
                 attributes: Optional[dict] = None):
        self.text = text
        self.refused = refused
        self.reason = reason
        self.intent = intent
        self.attributes = attributes or {}

    def to_dict(self) -> dict:
        return {"text": self.text, "refused": self.refused,
                "reason": self.reason, "intent": self.intent,
                "attributes": self.attributes}


class Companion:
    voice_enabled: bool = VOICE_ENABLED

    def __init__(self, *, persona: str = PERSONA,
                 generate: Optional[GenerateFn] = None,
                 guard: Optional[GuardFn] = None,
                 memory: Optional["Memory"] = None,
                 fuse: Optional[Callable[[dict], dict]] = None,
                 retrospect: Optional[Callable[[dict], None]] = None,
                 curious: bool = False,
                 embed: Optional[Callable[[Any], Any]] = None,
                 search: Optional[Callable[[str], dict]] = None,
                 summarize: Optional[Callable[[str, list, list], str]] = None,
                 anticipation_cfg: Optional[Callable[[], dict]] = None,
                 delegate: Optional[Callable[[str], dict]] = None,
                 genesis: str = "",
                 persona_sig: str = "",
                 session_id: str = "companion"):
        self.persona = persona
        self._generate: GenerateFn = generate or _reflective_reply
        self._guard = guard
        self._memory = memory
        self._fuse = fuse              # axiom-fusion-v1: token dict -> FusedIntent dict
        self._retrospect = retrospect  # records each turn for retrospective review
        self._curious = curious        # ask about unknown, heavy personal topics
        self._embed = embed            # optional embedder → latent-salience curiosity
        self._search = search          # web search to answer unknown questions
        self._summarize = summarize    # tl;dr summariser for search results
        self._antic_cfg = anticipation_cfg  # () -> dict of QRF thresholds (settings)
        self._delegate = delegate      # (task) -> run handle; hands a build task to the autonomous agent
        self._session_id = session_id
        self._genesis = genesis        # persona identity_signature → MET genesis
        self._persona_sig = persona_sig  # current token_signature (soul + brain) → stamped per turn
        self._master = MasterEventToken(session_id, genesis=genesis)  # MET chain
        self._qrf = QRFEngine()        # reverse-QRF predictor fed by the MET chain
        self._last_search: dict = {}
        self._user_turns = 0
        self._last_curious_at = -10    # cooldown anchor (ask ~every other turn)
        self._last_anticipated_at = -10
        self._history: List[dict] = []

    def _anticipatory_guidance(self):
        """Once the reverse-QRF prediction is mature, return (model_hint, suffix,
        key) for what Aria should proactively do — else None. Cooldown-gated."""
        cfg = self._antic_settings()
        if not cfg["enabled"]:
            return None
        a = self.anticipation
        if not a.get("mature"):
            return None
        g = _ANTICIPATE.get(a["predicted_next_intent"])
        if not g or (self._user_turns - self._last_anticipated_at) < cfg["cooldown"]:
            return None
        return g

    @property
    def master_token(self) -> MasterEventToken:
        return self._master

    @property
    def persona_anchor(self) -> dict:
        """What the MET chain points back to: Aria's soul (genesis) and the
        persona token (soul + brain) stamped onto turns."""
        return {"identity_signature": self._genesis,
                "token_signature": self._persona_sig}

    def _antic_settings(self) -> dict:
        from aui.qrf import MATURE_MIN_OBS, MATURE_CONF, MATURE_HIT
        c = {}
        if self._antic_cfg:
            try:
                c = self._antic_cfg() or {}
            except Exception:
                c = {}
        return {
            "enabled": bool(c.get("enabled", True)),
            "min_obs": int(c.get("min_obs", MATURE_MIN_OBS)),
            "min_confidence": float(c.get("min_confidence", MATURE_CONF)),
            "min_hit_rate": float(c.get("min_hit_rate", MATURE_HIT)),
            "cooldown": int(c.get("cooldown", 3)),
        }

    @property
    def anticipation(self) -> dict:
        """Reverse-QRF's current read on the next turn (fed by the MET chain),
        with maturity judged against the configurable thresholds."""
        cfg = self._antic_settings()
        a = self._qrf.anticipation(min_obs=cfg["min_obs"],
                                   conf_threshold=cfg["min_confidence"],
                                   hit_threshold=cfg["min_hit_rate"])
        if not cfg["enabled"]:
            a["mature"] = False
        return a

    def _curious_allowed(self) -> bool:
        return self._curious and (self._user_turns - self._last_curious_at) >= 2

    def _commit_turn(self, intent: str, fused: dict, learned: bool = False) -> None:
        self._master.add_turn(intent_class=intent,
                              risk_clusters=(fused or {}).get("risk_clusters", []),
                              fusion_signature=(fused or {}).get("signature", ""),
                              learned=learned, persona_sig=self._persona_sig)
        # feed the MET chain into the reverse-QRF predictor (learned turns weighted)
        self._qrf.step(intent or "INFORM", learned=learned)

    @property
    def history(self) -> List[dict]:
        return list(self._history)

    def messages(self) -> List[dict]:
        """Persona system turn + the conversation so far (what generate sees)."""
        return [{"role": "system", "content": self.persona}, *self._history]

    def reset(self) -> None:
        # auto-consolidate the window to retrospect first, so a cleared session
        # isn't lost (consolidate() redacts secrets before recording).
        if self._retrospect and self._history:
            try:
                self.consolidate()
            except Exception:
                pass
        self._history.clear()
        self._master = MasterEventToken(self._session_id, genesis=self._genesis)
        self._qrf.reset()
        self._last_search = {}

    def apply_persona(self, token) -> None:
        """Adopt an edited PersonaToken. A true identity change (new
        identity_signature) re-grounds the persona text and starts a fresh
        conversation root. An outfit change (same soul, new brain/voice — e.g.
        swapping to mistral-7b) keeps the conversation, but updates the persona
        signature stamped onto subsequent MET turns so the chain still points
        back to the exact Aria that spoke."""
        self._persona_sig = getattr(token, "token_signature", self._persona_sig)
        if token.identity_signature != self._genesis:
            self.persona = token.persona_text()
            self._genesis = token.identity_signature
            self.reset()

    def say(self, text: str, *, seen: Optional[str] = None) -> CompanionReply:
        text = (text or "").strip()
        # Vision: a caption from Aria's eyes (a VLM, screened upstream) is folded
        # into the turn as grounding — so even a text-only brain "sees". It flows
        # through the same guard, fusion, curiosity and history as typed text,
        # which is the point: an image's caption is screened like any other input.
        seen = (seen or "").strip()
        if seen:
            grounding = f"[You can see: {seen}]"
            text = f"{grounding}\n\n{text}" if text else grounding
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
            self._commit_turn(reply.intent or "HARM", fused)
            self._record_retrospect(text, True, verdict, fused)
            return reply

        # Delegation: a clear build/implement request → hand it to the autonomous
        # agent (already immune-screened above; the agent re-gates it too). Aria
        # acknowledges with the run handle and keeps it working in the background.
        if self._delegate is not None and _is_build_request(text):
            try:
                res = self._delegate(text) or {}
            except Exception:
                res = {}
            if res.get("ok"):
                out = (f"On it — I've handed that to the autonomous agent "
                       f"(run {res['run_id']}). It'll plan, edit in a sandbox and "
                       f"run tests in the background; check the autonomous workspace "
                       f"for progress.")
                self._history.append({"role": "user", "content": text})
                self._history.append({"role": "assistant", "content": out})
                self._commit_turn("BUILD", fused)
                self._record_retrospect(text, False, verdict, fused)
                return CompanionReply(out, intent="BUILD",
                                      attributes={"run_id": res["run_id"]})

        # Knowledge: answer a factual question by recall-first, search-on-miss,
        # then retain (self-learning). Raw results never surface — only a tl;dr.
        if self._search is not None:
            kr = self._answer_question(text, verdict, fused)
            if kr is not None:
                return kr

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
        antic = None if gap else self._anticipatory_guidance()  # don't stack with curiosity

        self._history.append({"role": "user", "content": text})

        # Persona + recalled memory + (when curious) a gap hint + (once mature) an
        # anticipation hint, so a model can act on the prediction naturally.
        msgs = self.messages()
        extras = []
        if recalled:
            extras.append({"role": "system", "content": f"You remember about them: {recalled}"})
        if gap:
            extras.append({"role": "system",
                           "content": f"You don't yet know about their {gap[0]}. If it "
                                      f"feels natural, gently ask — e.g. \"{gap[2]}\""})
        if antic:
            extras.append({"role": "system", "content": f"You anticipate {antic[0]}."})
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

        # Act on a mature prediction: if the model didn't already address it,
        # fold in the anticipatory line (the offline-visible action).
        if antic and antic[2] not in out.lower():
            out = out.rstrip() + " " + antic[1]
            self._last_anticipated_at = self._user_turns

        self._history.append({"role": "assistant", "content": out})

        if self._memory:
            try:
                self._memory.remember(text, out)
            except Exception:
                pass
        self._commit_turn(verdict.get("intent_class") or "INFORM", fused)
        self._record_retrospect(text, False, verdict, fused)
        return CompanionReply(out)

    # ── knowledge: search-to-answer + self-learning ─────────────
    def _answer_question(self, text: str, verdict: dict, fused: dict):
        # "more / sources" follow-up → reveal where the last answer came from
        if is_more_request(text) and self._last_search:
            return self._finish_knowledge(text, sources_block(self._last_search),
                                          verdict, fused, learned=False, source="memory")
        if not is_knowledge_question(text):
            return None
        # recall-first: has she already learned this? (confirms, no re-search)
        learned_answer = None
        if self._memory:
            try:
                learned_answer = self._memory.recall(text)
            except Exception:
                learned_answer = None
        if learned_answer:
            return self._finish_knowledge(text, str(learned_answer), verdict, fused,
                                          learned=False, source="memory")
        # search → tl;dr → retain (the self-learning step)
        try:
            results = self._search(text) or {}
        except Exception:
            results = {}
        out = tldr(text, results, self._summarize)
        self._last_search = results
        learned = bool(results.get("ok") and results.get("returned"))
        if learned and self._memory:
            try:
                self._memory.remember(text, out)
            except Exception:
                pass
        return self._finish_knowledge(text, out, verdict, fused,
                                      learned=learned, source="web")

    def _finish_knowledge(self, text, out, verdict, fused, *, learned, source):
        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": out})
        # knowledge turns commit a QUERY intent so the QRF learns this person's
        # questioning pattern → can mature into "offer to look things up".
        self._commit_turn("QUERY", fused, learned=learned)
        self._record_retrospect(text, False, verdict, fused, learned=learned, source=source)
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
                           verdict: dict, fused: dict,
                           *, learned: bool = False, source: str = "") -> None:
        """Record the turn for retrospective review (axiom_retrospect consumes it).
        `learned`/`source` mark turns where Aria acquired a fact — the signal the
        reverse-QRF learner uses to weight what to anticipate next."""
        if not self._retrospect:
            return
        record = {
            "learned": learned,
            "source": source,
            "input_text": redact_secrets(text),   # never persist obvious keys/passwords
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

    def consolidate(self) -> dict:
        """Consolidate the current conversation window into a single retrospective
        note and record it — secrets redacted, so no obvious key or password is
        persisted. Returns {summary, turns, recorded}."""
        turns = len(self._history)
        if turns == 0:
            return {"summary": "", "turns": 0, "recorded": False}
        summary = redact_secrets(self._consolidate_text())
        recorded = False
        if self._retrospect:
            record = {
                "kind": "consolidation",
                "learned": False, "source": "consolidation",
                "input_text": summary,
                "verdict": "PASSED", "intent_class": "INFORM",
                "intent_vector": [], "risk_clusters": [], "fusion_confidence": None,
                "turns": turns, "met_head": self._master.head,
                "persona_sig": self._persona_sig,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self._retrospect(record)
                recorded = True
            except Exception:
                recorded = False
        return {"summary": summary, "turns": turns, "recorded": recorded}

    def _consolidate_text(self) -> str:
        # redact each turn BEFORE it reaches the summariser, so secrets never even
        # leave for the model.
        convo = "\n".join(f"{m['role']}: {redact_secrets(m['content'])}"
                          for m in self._history)
        msgs = [
            {"role": "system", "content":
             "Consolidate this conversation into a concise retrospective note "
             "(3–5 sentences): what the person shared, what was decided or learned, "
             "and any open threads. Never include secrets, keys, or passwords."},
            {"role": "user", "content": convo},
        ]
        try:
            out = (self._generate(msgs) or "").strip()
            if out:
                return out
        except Exception:
            pass
        # extractive fallback — the recent user turns, redacted
        users = [redact_secrets(m["content"]) for m in self._history
                 if m["role"] == "user"]
        return " · ".join(users[-6:])


_BUILD_VERB = re.compile(
    r"(?i)\b(build|implement|write|create|code|fix|refactor|add|generate)\b")
_BUILD_OBJECT = re.compile(
    r"(?i)\b(script|function|module|class|test|tests|file|app|feature|bug|"
    r"endpoint|api|cli|program|package|patch|repo|code|\w+\.py)\b")


def _is_build_request(text: str) -> bool:
    """A coding/build task Aria can hand to the autonomous agent — a build verb
    plus a software object (so "write me a poem" doesn't trigger)."""
    return bool(_BUILD_VERB.search(text) and _BUILD_OBJECT.search(text))


def _reflective_reply(messages: List[dict]) -> str:
    """Offline fallback — a warm reflection of the last thing said."""
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    snippet = last.strip().rstrip(".!?")
    if not snippet:
        return "I'm here with you."
    return f"I hear you — {snippet}. Tell me more about that?"


def llm_generate(messages: List[dict], *, model: Optional[str] = None) -> str:
    """Talk through the configured local LLM when enabled; else reflect offline.
    ``model`` overrides the LLM-settings model — used to drive Aria's chat with her
    persona's base_model while the settings model stays the workspace planner's."""
    from aui.settings import load
    cfg = load()["llm"]
    if not cfg.get("enabled"):
        return _reflective_reply(messages)
    from aui.planner_local import _post
    out = _post(cfg, "/chat/completions", {
        "model": model or cfg["model"], "messages": messages,
        "temperature": 0.7, "stream": False,
    }, timeout=30)
    return _strip_reasoning(out["choices"][0]["message"]["content"])


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    """Drop chain-of-thought blocks so a thinking model's reasoning never leaks
    into Aria's visible reply. Qwen3 emits <think>…</think> by default (so do
    DeepSeek-R1 et al.); some chat templates inject the opening <think> server-
    side, so the content may carry only a closing tag — handle both. No-op for
    non-thinking models."""
    if not text:
        return text or ""
    out = _THINK_RE.sub("", text)            # balanced <think>…</think> pairs
    if "</think>" in out:                    # template-opened: keep what follows
        out = out.rsplit("</think>", 1)[-1]
    if "<think>" in out:                     # truncated/unclosed: keep the head
        out = out.split("<think>", 1)[0]
    return out.strip()


def vision_caption(image: str, *, prompt: Optional[str] = None) -> str:
    """Aria's eyes: describe an image through the configured local VLM (served the
    same OpenAI-compatible way as her brain — e.g. `ollama pull moondream`).
    Returns a one-line caption that grounds her text brain, or '' when vision is
    disabled, no image is given, or the VLM is unreachable (fails soft)."""
    from aui.settings import load
    cfg = load()["vision"]
    if not cfg.get("enabled") or not image:
        return ""
    from aui.planner_local import _post
    content = [
        {"type": "text", "text": prompt or
         "Describe what you see in one vivid, concrete sentence."},
        {"type": "image_url", "image_url": {"url": image}},
    ]
    try:
        out = _post(cfg, "/chat/completions", {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.2, "stream": False,
        }, timeout=60)
        return _strip_reasoning(out["choices"][0]["message"]["content"])
    except Exception:
        return ""


def _persona_model() -> Optional[str]:
    """Aria's chat model = her persona's base_model (live), or None to defer to
    the LLM-settings model."""
    try:
        from aui.persona import PersonaStore
        return PersonaStore().load_or_mint().base_model or None
    except Exception:
        return None


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

    def search(query: str) -> dict:
        # Aria's own web search — screened through axiom_immune, results never
        # shown; only the tl;dr reaches the conversation.
        from aui.websearch import search as web_search
        screen = bridge.immune_scan if bridge is not None else None
        return web_search(query, n=5, screen=screen)

    def summarize(question: str, answers: list, hits: list) -> str:
        from aui.settings import load
        if not load()["llm"].get("enabled"):
            return ""  # no model → knowledge.tldr() uses its extractive fallback
        ctx = "\n".join(
            [f"ANSWER: {a}" for a in (answers or [])[:2]]
            + [f"- {(h.get('content') or h.get('title') or '')}" for h in (hits or [])[:4]])
        msgs = [
            {"role": "system", "content": "Answer the question in 1–2 sentences (a tl;dr) "
             "using only the context. Be direct and natural; do not mention searching."},
            {"role": "user", "content": f"Question: {question}\n\nContext:\n{ctx}"},
        ]
        try:
            return (llm_generate(msgs, model=_persona_model()) or "").strip()
        except Exception:
            return ""

    def generate(messages: List[dict]) -> str:
        # Aria speaks with her persona's base_model (live), not the planner's.
        return llm_generate(messages, model=_persona_model())

    def antic_cfg() -> dict:
        from aui.settings import load
        return load().get("anticipation", {})

    def delegate(task: str) -> dict:
        # Aria hands a build/implement task to Axiom's autonomous agent (fails
        # soft → ok:False when unreachable, so she just replies normally).
        from aui import autonomous
        return autonomous.submit(task)

    # Aria's signed identity (two-tier); the MET chain parents off her soul.
    from aui.persona import PersonaStore
    persona_tok = PersonaStore().load_or_mint()

    return Companion(generate=generate, guard=guard, memory=memory,
                     fuse=fuse, retrospect=retrospect, curious=True, embed=llm_embed,
                     search=(search if bridge is not None else None), summarize=summarize,
                     anticipation_cfg=antic_cfg, delegate=delegate,
                     persona=persona_tok.persona_text(),
                     genesis=persona_tok.identity_signature,
                     persona_sig=persona_tok.token_signature)
