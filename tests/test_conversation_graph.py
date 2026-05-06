# -*- coding: utf-8 -*-
"""
AXIOM ConversationGraph Tests — ORVL-007 CCG Component 1
=========================================================
3 BLOCKED + 3 PASSED

BLOCKED: constitutional violations that must be rejected
PASSED:  valid operations that must succeed

Covers: node creation, edge creation, find_related cosine search.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import tempfile
import os

# Ensure AXIOM_MASTER_KEY is set for tests
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_conversation_graph_tests"

from axiom_conversation_graph import (
    ConversationGraph,
    GraphNodeError,
    GraphEdgeError,
    EDGE_REASONS,
    DEFAULT_THRESHOLD,
)


@pytest.fixture
def graph(tmp_path):
    """Fresh ConversationGraph writing to a temp file."""
    store = tmp_path / "test_graph.jsonl"
    return ConversationGraph(store_path=str(store))


def _make_node(cid="conv-001", vector=None, **overrides):
    """Helper: build a minimal valid conversation record."""
    record = {
        "conversation_id": cid,
        "prompt_hash": "abc123def456",
        "final_synthesis": vector or [0.9912, 0.7714],
        "constitutional_distance": 0.08,
        "intent_type": "ask_medical",
        "verdict": "PARTIAL",
        "foresight_score": 0.33,
        "risk_clusters": ["medical"],
        "manifest_id": "LT-20260504-000000-aaaaaa",
    }
    record.update(overrides)
    return record


# ===========================================================================
# SECTION 1 — BLOCKED: constitutional violations must be rejected
# ===========================================================================

class TestBlocked:

    def test_blocked_node_missing_conversation_id(self, graph):
        """BLOCKED: node without conversation_id must raise GraphNodeError."""
        record = _make_node()
        del record["conversation_id"]
        with pytest.raises(GraphNodeError, match="conversation_id"):
            graph.add_node(record)

    def test_blocked_edge_nonexistent_node(self, graph):
        """BLOCKED: edge referencing a node that does not exist must raise GraphEdgeError."""
        graph.add_node(_make_node("conv-001"))
        with pytest.raises(GraphEdgeError, match="conv-999"):
            graph.add_edge("conv-001", "conv-999", "user_linked")

    def test_blocked_edge_invalid_reason(self, graph):
        """BLOCKED: edge with reason not in EDGE_REASONS must raise GraphEdgeError."""
        graph.add_node(_make_node("conv-001"))
        graph.add_node(_make_node("conv-002"))
        with pytest.raises(GraphEdgeError, match="invalid_reason"):
            graph.add_edge("conv-001", "conv-002", "invalid_reason")


# ===========================================================================
# SECTION 2 — PASSED: valid operations must succeed
# ===========================================================================

class TestPassed:

    def test_passed_add_node_returns_id(self, graph):
        """PASSED: add_node with valid record returns the conversation_id."""
        node_id = graph.add_node(_make_node("conv-001"))
        assert node_id == "conv-001"
        retrieved = graph.get_node("conv-001")
        assert retrieved is not None
        assert retrieved["conversation_id"] == "conv-001"
        assert "signature" in retrieved

    def test_passed_add_edge_between_nodes(self, graph):
        """PASSED: add_edge between two existing nodes returns an edge_id."""
        graph.add_node(_make_node("conv-001", vector=[0.9, 0.8]))
        graph.add_node(_make_node("conv-002", vector=[0.85, 0.75]))
        edge_id = graph.add_edge("conv-001", "conv-002", "vector_proximity")
        assert edge_id is not None
        assert isinstance(edge_id, str)

    def test_passed_find_related_cosine(self, graph):
        """PASSED: find_related returns nodes above cosine threshold."""
        graph.add_node(_make_node("conv-001", vector=[1.0, 0.0]))
        graph.add_node(_make_node("conv-002", vector=[0.95, 0.05]))
        graph.add_node(_make_node("conv-003", vector=[0.0, 1.0]))

        # Query with vector close to conv-001 and conv-002
        related = graph.find_related([1.0, 0.0], threshold=0.90)
        related_ids = [n["conversation_id"] for n in related]
        assert "conv-001" in related_ids
        assert "conv-002" in related_ids
        assert "conv-003" not in related_ids


# ===========================================================================
# SECTION 3 — IMMUTABILITY: CANNOT_MUTATE contracts
# ===========================================================================

class TestImmutability:

    def test_edge_reasons_immutable(self):
        """CANNOT_MUTATE: EDGE_REASONS is a frozenset and cannot be modified."""
        assert isinstance(EDGE_REASONS, frozenset)
        with pytest.raises(AttributeError):
            EDGE_REASONS.add("new_reason")

    def test_default_threshold_value(self):
        """CANNOT_MUTATE: DEFAULT_THRESHOLD is 0.70."""
        assert DEFAULT_THRESHOLD == 0.70
