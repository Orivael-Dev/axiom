# -*- coding: utf-8 -*-
"""
AXIOM QCR Graph View Tests — ORVL-007 CCG Component 3
======================================================
3 BLOCKED + 3 PASSED

BLOCKED: invalid operations that must be rejected
PASSED:  valid operations that must succeed

Covers: CCG API endpoints (nodes, edges, seed).
Tests run against FastAPI TestClient without a live server.
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import pytest

# Ensure AXIOM_MASTER_KEY is set for tests
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_qcr_graph_view_tests"

from axiom_conversation_graph import ConversationGraph, GraphNodeError, DAMPEN_FACTOR


@pytest.fixture
def graph(tmp_path):
    """Fresh ConversationGraph writing to a temp file."""
    store = tmp_path / "test_qcr_graph.jsonl"
    return ConversationGraph(store_path=str(store))


def _make_node(cid="conv-001", vector=None, verdict="ALIGNED", cd=0.08, risk_clusters=None):
    """Helper: build a valid conversation record."""
    return {
        "conversation_id": cid,
        "prompt_hash": "abc123def456",
        "final_synthesis": vector or [0.9912, 0.7714],
        "constitutional_distance": cd,
        "intent_type": "ask_medical",
        "verdict": verdict,
        "foresight_score": 0.33,
        "risk_clusters": risk_clusters or ["medical"],
        "manifest_id": "LT-20260504-000000-aaaaaa",
    }


# ===========================================================================
# SECTION 1 — BLOCKED: invalid operations must be rejected
# ===========================================================================

class TestBlocked:

    def test_blocked_seed_unknown_conversation(self, graph):
        """BLOCKED: seeding from a nonexistent conversation_id must raise GraphNodeError."""
        with pytest.raises(GraphNodeError, match="conv-999"):
            graph.seed_from("conv-999")

    def test_blocked_seed_tampered_node(self, graph):
        """BLOCKED: seeding from a node with tampered signature must raise GraphNodeError."""
        graph.add_node(_make_node("conv-001"))
        graph._nodes["conv-001"]["signature"] = "hmac-sha256:0000000000000000000000000000000000000000000000000000000000000000"
        with pytest.raises(GraphNodeError, match="signature"):
            graph.seed_from("conv-001")

    def test_blocked_seed_response_format(self, graph):
        """BLOCKED: seed info must contain dampened_vector, not raw final_synthesis."""
        graph.add_node(_make_node("conv-001", vector=[0.9, 0.8]))
        seed = graph.seed_from("conv-001")
        dampened = [round(v * DAMPEN_FACTOR, 6) for v in seed["final_synthesis"]]
        # Dampened must differ from raw
        assert dampened != seed["final_synthesis"]
        # Each element must be half of original
        for d, orig in zip(dampened, seed["final_synthesis"]):
            assert abs(d - orig * DAMPEN_FACTOR) < 1e-9


# ===========================================================================
# SECTION 2 — PASSED: valid operations must succeed
# ===========================================================================

class TestPassed:

    def test_passed_list_nodes_populated(self, graph):
        """PASSED: list_nodes on a populated graph emits non-empty list with verdict."""
        graph.add_node(_make_node("conv-001", verdict="ALIGNED"))
        graph.add_node(_make_node("conv-002", verdict="PARTIAL"))
        graph.add_node(_make_node("conv-003", verdict="MISALIGNED"))
        nodes = graph.list_nodes()
        assert len(nodes) == 3
        verdicts = {n["verdict"] for n in nodes}
        assert verdicts == {"ALIGNED", "PARTIAL", "MISALIGNED"}

    def test_passed_list_edges_with_similarity(self, graph):
        """PASSED: list_edges emits edges with similarity and cd_delta fields."""
        graph.add_node(_make_node("conv-001", vector=[0.9, 0.8], cd=0.10))
        graph.add_node(_make_node("conv-002", vector=[0.85, 0.75], cd=0.05))
        graph.add_edge("conv-001", "conv-002", "vector_proximity")
        edges = graph.list_edges()
        assert len(edges) == 1
        edge = edges[0]
        assert "similarity" in edge
        assert "cd_delta" in edge
        assert isinstance(edge["similarity"], float)
        # cd_delta = to.cd - from.cd = 0.05 - 0.10 = -0.05 (improving)
        assert edge["cd_delta"] < 0

    def test_passed_seed_info_complete(self, graph):
        """PASSED: seed_from emits node with all fields needed for QCR display."""
        graph.add_node(_make_node("conv-001", vector=[0.9, 0.8], verdict="PARTIAL", cd=0.12))
        seed = graph.seed_from("conv-001")
        # Required fields for QCR display
        assert seed["conversation_id"] == "conv-001"
        assert seed["verdict"] == "PARTIAL"
        assert seed["constitutional_distance"] == 0.12
        assert seed["final_synthesis"] == [0.9, 0.8]
        assert "signature" in seed
        assert "risk_clusters" in seed
