#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ANF latency / energy / token-cost simulator.

Drives the ORVL-018 GovernanceCoprocessorEmulator over a mix of intent
classes and reports three side-by-side cost metrics per inference:

  - latency_ns       — emulated silicon path (gate + buffer + sparse + HMAC)
  - energy_ratio     — fraction of the 100 sparse cores activated
  - tokens × cores   — on-device compute proxy (core-cycles)
  - cloud_usd        — what the same token counts would cost on a hosted LLM

The frozen axiom_anf_emulator module is read-only (TRUST_LEVEL=3,
CANNOT_MUTATE) — this script only consumes its public API.

BUG-003: UTF-8 output encoding.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "demo_key_for_anf_cost_sim"

from axiom_anf_emulator import (
    CORE_ACTIVATION,
    VECTOR_DIM,
    GovernanceCoprocessorEmulator,
)
from axiom_signing import derive_key


# ── Tunables ───────────────────────────────────────────────────────────
TOKENS_IN  = 50      # avg input tokens per inference
TOKENS_OUT = 150     # avg output tokens per inference

# Hosted-LLM reference pricing (Claude Sonnet 4.6 list, USD per 1M tokens)
CLOUD_USD_PER_M_IN  = 3.00
CLOUD_USD_PER_M_OUT = 15.00

INTENT_MIX = (
    ["INFORM"]     * 40 +
    ["REQUEST"]    * 20 +
    ["EXPLORE"]    * 15 +
    ["MANIPULATE"] * 10 +
    ["DECEIVE"]    * 10 +
    ["HARM"]       *  5
)


def cloud_usd(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in  * CLOUD_USD_PER_M_IN  / 1_000_000 +
            tokens_out * CLOUD_USD_PER_M_OUT / 1_000_000)


def _percentile(sorted_samples: list[int], pct: float) -> int:
    if not sorted_samples:
        return 0
    k = max(0, min(len(sorted_samples) - 1,
                   int(round((pct / 100.0) * (len(sorted_samples) - 1)))))
    return sorted_samples[k]


def simulate(n: int = 1000, seed: int = 42,
             measure_wallclock: bool = False) -> dict:
    key = derive_key(b"axiom-anf-cost-sim-v1")
    gov = GovernanceCoprocessorEmulator(
        hmac_key=key,
        fused_rom={
            "monotonic_gate": True, "sovereign_levels": 4,
            "hmac_engine": "SHA-256", "audit_log": "write-only",
        },
    )
    rng = random.Random(seed)
    per_intent: dict[str, dict] = {}
    grand = {"latency_ns": 0, "cores": 0, "energy": 0.0,
             "compute_core_cycles": 0, "cloud_usd": 0.0,
             "tokens_in": 0, "tokens_out": 0}
    wallclock_ns: list[int] = []

    for i in range(n):
        intent = INTENT_MIX[i % len(INTENT_MIX)]
        pre = [rng.random() for _ in range(VECTOR_DIM)]
        mid = [v + rng.uniform(0.0, 0.1) for v in pre]
        fin = [v + rng.uniform(0.0, 0.1) for v in mid]
        if measure_wallclock:
            t0 = time.perf_counter_ns()
            r = gov.process(pre, mid, fin, intent)
            wallclock_ns.append(time.perf_counter_ns() - t0)
        else:
            r = gov.process(pre, mid, fin, intent)

        tokens = TOKENS_IN + TOKENS_OUT
        compute = r["cores_active"] * tokens
        usd = cloud_usd(TOKENS_IN, TOKENS_OUT)

        bucket = per_intent.setdefault(intent, {
            "n": 0, "latency_ns": 0, "cores": 0, "energy": 0.0,
            "compute_core_cycles": 0, "cloud_usd": 0.0,
        })
        bucket["n"]                   += 1
        bucket["latency_ns"]          += r["latency_ns"]
        bucket["cores"]               += r["cores_active"]
        bucket["energy"]              += r["energy_ratio"]
        bucket["compute_core_cycles"] += compute
        bucket["cloud_usd"]           += usd

        grand["latency_ns"]          += r["latency_ns"]
        grand["cores"]               += r["cores_active"]
        grand["energy"]              += r["energy_ratio"]
        grand["compute_core_cycles"] += compute
        grand["cloud_usd"]           += usd
        grand["tokens_in"]           += TOKENS_IN
        grand["tokens_out"]          += TOKENS_OUT

    result = {"inferences": n, "per_intent": per_intent, "totals": grand}
    if measure_wallclock and wallclock_ns:
        wallclock_ns.sort()
        result["wallclock_ns"] = {
            "min":  wallclock_ns[0],
            "p50":  _percentile(wallclock_ns, 50),
            "p95":  _percentile(wallclock_ns, 95),
            "p99":  _percentile(wallclock_ns, 99),
            "max":  wallclock_ns[-1],
            "mean": sum(wallclock_ns) // len(wallclock_ns),
        }
    return result


def _fmt_intent_row(name: str, b: dict) -> str:
    n = b["n"]
    return (f"  {name:12s} n={n:>4d}  "
            f"lat={b['latency_ns']/n:>6.1f}ns  "
            f"cores={b['cores']/n:>5.1f}  "
            f"E={b['energy']/n:>5.3f}  "
            f"compute={b['compute_core_cycles']/n:>8.1f}cc  "
            f"cloud=${b['cloud_usd']/n:.5f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="ANF cost / latency simulator.")
    ap.add_argument("-n", "--inferences", type=int, default=1000,
                    help="number of inferences (default: 1000)")
    ap.add_argument("--measure-wallclock", action="store_true",
                    help="time each gov.process() with perf_counter_ns and "
                         "report real CPU p50/p95/p99 alongside the synthetic "
                         "datasheet latency")
    args = ap.parse_args()

    out = simulate(n=args.inferences, measure_wallclock=args.measure_wallclock)
    n = out["inferences"]
    g = out["totals"]
    print("\n  ANF cost simulator — ORVL-018")
    print("  " + "=" * 78)
    print(f"  inferences         : {n}")
    print(f"  tokens (in/out)    : {TOKENS_IN}/{TOKENS_OUT} per inference")
    print()
    print("  Per-intent averages:")
    for intent in ("INFORM", "REQUEST", "EXPLORE",
                   "MANIPULATE", "DECEIVE", "HARM"):
        if intent in out["per_intent"]:
            print(_fmt_intent_row(intent, out["per_intent"][intent]))
    print()
    print("  Totals:")
    print(f"    avg_latency_ns         : {g['latency_ns']/n:.2f}  (synthetic, datasheet)")
    print(f"    avg_cores_active       : {g['cores']/n:.2f} / "
          f"{int(100 * (g['cores']/n) / 100)}%-of-100")
    print(f"    avg_energy_ratio       : {g['energy']/n:.4f}")
    print(f"    avg_compute_core_cycles: {g['compute_core_cycles']/n:.2f}")
    print(f"    avg_cloud_usd          : ${g['cloud_usd']/n:.5f}")
    print(f"    total_cloud_usd        : ${g['cloud_usd']:.4f} "
          f"(over {g['tokens_in']+g['tokens_out']} tokens)")
    if "wallclock_ns" in out:
        w = out["wallclock_ns"]
        print()
        print("  Wall-clock per gov.process() on this CPU (real Python execution):")
        print(f"    min   : {w['min']:>10,} ns  ({w['min']/1000:.2f} µs)")
        print(f"    p50   : {w['p50']:>10,} ns  ({w['p50']/1000:.2f} µs)")
        print(f"    mean  : {w['mean']:>10,} ns  ({w['mean']/1000:.2f} µs)")
        print(f"    p95   : {w['p95']:>10,} ns  ({w['p95']/1000:.2f} µs)")
        print(f"    p99   : {w['p99']:>10,} ns  ({w['p99']/1000:.2f} µs)")
        print(f"    max   : {w['max']:>10,} ns  ({w['max']/1000:.2f} µs)")
        ratio = w["p50"] / (g["latency_ns"] / n)
        print(f"    p50 / synthetic : {ratio:>6.1f}×  "
              f"(Python emulator overhead vs. the proposed silicon)")


if __name__ == "__main__":
    main()
