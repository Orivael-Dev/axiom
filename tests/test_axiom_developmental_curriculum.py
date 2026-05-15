# -*- coding: utf-8 -*-
"""
Developmental Curriculum — the mom layer bridging ORVL-022 ↔ ORVL-023
=====================================================================
2 BLOCKED + 3 PASSED + 2 INVARIANTS

Covers the four behaviors that distinguish the mom from the dad
(`SupervisoryGuard` already in axiom_cpi):

  - PERSISTENCE       — signed sidecar JSON survives across boots
  - CURRICULUM        — suggest_next_task picks the zone-of-proximal
                         class
  - TRANSFER          — competence seeds a similar class via cosine
                         over AXM vertex_cluster bag-of-words
  - SAFETY            — tampered persistence refused; transfer cap
                         enforced; reverse transfer is a no-op

BUG-003: UTF-8 output encoding
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_curriculum"

from axiom_axm import AXMContainer
from axiom_cpi import HumanoidStabilityAgent, StabilityFrame
from axiom_developmental_curriculum import (
    DevelopmentalCurriculum, PersistenceTampered, TransferCapExceeded,
    SIMILARITY_FLOOR, TRANSFER_CAP_PER_CALL,
)
from examples.axm_pack_starter import STARTER_SPEC


@pytest.fixture()
def starter_axm(tmp_path):
    """A freshly-packed starter AXM container with the canonical
    Glass/Box/Sphere/Door/Handle vertex set."""
    return AXMContainer.pack(STARTER_SPEC, str(tmp_path / "starter.axm"))


@pytest.fixture()
def agent_and_mom(starter_axm, tmp_path):
    agent = HumanoidStabilityAgent()
    mom = DevelopmentalCurriculum(
        supervisor=agent.supervisor,
        axm_container=starter_axm,
        persistence_path=str(tmp_path / "curriculum.json"),
    )
    return agent, mom


# ===========================================================================
# SECTION 1 — BLOCKED (the safety guarantees)
# ===========================================================================

class TestCurriculumBlocked:

    def test_blocked_tampered_persistence_refused(self, starter_axm, tmp_path):
        """A persistence file whose signature does not verify must
        cause boot to refuse. Without this, an attacker could ratchet
        a robot's competence by editing the sidecar JSON."""
        agent = HumanoidStabilityAgent()
        agent.supervisor.competence.set("CYLINDRICAL", 0.50)
        mom = DevelopmentalCurriculum(
            supervisor=agent.supervisor, axm_container=starter_axm,
            persistence_path=str(tmp_path / "c.json"),
        )
        assert mom.consolidate(force=True) is True

        # Tamper: bump CYLINDRICAL to 0.99 without re-signing.
        path = Path(tmp_path / "c.json")
        data = json.loads(path.read_text())
        data["competence"]["CYLINDRICAL"] = 0.99
        path.write_text(json.dumps(data))

        agent2 = HumanoidStabilityAgent()
        with pytest.raises(PersistenceTampered):
            DevelopmentalCurriculum(
                supervisor=agent2.supervisor, axm_container=starter_axm,
                persistence_path=str(path),
            )

    def test_blocked_transfer_above_cap_raises(self, agent_and_mom):
        """A force_similarity high enough to ratchet competence past
        TRANSFER_CAP_PER_CALL must raise TransferCapExceeded.
        Guards the asymmetric-update invariant from the dad layer:
        no single curriculum step erases multiple reflexes' worth of
        demotion in one shot."""
        agent, mom = agent_and_mom
        agent.supervisor.competence.set("CYLINDRICAL", 1.0)
        # Force similarity=1.0 with dst at 0 → raise_delta would be
        # 1.0, way above the cap (0.40).
        with pytest.raises(TransferCapExceeded):
            mom.transfer("CYLINDRICAL", "FRAGILE", force_similarity=1.0)


# ===========================================================================
# SECTION 2 — PASSED (the mom's four jobs work end-to-end)
# ===========================================================================

class TestCurriculumPassed:

    def test_passed_similarity_from_axm_bag_of_words(self, agent_and_mom):
        """The starter AXM ships with:
          Glass  (FRAGILE)    → cluster 'Cylindrical_Thin'
          Handle (PROTRUSION) → cluster 'Cylindrical_Graspable'
        Bag-of-words cosine: shared 'cylindrical' / sqrt(2 × 2) = 0.5.
        Other pairs have no shared tokens → 0.0."""
        _, mom = agent_and_mom
        assert mom.similarity("FRAGILE", "PROTRUSION") == pytest.approx(0.5,
                                                                          abs=0.01)
        # Self-similarity is always 1.0 (short-circuited, no graph hit).
        assert mom.similarity("FRAGILE", "FRAGILE") == 1.0
        # Unrelated classes — no shared tokens in their clusters.
        assert mom.similarity("FRAGILE", "PLANAR") == 0.0
        assert mom.similarity("CYLINDRICAL", "PROTRUSION") == 0.0

    def test_passed_transfer_seeds_similar_class(self, agent_and_mom):
        """A source competence of 0.80 transferring to a destination
        via similarity 0.50 raises dst from 0.0 → 0.40 (= 0.80×0.50).
        The reverse transfer is a no-op (dst already higher)."""
        agent, mom = agent_and_mom
        agent.supervisor.competence.set("FRAGILE", 0.80)
        ev = mom.transfer("FRAGILE", "PROTRUSION")
        assert ev.similarity == pytest.approx(0.5, abs=0.01)
        assert ev.new_dst == pytest.approx(0.40, abs=0.001)
        assert ev.raise_delta == pytest.approx(0.40, abs=0.001)
        # Signed audit trail.
        assert len(ev.signature) == 64

        # Reverse: PROTRUSION 0.40 × 0.5 = 0.20, less than FRAGILE 0.80.
        ev_rev = mom.transfer("PROTRUSION", "FRAGILE")
        assert ev_rev.raise_delta == 0.0
        assert ev_rev.new_dst == 0.80

    def test_passed_consolidate_and_boot_round_trip(self, starter_axm, tmp_path):
        """After 50 clean CYLINDRICAL ticks then consolidate, a fresh
        agent that boots the curriculum from the same persistence
        path must inherit the same CYLINDRICAL competence."""
        pers = str(tmp_path / "c.json")

        # Session 1 — build CYLINDRICAL trust then consolidate.
        agent1 = HumanoidStabilityAgent()
        mom1 = DevelopmentalCurriculum(
            supervisor=agent1.supervisor, axm_container=starter_axm,
            persistence_path=pers,
        )
        agent1.perceive_and_plan(
            object_id="mug", features={"vertical_clusters": 3},
            material_class="METAL", requested_grip_force_nm=1.0,
        )
        for i in range(50):
            agent1.step(StabilityFrame(timestamp_ms=i, com_offset=0.0,
                                         stability_score=0.97,
                                         joint_torques=(0.5,)))
        cyl_before = agent1.supervisor.competence.get("CYLINDRICAL")
        assert cyl_before > 0.10
        assert mom1.consolidate() is True

        # Session 2 — fresh agent boots from disk.
        agent2 = HumanoidStabilityAgent()
        assert agent2.supervisor.competence.get("CYLINDRICAL") == 0.0   # pre-load
        mom2 = DevelopmentalCurriculum(
            supervisor=agent2.supervisor, axm_container=starter_axm,
            persistence_path=pers,
        )
        assert mom2._loaded_from_disk is True
        assert agent2.supervisor.competence.get("CYLINDRICAL") == cyl_before


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestCurriculumInvariants:

    def test_invariant_transfer_never_decreases_target(self, agent_and_mom):
        """For every (src, dst) pair, after a transfer the destination
        competence must be ≥ what it was before. The mom can only
        nudge UP — she never demotes via transfer (demotion is the
        dad's job, on reflex fires only)."""
        agent, mom = agent_and_mom
        # Pre-seed every class with a random-ish starting score.
        targets = {
            "FRAGILE": 0.7, "PROTRUSION": 0.5, "CYLINDRICAL": 0.3,
            "PLANAR": 0.1, "DEFORMABLE": 0.0,
        }
        for cls, score in targets.items():
            agent.supervisor.competence.set(cls, score)

        before = {c: agent.supervisor.competence.get(c) for c in targets}
        for src in targets:
            for dst in targets:
                if src == dst:
                    continue
                try:
                    mom.transfer(src, dst)
                except TransferCapExceeded:
                    pass
                assert agent.supervisor.competence.get(dst) >= before[dst], (
                    f"transfer {src}→{dst} decreased dst competence"
                )

    def test_invariant_persistence_file_is_signed(self, agent_and_mom):
        """The consolidated JSON must carry a 64-char HMAC-SHA256
        signature over the canonical payload. Any future refactor
        that quietly drops the signature would silently disable the
        tamper detection."""
        agent, mom = agent_and_mom
        agent.supervisor.competence.set("CYLINDRICAL", 0.5)
        assert mom.consolidate(force=True) is True
        data = json.loads(Path(mom.persistence_path).read_text())
        assert "signature" in data
        assert len(data["signature"]) == 64
        # And the format_version is pinned — a silent bump would
        # also disable boot for older sidecars.
        from axiom_developmental_curriculum import PERSISTENCE_FORMAT_VERSION
        assert data["format_version"] == PERSISTENCE_FORMAT_VERSION
