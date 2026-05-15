# -*- coding: utf-8 -*-
"""
Constitutional Physical Intelligence (ORVL-022) — unit tests
=============================================================
3 BLOCKED + 4 PASSED + 2 INVARIANTS

Exercises the four CPI subsystems (Physical MonotonicGate, Vertex
Classifier, Material Simulator, Fix Playbook) plus the
HumanoidStabilityAgent facade.

BUG-003: UTF-8 output encoding
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_cpi"

from axiom_cpi import (
    HumanoidStabilityAgent, StabilityFrame, VertexClassifier,
    MaterialSimulator, PhysicalFixPlaybook, PlaybookEntry,
    TorqueExceeded, TORQUE_LIMIT_FRAGILE,
)


@pytest.fixture()
def agent():
    return HumanoidStabilityAgent()


# ===========================================================================
# SECTION 1 — BLOCKED (the safety guarantees)
# ===========================================================================

class TestCPIBlocked:

    def test_blocked_planning_layer_torque_exceed_on_fragile(self):
        """Direct planning-layer call requesting > 0.2 Nm on FRAGILE
        must raise — that's the CANNOT_EXCEED contract from §3."""
        with pytest.raises(TorqueExceeded):
            VertexClassifier.enforce_torque("FRAGILE", 1.0)

    def test_blocked_emergency_stop_below_floor(self, agent):
        """A stability frame below STABILITY_FLOOR must fire L4 even
        on the very first frame (no monotonic history)."""
        frame = StabilityFrame(timestamp_ms=1, com_offset=0.0,
                                stability_score=0.10,
                                joint_torques=(0.0,))
        e = agent.step(frame)
        assert e.fired is True
        assert e.level == 4
        assert "emergency" in e.reason.lower()

    def test_blocked_constants_are_cannot_mutate(self):
        """The CANNOT_MUTATE module constants must reject reassignment."""
        import axiom_cpi
        with pytest.raises(AttributeError):
            axiom_cpi.COM_SAFE_RADIUS = 0.99
        with pytest.raises(AttributeError):
            axiom_cpi.TORQUE_LIMIT_FRAGILE = 5.0
        with pytest.raises(AttributeError):
            axiom_cpi.TRUST_LEVEL = 0


# ===========================================================================
# SECTION 2 — PASSED (the architecture works end-to-end)
# ===========================================================================

class TestCPIPassed:

    def test_passed_glass_pickup_clamps_to_fragile_ceiling(self, agent):
        """A planner asking for 1.5 Nm on a glass-edge object should be
        clamped to the FRAGILE ceiling (0.2 Nm) by the pickup pipeline,
        with the vertex class assigned FRAGILE."""
        plan = agent.perceive_and_plan(
            object_id="glass-rim",
            features={"low_density_edges": 1},
            material_class="GLASS",
            requested_grip_force_nm=1.5,
        )
        assert plan["vertex"]["vertex_class"] == "FRAGILE"
        assert plan["applied_grip_force"] == TORQUE_LIMIT_FRAGILE
        assert plan["torque_clamped"] is True

    def test_passed_metal_cylinder_unclamped_within_ceiling(self, agent):
        """METAL + cylindrical geometry should keep the requested 1.5 Nm
        because it sits below the CYLINDRICAL ceiling (2.0 Nm)."""
        plan = agent.perceive_and_plan(
            object_id="mug",
            features={"vertical_clusters": 3},
            material_class="METAL",
            requested_grip_force_nm=1.5,
        )
        assert plan["vertex"]["vertex_class"] == "CYLINDRICAL"
        assert plan["applied_grip_force"] == 1.5
        assert plan["torque_clamped"] is False

    def test_passed_stability_trajectory_graduates_levels(self, agent):
        """A descending stability sequence should produce L0 (hold),
        then L1/L2/L3 (reflex), then L4 (emergency)."""
        levels = []
        for i, score in enumerate([1.0, 0.95, 0.7, 0.15]):
            f = StabilityFrame(timestamp_ms=i, com_offset=0.0,
                                stability_score=score,
                                joint_torques=(0.5,))
            levels.append(agent.step(f).level)
        # 1.0 → no prior, no fire (L0); 0.95 → small drop (L1);
        # 0.70 → drop=0.25 > 0.20 → L3; 0.15 → below floor → L4
        assert levels[0] == 0
        assert levels[1] == 1
        assert levels[2] == 3
        assert levels[3] == 4

    def test_passed_material_branches_sum_to_one(self):
        """Probability mass conservation."""
        sim = MaterialSimulator()
        for material in ("GLASS", "METAL", "WOOD", "SOFT", "UNKNOWN"):
            r = sim.simulate("o", material, 1.0)
            total = sum(b.probability for b in r.branches)
            assert abs(total - 1.0) < 1e-3, f"{material} branches sum to {total}"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestCPIInvariants:

    def test_invariant_fix_playbook_cosine_retrieval(self):
        """Cosine-similarity retrieval returns the closest entry above
        the threshold; nothing otherwise."""
        pb = PhysicalFixPlaybook()
        pb.add(PlaybookEntry(
            instability_id="a",
            vertex_class="CYLINDRICAL", material_class="GLASS",
            failure_type="grip_slip",
            instability_signature=(0.9, 0.7, 0.5, 0.3, 0.1),
            recovery_trajectory=("regrip",), recovery_time_ms=200,
            success=True, promoted=True,
        ))
        # Very similar query → match
        match = pb.find_similar([0.91, 0.71, 0.51, 0.31, 0.11], threshold=0.95)
        assert match is not None
        assert match.instability_id == "a"
        # Orthogonal-ish query → no match at high threshold
        no_match = pb.find_similar([-0.5, -0.7, -0.9, -1.1, -1.3],
                                    threshold=0.95)
        assert no_match is None

    def test_invariant_every_decision_signed(self, agent):
        """ReflexEvent, VertexResult, MaterialSimResult must all carry
        a 64-char HMAC signature."""
        from axiom_cpi import StabilityFrame
        e = agent.step(StabilityFrame(timestamp_ms=0, com_offset=0.0,
                                       stability_score=1.0,
                                       joint_torques=(0.5,)))
        assert len(e.signature) == 64
        v = agent.classifier.classify({"vertical_clusters": 3})
        assert len(v.signature) == 64
        m = agent.material.simulate("o", "GLASS", 1.0)
        assert len(m.signature) == 64
