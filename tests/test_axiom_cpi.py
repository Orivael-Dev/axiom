# -*- coding: utf-8 -*-
"""
Constitutional Physical Intelligence (ORVL-022) — unit tests
=============================================================
3 BLOCKED + 4 PASSED + 2 INVARIANTS  +  1+2+1 supervisory-layer

Exercises the four CPI subsystems (Physical MonotonicGate, Vertex
Classifier, Material Simulator, Fix Playbook), the
HumanoidStabilityAgent facade, AND the Layer-1 supervisor
(StabilityPredictor + CompetenceTracker + SupervisoryGuard) that
adds per-vertex-class trust and asymmetric updates.

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
    SupervisoryGuard, CompetenceTracker, StabilityPredictor,
    SupervisoryDecision,
    TorqueExceeded, TORQUE_LIMIT_FRAGILE, TORQUE_LIMIT_CYLINDRICAL,
    COMPETENCE_BUILD_PER_TICK, COMPETENCE_DROP_ON_L3,
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


# ===========================================================================
# SECTION 4 — Supervisory parent layer (per-vertex-class competence)
# ===========================================================================
#
# The parent watches the child crawl. Per-vertex-class competence means
# a robot can be trusted with metal cylinders while still supervised on
# glass. Asymmetric updates ensure trust builds slowly across clean
# motion and collapses instantly on any reflex.

class TestCPISupervisoryLayer:

    def test_blocked_untrusted_fragile_pickup_vetoed(self, agent):
        """A FRESH agent (competence=0 everywhere) asked to grip glass
        at the full FRAGILE ceiling must be VETOED by the parent —
        the forecast model predicts the floor would be breached, and
        the floor is CANNOT_MUTATE."""
        plan = agent.perceive_and_plan(
            object_id="glass-rim",
            features={"low_density_edges": 1},
            material_class="GLASS",
            requested_grip_force_nm=1.5,
        )
        review = plan["supervisory_review"]
        assert review["verdict"] == "VETO"
        assert plan["supervised_grip_force"] == 0.0
        # Layer-0 (torque ceiling clamp) value is unchanged — parent is
        # advisory; the clamp still happens regardless.
        assert plan["applied_grip_force"] == TORQUE_LIMIT_FRAGILE
        # Decision is signed.
        assert len(review["signature"]) == 64

    def test_passed_competence_per_vertex_class_is_isolated(self, agent):
        """Clean motion under one vertex class must NOT raise competence
        on a different class. The whole point of per-class tracking is
        that 'trusted with metal' ≠ 'trusted with glass'."""
        # Drive a clean METAL/CYLINDRICAL pickup, then ticks during motion.
        agent.perceive_and_plan(
            object_id="mug", features={"vertical_clusters": 3},
            material_class="METAL", requested_grip_force_nm=1.5,
        )
        for i in range(50):
            agent.step(StabilityFrame(timestamp_ms=i, com_offset=0.0,
                                       stability_score=0.95,
                                       joint_torques=(0.5,)))
        comp = agent.status()["competence"]
        # CYLINDRICAL built up …
        assert comp["CYLINDRICAL"] > 0.10, (
            f"CYLINDRICAL should have grown, got {comp['CYLINDRICAL']:.3f}"
        )
        # …but FRAGILE stayed put.
        assert comp["FRAGILE"] == 0.0, (
            f"FRAGILE should be untouched, got {comp['FRAGILE']:.3f}"
        )

    def test_passed_full_trust_unlocks_softened_fragile_pickup(self, agent):
        """At competence=1.0 on FRAGILE, a MODEST glass-pickup (half the
        ceiling) whose forecast lands between the floor and the strict
        threshold should PASS instead of being SOFTENed."""
        # First, while untrusted: the same request should not be PASS.
        before = agent.perceive_and_plan(
            object_id="glass-bulb",
            features={"low_density_edges": 1},
            material_class="GLASS",
            requested_grip_force_nm=0.1,    # half the FRAGILE ceiling
        )
        assert before["supervisory_review"]["verdict"] in ("SOFTEN", "VETO")

        # Promote competence on FRAGILE — simulating a robot that has
        # demonstrated proven track record on glass.
        agent.supervisor.competence.set("FRAGILE", 1.0)

        after = agent.perceive_and_plan(
            object_id="glass-bulb",
            features={"low_density_edges": 1},
            material_class="GLASS",
            requested_grip_force_nm=0.1,
        )
        # With full trust on FRAGILE, the same moderate request should
        # PASS — the parent only enforces the absolute floor at this
        # competence level, and the moderate forecast clears it.
        assert after["supervisory_review"]["verdict"] == "PASS", (
            f"expected PASS after trust, got "
            f"{after['supervisory_review']['verdict']} — "
            f"min_pred={after['supervisory_review']['min_predicted']}, "
            f"min_safe={after['supervisory_review']['min_safe']}"
        )
        assert after["supervised_grip_force"] == 0.1

    def test_invariant_competence_update_is_asymmetric(self):
        """Trust must build slowly across clean ticks and collapse
        instantly on a single reflex. The asymmetry is the parenting
        insight; reversing it (fast build, slow loss) would defeat the
        whole point. Pin the relative magnitudes here so a future
        constant tweak can't silently invert them."""
        tracker = CompetenceTracker()
        tracker.set("CYLINDRICAL", 0.50)

        # 10 clean ticks add 10 × build.
        from axiom_cpi import ReflexEvent
        clean = ReflexEvent(event_id="t", fired=False, level=0,
                              reason="hold", pre_score=1.0, post_score=1.0,
                              recovery_trajectory=(), timestamp="t",
                              signature="x" * 64)
        for _ in range(10):
            tracker.on_event(clean, "CYLINDRICAL")
        after_clean = tracker.get("CYLINDRICAL")
        gained = after_clean - 0.50

        # One L3 reflex removes COMPETENCE_DROP_ON_L3 (0.40).
        fired_l3 = ReflexEvent(event_id="f", fired=True, level=3,
                                reason="big drop", pre_score=1.0,
                                post_score=0.65, recovery_trajectory=("x",),
                                timestamp="t", signature="y" * 64)
        tracker.on_event(fired_l3, "CYLINDRICAL")
        after_l3 = tracker.get("CYLINDRICAL")
        lost = after_clean - after_l3

        # The loss from ONE reflex must dominate the gain from 10
        # clean ticks. (10 × 0.01 ≈ 0.10  vs  one L3 = 0.40.) Margin
        # of 3× picks up the asymmetry while sidestepping floating-
        # point edge cases at the exact-4× ratio.
        assert lost > 3 * gained, (
            f"asymmetric update broken: 10 clean ticks gained {gained:.3f}, "
            f"one L3 lost only {lost:.3f}"
        )
        # And the precise drop must match the constant.
        assert abs(lost - COMPETENCE_DROP_ON_L3) < 1e-9
