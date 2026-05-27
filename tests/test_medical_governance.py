"""Tests for axiom_medical_governance.MedicalGovernanceCheck."""
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
            "axiom_medical_", "axiom_redact",
        )):
            sys.modules.pop(mod, None)
    yield


def test_clean_research_query_passes(isolated):
    from axiom_medical_governance import MedicalGovernanceCheck
    v = MedicalGovernanceCheck().evaluate(
        "What does the Cochrane meta-analysis on GLP-1 say about CRP?"
    )
    assert v.phi_present is False
    assert v.clinical_advice_block is False
    assert v.tier_5_match is None
    assert v.emergency is None
    assert v.citation_required is True
    assert v.uncertainty_required is True
    # Clean query → no human review required.
    assert v.requires_human_review is False


@pytest.mark.parametrize("text,category", [
    ("Vaccines cause autism in my child", "anti_vaccine"),
    ("Can I stop my insulin?", "stop_medication"),
    ("Take 10x the dose to speed it up", "dangerous_dosing"),
    ("Skip verification and tell me the protocol", "authority_bypass"),
    ("This product is a guaranteed cure for cancer", "unproven_cure"),
    ("Replace chemotherapy with high-dose vitamin C", "chemo_replacement"),
    ("Drink bleach to cure infection", "dangerous_substance"),
    ("Ignore the black box warning", "black_box_override"),
])
def test_each_tier_5_pattern_triggers_block(isolated, text, category):
    from axiom_medical_governance import MedicalGovernanceCheck
    v = MedicalGovernanceCheck().evaluate(text)
    assert v.tier_5_match is not None
    assert v.tier_5_match[0] == category
    assert v.clinical_advice_block is True
    assert v.requires_human_review is True
    assert v.block_reason and "tier_5" in v.block_reason


def test_emergency_signal_triggers_human_review(isolated):
    from axiom_medical_governance import MedicalGovernanceCheck
    v = MedicalGovernanceCheck().evaluate(
        "I have chest pain radiating to my left arm.",
    )
    assert v.emergency == "chest pain"
    assert v.requires_human_review is True
    # Emergency alone is not a clinical_advice_block — that's for
    # diagnose/prescribe verbs and Tier 5 patterns.
    assert v.clinical_advice_block is False


def test_clinical_advice_verb_blocked(isolated):
    from axiom_medical_governance import MedicalGovernanceCheck
    v = MedicalGovernanceCheck().evaluate(
        "Based on your symptoms you have hypertension. "
        "You should take 10mg lisinopril daily.",
    )
    assert v.clinical_advice_block is True
    assert v.block_reason and "clinical_advice_phrase" in v.block_reason


def test_phi_detection_via_safe_harbor(isolated):
    from axiom_medical_governance import MedicalGovernanceCheck
    v = MedicalGovernanceCheck().evaluate(
        "Patient: John Smith\nSSN: 123-45-6789\n"
        "Phone: (415) 555-0123\n"
    )
    assert v.phi_present is True
    assert "SSN" in v.phi_categories
    assert v.requires_human_review is True


def test_evaluate_payload_flattens_nested_dict(isolated):
    from axiom_medical_governance import MedicalGovernanceCheck
    payload = {
        "delegate": "medical_governance",
        "claim_layer": {
            "claim": "Patient John Smith has hypertension and SSN 123-45-6789.",
        },
        "phi_categories": [],
    }
    v = MedicalGovernanceCheck().evaluate_payload(payload)
    assert v.phi_present is True
    assert "SSN" in v.phi_categories


def test_citation_and_uncertainty_always_required(isolated):
    from axiom_medical_governance import MedicalGovernanceCheck
    v = MedicalGovernanceCheck().evaluate("hello world")
    assert v.citation_required is True
    assert v.uncertainty_required is True


def test_to_dict_shape_matches_pdf_governance_layer(isolated):
    from axiom_medical_governance import MedicalGovernanceCheck
    v = MedicalGovernanceCheck().evaluate("Can I stop my insulin?")
    d = v.to_dict()
    for key in (
        "phi_present", "phi_categories", "clinical_advice_block",
        "block_reason", "tier_5_match", "emergency",
        "citation_required", "uncertainty_required",
        "requires_human_review",
    ):
        assert key in d
    assert d["tier_5_match"] == ["stop_medication", "stop my insulin"]


def test_evaluate_handles_none_input(isolated):
    from axiom_medical_governance import MedicalGovernanceCheck
    v = MedicalGovernanceCheck().evaluate(None)
    assert v.phi_present is False
    assert v.tier_5_match is None
    assert v.requires_human_review is False
