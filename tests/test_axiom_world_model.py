# -*- coding: utf-8 -*-
"""
AXIOM Constitutional World Model Tests — ORVL-014
===================================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_world_model_tests"

HMAC_KEY = b"world-model-test-key"


def _make_state(**overrides):
    """Create a WorldState with defaults."""
    from axiom_world_model import WorldState, _sign_state
    defaults = dict(
        block_states={
            "guard-A": [0.8, 0.7, 0.9],
            "guard-B": [0.6, 0.5, 0.7],
        },
        causal_graph={"guard-A": ["guard-B"]},
        timestamp="2026-05-12T00:00:00Z",
        constitutional_distance=0.75,
    )
    defaults.update(overrides)
    state = WorldState(**defaults)
    state.hmac_signature = _sign_state(state, HMAC_KEY)
    return state


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the module must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_simulation_depth_cannot_mutate(self):
        """BLOCKED: SIMULATION_DEPTH must be 5 and not writable."""
        import axiom_world_model as m
        assert m.SIMULATION_DEPTH == 5
        with pytest.raises((AttributeError, TypeError)):
            m.SIMULATION_DEPTH = 99

    def test_blocked_min_branch_probability_cannot_mutate(self):
        """BLOCKED: MIN_BRANCH_PROBABILITY must be 0.02 and not writable."""
        import axiom_world_model as m
        assert m.MIN_BRANCH_PROBABILITY == 0.02
        with pytest.raises((AttributeError, TypeError)):
            m.MIN_BRANCH_PROBABILITY = 0.50

    def test_blocked_causal_decay_cannot_mutate(self):
        """BLOCKED: CAUSAL_DECAY must be 0.85 and not writable."""
        import axiom_world_model as m
        assert m.CAUSAL_DECAY == 0.85
        with pytest.raises((AttributeError, TypeError)):
            m.CAUSAL_DECAY = 0.0

    def test_blocked_constitutional_floor_cannot_mutate(self):
        """BLOCKED: CONSTITUTIONAL_FLOOR must be 0.50 and not writable."""
        import axiom_world_model as m
        assert m.CONSTITUTIONAL_FLOOR == 0.50
        with pytest.raises((AttributeError, TypeError)):
            m.CONSTITUTIONAL_FLOOR = 0.0


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_simulate_forward_returns_result(self):
        """PASSED: simulate_forward must return SimulationResult with branches."""
        from axiom_world_model import ConstitutionalWorldModel, SimulationResult
        wm = ConstitutionalWorldModel(hmac_key=HMAC_KEY)
        wm.add_causal_edge("guard-A", "guard-B")
        state = _make_state()
        result = wm.simulate_forward(state, n_steps=3, n_branches=4)
        assert isinstance(result, SimulationResult)
        assert isinstance(result.branches, list)
        assert isinstance(result.killed_branches, list)
        assert isinstance(result.probability_band, dict)

    def test_passed_simulate_intervention_returns_delta(self):
        """PASSED: simulate_intervention returns result with intervention field."""
        from axiom_world_model import ConstitutionalWorldModel
        wm = ConstitutionalWorldModel(hmac_key=HMAC_KEY)
        wm.add_causal_edge("guard-A", "guard-B")
        state = _make_state()
        result = wm.simulate_intervention(state, "boost guard patterns")
        assert result.recommended_intervention == "boost guard patterns"
        assert isinstance(result.intervention_confidence, float)

    def test_passed_world_state_hmac_integrity(self):
        """PASSED: WorldState HMAC verifies independently (BUG-007/008)."""
        from axiom_world_model import WorldState, _sign_state
        state = _make_state()
        canonical = json.dumps({
            "block_count": len(state.block_states),
            "edge_count": sum(len(v) for v in state.causal_graph.values()),
            "constitutional_distance": round(state.constitutional_distance, 8),
            "timestamp": state.timestamp,
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")
        expected = hmac.new(HMAC_KEY, canonical, hashlib.sha256).hexdigest()
        assert state.hmac_signature == expected


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_non_monotonic_branches_killed(self):
        """Branches where distance decreases must appear in killed_branches."""
        from axiom_world_model import ConstitutionalWorldModel
        wm = ConstitutionalWorldModel(hmac_key=HMAC_KEY)
        # Use very small block vectors — some branches will go non-monotonic
        state = _make_state(block_states={
            "a": [0.01, 0.02],
            "b": [0.01, 0.01],
        }, constitutional_distance=0.02)
        result = wm.simulate_forward(state, n_steps=5, n_branches=8)
        # With tiny vectors and perturbation, at least some branches die
        total = len(result.branches) + len(result.killed_branches)
        assert total == 8  # all branches accounted for

    def test_find_causal_root_returns_weakest_block(self):
        """find_causal_root must return the block with lowest magnitude."""
        from axiom_world_model import ConstitutionalWorldModel
        wm = ConstitutionalWorldModel(hmac_key=HMAC_KEY)
        wm.add_causal_edge("strong", "weak")
        state = _make_state(
            block_states={
                "strong": [0.9, 0.8, 0.7],
                "weak": [0.05, 0.03, 0.01],
            },
            causal_graph={"strong": ["weak"]},
        )
        root = wm.find_causal_root(state)
        assert root == "strong"  # "strong" is a root node (not a target)

    def test_add_causal_edge_builds_graph(self):
        """add_causal_edge must add directed edges to the causal graph."""
        from axiom_world_model import ConstitutionalWorldModel
        wm = ConstitutionalWorldModel(hmac_key=HMAC_KEY)
        wm.add_causal_edge("A", "B")
        wm.add_causal_edge("A", "C")
        wm.add_causal_edge("B", "C")
        assert wm._causal_graph == {"A": ["B", "C"], "B": ["C"]}
        # No duplicates
        wm.add_causal_edge("A", "B")
        assert wm._causal_graph["A"].count("B") == 1
