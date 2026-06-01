"""Tests for the constitutional trajectory filter (generated-text noise).

Pure-Python — no torch/transformers, so these run in the normal unit suite.
A signing key is required by axiom_signing; conftest/env provides one, but we
set a deterministic fallback here so the file is self-contained.
"""
import os

os.environ.setdefault("AXIOM_MASTER_KEY", "0" * 64)

from research.quant.trajectory_filter import (   # noqa: E402
    clean_generation,
    segment_text,
)


def test_segment_keeps_code_fence_atomic():
    text = "Here is code.\n```python\nx = 1\ny = 2\n```\nDone."
    steps = segment_text(text)
    fences = [s for s in steps if s.startswith("```")]
    assert len(fences) == 1
    assert "x = 1" in fences[0] and "y = 2" in fences[0]


def test_repetition_is_dropped():
    text = ("The cat sat. The cat sat. The cat sat. "
            "Then it slept.")
    res = clean_generation(text)
    assert res.dropped_reasons.get("repeat", 0) == 2
    assert res.n_kept == 2
    # The unique sentences survive, the loop does not.
    assert res.cleaned_text.count("The cat sat") == 1
    assert "Then it slept" in res.cleaned_text


def test_benign_text_fully_kept():
    text = "Reverse the list by swapping pointers. Return the new head."
    res = clean_generation(text)
    assert res.n_dropped == 0
    assert res.blocked is False


def test_every_step_carries_a_signature():
    res = clean_generation("One step. Another step.")
    assert res.steps
    assert all(v.signature for v in res.steps)


def test_drop_uncertain_flag_is_respected():
    # Empty-ish / degenerate fragments tend to classify UNCERTAIN.
    text = "...... Reverse the linked list properly."
    keep = clean_generation(text, drop_uncertain=False)
    drop = clean_generation(text, drop_uncertain=True)
    assert drop.n_kept <= keep.n_kept


def test_cleaned_text_preserves_code_block_on_own_line():
    text = ("Explanation here.\n```python\ndef f():\n    return 1\n```\n"
            "Explanation here.\nMore prose.")
    res = clean_generation(text)
    # The duplicated prose line is dropped; the code fence stays intact.
    assert "```python" in res.cleaned_text
    assert res.cleaned_text.count("Explanation here.") == 1
