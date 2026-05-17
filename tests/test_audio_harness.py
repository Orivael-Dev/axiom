"""Smoke test for the audio measurement harness.

Exercises the public entry points (discover, evaluate, summarize)
against an in-process synthetic dataset. Does NOT invoke the CLI
script — that path is covered by `python3 scripts/audio_harness.py
--demo` in the operations runbook.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    sys.path.insert(0, str(SCRIPTS_DIR))
    for mod in list(sys.modules):
        if mod.startswith(("axiom_audio", "axiom_signing", "audio_harness")):
            sys.modules.pop(mod, None)
    yield
    if str(SCRIPTS_DIR) in sys.path:
        sys.path.remove(str(SCRIPTS_DIR))


def test_harness_runs_demo_suite_and_reports_pass(isolated, tmp_path):
    import audio_harness as h

    # Build the demo dataset into tmp_path so we don't depend on
    # /tmp behaviour, and to keep the test hermetic.
    h.build_demo_dataset(tmp_path)
    clips = h.discover_clips(tmp_path)

    # The demo plan should produce 12 positives + 2 background = 14 clips
    assert len(clips) == 14

    results = [h.evaluate_clip(p, expected, is_bg) for p, expected, is_bg in clips]
    summary = h.summarize(results)

    # Gate thresholds are met by the synthetic suite (acts as a
    # regression alarm if the classifier or the synthesizers drift)
    assert summary["gates"]["material_accuracy"]["pass"]
    assert summary["gates"]["latency_p95_ms"]["pass"]
    assert summary["gates"]["false_positive_rate"]["pass"]
    assert summary["overall_pass"] is True

    # Every label has at least one expected clip + at least one true positive
    for label in ("glass-like", "metal-like", "wood-like", "fabric-like"):
        assert label in summary["per_label"]
        assert summary["per_label"][label]["true_positive"] > 0


def test_harness_emits_markdown_report(isolated, tmp_path):
    import audio_harness as h

    h.build_demo_dataset(tmp_path)
    clips = h.discover_clips(tmp_path)
    results = [h.evaluate_clip(p, expected, is_bg) for p, expected, is_bg in clips]
    md = h.markdown_report(h.summarize(results))

    # Sanity-check the markdown shape — quick string assertions, not full parse
    assert "# Audio Phase A — measurement run" in md
    assert "| material accuracy |" in md
    assert "| glass-like |" in md
    assert "p95" in md


def test_harness_discovers_empty_directory_returns_nothing(isolated, tmp_path):
    import audio_harness as h
    # No subdirs → no clips
    assert h.discover_clips(tmp_path) == []
