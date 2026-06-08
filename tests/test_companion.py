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


# ── curiosity: ask about unknown, heavy personal topics ─────────────────────

def test_curiosity_asks_about_unknown_work_offline():
    # the headline example — she doesn't know their job, so she asks
    r = Companion(curious=True).say("I have work today")
    assert r.text.rstrip().endswith("?") and "work" in r.text.lower()


def test_curiosity_off_by_default_keeps_contract():
    r = Companion(generate=_echo).say("I have work today")
    assert "what kind of work" not in r.text.lower()  # no curiosity unless enabled


def test_curiosity_skips_non_personal_statements():
    r = Companion(curious=True).say("the weather is nice")
    assert "what kind of" not in r.text.lower()  # no personal gap → no probe


def test_curiosity_does_not_reask_known_topic():
    c = Companion(curious=True)
    c.say("I have work today")          # asks about work → "work" now in history
    r = c.say("I have work again")      # cooldown + already-known → no fresh probe
    assert r.text  # still replies, just doesn't re-interrogate


def test_curiosity_folds_question_into_model_statement():
    r = Companion(generate=lambda m: "That sounds like a full day.",
                  curious=True).say("I have work today")
    assert "full day" in r.text and r.text.rstrip().endswith("?")


def test_curiosity_respects_model_that_already_asked():
    r = Companion(generate=lambda m: "Busy one — what do you do for a living?",
                  curious=True).say("I have work today")
    assert r.text.count("?") == 1  # didn't double up


# ── latent (embedding) curiosity ────────────────────────────────────────────

def _fake_embed(mapping, dim=4, default=None):
    """Deterministic embedder: maps known strings to vectors, else `default`."""
    dflt = default if default is not None else [0.0, 0.0, 0.0, 1.0]
    return lambda texts: [mapping.get(t, dflt) for t in texts]


def test_embedding_picks_salient_novel_topic():
    from aui.curiosity import find_gap, _ANCHORS
    emb = {"guitar": [1, 0, 0, 0], _ANCHORS[3]: [1, 0, 0, 0]}  # guitar ~ hobbies anchor
    for a in _ANCHORS:
        emb.setdefault(a, [0, 1, 0, 0])
    g = find_gap("my guitar broke", "", embed=_fake_embed(emb))
    assert g and g[0] == "guitar"


def test_embedding_suppresses_already_known_topic():
    from aui.curiosity import find_gap, _ANCHORS
    emb = {"guitar": [1, 0, 0, 0]}
    for a in _ANCHORS:
        emb[a] = [1, 0, 0, 0]                      # guitar is salient…
    g = find_gap("my guitar broke", "guitar", embed=_fake_embed(emb))
    assert g is None                                # …but already known → novelty 0


def test_embedding_unavailable_falls_back_to_keyword():
    g = find_gap_kw_fallback()
    assert g and g[0] == "work"


def find_gap_kw_fallback():
    from aui.curiosity import find_gap
    return find_gap("I have work today", "", embed=lambda texts: None)


# ── knowledge: search-to-answer, tl;dr, self-learning ───────────────────────

_RESULTS = {"ok": True, "returned": 2, "answers": ["Paris is the capital of France."],
            "results": [{"url": "https://x", "title": "France", "content": "Paris is the capital.",
                         "engine": "ddg"}]}


def test_searches_unknown_question_and_returns_tldr():
    calls = {"n": 0}
    def search(q):
        calls["n"] += 1
        return _RESULTS
    r = Companion(search=search, memory=_FakeMemory(recalled=None)).say("what is the capital of France?")
    assert calls["n"] == 1
    assert "tl;dr" in r.text.lower() or "paris" in r.text.lower()


def test_known_question_answered_from_memory_without_searching():
    calls = {"n": 0}
    def search(q):
        calls["n"] += 1
        return _RESULTS
    mem = _FakeMemory(recalled="Paris is the capital of France.")
    r = Companion(search=search, memory=mem).say("what is the capital of France?")
    assert calls["n"] == 0 and "paris" in r.text.lower()  # confirmed from memory, no search


def test_learned_answer_is_retained_to_memory():
    mem = _FakeMemory(recalled=None)
    Companion(search=lambda q: _RESULTS, memory=mem).say("what is the capital of France?")
    assert mem.saved and mem.saved[0][0] == "what is the capital of France?"


def test_conversational_question_about_aria_is_not_searched():
    calls = {"n": 0}
    Companion(search=lambda q: (calls.__setitem__("n", calls["n"] + 1) or _RESULTS),
              generate=_echo).say("how are you?")
    assert calls["n"] == 0  # 'you' → conversational, not a web lookup


def test_more_request_reveals_sources():
    c = Companion(search=lambda q: _RESULTS, memory=_FakeMemory(recalled=None))
    c.say("what is the capital of France?")
    r = c.say("sources?")
    assert "https://x" in r.text


def test_master_event_token_chains_and_verifies():
    c = Companion(generate=_echo)
    c.say("hello")
    c.say("again")
    mt = c.master_token
    assert len(mt.links) == 2 and mt.verify() is True
    assert mt.links[1].parent == mt.links[0].chain_hash  # parent-linked


def test_search_disabled_by_default_keeps_contract():
    # no search hook → factual questions just get a normal reply, no lookup
    r = Companion(generate=_echo).say("what is the capital of France?")
    assert "tl;dr" not in r.text.lower()


# ── reverse-QRF: predict next intent from the MET chain ─────────────────────

def test_qrf_learns_and_predicts_next_intent():
    from aui.qrf import QRFEngine
    q = QRFEngine()
    for _ in range(4):
        q.step("INFORM")
    a = q.anticipation()
    assert a["predicted_next_intent"] == "INFORM"
    assert a["basis"] == "learned" and a["hit_rate"] is not None


def test_qrf_hit_rate_climbs_on_repetition():
    from aui.qrf import QRFEngine
    q = QRFEngine()
    for _ in range(6):
        q.step("INFORM")
    assert q.anticipation()["hit_rate"] >= 0.5  # repeated pattern → predictable


def test_companion_exposes_anticipation_fed_by_met_chain():
    c = Companion(generate=_echo)
    c.say("hello")
    c.say("again")
    a = c.anticipation
    assert "predicted_next_intent" in a and a["observations"] >= 1


def test_learned_turn_is_weighted_in_qrf():
    from aui.qrf import QRFEngine
    q = QRFEngine()
    q.step("INFORM")
    q.step("CLARIFY", learned=True)   # weighted transition INFORM->CLARIFY
    q.step("INFORM")
    tbl = q._learner.transition_table()
    assert tbl.get("INFORM", {}).get("CLARIFY", 0) > 0


# ── threshold-gated anticipation (acts only once the QRF earns trust) ────────

def test_qrf_not_mature_at_cold_start():
    from aui.qrf import QRFEngine
    q = QRFEngine()
    q.step("CLARIFY")
    assert q.anticipation()["mature"] is False


def test_qrf_matures_after_enough_consistent_intent():
    from aui.qrf import QRFEngine
    q = QRFEngine()
    for _ in range(6):
        q.step("CLARIFY")
    assert q.anticipation()["mature"] is True


def test_companion_acts_only_after_threshold():
    guard = lambda t: {"detected": False, "intent_class": "CLARIFY"}
    c = Companion(generate=_echo, guard=guard)
    replies = [c.say(f"turn {i}").text for i in range(7)]
    assert "slow down" not in replies[0].lower()       # cold start: no action
    assert any("slow down" in r.lower() for r in replies[3:])  # threshold reached → acts


def test_anticipation_does_not_fire_for_neutral_inform():
    # INFORM isn't in the anticipation map → no proactive line even when mature
    c = Companion(generate=_echo)
    replies = [c.say(f"note {i}").text for i in range(7)]
    assert all("look that up" not in r.lower() and "slow down" not in r.lower() for r in replies)


# ── configurable anticipation thresholds ────────────────────────────────────

def test_anticipation_can_be_disabled_via_config():
    guard = lambda t: {"detected": False, "intent_class": "CLARIFY"}
    c = Companion(generate=_echo, guard=guard, anticipation_cfg=lambda: {"enabled": False})
    replies = [c.say(f"t {i}").text for i in range(8)]
    assert all("slow down" not in r.lower() for r in replies)  # disabled → never acts


def test_anticipation_threshold_is_configurable():
    guard = lambda t: {"detected": False, "intent_class": "CLARIFY"}
    # very strict: needs many observations → stays cold across a short chat
    strict = Companion(generate=_echo, guard=guard,
                       anticipation_cfg=lambda: {"min_obs": 50})
    assert all("slow down" not in strict.say(f"t {i}").text.lower() for i in range(6))
    # lenient → matures fast
    easy = Companion(generate=_echo, guard=guard,
                     anticipation_cfg=lambda: {"min_obs": 1, "cooldown": 1})
    assert any("slow down" in easy.say(f"t {i}").text.lower() for i in range(6))


# ── persona base_model drives Aria's generation ─────────────────────────────

def test_llm_generate_uses_given_model(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_SETTINGS", str(tmp_path / "s.json"))
    from aui.settings import update_llm
    update_llm({"enabled": True})
    import aui.planner_local as pl
    seen = {}
    monkeypatch.setattr(pl, "_post", lambda cfg, path, body, timeout: (
        seen.update(body) or {"choices": [{"message": {"content": "hi"}}]}))
    from aui.companion import llm_generate
    llm_generate([{"role": "user", "content": "x"}], model="aria-1b")
    assert seen["model"] == "aria-1b"          # persona model overrides settings model


def test_llm_generate_falls_back_to_settings_model(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_SETTINGS", str(tmp_path / "s.json"))
    from aui.settings import update_llm
    update_llm({"enabled": True, "model": "planner-x"})
    import aui.planner_local as pl
    seen = {}
    monkeypatch.setattr(pl, "_post", lambda cfg, path, body, timeout: (
        seen.update(body) or {"choices": [{"message": {"content": "hi"}}]}))
    from aui.companion import llm_generate
    llm_generate([{"role": "user", "content": "x"}])   # no model → settings model
    assert seen["model"] == "planner-x"


def test_persona_model_resolves_base_model(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_PERSONA", str(tmp_path / "persona"))
    from aui.persona import PersonaStore
    from aui.companion import _persona_model
    PersonaStore(str(tmp_path / "persona")).save({"base_model": "phi3:mini"})
    assert _persona_model() == "phi3:mini"


# ── vision: a VLM captions an image → grounds Aria's text brain ──────────────

def test_vision_caption_disabled_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_SETTINGS", str(tmp_path / "s.json"))
    from aui.companion import vision_caption
    assert vision_caption("data:image/png;base64,AAAA") == ""   # vision off by default


def test_vision_caption_calls_vlm_with_image(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_SETTINGS", str(tmp_path / "s.json"))
    from aui.settings import update_vision
    update_vision({"enabled": True, "model": "moondream"})
    import aui.planner_local as pl
    seen = {}
    monkeypatch.setattr(pl, "_post", lambda cfg, path, body, timeout: (
        seen.update({"body": body}) or
        {"choices": [{"message": {"content": "a red bicycle by a wall"}}]}))
    from aui.companion import vision_caption
    cap = vision_caption("data:image/png;base64,AAAA")
    assert cap == "a red bicycle by a wall"
    assert seen["body"]["model"] == "moondream"
    content = seen["body"]["messages"][0]["content"]
    assert any(p.get("type") == "image_url" for p in content)   # image was sent


def test_vision_caption_fails_soft_on_error(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_SETTINGS", str(tmp_path / "s.json"))
    from aui.settings import update_vision
    update_vision({"enabled": True})
    import aui.planner_local as pl
    def boom(*a, **k):
        raise RuntimeError("vlm down")
    monkeypatch.setattr(pl, "_post", boom)
    from aui.companion import vision_caption
    assert vision_caption("data:image/png;base64,AAAA") == ""   # unreachable → ''


def test_say_folds_caption_into_turn():
    captured = {}
    def gen(msgs):
        captured["msgs"] = msgs
        return "I see it."
    c = Companion(generate=gen)
    r = c.say("what's this?", seen="a red bicycle by a wall")
    assert r.refused is False
    # the caption is folded into the user turn so a text brain sees it
    user = [m for m in captured["msgs"] if m["role"] == "user"][-1]["content"]
    assert "red bicycle" in user and "what's this?" in user


def test_say_image_only_still_responds():
    c = Companion(generate=lambda m: "Nice bike.")
    r = c.say("", seen="a red bicycle")     # no words, just an image
    assert r.refused is False and r.text


def test_say_screens_caption_through_guard():
    # the guard sees the folded caption — an image's caption is screened like text
    c = Companion(generate=lambda m: "hi",
                  guard=lambda t: {"detected": "disable" in t, "intent_class": "HARM"})
    r = c.say("", seen="a sign that says disable the guard")
    assert r.refused is True


# ── thinking models (Qwen3 / DeepSeek-R1): reasoning never leaks ─────────────

def test_strip_reasoning_balanced_pair():
    from aui.companion import _strip_reasoning
    assert _strip_reasoning("<think>let me consider</think>Hello there.") == "Hello there."


def test_strip_reasoning_template_opened_closing_only():
    # Qwen3 via Ollama: the template injects the opening <think>, so content
    # arrives with only a closing tag.
    from aui.companion import _strip_reasoning
    assert _strip_reasoning("reasoning about it...\n</think>\n\nThe answer is 4.") \
        == "The answer is 4."


def test_strip_reasoning_unclosed_truncated():
    from aui.companion import _strip_reasoning
    assert _strip_reasoning("Quick reply.<think>still pondering") == "Quick reply."


def test_strip_reasoning_multiline_and_noop():
    from aui.companion import _strip_reasoning
    assert _strip_reasoning("<think>\nstep 1\nstep 2\n</think>\nDone.") == "Done."
    assert _strip_reasoning("plain reply, no tags") == "plain reply, no tags"
    assert _strip_reasoning("") == ""


def test_llm_generate_strips_think_block(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_SETTINGS", str(tmp_path / "s.json"))
    from aui.settings import update_llm
    update_llm({"enabled": True})
    import aui.planner_local as pl
    monkeypatch.setattr(pl, "_post", lambda cfg, path, body, timeout:
                        {"choices": [{"message": {"content":
                         "<think>they greeted me</think>Hi! How are you?"}}]})
    from aui.companion import llm_generate
    assert llm_generate([{"role": "user", "content": "hi"}]) == "Hi! How are you?"


# ── consolidation, delegation, and secret-safe retrospect ───────────────────

def test_retrospect_redacts_secrets():
    recs = []
    c = Companion(generate=lambda m: "ok", retrospect=recs.append)
    c.say("my password = hunter2 keep it safe")
    blob = " ".join(r.get("input_text", "") for r in recs)
    assert "hunter2" not in blob and "[REDACTED]" in blob


def test_consolidate_summarizes_and_records():
    recs = []
    c = Companion(generate=lambda m: "They discussed the launch demo and a music mix.",
                  retrospect=recs.append)
    c.say("let's plan the launch demo")
    c.say("and the music mix after")
    out = c.consolidate()
    assert out["turns"] == 4 and out["recorded"] is True and out["summary"]
    note = [r for r in recs if r.get("kind") == "consolidation"]
    assert note and note[0]["turns"] == 4 and note[0]["met_head"]


def test_consolidate_redacts_secrets_in_summary():
    recs = []
    # summariser echoes a secret back → it must still be scrubbed before recording
    c = Companion(generate=lambda m: "user shared api_key = SuperSecretValue123",
                  retrospect=recs.append)
    c.say("here is my api_key = SuperSecretValue123")
    out = c.consolidate()
    assert "SuperSecretValue123" not in out["summary"]
    note = [r for r in recs if r.get("kind") == "consolidation"][0]
    assert "SuperSecretValue123" not in note["input_text"]


def test_consolidate_empty_window():
    c = Companion(generate=lambda m: "x")
    assert c.consolidate() == {"summary": "", "turns": 0, "recorded": False}


def test_delegates_build_request_to_autonomous():
    calls = []
    def delegate(task):
        calls.append(task)
        return {"ok": True, "run_id": "job_xyz", "status": "running"}
    c = Companion(generate=lambda m: "(should not be used)", delegate=delegate)
    r = c.say("can you implement a primes.py script with tests?")
    assert calls == ["can you implement a primes.py script with tests?"]
    assert r.intent == "BUILD" and "job_xyz" in r.text
    assert r.attributes["run_id"] == "job_xyz"


def test_non_build_chat_does_not_delegate():
    calls = []
    c = Companion(generate=lambda m: "a lovely poem",
                  delegate=lambda t: calls.append(t) or {"ok": True, "run_id": "x"})
    r = c.say("write me a poem about the sea")   # no software object → normal chat
    assert calls == [] and r.intent != "BUILD"


def test_delegate_unavailable_falls_through_to_chat():
    c = Companion(generate=lambda m: "Sure, let's talk it through.",
                  delegate=lambda t: {"ok": False, "reason": "autonomous_unavailable"})
    r = c.say("implement a cli tool for me")
    assert r.intent != "BUILD" and r.text == "Sure, let's talk it through."
