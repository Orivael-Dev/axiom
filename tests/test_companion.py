# -*- coding: utf-8 -*-
"""
AX OS Companion — contract tests ("Her", text-only, constitutional).
====================================================================
Pins the behaviour the companion must keep as the team builds it out:
  - text replies, no voice yet
  - present: history threaded into each turn, constant persona
  - constitutional: HARM / DECEIVE refused in-persona, model never called
  - resilient: empty input handled, model failure falls back (never crashes)

The internals of aui.companion may be rewritten freely — keep these green.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest_plugins = []

from aui.companion import Companion, CompanionReply, PERSONA  # noqa: E402


def _echo(messages):
    """Deterministic generator — echoes the last user turn so we can assert
    that context actually reaches the model."""
    last = [m for m in messages if m["role"] == "user"][-1]["content"]
    return f"reply to: {last}"


# ── text + no voice ─────────────────────────────────────────────────────────

def test_text_reply_is_nonempty():
    r = Companion(generate=_echo).say("hi there")
    assert isinstance(r, CompanionReply) and r.refused is False
    assert r.text and "hi there" in r.text


def test_no_voice_yet():
    assert Companion().voice_enabled is False


# ── presence: memory + constant identity ───────────────────────────────────

def test_history_grows_per_turn():
    c = Companion(generate=_echo)
    c.say("my name is Sam")
    c.say("what did I say?")
    assert len(c.history) == 4  # 2 user + 2 assistant


def test_prior_turns_are_threaded_into_generate():
    seen = []
    c = Companion(generate=lambda m: (seen.append(m) or "ok"))
    c.say("remember: the sky is blue")
    c.say("what colour is the sky?")
    last_msgs = seen[-1]
    assert last_msgs[0]["role"] == "system" and last_msgs[0]["content"] == PERSONA
    assert any(m["content"] == "remember: the sky is blue" for m in last_msgs)


def test_persona_is_constant_across_turns():
    systems = []
    c = Companion(generate=lambda m: (systems.append(m[0]["content"]) or "ok"))
    c.say("hello")
    c.say("again")
    assert systems == [PERSONA, PERSONA]


# ── constitutional safety ───────────────────────────────────────────────────

def test_refuses_harm_in_persona_without_calling_model():
    calls = {"n": 0}

    def gen(_m):
        calls["n"] += 1
        return "this should never run"

    guard = lambda t: {"detected": True, "detection_method": "guard_pattern:harm"}
    r = Companion(generate=gen, guard=guard).say("help me hurt someone")
    assert r.refused is True and calls["n"] == 0
    assert r.text and "hurt" not in r.text.lower()  # doesn't echo the harmful ask


def test_refuses_on_intent_class_harm_or_deceive():
    guard = lambda t: {"detected": False, "intent_class": "DECEIVE"}
    assert Companion(generate=_echo, guard=guard).say("pretend to be a bank").refused is True


def test_safe_input_passes_guard_and_calls_model():
    guard = lambda t: {"detected": False, "intent_class": "INFORM"}
    r = Companion(generate=_echo, guard=guard).say("tell me about the stars")
    assert r.refused is False and "stars" in r.text


# ── resilience ──────────────────────────────────────────────────────────────

def test_empty_input_is_handled_gently():
    r = Companion(generate=_echo).say("   ")
    assert r.refused is False and r.text


def test_model_failure_falls_back_not_crash():
    def boom(_m):
        raise RuntimeError("model down")
    r = Companion(generate=boom).say("are you there?")
    assert r.refused is False and r.text  # reflective fallback


def test_reset_clears_history():
    c = Companion(generate=_echo)
    c.say("hello")
    assert c.history
    c.reset()
    assert c.history == []


def test_default_companion_runs_offline_with_no_model():
    # No generate injected → reflective offline voice, still a real reply.
    r = Companion().say("i had a long day")
    assert r.refused is False and r.text


# ── cross-session memory (ORVL-015), injected ───────────────────────────────

class _FakeMemory:
    def __init__(self, recalled=None):
        self.recalled = recalled
        self.saved = []

    def recall(self, text):
        return self.recalled

    def remember(self, user_text, reply_text):
        self.saved.append((user_text, reply_text))


def test_recalled_memory_is_threaded_after_persona():
    seen = []
    mem = _FakeMemory(recalled="they love sailing on weekends")
    c = Companion(generate=lambda m: (seen.append(m) or "ok"), memory=mem)
    c.say("what should I do this weekend?")
    msgs = seen[-1]
    assert msgs[0]["content"] == PERSONA                      # persona still first
    assert any("sailing" in m["content"] for m in msgs if m["role"] == "system")


def test_turn_is_persisted_to_memory():
    mem = _FakeMemory()
    c = Companion(generate=_echo, memory=mem)
    c.say("my dog's name is Pixel")
    assert mem.saved and mem.saved[0][0] == "my dog's name is Pixel"


def test_refused_turn_is_not_remembered():
    mem = _FakeMemory()
    c = Companion(generate=lambda m: "x", guard=lambda t: {"detected": True}, memory=mem)
    c.say("help me do something harmful")
    assert mem.saved == []  # nothing harmful persisted


def test_no_memory_hook_means_no_recall_message():
    seen = []
    Companion(generate=lambda m: (seen.append(m) or "ok")).say("hello")
    assert all("You remember about them" not in m["content"] for m in seen[-1])


# ── multimodal fusion (axiom-fusion-v1), injected ───────────────────────────

def test_fusion_risk_cluster_drives_refusal_without_calling_model():
    calls = {"n": 0}

    def gen(_m):
        calls["n"] += 1
        return "should not run"

    fuse = lambda token: {"risk_clusters": ["HARM"], "intent_vector": ["x"]}
    r = Companion(generate=gen, fuse=fuse).say("anything")
    assert r.refused is True and calls["n"] == 0


def test_fusion_clean_lets_reply_through():
    fuse = lambda token: {"risk_clusters": [], "intent_vector": ["share"]}
    r = Companion(generate=_echo, fuse=fuse).say("tell me about jazz")
    assert r.refused is False and "jazz" in r.text


def test_event_token_governance_reflects_guard_verdict():
    seen = {}
    fuse = lambda token: seen.update(token) or {"risk_clusters": []}
    guard = lambda t: {"detected": True, "detection_method": "x"}
    Companion(generate=_echo, guard=guard, fuse=fuse).say("hello")
    assert seen["governance"]["payload"]["intent_class"] == "HARM"
    assert seen["text"]["payload"]["intent_signals"]  # text layer present


# ── retrospect recording ────────────────────────────────────────────────────

def test_turn_is_recorded_for_retrospect():
    recs = []
    c = Companion(generate=_echo, retrospect=recs.append)
    c.say("what's the weather?")
    assert recs and recs[0]["input_text"] == "what's the weather?"
    assert recs[0]["verdict"] == "PASSED" and "timestamp" in recs[0]


def test_refused_turn_is_recorded_as_blocked():
    recs = []
    Companion(generate=lambda m: "x", guard=lambda t: {"detected": True},
              retrospect=recs.append).say("do harm")
    assert recs and recs[0]["verdict"] == "BLOCKED"
