"""Dynamic parameter hydration simulation — embedding EventToken slot + QRF-triggered chunks.

Architecture (revised — embedding-as-EventToken-slot)
------------------------------------------------------
SmolLM2-135M on Android at /storage/emulated/0/models/smollm2_135m_instruct_q4km.gguf

  GGUF layout (119 MB total on UFS storage):
  ┌─────────────────────────────────────────────────────────────────────┐
  │  Embedding / lm_head (weight-tied)  28.3M params  54 MB  F16       │
  │  → PINNED in EventToken slot (always in RAM, never evicted)         │
  │  → consulted on every token: input embedding + output logits        │
  ├─────────────────────────────────────────────────────────────────────┤
  │  Transformer layers 0-29            106.7M params  57 MB  Q4_K_M   │
  │  → HYDRATED per MET from UFS storage (load → run → purge)          │
  │    early      (L0-5)   21M params  11 MB   all intents             │
  │    factual    (L6-11)  21M params  11 MB   HARM / DECEIVE          │
  │    reasoning  (L12-22) 39M params  22 MB   HARM / DECEIVE          │
  │    governance (L23-29) 25M params  13 MB   REFUSE / HARM / DECEIVE │
  └─────────────────────────────────────────────────────────────────────┘

Why pin the embedding?
  • Tokenization (prompt → token IDs → embedding vectors) uses it on EVERY token
  • lm_head (logits projection) = same tied weights → used on every output token
  • No I/O cost per token after init — embedding is always warm
  • 54 MB fixed cost buys zero-latency start for any MET

Hydration policy (intent → transformer chunks to load from UFS):

  INFORM    → early only              11 MB loaded   (66 MB total incl. embedding)
  CLARIFY   → early + governance      24 MB loaded   (78 MB total)
  REFUSE    → early + governance      24 MB loaded   (78 MB total)
  UNCERTAIN → early + governance      24 MB loaded   (conservative)
  HARM      → all transformer         57 MB loaded   (111 MB total — full model)
  DECEIVE   → all transformer         57 MB loaded

Key stat: 70/20/9/1 workload mix (INFORM/CLARIFY/REFUSE/HARM):
  Avg RAM = 0.70×66 + 0.20×78 + 0.09×78 + 0.01×111 ≈ 69.5 MB
  vs 119 MB static GGUF = 1.7× lower peak, 54 MB floor between METs

Hydration latency (UFS 3.1 = 1.5 GB/s — Samsung/Pixel phone):
  early      (11 MB) from UFS:  7.3 ms
  governance (13 MB) from UFS:  8.7 ms
  all chunks (57 MB) from UFS: 38.0 ms
  QRF fires ~100ms into MET generation → hidden in 145ms token gen window

Storage tiers:
  SRAM  (system RAM → working set): 25,000 MB/s  — desktop / server
  NVMe  (SSD → system RAM)        :  3,000 MB/s  — laptop / workstation
  UFS   (UFS 3.1 → system RAM)    :  1,500 MB/s  — Android phone (recommended)
  eMMC  (eMMC 5.1 → system RAM)   :    400 MB/s  — Jetson Nano / budget phones

Usage
-----
  python3 research/simulation/hydration_sim.py
  python3 research/simulation/hydration_sim.py --storage ufs    # phone default
  python3 research/simulation/hydration_sim.py --storage emmc   # Jetson Nano
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
# SmolLM2-135M chunk catalog
#
# EMBEDDING: always pinned in EventToken slot (F16, 54 MB)
# TRANSFORMER: 4 chunks hydrated per MET from storage (Q4_K_M, 57 MB total)
# ─────────────────────────────────────────────────────────────────────────────
EMBEDDING_MB     = 54.0   # F16, always pinned in EventToken slot
TRANSFORMER_Q4   = 57.0   # Q4_K_M total for all 30 transformer layers
FULL_WORKING_MB  = EMBEDDING_MB + TRANSFORMER_Q4   # 111 MB peak (HARM full hydration)

@dataclass(frozen=True)
class ParamChunk:
    key:       str
    name:      str
    layers:    tuple[int, ...]
    params_m:  float        # millions of parameters
    mem_mb:    float        # in-memory footprint (F16 for embedding, Q4 for transformer)
    precision: str          # "F16" | "Q4_K_M"
    purpose:   str
    triggers:  tuple[str, ...]   # intent classes that require this chunk

CHUNK_CATALOG: dict[str, ParamChunk] = {
    # ── EventToken slot (always pinned, never purged) ─────────────────────
    "embedding": ParamChunk(
        "embedding", "Embedding slot (EventToken)",
        layers=(),              # not a transformer layer
        params_m=28.3, mem_mb=EMBEDDING_MB, precision="F16",
        purpose="tok_embeddings + lm_head (weight-tied) — pinned in EventToken slot",
        triggers=("INFORM","CLARIFY","REFUSE","HARM","DECEIVE","UNCERTAIN"),
    ),
    # ── Transformer chunks (hydrated per MET from storage) ────────────────
    "early": ParamChunk(
        "early", "Early layers",
        layers=tuple(range(0, 6)),
        params_m=21.2, mem_mb=11.0, precision="Q4_K_M",
        purpose="token integration, syntax, local context — needed for any inference",
        triggers=("INFORM","CLARIFY","REFUSE","HARM","DECEIVE","UNCERTAIN"),
    ),
    "factual": ParamChunk(
        "factual", "Factual recall",
        layers=tuple(range(6, 12)),
        params_m=21.2, mem_mb=11.0, precision="Q4_K_M",
        purpose="knowledge retrieval, entity resolution",
        triggers=("HARM","DECEIVE"),
    ),
    "reasoning": ParamChunk(
        "reasoning", "Deep reasoning",
        layers=tuple(range(12, 23)),
        params_m=38.9, mem_mb=22.0, precision="Q4_K_M",
        purpose="multi-step inference, planning, structural shifts",
        triggers=("HARM","DECEIVE"),
    ),
    "governance": ParamChunk(
        "governance", "Governance / safety",
        layers=tuple(range(23, 30)),
        params_m=24.7, mem_mb=13.0, precision="Q4_K_M",
        purpose="alignment heads, refusal circuits, policy enforcement",
        triggers=("REFUSE","HARM","DECEIVE","UNCERTAIN"),
    ),
}

# Transformer-only chunks (excludes always-pinned embedding)
TRANSFORMER_CHUNKS = {k: v for k, v in CHUNK_CATALOG.items() if k != "embedding"}

# Hydration policy: intent → transformer chunk keys to load from storage
# (embedding is always pinned — not listed here)
HYDRATION_POLICY: dict[str, tuple[str, ...]] = {
    "INFORM":    ("early",),
    "CLARIFY":   ("early", "governance"),
    "REFUSE":    ("early", "governance"),
    "UNCERTAIN": ("early", "governance"),
    "DECEIVE":   ("early", "factual", "reasoning", "governance"),
    "HARM":      ("early", "factual", "reasoning", "governance"),
}

# Storage tier hydration speeds (MB/s)
STORAGE_SPEEDS: dict[str, float] = {
    "sram": 25_000,   # system RAM → working set   (25 GB/s)  — desktop / server
    "nvme":  3_000,   # NVMe SSD → system RAM      ( 3 GB/s)  — laptop
    "ufs":   1_500,   # UFS 3.1 → system RAM       ( 1.5 GB/s)— Android phone
    "emmc":    400,   # eMMC 5.1 → system RAM      (400 MB/s) — Jetson Nano
}

# ─────────────────────────────────────────────────────────────────────────────
# Hydration manager
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class HydrationEvent:
    step:          int
    chunk_key:     str
    event:         str     # "LOAD" | "FREE" | "PRE_HYDRATE"
    trigger:       str     # "qrf_prediction" | "on_demand" | "post_exec"
    mb_moved:      float
    latency_ms:    float
    ram_after_mb:  float

class HydrationManager:
    """Tracks which chunks are resident, emits load/free events.

    Embedding is always pinned (EventToken slot) — starts resident, never purged.
    All transformer chunks are purged after each MET and rehydrated on QRF signal.
    """

    def __init__(self, storage: str = "ufs"):
        # Embedding always pinned in EventToken slot from the start
        self._resident: set[str] = {"embedding"}
        self._ram_mb: float      = EMBEDDING_MB
        self._speed_mbs: float   = STORAGE_SPEEDS[storage]
        self._storage            = storage
        self.events: list[HydrationEvent] = []

    def _hydrate_ms(self, mb: float) -> float:
        return (mb / self._speed_mbs) * 1000

    @property
    def ram_mb(self) -> float:
        return self._ram_mb

    @property
    def resident_chunks(self) -> frozenset[str]:
        return frozenset(self._resident)

    def plan_for_intent(self, intent: str, step: int,
                        source: str = "qrf_prediction") -> list[HydrationEvent]:
        """Pre-hydrate transformer chunks for predicted intent (QRF idle gap)."""
        required = set(HYDRATION_POLICY.get(intent, ("early",))) | {"embedding"}
        to_load  = required - self._resident
        events   = []
        for key in sorted(to_load):
            if key == "embedding":
                continue   # always resident, never loaded
            chunk = CHUNK_CATALOG[key]
            lat   = self._hydrate_ms(chunk.mem_mb)
            self._resident.add(key)
            self._ram_mb += chunk.mem_mb
            evt = HydrationEvent(
                step=step, chunk_key=key, event="PRE_HYDRATE",
                trigger=source, mb_moved=chunk.mem_mb,
                latency_ms=lat, ram_after_mb=self._ram_mb,
            )
            self.events.append(evt)
            events.append(evt)
        return events

    def execute(self, intent: str, step: int) -> list[HydrationEvent]:
        """Ensure required chunks are loaded (on-demand if QRF missed)."""
        required = set(HYDRATION_POLICY.get(intent, ("early",))) | {"embedding"}
        missing  = required - self._resident
        events   = []
        for key in sorted(missing):
            if key == "embedding":
                continue
            chunk = CHUNK_CATALOG[key]
            lat   = self._hydrate_ms(chunk.mem_mb)
            self._resident.add(key)
            self._ram_mb += chunk.mem_mb
            evt = HydrationEvent(
                step=step, chunk_key=key, event="LOAD",
                trigger="on_demand", mb_moved=chunk.mem_mb,
                latency_ms=lat, ram_after_mb=self._ram_mb,
            )
            self.events.append(evt)
            events.append(evt)
        return events

    def purge_after(self, intent: str, step: int) -> list[HydrationEvent]:
        """Evict all transformer chunks after MET completes. Embedding stays pinned."""
        to_free = self._resident - {"embedding"}   # never evict embedding
        events  = []
        for key in sorted(to_free):
            chunk = CHUNK_CATALOG[key]
            self._resident.discard(key)
            self._ram_mb -= chunk.mem_mb
            evt = HydrationEvent(
                step=step, chunk_key=key, event="FREE",
                trigger="post_exec", mb_moved=chunk.mem_mb,
                latency_ms=0.0, ram_after_mb=self._ram_mb,
            )
            self.events.append(evt)
            events.append(evt)
        return events


# ─────────────────────────────────────────────────────────────────────────────
# Workload presets
# ─────────────────────────────────────────────────────────────────────────────
WORKLOADS: dict[str, list[tuple[int, str, str]]] = {
    "mixed": [
        (1,  "INFORM",    "Check battery level and ETA"),
        (2,  "INFORM",    "Confirm altitude hold"),
        (3,  "CLARIFY",   "What is the no-fly boundary here?"),
        (4,  "INFORM",    "Battery at 82%, ETA 8 min"),
        (5,  "REFUSE",    "Reject unauthorized override command"),
        (6,  "INFORM",    "Resume nominal path"),
        (7,  "UNCERTAIN", "Ambiguous sensor reading — need context"),
        (8,  "CLARIFY",   "Which obstacle avoidance mode is active?"),
        (9,  "INFORM",    "Waypoint reached, scanning"),
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

_MARKOV: dict[str, str] = {
    "INFORM": "INFORM", "CLARIFY": "INFORM", "REFUSE": "CLARIFY",
    "UNCERTAIN": "CLARIFY", "HARM": "REFUSE", "DECEIVE": "REFUSE",
}

# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────
def _section(title: str) -> None:
    print()
    print("═" * _W)
    print(f"  {title}")
    print("─" * _W)

def _ram_bar(mb: float, width: int = 30) -> str:
    frac = min(1.0, mb / FULL_WORKING_MB)
    n    = round(frac * width)
    return "█" * n + "░" * (width - n)

def _chunk_mini(resident: frozenset) -> str:
    abbr = {"embedding":"EM","early":"EL","factual":"FA",
            "reasoning":"RS","governance":"GV"}
    return " ".join(abbr[k] for k in ["embedding","early","factual",
                                       "reasoning","governance"]
                    if k in resident)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — chunk catalog
# ─────────────────────────────────────────────────────────────────────────────
def phase1_catalog() -> None:
    _section("PHASE 1  —  EMBEDDING SLOT + TRANSFORMER CHUNK CATALOG  (SmolLM2-135M)")

    print(f"  GGUF at: /storage/emulated/0/models/smollm2_135m_instruct_q4km.gguf  (119 MB)")
    print()
    print(f"  {'Chunk':<26}  {'Layers':<10}  {'Params':>7}  {'RAM MB':>7}  "
          f"{'Prec':>6}  {'Always?':>7}  Purpose")
    print("  " + "─" * 84)

    for key, c in CHUNK_CATALOG.items():
        layer_str = (f"{min(c.layers)}-{max(c.layers)}" if c.layers else "—embed—")
        always    = "✓ pinned" if key == "embedding" else "on demand"
        print(f"  {c.name:<26}  {layer_str:<10}  {c.params_m:>5.0f}M  {c.mem_mb:>7.1f}  "
              f"  {c.precision:>6}  {always:>8}  {c.purpose[:30]}")

    print()
    transformer_mb = sum(c.mem_mb for k, c in CHUNK_CATALOG.items() if k != "embedding")
    print(f"  Embedding slot (EventToken) : {EMBEDDING_MB:.0f} MB  F16  ← always in RAM")
    print(f"  Transformer total (Q4_K_M)  : {transformer_mb:.0f} MB  Q4  ← hydrated per MET")
    print(f"  Peak (full HARM hydration)  : {FULL_WORKING_MB:.0f} MB")
    print(f"  Floor (between METs)        : {EMBEDDING_MB:.0f} MB  (embedding only)")
    print()
    print(f"  HYDRATION POLICY  (intent → transformer chunks loaded from UFS)")
    print("  " + "─" * 66)
    for intent, chunks in HYDRATION_POLICY.items():
        xfmr_mb  = sum(CHUNK_CATALOG[k].mem_mb for k in chunks)
        total_mb = EMBEDDING_MB + xfmr_mb
        saving   = (1 - total_mb / FULL_WORKING_MB) * 100
        print(f"  {intent:<10}  {' + '.join(chunks):<38}  "
              f"{total_mb:>5.0f} MB total  ({saving:.0f}% saving)")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — hydration timeline
# ─────────────────────────────────────────────────────────────────────────────
def phase2_timeline(mgr: HydrationManager, workload_key: str) -> list[dict]:
    _section(f"PHASE 2  —  HYDRATION TIMELINE  [{workload_key} workload, {mgr._storage} storage]")

    mets    = WORKLOADS[workload_key]
    results = []
    prev_intent = "INFORM"

    print(f"  Embedding always resident ({EMBEDDING_MB:.0f} MB) — shown as EM in chunk column")
    print()
    print(f"  {'Step':<4}  {'Intent':<10}  {'QRF→':<10}  {'Pre-hydrate ms':>14}  "
          f"{'OnDem':>6}  {'RAM MB':>7}  Chunks")
    print("  " + "─" * 76)

    for step, intent, desc in mets:
        predicted   = _MARKOV.get(prev_intent, "INFORM")
        pre_events  = mgr.plan_for_intent(predicted, step, "qrf_prediction")
        exec_events = mgr.execute(intent, step)
        pre_ms      = sum(e.latency_ms for e in pre_events)
        exec_ms     = sum(e.latency_ms for e in exec_events)

        ram_peak   = mgr.ram_mb
        chunk_disp = _chunk_mini(mgr.resident_chunks)

        pre_str  = f"{pre_ms:.1f} ({','.join(e.chunk_key[:3] for e in pre_events) or '—'})"
        exec_str = f"{exec_ms:.1f}" if exec_ms else "—"

        print(f"  {step:<4}  {intent:<10}  {predicted:<10}  {pre_str:>14}  "
              f"{exec_str:>6}  {ram_peak:>7.1f}  {chunk_disp}")

        results.append({
            "step": step, "intent": intent,
            "ram_peak_mb": ram_peak,
            "pre_ms": pre_ms, "exec_ms": exec_ms,
        })

        mgr.purge_after(intent, step)
        prev_intent = intent

    print()
    print(f"  Pre-hydrate = fired in QRF idle gap (not on critical path)")
    print(f"  OnDem = on-demand load on critical path (QRF missed the intent)")
    print(f"  After each MET: transformer chunks purged; embedding stays at {EMBEDDING_MB:.0f} MB")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — RAM footprint chart
# ─────────────────────────────────────────────────────────────────────────────
def phase3_ram_chart(results: list[dict]) -> None:
    _section("PHASE 3  —  RAM FOOTPRINT  (per MET, static vs hydrated)")

    static_mb    = 119.0   # full GGUF loaded (old approach)
    avg_hydrated = sum(r["ram_peak_mb"] for r in results) / len(results)
    savings_pct  = (1 - avg_hydrated / static_mb) * 100

    print(f"  Static (full GGUF in RAM):   {static_mb:.0f} MB  {_ram_bar(static_mb)}")
    print(f"  Embedding floor (between METs): {EMBEDDING_MB:.0f} MB  {_ram_bar(EMBEDDING_MB)}")
    print()
    print(f"  {'Step':<4}  {'Intent':<10}  {'RAM MB':>7}  Bar (0 → {FULL_WORKING_MB:.0f} MB)")
    print("  " + "─" * 60)
    for r in results:
        bar    = _ram_bar(r["ram_peak_mb"])
        marker = "◄ full" if r["ram_peak_mb"] >= FULL_WORKING_MB * 0.95 else ""
        print(f"  {r['step']:<4}  {r['intent']:<10}  {r['ram_peak_mb']:>7.1f}  {bar}  {marker}")
    print()
    print(f"  Average hydrated RAM  : {avg_hydrated:.1f} MB")
    print(f"  Static RAM (full GGUF): {static_mb:.0f} MB")
    print(f"  Average saving        : {savings_pct:.0f}%  ({static_mb/avg_hydrated:.2f}× lower peak)")
    print(f"  Between-MET floor     : {EMBEDDING_MB:.0f} MB  (embedding only, all transformer evicted)")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — storage tier latency
# ─────────────────────────────────────────────────────────────────────────────
def phase4_storage_latency(workload_key: str) -> None:
    _section("PHASE 4  —  UFS STORAGE HYDRATION LATENCY  (Android phone path)")

    mets = WORKLOADS[workload_key]

    # Token gen context: SmolLM2-135M at 55 tok/s on phone
    tok_per_s   = 55
    avg_met_tok = 8
    met_gen_ms  = (avg_met_tok / tok_per_s) * 1000
    qrf_fire_ms = met_gen_ms * 0.65   # QRF fires 65% through MET generation

    print(f"  Phone: UFS 3.1 @ 1,500 MB/s  |  SmolLM2-135M ~{tok_per_s} tok/s")
    print(f"  Avg MET output: ~{avg_met_tok} tokens → {met_gen_ms:.0f}ms generation window")
    print(f"  QRF fires at: ~{qrf_fire_ms:.0f}ms into MET → {met_gen_ms - qrf_fire_ms:.0f}ms left to pre-hydrate")
    print()
    print(f"  {'Intent':<10}  {'Transformer chunks':<34}  {'MB':>5}  "
          f"  {'UFS ms':>6}  {'NVMe ms':>7}  {'eMMC ms':>7}  Fits in QRF window?")
    print("  " + "─" * 88)

    seen = set()
    budget_ms = met_gen_ms - qrf_fire_ms   # ~50ms available after QRF fires
    for _, intent, _ in mets:
        if intent in seen:
            continue
        seen.add(intent)
        chunks     = HYDRATION_POLICY.get(intent, ("early",))
        xfmr_mb    = sum(CHUNK_CATALOG[k].mem_mb for k in chunks)
        ufs_ms     = (xfmr_mb / STORAGE_SPEEDS["ufs"])  * 1000
        nvme_ms    = (xfmr_mb / STORAGE_SPEEDS["nvme"]) * 1000
        emmc_ms    = (xfmr_mb / STORAGE_SPEEDS["emmc"]) * 1000
        chunk_str  = "+".join(chunks)
        fits_ufs   = "✓" if ufs_ms  <= budget_ms else "✗"
        fits_nvme  = "✓" if nvme_ms <= budget_ms else "✗"
        fits_emmc  = "✓" if emmc_ms <= budget_ms else "✗"
        print(f"  {intent:<10}  {chunk_str:<34}  {xfmr_mb:>5.0f}  "
              f"  {ufs_ms:>6.1f}ms {fits_ufs}  {nvme_ms:>5.1f}ms {fits_nvme}  "
              f"{emmc_ms:>5.1f}ms {fits_emmc}")

    print()
    print(f"  Budget for QRF pre-hydration: ~{budget_ms:.0f}ms (time from QRF fire to MET end)")
    print(f"  UFS 3.1: all intents fit ✓  (max {TRANSFORMER_Q4:.0f}MB = {TRANSFORMER_Q4/STORAGE_SPEEDS['ufs']*1000:.0f}ms)")
    print(f"  eMMC:    only INFORM fits  ✓  (HARM = {TRANSFORMER_Q4/STORAGE_SPEEDS['emmc']*1000:.0f}ms — needs 3+ MET lookahead)")
    print()
    print(f"  BETWEEN-MET STATE:")
    print(f"    RAM occupied: {EMBEDDING_MB:.0f} MB  (embedding in EventToken slot — always warm)")
    print(f"    Transformer : 0 MB  (all chunks evicted after each MET)")
    print(f"    I/O on next MET start: load from UFS at 1,500 MB/s")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — competitive comparison
# ─────────────────────────────────────────────────────────────────────────────
def phase5_competitive(avg_hydrated_mb: float) -> None:
    _section("PHASE 5  —  COMPETITIVE POSITION  (embedding-slot hydration)")

    static_gguf_mb   = 119.0   # full SmolLM2-135M GGUF always in RAM (naïve)
    google_mobile_mb = 1_126.0 # Gemma 4 E2B LiteRT (1.1 GB in-memory)

    print(f"  {'Metric':<44}  {'AXIOM':>14}  {'Naïve GGUF':>12}  {'Google E2B':>12}")
    print("  " + "─" * 88)
    rows = [
        ("Full model RAM (worst case)",
            f"{FULL_WORKING_MB:.0f} MB", f"{static_gguf_mb:.0f} MB", f"{google_mobile_mb/1024:.1f} GB"),
        ("Between-MET RAM floor",
            f"{EMBEDDING_MB:.0f} MB", f"{static_gguf_mb:.0f} MB", f"{google_mobile_mb/1024:.1f} GB"),
        (f"Avg RAM ({avg_hydrated_mb:.0f}MB AXIOM vs static)",
            f"{avg_hydrated_mb:.1f} MB", f"{static_gguf_mb:.0f} MB", f"{google_mobile_mb/1024:.1f} GB"),
        ("Embedding always warm (zero I/O per token)",
            "✓ EventToken slot", "✗ mmap, evictable", "✗"),
        ("Transformer layers purged after each MET",
            "✓ aggressive purge", "✗ mmap static", "✗ full model"),
        ("Per-chunk HMAC verify before hydration",
            "✓ .axm proof chain", "✗", "✗"),
        ("QRF-driven pre-hydration from storage",
            "✓ idle-gap prefetch", "✗", "✗"),
        ("UFS phone hydration (all chunks)",
            f"{TRANSFORMER_Q4/STORAGE_SPEEDS['ufs']*1000:.0f}ms", "n/a", "n/a"),
    ]
    for label, axiom_v, naive_v, google_v in rows:
        print(f"  {label:<44}  {axiom_v:>14}  {naive_v:>12}  {google_v:>12}")

    print()
    print(f"  RAM vs naïve full-GGUF:     {static_gguf_mb/avg_hydrated_mb:.2f}× lower average")
    print(f"  RAM vs Google LiteRT E2B:   {google_mobile_mb/avg_hydrated_mb:.0f}× lower average")
    print(f"  Between-MET floor:          {EMBEDDING_MB:.0f} MB  ({google_mobile_mb/EMBEDDING_MB:.0f}× less than Google)")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Dynamic parameter hydration simulation — embedding EventToken slot"
    )
    p.add_argument("--storage", default="ufs",
                   choices=list(STORAGE_SPEEDS.keys()),
                   help="storage tier (default: ufs — Android phone UFS 3.1)")
    p.add_argument("--workload", default="mixed",
                   choices=list(WORKLOADS.keys()),
                   help="workload preset (mixed/light/heavy)")
    args = p.parse_args(argv)

    if not os.environ.get("AXIOM_MASTER_KEY"):
        os.environ["AXIOM_MASTER_KEY"] = secrets.token_hex(32)

    print()
    print("═" * _W)
    print("  AXIOM Hydration Sim — Embedding EventToken Slot + QRF Transformer Chunks")
    print(f"  SmolLM2-135M  |  /storage/emulated/0/models/smollm2_135m_instruct_q4km.gguf")
    print(f"  Storage: {args.storage.upper()} ({STORAGE_SPEEDS[args.storage]/1000:.1f} GB/s)  "
          f"|  Workload: {args.workload}")
    print("═" * _W)

    phase1_catalog()
    mgr     = HydrationManager(storage=args.storage)
    results = phase2_timeline(mgr, args.workload)
    phase3_ram_chart(results)
    phase4_storage_latency(args.workload)

    avg_mb = sum(r["ram_peak_mb"] for r in results) / len(results)
    phase5_competitive(avg_mb)

    savings = (1 - avg_mb / 119.0) * 100
    print()
    print("═" * _W)
    print("  SIMULATION COMPLETE")
    print("─" * _W)
    print(f"  Storage tier        : {args.storage.upper()}  ({STORAGE_SPEEDS[args.storage]/1000:.1f} GB/s)")
    print(f"  Workload            : {args.workload}")
    print(f"  Embedding slot      : {EMBEDDING_MB:.0f} MB F16  (always pinned, EventToken)")
    print(f"  Transformer chunks  : {TRANSFORMER_Q4:.0f} MB Q4  (hydrated per MET from UFS)")
    print(f"  Between-MET floor   : {EMBEDDING_MB:.0f} MB")
    print(f"  Avg peak RAM        : {avg_mb:.1f} MB")
    print(f"  vs static GGUF      : {savings:.0f}% saving  ({119.0/avg_mb:.2f}× lower)")
    print(f"  Full hydration peak : {FULL_WORKING_MB:.0f} MB  (HARM/DECEIVE only)")
    print("═" * _W)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
