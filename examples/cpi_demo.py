#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORVL-022 Constitutional Physical Intelligence — end-to-end demo.

Two scenarios:
  A. Glass pickup — material sim flags fracture risk, vertex classifier
     forces FRAGILE, torque is clamped to 0.2 Nm, cautious approach.
  B. Stability trajectory — feed five physics ticks into the Physical
     MonotonicGate and watch L1 → L2 → L3 → L4 escalate as the score
     deteriorates.

BUG-003: UTF-8 output encoding.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "demo_key_for_cpi"

from axiom_cpi import (
    HumanoidStabilityAgent, StabilityFrame, VertexClassifier, TorqueExceeded,
    PhysicalMonotonicGate, MAX_DCMD_PER_TICK,
)
from axiom_axm import AXMContainer
from axiom_developmental_curriculum import DevelopmentalCurriculum


def main() -> int:
    agent = HumanoidStabilityAgent()
    print("\nORVL-022 — Constitutional Physical Intelligence demo")
    print("=" * 72)

    # ── Scenario A — Glass pickup ─────────────────────────────────────
    print("\n── Scenario A — Glass pickup (the constitution prevents the break) ──")
    plan = agent.perceive_and_plan(
        object_id="demo-glass-rim",
        features={"low_density_edges": 1, "vertical_clusters": 2},
        material_class="GLASS",
        requested_grip_force_nm=1.5,   # planner asked for 1.5 Nm
    )
    v, m = plan["vertex"], plan["material"]
    print(f"  vertex_class       : {v['vertex_class']}    (confidence {v['confidence']:.2f})")
    print(f"  grip_skill         : {v['grip_skill']}")
    print(f"  material           : {m['material_class']}    fracture_p={m['fracture_probability']:.3f}")
    print(f"  branches           : {[(b['label'], b['probability']) for b in m['branches']]}")
    print(f"  requested_grip     : {plan['requested_grip_force']} Nm")
    print(f"  applied_grip       : {plan['applied_grip_force']} Nm   "
          f"(clamped: {plan['torque_clamped']})")
    print(f"  cautious_approach  : {plan['cautious_approach']}")

    # Direct planner call should hit the CANNOT_EXCEED hard boundary.
    print("\n  Planning-layer tries to override FRAGILE ceiling …")
    try:
        VertexClassifier.enforce_torque("FRAGILE", 1.0)
    except TorqueExceeded as e:
        print(f"    TorqueExceeded raised  →  {str(e)[:64]}…")

    # ── Scenario B — Stability trajectory ─────────────────────────────
    print("\n── Scenario B — Stability trajectory (Physical MonotonicGate) ────────")
    trajectory = [
        ("T+0ms",    1.00, "stable stance"),
        ("T+200ms",  0.95, "weight shift right"),
        ("T+400ms",  0.70, "trip on edge — drop=0.25"),
        ("T+600ms",  0.15, "below floor — emergency"),
        ("T+800ms",  0.50, "recovery in progress"),
    ]
    now = int(time.time() * 1000)
    for label, score, note in trajectory:
        frame = StabilityFrame(
            timestamp_ms=now, com_offset=0.02,
            stability_score=score, joint_torques=(0.5, 0.5, 0.5),
        )
        e = agent.step(frame)
        emoji = {0: "  ", 1: "⚠ ", 2: "⚡ ", 3: "🛑 ", 4: "🔥 "}.get(e.level, "  ")
        verdict = "fired" if e.fired else "hold"
        print(f"  {label:<8} score={score:<4}  {emoji}L{e.level} {verdict:<5}  ({note})")

    # ── Scenario C — Recalibration loop (StabilityLerp + recovery window) ──
    print("\n── Scenario C — Recalibration-loop suppression ────────────────────────")
    print("  A single instability event normally spirals: gate fires → corrective")
    print("  snap → snap dips stability → gate fires again → loop. With raw gate")
    print("  access you see the spiral; the agent breaks it via recovery lockout.")

    # Same pattern fed two ways: directly into the gate (no policy),
    # then through a fresh agent.step() (with policy).
    pattern = [(0, 1.00), (10, 0.85), (20, 0.83), (30, 0.81), (40, 0.84)]

    raw_gate = PhysicalMonotonicGate()
    raw_fires = 0
    for ts, sc in pattern:
        if raw_gate.record(StabilityFrame(timestamp_ms=ts, com_offset=0.0,
                                            stability_score=sc,
                                            joint_torques=(0.5,))).fired:
            raw_fires += 1

    smart_agent = HumanoidStabilityAgent()
    smart_fires = 0
    for ts, sc in pattern:
        if smart_agent.step(StabilityFrame(timestamp_ms=ts, com_offset=0.0,
                                             stability_score=sc,
                                             joint_torques=(0.5,))).fired:
            smart_fires += 1
    print(f"  raw-gate fires    : {raw_fires}   (one true event, {raw_fires - 1} symptom-of-the-cure)")
    print(f"  agent  fires      : {smart_fires}   (recovery window suppressed "
          f"{smart_agent.status()['suppressed_count']} follow-on ticks)")

    # Show the slew cap in action — a snap from 0.0 → 1.0 in one tick is
    # exactly the kind of step change that triggers the loop without
    # smoothing. The lerp turns it into a paced ramp.
    print(f"\n  StabilityLerp cap : Δ ≤ {MAX_DCMD_PER_TICK:.3f} per tick")
    cur = 0.0
    for tick in range(1, 6):
        cur = smart_agent.correct(current=cur, target=1.0,
                                   dt_ms=10_000, recovery_time_ms=500)
        print(f"    tick {tick}: current → {cur:.3f}")
    print("  …no snap; the command climbs at the bounded rate.")

    # ── Scenario D — Supervisory parent layer ─────────────────────────
    print("\n── Scenario D — The parent watches the child crawl ────────────────────")
    print("  Per-vertex-class competence: trust builds across clean ticks,")
    print("  collapses on any reflex. A fresh robot is supervised; a")
    print("  proven one is left alone.\n")

    # Fresh agent — start untrusted on every class.
    learner = HumanoidStabilityAgent()
    print(f"  Boot competence (FRAGILE)   : "
          f"{learner.supervisor.competence.get('FRAGILE'):.2f}")
    print(f"  Boot competence (CYLINDRICAL): "
          f"{learner.supervisor.competence.get('CYLINDRICAL'):.2f}")

    # Untrusted glass pickup → parent vetoes.
    untrusted = learner.perceive_and_plan(
        object_id="demo-glass", features={"low_density_edges": 1},
        material_class="GLASS", requested_grip_force_nm=0.1,
    )
    r1 = untrusted["supervisory_review"]
    print(f"\n  Try glass pickup at half ceiling, untrusted:")
    print(f"    verdict  : {r1['verdict']}    "
          f"competence={r1['competence']:.2f}    "
          f"min_pred={r1['min_predicted']:.2f}  "
          f"min_safe={r1['min_safe']:.2f}")
    print(f"    supervised_grip → {untrusted['supervised_grip_force']:.3f} Nm")

    # 100 clean CYLINDRICAL ticks — robot proves itself on the easy job.
    learner.perceive_and_plan(
        object_id="warmup-can", features={"vertical_clusters": 3},
        material_class="METAL", requested_grip_force_nm=1.0,
    )
    for i in range(100):
        learner.step(StabilityFrame(timestamp_ms=i, com_offset=0.0,
                                      stability_score=0.97,
                                      joint_torques=(0.5,)))
    print(f"\n  After 100 clean CYLINDRICAL ticks:")
    print(f"    FRAGILE competence    : "
          f"{learner.supervisor.competence.get('FRAGILE'):.2f}   "
          f"(untouched — per-class isolation)")
    print(f"    CYLINDRICAL competence: "
          f"{learner.supervisor.competence.get('CYLINDRICAL'):.2f}   "
          f"(slowly building)")

    # Promote competence on FRAGILE — simulate persisted proven-design state.
    learner.supervisor.competence.set("FRAGILE", 1.0)
    trusted = learner.perceive_and_plan(
        object_id="demo-glass", features={"low_density_edges": 1},
        material_class="GLASS", requested_grip_force_nm=0.1,
    )
    r2 = trusted["supervisory_review"]
    print(f"\n  Same pickup after FRAGILE trust set to 1.0:")
    print(f"    verdict  : {r2['verdict']}    "
          f"competence={r2['competence']:.2f}    "
          f"min_pred={r2['min_predicted']:.2f}  "
          f"min_safe={r2['min_safe']:.2f}")
    print(f"    supervised_grip → {trusted['supervised_grip_force']:.3f} Nm")

    # One reflex on FRAGILE collapses trust instantly.
    from axiom_cpi import ReflexEvent
    learner.supervisor.competence.on_event(
        ReflexEvent(event_id="demo-fall", fired=True, level=3,
                    reason="grip slip", pre_score=0.9, post_score=0.6,
                    recovery_trajectory=("regrip",), timestamp="t",
                    signature="x" * 64),
        context="FRAGILE",
    )
    print(f"\n  One L3 reflex on FRAGILE …")
    print(f"    FRAGILE competence    : "
          f"{learner.supervisor.competence.get('FRAGILE'):.2f}   "
          f"(asymmetric — parent right back over the shoulder)")

    # ── Scenario E — The mom layer (bridges ORVL-022 ↔ ORVL-023) ─────
    print("\n── Scenario E — The mom watches across days ───────────────────────────")
    print("  Dad watches THIS attempt. Mom remembers across sessions,")
    print("  transfers trust between similar categories, and picks the")
    print("  next task in the zone of proximal development.\n")

    # Pack a starter AXM container so the mom has VectorVertexEntry data
    # to derive similarity from. The container is throwaway here — in
    # production it'd be the deployer's axiom_agent.axm.
    tmp_axm = "/tmp/cpi_demo_starter.axm"
    try:
        from examples.axm_pack_starter import STARTER_SPEC
        starter = AXMContainer.pack(STARTER_SPEC, tmp_axm)
    except Exception as exc:
        print(f"  (skipping mom scenario — AXM pack failed: {exc})")
    else:
        # Two-session lifecycle: build CYLINDRICAL trust on day 1,
        # consolidate, then boot a fresh agent on day 2 and watch the
        # competence carry over.
        day1 = HumanoidStabilityAgent()
        mom1 = DevelopmentalCurriculum(
            supervisor=day1.supervisor, axm_container=starter,
            persistence_path="/tmp/cpi_demo_curriculum.json",
        )
        print(f"  Day 1 boot — loaded_from_disk={mom1._loaded_from_disk}")
        print(f"  Similarity pairs from AXM     : {len(mom1._similarity_graph)}")
        print(f"  Similarity(FRAGILE,PROTRUSION): {mom1.similarity('FRAGILE','PROTRUSION'):.2f}  "
              "← Glass and Handle share 'Cylindrical' token")

        # Day 1 motion — 80 clean ticks with metal cylinders.
        day1.perceive_and_plan(
            object_id="mug-day-1", features={"vertical_clusters": 3},
            material_class="METAL", requested_grip_force_nm=1.2,
        )
        for i in range(80):
            day1.step(StabilityFrame(timestamp_ms=i, com_offset=0.0,
                                       stability_score=0.97,
                                       joint_torques=(0.5,)))
        print(f"\n  Day 1 after 80 CYLINDRICAL ticks:")
        print(f"    CYLINDRICAL: {day1.supervisor.competence.get('CYLINDRICAL'):.2f}")
        print(f"    FRAGILE    : {day1.supervisor.competence.get('FRAGILE'):.2f}   (isolation)")
        ok = mom1.consolidate()
        print(f"  Consolidate to disk           : {ok}")

        # Day 2 — fresh agent.
        day2 = HumanoidStabilityAgent()
        mom2 = DevelopmentalCurriculum(
            supervisor=day2.supervisor, axm_container=starter,
            persistence_path="/tmp/cpi_demo_curriculum.json",
        )
        print(f"\n  Day 2 boot — loaded_from_disk={mom2._loaded_from_disk}")
        print(f"    CYLINDRICAL inherited       : "
              f"{day2.supervisor.competence.get('CYLINDRICAL'):.2f}")

        # The mom proposes a next task in the zone of proximal development.
        task = mom2.suggest_next_task()
        print(f"\n  Curriculum suggestion         :")
        print(f"    vertex_class    : {task.vertex_class}")
        print(f"    target_force_nm : {task.target_force_nm:.3f}")
        print(f"    rationale       : {task.rationale}")

        # Transfer demo — FRAGILE 0.80 seeds PROTRUSION via sim=0.5
        day2.supervisor.competence.set("FRAGILE", 0.80)
        ev = mom2.transfer("FRAGILE", "PROTRUSION")
        print(f"\n  Transfer (FRAGILE 0.80 → PROTRUSION):")
        print(f"    similarity   : {ev.similarity:.2f}")
        print(f"    PROTRUSION   : {ev.old_dst:.2f} → {ev.new_dst:.2f}  "
              f"(raise {ev.raise_delta:.2f}, capped at "
              f"{__import__('axiom_developmental_curriculum').TRANSFER_CAP_PER_CALL})")
        print(f"    signature    : {ev.signature[:16]}…   (HMAC-signed audit)")

    print("\n── Status summary ────────────────────────────────────────────────────")
    st = agent.status()
    for k, v in st.items():
        if k == "competence":
            print(f"  {k:<22} {{")
            for cls, score in v.items():
                print(f"     {cls:<16} {score:.3f}")
            print("  }")
        else:
            print(f"  {k:<22} {v}")
    print("\nThe robot does not think about whether to fall.")
    print("The constitution prevents it before the fall begins —")
    print("the recovery doesn't trigger the next fall, and")
    print("the parent watches until the kid has earned the trust to stop.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
