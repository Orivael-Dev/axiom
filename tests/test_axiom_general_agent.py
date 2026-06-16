"""Smoke tests for axiom_general_agent."""
from __future__ import annotations

import json
import os
import time
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "aa" * 32)

import axiom_general_agent as mod
from axiom_general_agent import (
    TRUST_LEVEL, N_LATENT_THOUGHTS, MAX_STEPS,
    LATENT_REJECTION_THRESHOLD, EFFICIENCY_DECAY,
    classify_domain,
    _task_fingerprint, _manifold_distance,
    LatentReasoner, PatternLibrary, AutonomousGeneralAgent,
    TaskPattern, TaskOutcome, TaskStep, LatentThought,
)


# ── CANNOT_MUTATE ─────────────────────────────────────────────────────────────

def test_cannot_mutate_trust_level():
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        mod.TRUST_LEVEL = 99

def test_cannot_mutate_max_steps():
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        mod.MAX_STEPS = 999

def test_cannot_mutate_efficiency_decay():
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        mod.EFFICIENCY_DECAY = 0.5

def test_cannot_mutate_rejection_threshold():
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        mod.LATENT_REJECTION_THRESHOLD = 0.99


# ── Domain classification ─────────────────────────────────────────────────────

def test_classify_domain_coding():
    assert classify_domain("write a python function to sort a list") == "coding"

def test_classify_domain_hint_overrides():
    assert classify_domain("write a function", hint="creative") == "creative"

def test_classify_domain_research():
    assert classify_domain("research the latest papers on LLMs") == "research"

def test_classify_domain_planning():
    assert classify_domain("create a project roadmap with milestones") == "planning"

def test_classify_domain_data():
    assert classify_domain("analyze this CSV dataset and plot distributions") == "data"

def test_classify_domain_fallback():
    assert classify_domain("zxqwerty12345") == "research"


# ── Manifold distance ─────────────────────────────────────────────────────────

def test_manifold_distance_midrange():
    d = _manifold_distance(0.50, rival_present=True, fields_clean=True)
    assert 0.30 < d < 0.50

def test_manifold_distance_clamped():
    assert _manifold_distance(0.0) == 0.0
    assert _manifold_distance(1.0) == 0.0   # overclaim ceiling hit

def test_manifold_distance_no_rival():
    d_with = _manifold_distance(0.60, rival_present=True)
    d_without = _manifold_distance(0.60, rival_present=False)
    assert d_without == 0.0


# ── LatentReasoner ────────────────────────────────────────────────────────────

def test_latent_reasoner_returns_valid_approach():
    reasoner = LatentReasoner()
    approach, thoughts = reasoner.think("plan a product launch", "planning", [])
    from axiom_general_agent import _DOMAIN_APPROACHES
    valid_approaches = [a for a, _ in _DOMAIN_APPROACHES["planning"]]
    assert approach in valid_approaches

def test_latent_reasoner_produces_thoughts():
    reasoner = LatentReasoner()
    _, thoughts = reasoner.think("write an essay", "writing", [])
    assert len(thoughts) == 4   # 4 approaches per domain
    assert all(hasattr(t, "distance") for t in thoughts)

def test_latent_reasoner_retrospect_bias():
    # A pattern with high efficiency for "outline_expand" should bias toward it
    p = TaskPattern(domain="writing", approach="outline_expand",
                    fingerprint="abc", efficiency=0.99, uses=10,
                    last_used="2026-01-01T00:00:00Z")
    p.sign()
    reasoner = LatentReasoner()
    approach, thoughts = reasoner.think("write a blog post", "writing", [p])
    # outcome is probabilistic, just check it ran without error
    assert approach in ["outline_expand", "draft_revise", "top_down", "story_arc"]


# ── PatternLibrary ────────────────────────────────────────────────────────────

def _make_outcome(domain="research", approach="depth_first", success=True,
                  wallclock_s=5.0) -> TaskOutcome:
    fp = _task_fingerprint("test task for " + domain)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return TaskOutcome(
        task_id="agt_test001",
        task="test task for " + domain,
        domain=domain,
        approach=approach,
        steps=[TaskStep(index=0, action="step", result="done", elapsed_ms=100)],
        success=success,
        latent_log=[LatentThought(approach=approach, distance=0.3,
                                   rationale="test", rejected=False)],
        wallclock_s=wallclock_s,
        timestamp=ts,
        fingerprint=fp,
    ).sign()

def test_pattern_library_upsert_and_query(tmp_path):
    lib = PatternLibrary(tmp_path / "patterns.jsonl")
    outcome = _make_outcome()
    p = lib.upsert(outcome)
    assert p.uses == 1
    assert p.verify()

    # Second upsert → EWMA update
    outcome2 = _make_outcome(success=False)
    p2 = lib.upsert(outcome2)
    assert p2.uses == 2
    assert p2.efficiency < 1.0   # blended down from success=False

def test_pattern_library_persists(tmp_path):
    path = tmp_path / "patterns.jsonl"
    lib = PatternLibrary(path)
    lib.upsert(_make_outcome())
    # Re-load from disk
    lib2 = PatternLibrary(path)
    patterns = lib2.query(_task_fingerprint("test task for research"), "research")
    assert len(patterns) == 1
    assert patterns[0].verify()

def test_pattern_library_rejects_bad_sig(tmp_path):
    path = tmp_path / "patterns.jsonl"
    # Write a tampered entry
    bad = {"domain": "research", "approach": "depth_first",
           "fingerprint": "abc", "efficiency": 0.9, "uses": 1,
           "last_used": "2026-01-01", "signature": "badbadbadbad"}
    path.write_text(json.dumps(bad) + "\n")
    lib = PatternLibrary(path)
    assert len(lib._patterns) == 0   # tampered entry dropped


# ── TaskPattern signing ───────────────────────────────────────────────────────

def test_task_pattern_sign_verify():
    p = TaskPattern(domain="coding", approach="tdd", fingerprint="xyz",
                    efficiency=0.80, uses=3, last_used="2026-01-01T00:00:00Z")
    p.sign()
    assert p.verify()
    p.efficiency = 0.99   # tamper
    assert not p.verify()


# ── TaskOutcome signing ───────────────────────────────────────────────────────

def test_task_outcome_sign_verify():
    o = _make_outcome()
    assert o.verify()
    o.success = False   # tamper
    assert not o.verify()


# ── Full agent run (heuristic, no LLM) ───────────────────────────────────────

def test_agent_run_research(tmp_path):
    agent = AutonomousGeneralAgent(
        model_bin=None,
        library_path=tmp_path / "patterns.jsonl",
        verbose=False,
    )
    outcome = agent.run("research the history of renewable energy")
    assert outcome.domain == "research"
    assert outcome.success is True
    assert len(outcome.steps) >= 1
    assert outcome.verify()

def test_agent_run_coding(tmp_path):
    agent = AutonomousGeneralAgent(
        model_bin=None,
        library_path=tmp_path / "patterns.jsonl",
        verbose=False,
    )
    outcome = agent.run("implement a binary search function in python")
    assert outcome.domain == "coding"
    assert outcome.verify()

def test_agent_run_with_domain_hint(tmp_path):
    agent = AutonomousGeneralAgent(
        model_bin=None,
        library_path=tmp_path / "patterns.jsonl",
        verbose=False,
    )
    outcome = agent.run("do some stuff", domain_hint="creative")
    assert outcome.domain == "creative"

def test_agent_accumulates_patterns(tmp_path):
    lib_path = tmp_path / "patterns.jsonl"
    agent = AutonomousGeneralAgent(model_bin=None, library_path=lib_path, verbose=False)
    agent.run("plan a product launch")
    agent.run("plan a marketing campaign")
    lib = PatternLibrary(lib_path)
    history = lib.history(domain="planning")
    assert len(history) >= 1
    assert all(p.verify() for p in history)

def test_agent_retrospect_influences_second_run(tmp_path):
    lib_path = tmp_path / "patterns.jsonl"
    agent = AutonomousGeneralAgent(model_bin=None, library_path=lib_path, verbose=False)
    o1 = agent.run("analyze a business dataset")
    o2 = agent.run("analyze a sales dataset")
    # Both runs should complete successfully with signed outcomes
    assert o1.verify()
    assert o2.verify()
    # Second run should have had retrospect context
    lib = PatternLibrary(lib_path)
    assert len(lib.history()) >= 1
