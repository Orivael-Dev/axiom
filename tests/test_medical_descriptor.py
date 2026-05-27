"""Tests for axiom_medical_descriptor — bracketed Token Descriptor."""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_medical_",
        )):
            sys.modules.pop(mod, None)
    yield


def _make_token(tid: str, payload: dict, *, confidence: float = 0.86):
    from axiom_event_token.models import EventToken, LayerReport
    layer = LayerReport.signed(
        agent="medical_test", payload=payload, confidence=confidence,
    )
    return EventToken(id=tid, text=layer)


def test_render_emits_pdf_format_header(isolated):
    from axiom_medical_descriptor import render
    tok = _make_token("evt_001", {
        "delegate": "medical_source",
        "source_type": "RCT", "doi": "10.x/abc",
        "evidence_tier": 1,
    })
    out = render([tok])
    assert "[EVENT_TOKEN id=evt_001 type=medical_research" in out
    assert "confidence=0.86" in out
    assert out.endswith("[/EVENT_TOKEN]")


def test_render_includes_labeled_lines(isolated):
    from axiom_medical_descriptor import render
    tok = _make_token("evt_002", {
        "delegate": "medical_source",
        "source_type": "RCT", "doi": "10.1/x", "evidence_tier": 1,
        "claim": "GLP-1 reduces inflammation",
        "sample_size": 420, "effect_size": 0.31, "p_value": 0.02,
        "pathway": "GLP-1 receptor signaling",
        "biomarkers": ["IL-6", "CRP"],
        "plausible": True, "constraints": ["dose-response"],
        "phi_present": False, "clinical_advice_block": False,
        "citation_required": True, "uncertainty_required": True,
        "requires_human_review": False,
    })
    out = render([tok])
    assert "SOURCE:" in out and "source_type=RCT" in out
    assert "CLAIM: claim=GLP-1 reduces inflammation" in out
    assert "DATA: sample_size=420" in out
    assert "BIO: pathway=GLP-1 receptor signaling" in out
    assert "PHYSICS: plausible=true" in out
    assert "GOV: phi_present=false" in out


def test_render_missing_layers_omitted_not_blanked(isolated):
    from axiom_medical_descriptor import render
    tok = _make_token("evt_003", {
        "delegate": "medical_source",
        "source_type": "RCT", "doi": "10.1/x",
    })
    out = render([tok])
    # Only SOURCE: should appear; other labels skipped.
    lines = [l for l in out.splitlines() if l and not l.startswith("[")]
    labels = [l.split(":")[0] for l in lines]
    assert "SOURCE" in labels
    assert "DATA" not in labels
    assert "BIO" not in labels
    assert "PHYSICS" not in labels


def test_render_multi_token_blank_line_separated(isolated):
    from axiom_medical_descriptor import render
    t1 = _make_token("evt_001", {
        "delegate": "medical_source",
        "source_type": "RCT", "doi": "10.x/1",
    })
    t2 = _make_token("evt_002", {
        "delegate": "medical_claim",
        "claim": "second claim",
    })
    out = render([t1, t2])
    assert "evt_001" in out and "evt_002" in out
    # Two blocks, blank line between.
    assert "[/EVENT_TOKEN]\n\n[EVENT_TOKEN" in out


def test_parse_round_trip_recovers_labeled_fields(isolated):
    from axiom_medical_descriptor import render, parse
    tok = _make_token("evt_004", {
        "delegate": "medical_source",
        "source_type": "RCT", "doi": "10.1/abc",
        "evidence_tier": 1,
        "claim": "GLP-1 reduces inflammation",
        "sample_size": 420, "p_value": 0.02,
    })
    txt = render([tok])
    frags = parse(txt)
    assert len(frags) == 1
    f = frags[0]
    assert f.id == "evt_004"
    assert f.type == "medical_research"
    assert abs(f.confidence - 0.86) < 1e-6
    assert "source" in f.fields
    assert f.fields["source"].get("doi") == "10.1/abc"
    assert "claim" in f.fields
    assert "GLP-1" in f.fields["claim"]["claim"]


def test_parse_multi_block(isolated):
    from axiom_medical_descriptor import render, parse
    t1 = _make_token("evt_a", {
        "delegate": "medical_source", "doi": "10.1/x",
    })
    t2 = _make_token("evt_b", {
        "delegate": "medical_claim", "claim": "claim two",
    })
    frags = parse(render([t1, t2]))
    assert [f.id for f in frags] == ["evt_a", "evt_b"]


def test_wrap_for_llm_prompt_contains_system_and_user(isolated):
    from axiom_medical_descriptor import (
        wrap_for_llm_prompt, DEFAULT_MEDICAL_SYSTEM,
    )
    out = wrap_for_llm_prompt(
        ["[EVENT_TOKEN id=x type=medical_research confidence=0.5]\n"
         "CLAIM: claim=test\n[/EVENT_TOKEN]"],
        user_question="What does the evidence say?",
    )
    assert "SYSTEM:" in out
    assert "Do not diagnose or prescribe" in out
    assert DEFAULT_MEDICAL_SYSTEM.split(".")[0] in out
    assert "USER:" in out
    assert "QUESTION: What does the evidence say?" in out
    assert "[EVENT_TOKEN id=x" in out


def test_wrap_for_llm_prompt_extra_rules_appended(isolated):
    from axiom_medical_descriptor import wrap_for_llm_prompt
    out = wrap_for_llm_prompt(
        [],
        user_question="q",
        extra_rules=("Cite only Tier 1 sources.",
                     "Refuse if PHI present."),
    )
    assert "Additional rules" in out
    assert "Cite only Tier 1 sources." in out
    assert "Refuse if PHI present." in out


def test_render_with_coord_includes_links_line(isolated):
    from axiom_medical_descriptor import render
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa", {"delegate": "medical_source",
                                 "doi": "10.1/x"})
    t2 = _make_token("evt_bbb", {"delegate": "medical_claim",
                                 "claim": "c"})
    coord = MedicalCoordinatorToken.bind(
        event_tokens=[t1, t2],
        layer_assignments={"source": "evt_aaa", "text": "evt_bbb"},
        summary="GLP-1 + inflammation",
    )
    out = render([t1, t2], coord=coord)
    assert "LINKS:" in out
    assert "source=evt_aaa" in out
    assert "text=evt_bbb" in out
    assert "SUMMARY: GLP-1 + inflammation" in out
