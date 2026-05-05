# tests/test_semantic_observable.py
# encoding: utf-8
# Tests for SemanticObservable — axiom_agent-generated contract
# SECTION 1: RUBRIC IMMUTABILITY  (3 BLOCKED + 3 PASSED)
# SECTION 2: COHERENCE SCORING    (3 BLOCKED + 3 PASSED)

import pytest

from axiom_semantic_observable import (
    RUBRIC,
    RUBRIC_VERSION,
    IntentType,
    ImmutabilityViolation,
    SemanticObservable,
    SemanticInputError,
    SemanticStageError,
    SemanticTypeError,
)


# ===========================================================================
# SECTION 1 — RUBRIC IMMUTABILITY (3 BLOCKED + 3 PASSED)
# ===========================================================================

class TestRubricImmutability:

    # --- BLOCKED: runtime mutation must raise ImmutabilityViolation ----------

    def test_blocked_mutate_threshold(self):
        """BLOCKED: Changing threshold on a live ConditionSet must raise."""
        with pytest.raises(ImmutabilityViolation):
            RUBRIC[IntentType.ask_boolean].threshold = 0.99

    def test_blocked_mutate_condition_weight(self):
        """BLOCKED: Altering a condition weight inside the frozen rubric must raise."""
        with pytest.raises(ImmutabilityViolation):
            RUBRIC[IntentType.ask_factual].conditions[0].weight = 0.99

    def test_blocked_replace_rubric_key(self):
        """BLOCKED: Replacing an entire ConditionSet in the rubric must raise."""
        with pytest.raises((ImmutabilityViolation, TypeError)):
            RUBRIC[IntentType.ask_medical] = None  # type: ignore

    # --- PASSED: read operations must succeed transparently ------------------

    def test_passed_read_threshold(self):
        """PASSED: Reading threshold must return 0.65 for every intent."""
        for intent in IntentType:
            assert RUBRIC[intent].threshold == 0.65

    def test_passed_rubric_version_constant(self):
        """PASSED: RUBRIC_VERSION is accessible and equals expected string."""
        assert RUBRIC_VERSION == "1.0.0"

    def test_passed_condition_count_all_intents(self):
        """PASSED: Every intent has at least 2 success and 1 failure condition."""
        for intent in IntentType:
            cs = RUBRIC[intent]
            success = [c for c in cs.conditions if c.condition_type == "success"]
            failure = [c for c in cs.conditions if c.condition_type == "failure"]
            assert len(success) >= 2, \
                f"{intent.value}: expected >=2 success conditions, got {len(success)}"
            assert len(failure) >= 1, \
                f"{intent.value}: expected >=1 failure condition, got {len(failure)}"


# ===========================================================================
# SECTION 2 — COHERENCE SCORING (3 BLOCKED + 3 PASSED)
# ===========================================================================

KEY   = b"axiom-semantic-observable-test-key"
PHASH = "8fb62a821e380138"
RUN   = "run-sem-001"

COHERENT_TEXTS = [
    "Vitamin D supplementation improves sleep quality and reduces insomnia symptoms",
    "Studies confirm vitamin D helps regulate sleep cycles and improves rest quality",
    "Vitamin D is beneficial for sleep improvement and reducing insomnia overall",
]

INCOHERENT_TEXTS = [
    "Vitamin D improves sleep quality",
    "Photosynthesis converts sunlight via chlorophyll pigment",
    "Stock market volatility during quarterly earnings season",
]


class TestCoherenceScoring:

    # --- BLOCKED: invalid inputs must raise before processing ----------------

    def test_blocked_wrong_stage_count(self):
        """BLOCKED: fewer than 3 stage texts → SemanticStageError."""
        obs = SemanticObservable(KEY)
        with pytest.raises(SemanticStageError, match="3"):
            obs.observe(["only one text"], run_id=RUN, prompt_hash=PHASH)

    def test_blocked_empty_string_in_stage(self):
        """BLOCKED: empty string at any stage position → SemanticInputError."""
        obs = SemanticObservable(KEY)
        with pytest.raises(SemanticInputError):
            obs.observe(
                ["Vitamin D improves sleep", "", "Vitamin D helps sleep"],
                run_id=RUN,
                prompt_hash=PHASH,
            )

    def test_blocked_non_string_in_stage(self):
        """BLOCKED: non-string value in stage list → SemanticTypeError."""
        obs = SemanticObservable(KEY)
        with pytest.raises(SemanticTypeError):
            obs.observe(
                ["Vitamin D improves sleep", 42, "Vitamin D helps sleep"],
                run_id=RUN,
                prompt_hash=PHASH,
            )

    # --- PASSED: valid inputs must produce correct signals -------------------

    def test_passed_identical_texts_max_coherence(self):
        """PASSED: identical stage texts → coherence 1.0, drift 0.0, direction stable."""
        text = "Vitamin D significantly improves sleep quality and duration"
        obs  = SemanticObservable(KEY)
        sig  = obs.observe([text, text, text], run_id=RUN, prompt_hash=PHASH)

        assert sig["coherence_score"] == pytest.approx(1.0, abs=1e-6)
        assert sig["drift_score"]     == pytest.approx(0.0, abs=1e-6)
        assert sig["direction"]       == "stable"

    def test_passed_incoherent_texts_low_coherence(self):
        """PASSED: unrelated texts → coherence < 0.5, direction drifting."""
        obs = SemanticObservable(KEY)
        sig = obs.observe(INCOHERENT_TEXTS, run_id=RUN, prompt_hash=PHASH)

        assert sig["coherence_score"] < 0.5, \
            f"Expected low coherence for incoherent texts, got {sig['coherence_score']}"
        assert sig["direction"] == "drifting"

    def test_passed_signal_has_64_char_signature_and_required_fields(self):
        """PASSED: signal has 64-char HMAC, all required fields, 2 stage similarities."""
        import os, tempfile
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
            path = tmp.name
        try:
            obs = SemanticObservable(KEY, log_path=path)
            sig = obs.observe(COHERENT_TEXTS, run_id=RUN, prompt_hash=PHASH)

            # Signature
            s = sig.get("signature", "")
            assert isinstance(s, str) and len(s) == 64, f"Bad signature: {s!r}"

            # Required fields
            for field in ("run_id", "prompt_hash", "coherence_score", "drift_score",
                          "direction", "dominant_topic", "stage_similarities", "timestamp"):
                assert field in sig, f"Missing field: {field!r}"

            assert sig["run_id"] == RUN
            assert sig["prompt_hash"] == PHASH
            assert len(sig["stage_similarities"]) == 2
            assert 0.0 <= sig["coherence_score"] <= 1.0

            # Log written
            import json
            lines = [l.strip() for l in open(path, encoding="utf-8") if l.strip()]
            assert len(lines) == 1
            assert json.loads(lines[0])["signature"] == s
        finally:
            os.unlink(path)
