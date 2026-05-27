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
    material_clips, tempo_clips = h.discover_clips(tmp_path)

    # Material: 12 positives + 2 background = 14 clips
    assert len(material_clips) == 14
    # Tempo: 5 metronomes (60/90/120/150/180 BPM)
    assert len(tempo_clips) == 5

    results = [h.evaluate_clip(p, e, b) for p, e, b in material_clips]
    tempo_results = [h.evaluate_tempo_clip(p, bpm) for p, bpm in tempo_clips]
    summary = h.summarize(results, tempo_results)

    # All four gates pass on the synthetic suite (regression alarm if
    # the classifier or synthesizers drift)
    assert summary["gates"]["material_accuracy"]["pass"]
    assert summary["gates"]["latency_p95_ms"]["pass"]
    assert summary["gates"]["false_positive_rate"]["pass"]
    assert summary["gates"]["tempo_accuracy"]["pass"]
    assert summary["overall_pass"] is True

    for label in ("glass-like", "metal-like", "wood-like", "fabric-like"):
        assert label in summary["per_label"]
        assert summary["per_label"][label]["true_positive"] > 0


def test_harness_emits_markdown_report(isolated, tmp_path):
    import audio_harness as h

    h.build_demo_dataset(tmp_path)
    material_clips, tempo_clips = h.discover_clips(tmp_path)
    results = [h.evaluate_clip(p, e, b) for p, e, b in material_clips]
    tempo_results = [h.evaluate_tempo_clip(p, bpm) for p, bpm in tempo_clips]
    md = h.markdown_report(h.summarize(results, tempo_results))

    assert "# Audio Phase A — measurement run" in md
    assert "| material accuracy |" in md
    assert "| tempo accuracy |" in md
    assert "| glass-like |" in md
    assert "p95" in md


def test_harness_discovers_empty_directory_returns_nothing(isolated, tmp_path):
    import audio_harness as h
    material, tempo = h.discover_clips(tmp_path)
    assert material == []
    assert tempo == []
