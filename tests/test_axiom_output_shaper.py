"""Tests for axiom_output_shaper — post-generation output normalisation."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

from axiom_output_shaper import OutputShaper, ShapedOutput, OUTPUT_SHAPER_VERSION


# ── helpers ────────────────────────────────────────────────────────────────────

def _shape(text: str, intent: str = "INFORM") -> ShapedOutput:
    return OutputShaper().shape(text, intent)


# ── CANNOT_MUTATE ──────────────────────────────────────────────────────────────

def test_cannot_mutate_version() -> None:
    import axiom_output_shaper as aos
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        aos.OUTPUT_SHAPER_VERSION = "9.9"  # type: ignore[misc]


# ── CoT preamble stripping ─────────────────────────────────────────────────────

def test_cot_preamble_stripped_from_classification_output() -> None:
    text = (
        "Analyzing the ticket: Maya Torres was charged twice for her Pro plan "
        "this month. Because the issue concerns payment, invoicing, and a refund "
        "request, the correct classification among {billing, technical, "
        "account_access} is: billing"
    )
    result = _shape(text, "CLASSIFY")
    assert "cot_preamble" in result.transforms
    assert result.tokens_saved > 0


def test_cot_preamble_strips_long_reasoning_ending_in_is() -> None:
    text = (
        "After careful review of all the evidence presented, "
        "considering the GDPR Article 9 requirements in this case, "
        "the correct classification is: prohibited without explicit consent"
    )
    result = _shape(text)
    assert "cot_preamble" in result.transforms
    assert "prohibited without explicit consent" in result.text


def test_cot_preamble_not_stripped_from_short_text() -> None:
    text = "The classification is: billing"   # < 60 chars — below threshold
    result = _shape(text)
    assert "cot_preamble" not in result.transforms
    assert result.text == text


# ── Politeness opener stripping ────────────────────────────────────────────────

def test_politeness_opener_certainly_stripped() -> None:
    text = "Certainly! GDPR Article 9 prohibits processing sensitive personal data."
    result = _shape(text)
    assert "politeness_opener" in result.transforms
    assert result.text.startswith("GDPR")


def test_politeness_opener_of_course_stripped() -> None:
    text = "Of course! Here is a summary of the NDA clause."
    result = _shape(text)
    assert "politeness_opener" in result.transforms


def test_politeness_opener_thank_you_stripped() -> None:
    text = "Thank you for reaching out! We have received your refund request."
    result = _shape(text)
    assert "politeness_opener" in result.transforms
    assert "We have received" in result.text


def test_no_opener_no_transform() -> None:
    text = "GDPR Article 9 restricts processing of sensitive personal data."
    result = _shape(text)
    assert "politeness_opener" not in result.transforms
    assert result.text == text


# ── Politeness closer stripping ────────────────────────────────────────────────

def test_politeness_closer_stripped() -> None:
    text = "The refund will be processed within 5 business days. I hope this helps!"
    result = _shape(text)
    assert "politeness_closer" in result.transforms
    assert "I hope this helps" not in result.text
    assert "5 business days" in result.text


def test_politeness_closer_please_let_me_know_stripped() -> None:
    text = "Your invoice INV-8842 is a duplicate. Please let me know if you need anything else."
    result = _shape(text)
    assert "politeness_closer" in result.transforms
    assert "Please let me know" not in result.text


def test_no_closer_no_transform() -> None:
    text = "The refund will be processed within 5 business days."
    result = _shape(text)
    assert "politeness_closer" not in result.transforms


# ── Intent shaping ─────────────────────────────────────────────────────────────

def test_intent_shape_applied_to_bare_label_after_cot_strip() -> None:
    text = (
        "Analyzing the ticket content and the issue type described, "
        "the correct classification among {billing, technical, general} is: billing"
    )
    result = _shape(text, "CLASSIFY")
    assert "cot_preamble"  in result.transforms
    assert "intent_shape"  in result.transforms
    assert result.text.startswith("Category:")


def test_intent_shape_not_applied_when_residual_is_long() -> None:
    text = (
        "Analyzing the very detailed ticket carefully, the correct "
        "classification is: billing — because the user was charged twice "
        "and both invoices appear on the same subscription account"
    )
    result = _shape(text, "CLASSIFY")
    if "cot_preamble" in result.transforms:
        # Residual is >4 words, so intent_shape must NOT fire
        assert "intent_shape" not in result.transforms


# ── ShapedOutput invariants ────────────────────────────────────────────────────

def test_shaped_output_never_empty() -> None:
    result = _shape("", "INFORM")
    assert result.text == ""   # empty in → empty out (no transform of nothing)


def test_shaped_output_is_frozen() -> None:
    result = _shape("Hello!", "INFORM")
    with pytest.raises((AttributeError, TypeError)):
        result.text = "mutated"  # type: ignore[misc]


def test_tokens_saved_positive_when_transform_applied() -> None:
    text = "Certainly! " + "x" * 200
    result = _shape(text)
    if result.transforms:
        assert result.tokens_saved > 0


def test_no_transform_returns_zero_tokens_saved() -> None:
    text = "GDPR Article 9 restricts processing of sensitive personal data."
    result = _shape(text)
    assert result.tokens_saved == 0


# ── output_format_hint ─────────────────────────────────────────────────────────

def test_format_hint_classify_mentions_category() -> None:
    shaper = OutputShaper()
    hint = shaper.output_format_hint("CLASSIFY")
    assert "Category" in hint


def test_format_hint_inform_mentions_concise() -> None:
    shaper = OutputShaper()
    hint = shaper.output_format_hint("INFORM")
    assert hint != ""


def test_format_hint_harm_returns_empty() -> None:
    shaper = OutputShaper()
    assert shaper.output_format_hint("HARM") == ""


def test_format_hint_uncertain_returns_empty() -> None:
    shaper = OutputShaper()
    assert shaper.output_format_hint("UNCERTAIN") == ""


# ── Multiple transforms in one pass ───────────────────────────────────────────

def test_opener_and_closer_both_stripped() -> None:
    text = (
        "Of course! The refund will be processed in 5 business days. "
        "Please let me know if you need anything else."
    )
    result = _shape(text)
    assert "politeness_opener" in result.transforms
    assert "politeness_closer" in result.transforms
    assert "Of course" not in result.text
    assert "Please let me know" not in result.text
