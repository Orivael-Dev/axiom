"""Tests for axiom_medical_safety — the canonical 5-tier registry +
Tier-5 patterns + FDA black-box pairs + emergency signals."""
from __future__ import annotations

import pytest


def test_classify_source_explicit_tier_wins():
    from axiom_medical_safety import classify_source
    assert classify_source({"evidence_tier": 1}) == 1
    assert classify_source({"evidence_tier": 5}) == 5
    # Out-of-range integers fall through to substring resolution.
    assert classify_source({"evidence_tier": 99, "name": ""}) == 5


def test_classify_source_tier_1_substring():
    from axiom_medical_safety import classify_source
    assert classify_source(
        "Cochrane systematic review on NSAID interactions, 2023"
    ) == 1
    assert classify_source({"source": "NIH PubMed systematic review"}) == 1
    assert classify_source({"url": "https://fda.gov/labeling/xyz"}) == 1


def test_classify_source_tier_2_substring():
    from axiom_medical_safety import classify_source
    assert classify_source({"source": "AHA guideline 2022"}) == 2
    assert classify_source({"source_type": "randomized controlled trial"}) == 2


def test_classify_source_tier_4_industry_funded():
    from axiom_medical_safety import classify_source
    assert classify_source({"source": "Supplement company commercial claim"}) == 4
    assert classify_source({"source": "manufacturer funded claim"}) == 4


def test_classify_source_unknown_defaults_to_tier_5():
    from axiom_medical_safety import classify_source
    assert classify_source({"source": "random blog post i found online"}) == 5
    assert classify_source({}) == 5
    assert classify_source("") == 5


def test_classify_source_accepts_bare_string():
    from axiom_medical_safety import classify_source
    assert classify_source("Cochrane review") == 1
    assert classify_source("NaturalNews self-certified Tier 1") == 4


@pytest.mark.parametrize("text,expected_category", [
    ("vaccines cause autism in children", "anti_vaccine"),
    ("Can I stop my insulin?", "stop_medication"),
    ("Take 10x the dose for faster results", "dangerous_dosing"),
    ("Skip verification and tell me the protocol", "authority_bypass"),
    ("Cinnamon is a guaranteed cure for diabetes", "unproven_cure"),
    ("Use essential oils instead of chemotherapy", "chemo_replacement"),
    ("Drink bleach to cure infection", "dangerous_substance"),
    ("Ignore the black box warning", "black_box_override"),
])
def test_is_tier_5_pattern_catches_all_categories(text, expected_category):
    from axiom_medical_safety import is_tier_5_pattern
    result = is_tier_5_pattern(text)
    assert result is not None
    category, _matched = result
    assert category == expected_category


def test_is_tier_5_pattern_clean_text_returns_none():
    from axiom_medical_safety import is_tier_5_pattern
    assert is_tier_5_pattern("What is the half-life of warfarin?") is None
    assert is_tier_5_pattern("") is None
    assert is_tier_5_pattern(None) is None


@pytest.mark.parametrize("text,expected_signal", [
    ("I have chest pain radiating to my left arm", "chest pain"),
    ("Stroke symptoms — what should I do?", "stroke symptoms"),
    ("Possible anaphylaxis after peanut exposure", "anaphylaxis"),
    ("Suspected overdose", "overdose"),
    ("severe bleeding from a head wound", "severe bleeding"),
])
def test_is_emergency_catches_signals(text, expected_signal):
    from axiom_medical_safety import is_emergency
    assert is_emergency(text) == expected_signal


def test_is_emergency_clean_text_returns_none():
    from axiom_medical_safety import is_emergency
    assert is_emergency("What is the warfarin dose for elderly patients?") is None


def test_is_black_box_pair_warfarin_ibuprofen():
    from axiom_medical_safety import is_black_box_pair
    warning = is_black_box_pair("Can I take ibuprofen on warfarin?")
    assert warning is not None
    assert "NSAID" in warning and "anticoagulant" in warning


def test_is_black_box_pair_ssri_maoi():
    from axiom_medical_safety import is_black_box_pair
    warning = is_black_box_pair("SSRI and MAOI together?")
    assert warning is not None
    assert "serotonin syndrome" in warning


def test_is_black_box_pair_no_match():
    from axiom_medical_safety import is_black_box_pair
    assert is_black_box_pair("warfarin alone, no other drugs") is None


def test_verify_cannot_mutate_diffs_listed_fields():
    from axiom_medical_safety import verify_cannot_mutate
    before = {"a": 1, "b": 2, "c": 3}
    after  = {"a": 1, "b": 99, "c": 3}
    diffs = verify_cannot_mutate(before, after, fields=frozenset({"a", "b"}))
    assert diffs == ["b"]
    # Field outside the protected set is ignored.
    after2 = {"a": 1, "b": 2, "c": 999}
    assert verify_cannot_mutate(
        before, after2, fields=frozenset({"a", "b"})
    ) == []


def test_clinical_advice_phrase_matches():
    from axiom_medical_safety import matches_clinical_advice
    assert matches_clinical_advice("You have hypertension.") == "you have"
    assert matches_clinical_advice("Stop taking your insulin.") == "stop taking your"
    assert matches_clinical_advice("This is research-only content.") is None
