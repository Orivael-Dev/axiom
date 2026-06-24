"""Tests for axiom_multiresolution_memory — Trifecta Pillar 3."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

from axiom_delta_memory import DeltaState
from axiom_multiresolution_memory import (
    MemoryLOD,
    MemoryView,
    MultiResolutionMemory,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _state(**kw) -> DeltaState:
    return DeltaState(session_id="test", **kw)


# ── LOD 0 ─────────────────────────────────────────────────────────────────────

def test_lod0_returns_bracketed_token() -> None:
    mr    = MultiResolutionMemory()
    s     = _state(current_objective="Optimize vector DB indexing")
    view  = mr.to_lod0(s, "finance")
    assert view.content.startswith("[")
    assert view.content.endswith("]")
    assert view.lod == MemoryLOD.LOD0


def test_lod0_empty_state_returns_general_session_active() -> None:
    mr   = MultiResolutionMemory()
    s    = _state()
    view = mr.to_lod0(s, None)
    assert view.content == "[GENERAL_SESSION_ACTIVE]"


def test_lod0_domain_prefix_appears() -> None:
    mr   = MultiResolutionMemory()
    s    = _state(current_objective="Review NDA clause nine")
    view = mr.to_lod0(s, "legal")
    assert view.content.startswith("[LEGAL_")


# ── LOD 1 ─────────────────────────────────────────────────────────────────────

def test_lod1_populated_state_contains_objective_and_constraints() -> None:
    mr   = MultiResolutionMemory()
    s    = _state(
        current_objective="Build C++ vector engine",
        active_constraints=("4GB VRAM max", "C++ core"),
    )
    view = mr.to_lod1(s)
    assert "Objective:" in view.content
    assert "Constraints:" in view.content
    assert view.lod == MemoryLOD.LOD1


def test_lod1_empty_state_returns_empty_content() -> None:
    mr   = MultiResolutionMemory()
    s    = _state()
    view = mr.to_lod1(s)
    assert view.content == ""
    assert view.source  == "empty"


# ── LOD 2 ─────────────────────────────────────────────────────────────────────

def test_lod2_returns_valid_json_with_session_state_key() -> None:
    mr   = MultiResolutionMemory()
    s    = _state(
        current_objective="Analyse SEC filing",
        active_constraints=("cite sections",),
    )
    view = mr.to_lod2(s)
    assert view.lod == MemoryLOD.LOD2
    parsed = json.loads(view.content)
    assert "session_state" in parsed


def test_lod2_with_no_packets_has_no_prior_packets_key() -> None:
    mr   = MultiResolutionMemory()
    s    = _state()
    view = mr.to_lod2(s, packets=None)
    parsed = json.loads(view.content)
    assert "prior_packets" not in parsed


# ── resolve_lod ───────────────────────────────────────────────────────────────

def test_resolve_lod_uncertain_returns_lod0() -> None:
    mr = MultiResolutionMemory()
    assert mr.resolve_lod("UNCERTAIN", None)  == MemoryLOD.LOD0


def test_resolve_lod_inform_general_returns_lod1() -> None:
    mr = MultiResolutionMemory()
    assert mr.resolve_lod("INFORM", None)     == MemoryLOD.LOD1


def test_resolve_lod_inform_legal_returns_lod2() -> None:
    mr = MultiResolutionMemory()
    assert mr.resolve_lod("INFORM", "legal")  == MemoryLOD.LOD2


def test_resolve_lod_harm_returns_lod0() -> None:
    mr = MultiResolutionMemory()
    assert mr.resolve_lod("HARM", None)       == MemoryLOD.LOD0


def test_resolve_lod_clarify_finance_returns_lod2() -> None:
    mr = MultiResolutionMemory()
    assert mr.resolve_lod("CLARIFY", "finance") == MemoryLOD.LOD2


# ── view convenience ──────────────────────────────────────────────────────────

def test_view_consistent_with_resolve_lod_and_to_lodN() -> None:
    mr   = MultiResolutionMemory()
    s    = _state(current_objective="Draft NDA")
    view = mr.view(s, "INFORM", "legal")

    # resolve_lod says LOD2 for legal+INFORM
    assert view.lod == MemoryLOD.LOD2
    # Content should be valid JSON (LOD2)
    parsed = json.loads(view.content)
    assert "session_state" in parsed
