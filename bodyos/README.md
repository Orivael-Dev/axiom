# BodyOS (ORVL-029)

> **STATUS: DESIGN ONLY — NOT IMPLEMENTED.**
> This directory is a scaffold. No code has been written yet. The build is parked
> behind the `.axm` signing-key / guest-key delegation work
> (`docs/AXM_GUEST_KEY_DELEGATION.md`) because BodyOS's Audit/Fleet-Sync layer depends
> on it. Pick this up once that lands and merges to `main`.

**Orivael BodyOS** is an interoceptive survival-routing and predictive-wear layer for
embodied AI / humanoid robots. It turns internal machine stress — sensor load, actuator
fatigue, compute cost, entropy, safety violations — into **routing penalties**, so a
robot changes behavior *before* breakdown.

Full disclosure: [`docs/ORVL-029_BODYOS_DISCLOSURE.md`](../docs/ORVL-029_BODYOS_DISCLOSURE.md).

---

## The key insight: BodyOS is mostly a wiring job

Reading the disclosure against the existing codebase, **~80% of BodyOS maps onto Axiom
primitives that already exist and are tested.** This is the same pattern as the
interoception note — a new (embodied) domain framing over layers Axiom already ships.
The genuinely new work is small and well-bounded.

### Reuse map — BodyOS layer → existing Axiom module

| BodyOS layer (disclosure §8) | Wires to (existing) | New work |
|---|---|---|
| Body-State Encoder (amplitude / freq / phase) | `axiom_resonance/encoder.py` — `ResonanceEncoder` | adapt to machine telemetry |
| Resonance Router (safe-agent / slow / refuse routing) | `axiom_resonance/router.py` — `ResonanceRouter` (already signed sparse routing) | define action-routing targets |
| Metabolic Evaluator (`C_compute/motion/thermal/battery/balance/wear/constitutional`) | interoception reward concept (α/β/γ loss); `docs/INFERENCE_OS_BLUEPRINT.md` | the composite-cost function |
| Constitutional Manifold (allowed / restricted / forbidden action regions) | `axiom_latent_v2.py` — CLCA manifold *M* (ORVL-005) | physical-action regions |
| VectorStateStore (signed Body-State Packets, clustered) | `axiom_memory_engine.py` — `ConstitutionalPacket`; `axiom_multiresolution_memory.py` | wear-packet schema |
| Retrospective Sandbox / charging-cycle learning | `axiom_retrospect.py` — `ConstitutionalRetrospect` (ORVL-020) | replay over body-state logs |
| Penalty Update Engine (validated penalties + decay) | `ConstitutionalRetrospect` + PatternLibrary EWMA | wear-penalty extraction |
| Sandbox validation (adversarial) | `axiom_cas_orchestrator.py` — CAS (ORVL-008) | penalty non-overlap checks |
| Audit + Fleet Sync (signed events, fleet propagation) | `axiom_signing.py` + `.axm` attestation + **guest-key delegation** + `axiom_cmaa.py` (fleet) | **depends on signing work** |
| Machine Pain signal (non-conscious routing penalty) | thin new layer: composite cost → penalty | maps to interoception "pain" |
| Sensor Layer / telemetry adapters (ROS 2 / CAN / EtherCAT) | — | **genuinely new embodied part** |

### What's actually new (the only build risk)
1. **Telemetry adapters** — ROS 2 / CAN bus / EtherCAT / BMS / GPU telemetry ingestion.
   The embodied I/O that doesn't exist anywhere in Axiom yet.
2. **The composite-cost function** — `C_compute/motion/thermal/battery/balance/wear/
   constitutional` blended into one survival score (the robot analogue of the α/β/γ loss).
3. **The wear-packet schema** — what a Body-State Packet records for joints/actuators so
   the existing memory + clustering can do predictive maintenance.

Everything else is wiring an existing, tested layer to robot signals.

---

## Planned module layout (names only — nothing created yet)

```
bodyos/
  README.md              ← this scaffold
  telemetry/             ← sensor adapters (ROS 2 / CAN / EtherCAT) — new embodied part
  body_state_encoder.py  ← wraps ResonanceEncoder for machine telemetry
  metabolic_evaluator.py ← composite cost C_*
  survival_router.py     ← wraps ResonanceRouter for action routing
  body_state_store.py    ← wraps axiom_memory_engine for Body-State Packets
  retrospect_cycle.py    ← wraps ConstitutionalRetrospect for charging-cycle learning
  fleet_sync.py          ← signed propagation (guest-key delegation + CMAA)
```

## Dependency / sequencing
- **Blocked on:** `.axm` attestation + guest-key delegation
  (`docs/AXM_GUEST_KEY_DELEGATION.md`). `fleet_sync.py` is the consumer — signed,
  scoped, expiring fleet updates are exactly that work applied to robot telemetry.
- **Order:** finish signing-key work → merge to `main` → rebase this branch → build
  the three new pieces above → wire the rest.

## Layer mapping (Inference OS)
BodyOS spans **Layer 4 (Governance Guard)** — constitutional physical-safety boundaries —
and **Layer 5 (Adversarial Lab)** — sandbox-validated penalty learning — pushed to the
**edge** (robot onboard compute, Jetson-class). It is the embodied extension of the
seven-layer architecture in `CLAUDE.md`.

## Scope discipline for whoever builds this
Resist re-implementing routing, memory, retrospection, signing, or clustering — they
exist and are tested. If a BodyOS module is more than a thin adapter over an existing
Axiom module, stop and check the reuse map above first.
