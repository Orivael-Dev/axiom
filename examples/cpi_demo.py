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

    print("\n── Status summary ────────────────────────────────────────────────────")
    st = agent.status()
    for k, v in st.items():
        print(f"  {k:<22} {v}")
    print("\nThe robot does not think about whether to fall.")
    print("The constitution prevents it before the fall begins —")
    print("and the recovery doesn't trigger the next fall.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
