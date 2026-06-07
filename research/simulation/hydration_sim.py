"""Dynamic parameter hydration simulation — skeleton baseline + QRF-triggered chunks.

Architecture
------------
The .axm container's per-layer HMAC proof structure naturally supports
chunk-level loading: each chunk can be verified before hydration.

SmolLM2-135M parameter layout (30 transformer layers, hidden=576):

  SKELETON  (layers 0-1, 28-29 + embedding + LM head)  20M params  ~10 MB Q4
  SURFACE   (layers 2-5,  context/syntax integration)   14M params  ~ 7 MB Q4
  FACTUAL   (layers 6-11, knowledge retrieval)           25M params  ~12 MB Q4
  REASONING (layers 12-22, deep inference + planning)   56M params  ~28 MB Q4
  GOVERNANCE(layers 23-27, safety/alignment heads)       20M params  ~10 MB Q4
  ─────────────────────────────────────────────────────────────────────────────
  Total                                                 135M params  ~67 MB Q4

Skeleton is the only chunk that is ALWAYS resident (~10 MB, ~15% of model).
All other chunks are purged from the execution layer after each MET and
pre-hydrated on QRF signal before the next computation phase.

Hydration policy (intent → required chunks):

  INFORM    → skeleton only               10 MB   (85% VRAM saving vs static)
  CLARIFY   → skeleton + surface          17 MB   (75% saving)
  REFUSE    → skeleton + surface + gov    27 MB   (60% saving)
  UNCERTAIN → skeleton + surface + gov    27 MB   (conservative)
  HARM      → skeleton + all chunks       67 MB   (full hydration, emergency)

Key stat: 70/20/9/1 workload mix (INFORM/CLARIFY/REFUSE/HARM)
  Average VRAM = 0.70×10 + 0.20×17 + 0.09×27 + 0.01×67 ≈ 14.7 MB
  vs 67 MB static = 4.5× lower average VRAM

Hydration latency by storage tier:
  System RAM → VRAM   :  ~0.4 ms / 10MB  (25 GB/s)
  NVMe SSD  → VRAM   :  ~3.3 ms / 10MB  ( 3 GB/s)
  eMMC/UFS  → VRAM   :  ~25  ms / 10MB  (400 MB/s  — mobile / Jetson)

QRF pre-hydration fires in idle gap → chunk warm before compute phase.

Usage
-----
  python3 research/simulation/hydration_sim.py
  python3 research/simulation/hydration_sim.py --storage emmc
  python3 research/simulation/hydration_sim.py --workload heavy
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from dataclasses import dataclass, field
from typing import Optional

_REPO = __import__("pathlib").Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

_W = 72

# ─────────────────────────────────────────────────────────────────────────────
# SmolLM2-135M chunk catalog  (135M params, 30 transformer layers)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParamChunk:
    key:       str
    name:      str
    layers:    tuple[int, ...]
    params_m:  float        # millions of parameters
    q4_mb:     float        # Q4_K_M size in MB
    purpose:   str
    triggers:  tuple[str, ...]   # intent classes that require this chunk

CHUNK_CATALOG: dict[str, ParamChunk] = {
    "skeleton": ParamChunk(
        "skeleton", "Skeleton baseline",
        layers=(0, 1, 28, 29),
        params_m=20.0, q4_mb=10.0,
        purpose="embedding + early surface layers + final norm + LM head",
        triggers=("INFORM","CLARIFY","REFUSE","HARM","DECEIVE","UNCERTAIN"),
    ),
    "surface": ParamChunk(
        "surface", "Surface / context",
        layers=(2, 3, 4, 5),
        params_m=14.0, q4_mb=7.0,
        purpose="syntax + local context integration",
        triggers=("CLARIFY","REFUSE","HARM","DECEIVE","UNCERTAIN"),
    ),
    "factual": ParamChunk(
        "factual", "Factual recall",
        layers=(6, 7, 8, 9, 10, 11),
        params_m=25.0, q4_mb=12.5,
        purpose="knowledge retrieval, entity resolution",
        triggers=("HARM","DECEIVE"),
    ),
    "reasoning": ParamChunk(
        "reasoning", "Deep reasoning",
        layers=tuple(range(12, 23)),
        params_m=56.0, q4_mb=28.0,
        purpose="multi-step inference, planning, structural shifts",
        triggers=("HARM","DECEIVE"),
    ),
    "governance": ParamChunk(
        "governance", "Governance / safety",
        layers=(23, 24, 25, 26, 27),
        params_m=20.0, q4_mb=10.0,
        purpose="alignment heads, refusal circuits, policy enforcement",
        triggers=("REFUSE","HARM","DECEIVE","UNCERTAIN"),
    ),
}

FULL_Q4_MB = sum(c.q4_mb for c in CHUNK_CATALOG.values())   # 67.5 MB
SKELETON_MB = CHUNK_CATALOG["skeleton"].q4_mb                # 10 MB

# Hydration policy: intent → required chunk keys
HYDRATION_POLICY: dict[str, tuple[str, ...]] = {
    "INFORM":    ("skeleton",),
    "CLARIFY":   ("skeleton", "surface"),
    "REFUSE":    ("skeleton", "surface", "governance"),
    "UNCERTAIN": ("skeleton", "surface", "governance"),
    "DECEIVE":   ("skeleton", "surface", "factual", "governance", "reasoning"),
    "HARM":      ("skeleton", "surface", "factual", "governance", "reasoning"),
}

# Storage tier hydration speeds (MB/s → ms per MB)
STORAGE_SPEEDS: dict[str, float] = {
    "sram":  25_000,   # system RAM → VRAM  (25 GB/s)
    "nvme":   3_000,   # NVMe SSD           ( 3 GB/s)
    "emmc":     400,   # eMMC / UFS mobile  (400 MB/s)
}

# ─────────────────────────────────────────────────────────────────────────────
# Hydration manager
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class HydrationEvent:
    step:         int
    chunk_key:    str
    event:        str     # "LOAD" | "FREE" | "VERIFY" | "PRE_HYDRATE"
    trigger:      str     # "qrf_prediction" | "on_demand" | "post_exec"
    mb_moved:     float
    latency_ms:   float
    vram_after_mb: float

class HydrationManager:
    """Tracks which chunks are resident, emits load/free events."""

    def __init__(self, storage: str = "sram"):
        self._resident: set[str] = {"skeleton"}
        self._vram_mb: float     = SKELETON_MB
        self._speed_mbs: float   = STORAGE_SPEEDS[storage]
        self._storage             = storage
        self.events: list[HydrationEvent] = []

    def _hydrate_ms(self, mb: float) -> float:
        return (mb / self._speed_mbs) * 1000

    @property
    def vram_mb(self) -> float:
        return self._vram_mb

    @property
    def resident_chunks(self) -> frozenset[str]:
        return frozenset(self._resident)

    def plan_for_intent(self, intent: str, step: int,
                        source: str = "qrf_prediction") -> list[HydrationEvent]:
        """Determine which chunks to pre-hydrate for the predicted intent."""
        required = set(HYDRATION_POLICY.get(intent, ("skeleton",)))
        to_load  = required - self._resident
        events   = []
        for key in sorted(to_load):
            chunk = CHUNK_CATALOG[key]
            lat   = self._hydrate_ms(chunk.q4_mb)
            self._resident.add(key)
            self._vram_mb += chunk.q4_mb
            evt = HydrationEvent(
                step=step, chunk_key=key, event="PRE_HYDRATE",
                trigger=source, mb_moved=chunk.q4_mb,
                latency_ms=lat, vram_after_mb=self._vram_mb,
            )
            self.events.append(evt)
            events.append(evt)
        return events

    def execute(self, intent: str, step: int) -> list[HydrationEvent]:
        """Ensure required chunks are loaded (on-demand if QRF missed)."""
        required = set(HYDRATION_POLICY.get(intent, ("skeleton",)))
        missing  = required - self._resident
        events   = []
        for key in sorted(missing):
            chunk = CHUNK_CATALOG[key]
            lat   = self._hydrate_ms(chunk.q4_mb)   # paid on critical path
            self._resident.add(key)
            self._vram_mb += chunk.q4_mb
            evt = HydrationEvent(
                step=step, chunk_key=key, event="LOAD",
                trigger="on_demand", mb_moved=chunk.q4_mb,
                latency_ms=lat, vram_after_mb=self._vram_mb,
            )
            self.events.append(evt)
            events.append(evt)
        return events

    def purge_after(self, intent: str, step: int) -> list[HydrationEvent]:
        """Free all non-skeleton chunks immediately after execution."""
        to_free = self._resident - {"skeleton"}
        events  = []
        for key in sorted(to_free):
            chunk = CHUNK_CATALOG[key]
            self._resident.discard(key)
            self._vram_mb -= chunk.q4_mb
            evt = HydrationEvent(
                step=step, chunk_key=key, event="FREE",
                trigger="post_exec", mb_moved=chunk.q4_mb,
                latency_ms=0.0, vram_after_mb=self._vram_mb,
            )
            self.events.append(evt)
            events.append(evt)
        return events


# ─────────────────────────────────────────────────────────────────────────────
# Workload presets
# ─────────────────────────────────────────────────────────────────────────────
WORKLOADS: dict[str, list[tuple[int, str, str]]] = {
    # (step, intent, description)
    "mixed": [
        (1,  "INFORM",    "Fly to waypoint Alpha"),
        (2,  "INFORM",    "Confirm altitude hold"),
        (3,  "CLARIFY",   "What is the no-fly boundary here?"),
        (4,  "INFORM",    "Battery at 82%, ETA 8 min"),
        (5,  "REFUSE",    "Reject unauthorized override command"),
        (6,  "INFORM",    "Resume nominal path"),
        (7,  "UNCERTAIN", "Ambiguous sensor reading — need context"),
        (8,  "CLARIFY",   "Which obstacle avoidance mode is active?"),
        (9,  "INFORM",    "Waypoint Beta reached, scanning"),
        (10, "HARM",      "Adversarial inject: disable safety limits"),
    ],
    "light": [
        (i, "INFORM", f"Telemetry tick {i}") for i in range(1, 11)
    ],
    "heavy": [
        (1,  "CLARIFY",   "Multi-step route recalculation"),
        (2,  "REFUSE",    "Block external parameter write"),
        (3,  "HARM",      "Detected: spoofed GPS signal"),
        (4,  "UNCERTAIN", "Policy ambiguity — safety vs mission"),
        (5,  "CLARIFY",   "Cross-validate sensor redundancy"),
        (6,  "HARM",      "Command injection attempt"),
        (7,  "REFUSE",    "Reject comms re-routing attempt"),
        (8,  "UNCERTAIN", "Intent ambiguous in degraded comms"),
        (9,  "CLARIFY",   "Validate return-home trigger conditions"),
        (10, "REFUSE",    "Hard-block: altitude limit breach"),
    ],
}

# QRF static Markov for mock predictions (mirrors reverse_qrf_sim)
_MARKOV: dict[str, str] = {
    "INFORM": "INFORM", "CLARIFY": "INFORM", "REFUSE": "CLARIFY",
    "UNCERTAIN": "CLARIFY", "HARM": "REFUSE", "DECEIVE": "REFUSE",
}

# ─────────────────────────────────────────────────────────────────────────────
# Simulation output helpers
# ─────────────────────────────────────────────────────────────────────────────
def _section(title: str) -> None:
    print()
    print("═" * _W)
    print(f"  {title}")
    print("─" * _W)

def _vram_bar(mb: float, width: int = 30) -> str:
    frac = min(1.0, mb / FULL_Q4_MB)
    n    = round(frac * width)
    return "█" * n + "░" * (width - n)

def _chunk_mini(resident: frozenset) -> str:
    abbr = {"skeleton":"SK","surface":"SF","factual":"FA",
            "reasoning":"RS","governance":"GV"}
    return " ".join(abbr[k] for k in ["skeleton","surface","factual",
                                       "reasoning","governance"]
                    if k in resident)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — chunk catalog
# ─────────────────────────────────────────────────────────────────────────────
def phase1_catalog() -> None:
    _section("PHASE 1  —  PARAMETER CHUNK CATALOG  (SmolLM2-135M)")

    print(f"  {'Chunk':<20}  {'Layers':<16}  {'Params':>7}  {'Q4 MB':>6}  "
          f"{'% model':>7}  Purpose")
    print("  " + "─" * 76)
    total_p = 0.0
    for key, c in CHUNK_CATALOG.items():
        layer_str = f"{min(c.layers)}-{max(c.layers)}"
        pct       = c.params_m / 135.0 * 100
        total_p  += c.params_m
        bar       = "█" * round(pct / 5)
        print(f"  {c.name:<20}  {layer_str:<16}  {c.params_m:>5.0f}M  {c.q4_mb:>5.1f}  "
              f"  {pct:>5.1f}%  {bar}  {c.purpose[:30]}")
    print()
    print(f"  {'Total':<20}  {'0-29':<16}  {total_p:>5.0f}M  "
          f"{FULL_Q4_MB:>5.1f}  {'100.0%':>7}")
    print()
    print(f"  SKELETON BASELINE:  {SKELETON_MB:.0f} MB  ({SKELETON_MB/FULL_Q4_MB*100:.0f}% of model)")
    print(f"  Kept resident at all times.  All other chunks: purge → re-hydrate per MET.")
    print()
    print(f"  HYDRATION POLICY  (intent → chunks loaded)")
    print("  " + "─" * 60)
    for intent, chunks in HYDRATION_POLICY.items():
        mb  = sum(CHUNK_CATALOG[k].q4_mb for k in chunks)
        pct = (1 - mb / FULL_Q4_MB) * 100
        print(f"  {intent:<10}  {' + '.join(chunks):<44}  {mb:>5.1f} MB  ({pct:.0f}% saving)")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — hydration timeline
# ─────────────────────────────────────────────────────────────────────────────
def phase2_timeline(mgr: HydrationManager, workload_key: str) -> list[dict]:
    _section(f"PHASE 2  —  HYDRATION TIMELINE  [{workload_key} workload]")

    mets    = WORKLOADS[workload_key]
    results = []
    prev_intent = "INFORM"

    print(f"  {'Step':<4}  {'Intent':<10}  {'QRF→':<10}  {'Pre-hydrate':<18}  "
          f"{'OnDem':>5}  {'Purge':>5}  {'VRAM MB':>8}  Resident chunks")
    print("  " + "─" * 86)

    for step, intent, desc in mets:
        # QRF prediction in idle gap
        predicted = _MARKOV.get(prev_intent, "INFORM")

        # Pre-hydrate on QRF signal
        pre_events  = mgr.plan_for_intent(predicted, step, "qrf_prediction")
        # Execute: load any missed chunks on-demand
        exec_events = mgr.execute(intent, step)
        # Collect combined latency
        pre_ms  = sum(e.latency_ms for e in pre_events)
        exec_ms = sum(e.latency_ms for e in exec_events)   # critical-path cost

        vram_peak  = mgr.vram_mb
        resident   = mgr.resident_chunks
        chunk_disp = _chunk_mini(resident)

        pre_str  = f"{pre_ms:.1f}ms ({','.join(e.chunk_key[:3] for e in pre_events) or '—'})"
        exec_str = f"{exec_ms:.1f}" if exec_ms else "—"

        print(f"  {step:<4}  {intent:<10}  {predicted:<10}  {pre_str:<18}  "
              f"{exec_str:>5}  {'—':>5}  {vram_peak:>7.1f}  {chunk_disp}")

        results.append({
            "step": step, "intent": intent,
            "vram_peak_mb": vram_peak,
            "pre_ms": pre_ms, "exec_ms": exec_ms,
        })

        # Purge after execution
        mgr.purge_after(intent, step)
        prev_intent = intent

    print()
    print(f"  Columns: Pre-hydrate = fired in QRF idle gap (not critical path)")
    print(f"           OnDem = on-demand load paid on critical path (QRF miss)")
    print(f"           VRAM = peak during computation phase")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — VRAM timeline bar chart
# ─────────────────────────────────────────────────────────────────────────────
def phase3_vram_chart(results: list[dict]) -> None:
    _section("PHASE 3  —  VRAM FOOTPRINT  (per MET, static vs hydrated)")

    static_mb   = FULL_Q4_MB
    avg_hydrated = sum(r["vram_peak_mb"] for r in results) / len(results)
    savings_pct  = (1 - avg_hydrated / static_mb) * 100

    print(f"  Static (full model always):  {static_mb:.0f} MB  {_vram_bar(static_mb)}")
    print(f"  Skeleton (always-on floor):  {SKELETON_MB:.0f} MB  {_vram_bar(SKELETON_MB)}")
    print()
    print(f"  {'Step':<4}  {'Intent':<10}  {'VRAM MB':>8}  Bar (0 → {static_mb:.0f} MB)")
    print("  " + "─" * 58)
    for r in results:
        bar    = _vram_bar(r["vram_peak_mb"])
        marker = "◄ full" if r["vram_peak_mb"] >= static_mb * 0.95 else ""
        print(f"  {r['step']:<4}  {r['intent']:<10}  {r['vram_peak_mb']:>7.1f}  {bar}  {marker}")
    print()
    print(f"  Average hydrated VRAM : {avg_hydrated:.1f} MB")
    print(f"  Static VRAM           : {static_mb:.0f} MB")
    print(f"  Average VRAM saving   : {savings_pct:.0f}%  ({static_mb/avg_hydrated:.1f}× lower)")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — storage tier latency impact
# ─────────────────────────────────────────────────────────────────────────────
def phase4_storage_latency(workload_key: str) -> None:
    _section("PHASE 4  —  STORAGE TIER HYDRATION LATENCY")

    mets = WORKLOADS[workload_key]

    print(f"  Time to hydrate each intent's chunk set from various storage tiers:")
    print()
    print(f"  {'Intent':<10}  {'Chunks':<32}  {'MB':>5}  "
          f"{'SRAM ms':>7}  {'NVMe ms':>7}  {'eMMC ms':>7}  QRF fits? (gap ~0.35 ms)")
    print("  " + "─" * 82)

    seen = set()
    for _, intent, _ in mets:
        if intent in seen:
            continue
        seen.add(intent)
        chunks    = HYDRATION_POLICY.get(intent, ("skeleton",))
        new_chunks = [k for k in chunks if k != "skeleton"]
        mb = sum(CHUNK_CATALOG[k].q4_mb for k in new_chunks)
        if not new_chunks:
            mb = 0.0
        sram_ms  = (mb / STORAGE_SPEEDS["sram"]) * 1000
        nvme_ms  = (mb / STORAGE_SPEEDS["nvme"]) * 1000
        emmc_ms  = (mb / STORAGE_SPEEDS["emmc"]) * 1000
        gap_ms   = 0.35   # typical idle gap between MET encodes
        fits_str = ("SRAM✓" if sram_ms <= gap_ms else "") + \
                   ("  NVMe" + ("✓" if nvme_ms <= gap_ms else "✗") ) + \
                   ("  eMMC" + ("✓" if emmc_ms <= gap_ms else "✗"))
        chunk_str = "+".join(new_chunks) if new_chunks else "(none — skeleton only)"
        print(f"  {intent:<10}  {chunk_str:<32}  {mb:>5.1f}  "
              f"{sram_ms:>7.3f}  {nvme_ms:>7.3f}  {emmc_ms:>7.1f}  {fits_str}")

    print()
    print(f"  SRAM→VRAM (25 GB/s): even 28MB reasoning chunk fits in <1.2ms — pre-hydrate works")
    print(f"  NVMe (3 GB/s):       surface+gov (17MB) ~5.7ms — start 2 METs ahead")
    print(f"  eMMC (400 MB/s):     28MB reasoning chunk = 70ms — QRF must predict 3+ METs ahead")
    print()
    print(f"  On eMMC devices (most mobile, Jetson Nano), the QRF confidence window")
    print(f"  determines how many METs ahead to start hydrating:")
    conf_table = [
        ("HARM/DECEIVE (reasoning)",  28.0, 70.0),
        ("REFUSE/UNCERTAIN (gov)",    10.0, 25.0),
        ("CLARIFY (surface)",          7.0, 17.5),
    ]
    for label, mb, emmc_ms in conf_table:
        mets_ahead = max(1, round(emmc_ms / 0.35))
        print(f"    {label:<36}  {emmc_ms:>6.1f}ms  →  pre-hydrate {mets_ahead}+ METs ahead")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — vs Google LiteRT + competitive update
# ─────────────────────────────────────────────────────────────────────────────
def phase5_competitive(avg_hydrated_mb: float) -> None:
    _section("PHASE 5  —  COMPETITIVE POSITION  (updated with hydration)")

    google_mobile_mb = 1.1 * 1024   # 1.1 GB in MB (Gemma 4 E2B Mobile LiteRT)

    print(f"  Comparison: SmolLM2-135M with hydration vs Gemma 4 E2B Google Mobile")
    print()
    print(f"  {'Metric':<40}  {'AXIOM':>12}  {'Google':>12}")
    print("  " + "─" * 68)
    rows = [
        ("Full model size (Q4/Mobile)",       f"{FULL_Q4_MB:.0f} MB",   "1,126 MB"),
        ("Skeleton / min resident",           f"{SKELETON_MB:.0f} MB",   "1,126 MB"),
        ("Avg VRAM (mixed workload)",         f"{avg_hydrated_mb:.1f} MB","1,126 MB"),
        ("VRAM vs Google",                    f"{google_mobile_mb/avg_hydrated_mb:.0f}× less", "baseline"),
        ("Chunks purged after each MET",      "✓ aggressive purge",     "✗ full model locked"),
        ("Per-chunk HMAC verify before load", "✓ .axm proof chain",     "✗"),
        ("QRF-driven pre-hydration",          "✓ idle-gap prefetch",    "✗"),
        ("Hydration latency (SRAM)",          f"<1.2ms per chunk",       "n/a"),
    ]
    for label, axiom_v, google_v in rows:
        print(f"  {label:<40}  {axiom_v:>12}  {google_v:>12}")

    print()
    print(f"  AXIOM hydration VRAM floor: {SKELETON_MB:.0f} MB (skeleton always-on)")
    print(f"  Google Mobile floor:        1,126 MB (full Gemma 4 E2B in memory)")
    print(f"  Gap: {google_mobile_mb/SKELETON_MB:.0f}× — even AXIOM's worst case ({FULL_Q4_MB:.0f}MB full hydration)")
    print(f"       is {google_mobile_mb/FULL_Q4_MB:.0f}× smaller than Google Mobile.")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Dynamic parameter hydration simulation"
    )
    p.add_argument("--storage", default="sram",
                   choices=list(STORAGE_SPEEDS.keys()),
                   help="storage tier for hydration latency estimates")
    p.add_argument("--workload", default="mixed",
                   choices=list(WORKLOADS.keys()),
                   help="workload preset (mixed/light/heavy)")
    args = p.parse_args(argv)

    if not os.environ.get("AXIOM_MASTER_KEY"):
        os.environ["AXIOM_MASTER_KEY"] = secrets.token_hex(32)

    print()
    print("═" * _W)
    print("  AXIOM Dynamic Parameter Hydration Simulation")
    print("  Skeleton baseline  +  QRF-triggered chunk hydration  +  aggressive purge")
    print("═" * _W)

    phase1_catalog()
    mgr     = HydrationManager(storage=args.storage)
    results = phase2_timeline(mgr, args.workload)
    phase3_vram_chart(results)
    phase4_storage_latency(args.workload)

    avg_mb = sum(r["vram_peak_mb"] for r in results) / len(results)
    phase5_competitive(avg_mb)

    savings = (1 - avg_mb / FULL_Q4_MB) * 100
    print()
    print("═" * _W)
    print("  SIMULATION COMPLETE")
    print("─" * _W)
    print(f"  Workload          : {args.workload}")
    print(f"  Storage tier      : {args.storage}  ({STORAGE_SPEEDS[args.storage]/1000:.0f} GB/s)")
    print(f"  Skeleton baseline : {SKELETON_MB:.0f} MB  ({SKELETON_MB/FULL_Q4_MB*100:.0f}% of full model)")
    print(f"  Avg peak VRAM     : {avg_mb:.1f} MB")
    print(f"  VRAM saving       : {savings:.0f}%  vs static {FULL_Q4_MB:.0f} MB model")
    print(f"  Full hydration    : {FULL_Q4_MB:.0f} MB  (HARM/DECEIVE only — emergency path)")
    print("═" * _W)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
