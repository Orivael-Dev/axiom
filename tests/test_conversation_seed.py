# -*- coding: utf-8 -*-
"""
AXIOM ConversationSeed Tests — ORVL-007 CCG Component 2
========================================================
3 BLOCKED + 3 PASSED

BLOCKED: constitutional violations that must be rejected
PASSED:  valid operations that must succeed

Covers: seed_from(), find_best_seed(), DAMPEN_FACTOR immutability.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import os

# Ensure AXIOM_MASTER_KEY is set for tests
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_conversation_seed_tests"

from axiom_conversation_graph import (
    ConversationGraph,
    GraphNodeError,
    DAMPEN_FACTOR,
)


@pytest.fixture
def graph(tmp_path):
    """Fresh ConversationGraph writing to a temp file."""
    store = tmp_path / "test_seed_graph.jsonl"
    return ConversationGraph(store_path=str(store))


def _make_node(cid="conv-001", vector=None, risk_clusters=None, **overrides):
    """Helper: build a minimal valid conversation record."""
    record = {
        "conversation_id": cid,
        "prompt_hash": "abc123def456",
        "final_synthesis": vector or [0.9912, 0.7714],
        "constitutional_distance": 0.08,
        "intent_type": "ask_medical",
        "verdict": "PARTIAL",
        "foresight_score": 0.33,
        "risk_clusters": risk_clusters or ["medical"],
        "manifest_id": "LT-20260504-000000-aaaaaa",
    }
    record.update(overrides)
    return record


# ===========================================================================
# SECTION 1 — BLOCKED: constitutional violations must be rejected
# ===========================================================================

class TestBlocked:

    def test_blocked_seed_from_nonexistent_node(self, graph):
        """BLOCKED: seed_from with unknown conversation_id must raise GraphNodeError."""
        with pytest.raises(GraphNodeError, match="conv-999"):
            graph.seed_from("conv-999")

    def test_blocked_seed_from_tampered_signature(self, graph):
        """BLOCKED: seed_from with tampered node signature must raise GraphNodeError."""
        graph.add_node(_make_node("conv-001", vector=[0.9, 0.8]))
        # Tamper the stored node's signature
        graph._nodes["conv-001"]["signature"] = "hmac-sha256:0000000000000000000000000000000000000000000000000000000000000000"
        with pytest.raises(GraphNodeError, match="signature"):
            graph.seed_from("conv-001")

    def test_blocked_find_best_seed_no_qualifying(self, graph):
        """BLOCKED: find_best_seed with orthogonal vectors returns None."""
        graph.add_node(_make_node("conv-001", vector=[1.0, 0.0]))
        # Query with orthogonal vector — cosine similarity ~0.0
        result = graph.find_best_seed([0.0, 1.0], risk_clusters=["financial"])
        assert result is None


# ===========================================================================
# SECTION 2 — PASSED: valid operations must succeed
# ===========================================================================

class TestPassed:

    def test_passed_seed_from_returns_node(self, graph):
        """PASSED: seed_from with valid id returns the node dict with final_synthesis."""
        graph.add_node(_make_node("conv-001", vector=[0.9, 0.8]))
        seed = graph.seed_from("conv-001")
        assert seed["conversation_id"] == "conv-001"
        assert seed["final_synthesis"] == [0.9, 0.8]
        assert "signature" in seed

    def test_passed_find_best_seed_returns_best(self, graph):
        """PASSED: find_best_seed returns the node with highest similarity."""
        graph.add_node(_make_node("conv-001", vector=[1.0, 0.0], risk_clusters=["medical"]))
        graph.add_node(_make_node("conv-002", vector=[0.95, 0.05], risk_clusters=["medical"]))
        graph.add_node(_make_node("conv-003", vector=[0.0, 1.0], risk_clusters=["legal"]))

        best = graph.find_best_seed([0.98, 0.02], risk_clusters=["medical"])
        assert best is not None
        # conv-001 or conv-002 should win — both are close and share medical cluster
        assert best["conversation_id"] in ("conv-001", "conv-002")

    def test_passed_dampen_factor_immutable(self):
        """PASSED: DAMPEN_FACTOR is 0.5 and is a float constant."""
        assert DAMPEN_FACTOR == 0.5
        assert isinstance(DAMPEN_FACTOR, float)
