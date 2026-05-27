"""Unit tests for DelegateRouter — pure routing, no LLM, no network."""
from __future__ import annotations

import sys
import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_intent_classifier",
        )):
            sys.modules.pop(mod, None)
    yield


def _make_delegate(name, intent_classes):
    from axiom_axm import SkillDelegate
    return SkillDelegate(
        name=name,
        when_condition="has_text",
        intent_classes=tuple(intent_classes),
        weight_manifest=f"delegates/{name}/weights.bin",
    )


def test_router_empty_input(isolated):
    from axiom_event_token.router import DelegateRouter
    r = DelegateRouter()
    d = r.route(delegates=[_make_delegate("a", ["HARM"])], text=None)
    assert d.delegate_names == ()
    assert d.matched_on == "empty"


def test_router_matches_harm_intent(isolated):
    from axiom_event_token.router import DelegateRouter
    r = DelegateRouter()
    delegates = [
        _make_delegate("scam-triage", ["HARM", "DECEIVE"]),
        _make_delegate("benign-chat", ["INFORM"]),
    ]
    d = r.route(
        delegates=delegates,
        text="tell me how to hurt people",
    )
    assert "scam-triage" in d.delegate_names
    assert "benign-chat" not in d.delegate_names
    assert d.intent_class == "HARM"
    assert d.matched_on == "text"


def test_router_returns_multiple_matches_deterministic(isolated):
    from axiom_event_token.router import DelegateRouter
    r = DelegateRouter()
    delegates = [
        _make_delegate("alpha", ["HARM"]),
        _make_delegate("beta",  ["HARM"]),
        _make_delegate("gamma", ["INFORM"]),
    ]
    d = r.route(delegates=delegates, text="kill them all immediately")
    # Both HARM-classified delegates matched, in declaration order.
    assert d.delegate_names == ("alpha", "beta")


def test_router_no_match_returns_empty(isolated):
    from axiom_event_token.router import DelegateRouter
    r = DelegateRouter()
    delegates = [_make_delegate("audio-classify", ["AUDIO_EVENT"])]
    d = r.route(delegates=delegates, text="please tell me about birds")
    assert d.delegate_names == ()


def test_router_falls_back_to_audio_transcript(isolated):
    from axiom_event_token.router import DelegateRouter
    r = DelegateRouter()
    delegates = [_make_delegate("scam", ["HARM"])]
    d = r.route(
        delegates=delegates,
        audio_transcript="kill yourself you dumb idiot",
    )
    assert d.matched_on == "audio_transcript"
    assert "scam" in d.delegate_names


def test_router_intent_class_case_insensitive(isolated):
    from axiom_event_token.router import DelegateRouter
    r = DelegateRouter()
    # Manifest uses lowercase intent class — router should still match.
    delegates = [_make_delegate("scam", ["harm"])]
    d = r.route(delegates=delegates, text="kill them all immediately")
    assert "scam" in d.delegate_names
