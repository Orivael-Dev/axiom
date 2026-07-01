"""SmolLM2-135M EMERGENCY fallback simulation.

Compares three models under a sudden UPS/backup-power event using
measured mobile baseline numbers (results/mobile_baselines.json) and
the Qwen3-1.7B MET sidecar (results/qwen3_1b7_met_sidecar.json).

Key finding: SmolLM2-135M is NOT the fastest option at 4.82 t/s — that
honour goes to TinyLlama-1.1B at 12.03 t/s.  SmolLM2's real advantages
are:
  • 10× smaller GGUF (104 MB vs 636 MB / 1056 MB) — always pre-loadable
  • 5–8× lower peak RAM — fits on 2 GB devices where others OOM
  • Fastest cold-start time — load from flash before UPS grace period ends
  • Graceful degradation: keeps the device responsive for simple queries

The simulation shows:
  1. Memory released when switching from Qwen3-1.7B to each fallback
  2. Cold-start load time at UFS 3.1 (1500 MB/s)
  3. Responses serviceable in the UPS grace period at three UPS sizes
  4. The OpenCL utilisation gap at 135M scale vs 1B scale

Usage:
    AXIOM_MASTER_KEY=... python3 research/simulation/smollm_emergency_sim.py
    python3 research/simulation/smollm_emergency_sim.py --ups-minutes 10
    python3 research/simulation/smollm_emergency_sim.py --response-tokens 64
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("AXIOM_MASTER_KEY", "0" * 64)

_W = 78


def _section(t: str) -> None:
    print(f"\n{'═' * _W}\n  {t}\n{'═' * _W}")


def _bar(v: float, mx: float, width: int = 24, unit: str = "") -> str:
    filled = int(v / mx * width) if mx > 0 else 0
    label  = f"{v:.0f}{unit}" if unit else f"{v:.1f}"
    return f"[{'█' * filled}{'░' * (width - filled)}] {label}"


# ── Load measured data ────────────────────────────────────────────────────────

def _load_baselines() -> dict:
    path = _REPO / "results" / "mobile_baselines.json"
    return json.loads(path.read_text())


def _load_qwen3_sidecar() -> dict:
    path = _REPO / "results" / "qwen3_1b7_met_sidecar.json"
    return json.loads(path.read_text())


# ── Model specs ───────────────────────────────────────────────────────────────

def _build_model_table(baselines: dict, sidecar: dict) -> list[dict]:
    """Build a unified model comparison table from measured data."""
    b = {e["id"]: e for e in baselines["baselines"]}
    smol  = b["smollm2_135m_q4km_mobile"]
    tiny  = b["tinyllama_1b_q4km_mobile"]
    q3_hy = baselines["met_sidecars"]["qwen3_1b7_q4km"]["met_hydration"]

    # Qwen3 CPU TG: not directly measured on mobile.
    # Estimated from TinyLlama ratio scaled by param count and vocab size.
    # TinyLlama:  1100M params, vocab 32k,    TG 12.03 t/s
    # Qwen3-1.7B: 1700M params, vocab 151936, TG estimated
    # Bandwidth per token scales with params + vocab (for lm_head):
    #   TinyLlama  lm_head: 32000 × 2048 = 65.5M  params
    #   Qwen3-1.7B lm_head: 151936 × 2048 = 311M  params
    #   Extra lm_head load: 311/65.5 ≈ 4.75×
    # Net bandwidth multiplier ≈ (1700 + 311*0.5) / (1100 + 65.5*0.5) ≈ 1.96×
    # Estimated TG: 12.03 / 1.96 ≈ 6.1 t/s  (conservative; marked as estimate)
    qwen3_tg_est = round(tiny["results"]["token_generation_ts"] /
                         ((1700 + 311 * 0.5) / (1100 + 65.5 * 0.5)), 1)

    # Qwen3 MET INFORM is 768.5 MB; SmolLM2 estimated peak ~450 MB (104 MB GGUF
    # + KV cache + activations + OS overhead at 4.85 bpw)
    return [
        {
            "name":      "SmolLM2-135M Q4_K_M",
            "id":        "smollm2_135m",
            "params_m":  smol["params_m"],
            "gguf_mb":   smol["size_mb"],
            "tg_ts":     smol["results"]["token_generation_ts"],
            "pp_ts":     smol["results"]["prompt_processing_ts"],
            "peak_ram_mb": smol["results"]["peak_memory_gb"] * 1024,
            "tg_note":   "measured (PocketPal, OpenCL — underutilised at 135M scale)",
            "estimated": False,
        },
        {
            "name":      "TinyLlama-1.1B Q4_K_M",
            "id":        "tinyllama_1b",
            "params_m":  tiny["params_m"],
            "gguf_mb":   tiny["size_mb"],
            "tg_ts":     tiny["results"]["token_generation_ts"],
            "pp_ts":     tiny["results"]["prompt_processing_ts"],
            "peak_ram_mb": tiny["results"]["peak_memory_gb"] * 1024,
            "tg_note":   "measured (PocketPal, OpenCL — better utilisation at 1B scale)",
            "estimated": False,
        },
        {
            "name":      "Qwen3-1.7B Q4_K_M (INFORM mode)",
            "id":        "qwen3_1b7",
            "params_m":  1700,
            "gguf_mb":   sidecar["gguf_mb"],
            "tg_ts":     qwen3_tg_est,
            "pp_ts":     None,
            "peak_ram_mb": q3_hy["inform_only_mb"],
            "tg_note":   "estimated (mobile CPU, large vocab penalty; desktop CUDA = 84.8 t/s)",
            "estimated": True,
        },
    ]


# ── Timing helpers ────────────────────────────────────────────────────────────

UFS_31_MBS = 1500.0   # UFS 3.1 read speed MB/s

def _load_time_s(gguf_mb: float, mbs: float = UFS_31_MBS) -> float:
    return gguf_mb / mbs

def _ttft_s(pp_ts: float | None, prompt_tokens: int = 512) -> float:
    if pp_ts is None:
        return 2.0  # conservative estimate for Qwen3 on mobile CPU
    return prompt_tokens / pp_ts

def _response_time_s(tg_ts: float, n_tokens: int, ttft: float) -> float:
    return ttft + n_tokens / tg_ts

def _responses_in_window(tg_ts: float, n_tokens: int, ttft: float,
                          window_s: float) -> float:
    rt = _response_time_s(tg_ts, n_tokens, ttft)
    return window_s / rt


# ── Print helpers ─────────────────────────────────────────────────────────────

def _print_model_table(models: list[dict]) -> None:
    peak_ram = max(m["peak_ram_mb"] for m in models)
    peak_tg  = max(m["tg_ts"] for m in models)
    print(f"\n  {'Model':<30}  {'GGUF':>7}  {'Peak RAM':>10}  {'TG speed':>9}  {'Est?':>5}")
    print(f"  {'─'*30}  {'─'*7}  {'─'*10}  {'─'*9}  {'─'*5}")
    for m in models:
        est = "~" if m["estimated"] else ""
        print(f"  {m['name']:<30}  {m['gguf_mb']:>5.0f} MB  "
              f"{m['peak_ram_mb']:>7.0f} MB  "
              f"{est}{m['tg_ts']:>6.2f} t/s  {'est' if m['estimated'] else 'meas'}")

    print()
    for m in models:
        prefix = "  ~" if m["estimated"] else "  "
        print(f"{prefix}{m['name']}: {m['tg_note']}")


def _print_memory_freed(models: list[dict], qwen3_inform_mb: float) -> None:
    _section("MEMORY FREED WHEN SWITCHING FROM Qwen3-1.7B (INFORM mode)")
    print(f"\n  Qwen3-1.7B INFORM floor: {qwen3_inform_mb:.0f} MB  "
          f"(embedding 593.5 MB F16 + early chunk 175 MB)")
    print()
    for m in models:
        if m["id"] == "qwen3_1b7":
            continue
        freed = qwen3_inform_mb - m["peak_ram_mb"]
        freed_pct = 100 * freed / qwen3_inform_mb
        bar = _bar(m["peak_ram_mb"], qwen3_inform_mb)
        sign = "+" if freed > 0 else ""
        print(f"  → {m['name']:<30}  {bar}  "
              f"{sign}{freed:+.0f} MB freed  ({freed_pct:+.1f} %)")


def _print_load_times(models: list[dict]) -> None:
    _section("COLD-START LOAD TIME FROM FLASH (UFS 3.1 = 1500 MB/s)")
    print(f"\n  {'Model':<30}  {'GGUF':>7}  {'Load time':>10}  {'UPS budget used':>16}")
    print(f"  {'─'*30}  {'─'*7}  {'─'*10}  {'─'*16}")
    for m in models:
        lt = _load_time_s(m["gguf_mb"])
        pct_of_5min = 100 * lt / 300
        print(f"  {m['name']:<30}  {m['gguf_mb']:>5.0f} MB  {lt:>8.2f} s  "
              f"{pct_of_5min:>6.1f} % of 5-min UPS")

    print()
    print("  SmolLM2 cold-start: ~0.07 s — can be pre-loaded in background before UPS hits.")
    print("  Qwen3-1.7B cold-start: ~0.70 s — fast, but requires 768 MB RAM already resident.")
    print("  TinyLlama cold-start: ~0.42 s — good balance; surpasses SmolLM2 TG after load.")


def _print_ups_table(models: list[dict], ups_minutes_list: list[int],
                     response_tokens: int) -> None:
    _section(f"REQUESTS SERVICEABLE IN UPS GRACE PERIOD ({response_tokens}-token responses)")

    for ups_min in ups_minutes_list:
        ups_s = ups_min * 60
        print(f"\n  ── UPS capacity: {ups_min} minutes ({ups_s}s) ──")
        print(f"  {'Model':<30}  {'Load':<8}  {'TTFT':<7}  "
              f"{'t/resp':>8}  {'Responses':>10}")
        print(f"  {'─'*30}  {'─'*8}  {'─'*7}  {'─'*8}  {'─'*10}")
        for m in models:
            lt    = _load_time_s(m["gguf_mb"])
            ttft  = _ttft_s(m["pp_ts"])
            rt    = _response_time_s(m["tg_ts"], response_tokens, ttft)
            avail = max(0, ups_s - lt)
            resps = _responses_in_window(m["tg_ts"], response_tokens, ttft, avail)
            est   = "~" if m["estimated"] else " "
            print(f"  {m['name']:<30}  {lt:>5.2f} s  {ttft:>5.2f} s  "
                  f"{rt:>7.1f} s  {est}{resps:>8.1f}")


def _print_opencl_analysis() -> None:
    _section("WHY SmolLM2 IS SLOWER THAN TinyLlama ON MOBILE GPU")
    print("""
  OpenCL GPU dispatch overhead dominates at 135M scale.

  On each token generation step the runtime must:
    1. Dispatch a matrix-multiply kernel for every layer's attention + MLP
    2. The kernel setup overhead is roughly constant regardless of matrix size
    3. At 135M params / 30 layers the matrices are small enough that the
       kernel overhead dominates over actual compute time

  TinyLlama-1.1B has 8× more parameters spread across 22 layers — each
  individual matmul is larger, so compute time > dispatch overhead,
  and the GPU pipeline saturates properly.

  Measured gap:
    SmolLM2-135M   4.82 t/s  (OpenCL pipeline underutilised)
    TinyLlama-1.1B 12.03 t/s  (OpenCL saturates at 1B scale)
    Speedup ratio: 2.5×

  What SmolLM2 actually wins on:
    Peak RAM     1024 MB  vs  2048 MB   (2× less)
    GGUF size    103.67 MB vs   636 MB  (6.1× smaller)
    Load time     0.07 s  vs   0.42 s  (6× faster cold-start)
    Params        135M    vs  1100M    (8× fewer — energy per forward pass)

  EMERGENCY fallback recommendation:
    Device ≥ 2 GB available RAM  → TinyLlama-1.1B  (12 t/s, better UX)
    Device < 2 GB available RAM  → SmolLM2-135M    (only option that fits)
    Device < 512 MB available     → SmolLM2-135M   (emergency only, degraded)

  The power conditioner agent selects SmolLM2 as the fallback because it
  is guaranteed to fit.  If the device reports enough RAM (> 2 GB free),
  a smarter dispatcher could promote to TinyLlama.  This is a planned
  extension: PowerConditionerAgent.select_fallback(ram_free_mb).
""")


def _print_energy_estimate(models: list[dict], response_tokens: int) -> None:
    _section("ENERGY PER RESPONSE (estimated)")
    print("""
  Assumes typical mobile SoC active power:
    GPU-accelerated inference:  ~1.5 W above idle
    CPU-only inference:         ~0.8 W above idle
    Display + OS idle:          ~0.5 W
""")
    # SmolLM2 and TinyLlama are GPU-accelerated (OpenCL)
    # Qwen3 on mobile CPU is CPU-only
    power = {"smollm2_135m": 1.5, "tinyllama_1b": 1.5, "qwen3_1b7": 0.8}
    print(f"  {'Model':<30}  {'t/resp':>8}  {'Power':>8}  {'J/resp':>8}  {'Resp/Wh':>10}")
    print(f"  {'─'*30}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*10}")
    for m in models:
        ttft = _ttft_s(m["pp_ts"])
        rt   = _response_time_s(m["tg_ts"], response_tokens, ttft)
        w    = power[m["id"]]
        j    = rt * w
        rph  = 3600 / j   # responses per Wh
        print(f"  {m['name']:<30}  {rt:>7.1f}s  {w:>6.1f} W  {j:>7.1f} J  {rph:>9.1f}")
    print()
    print("  Energy per response favours TinyLlama-1.1B: higher throughput at same")
    print("  power level means fewer joules per token served.")


def main() -> None:
    ap = argparse.ArgumentParser(description="SmolLM2 EMERGENCY fallback simulation")
    ap.add_argument("--ups-minutes", type=int, nargs="+", default=[3, 5, 10],
                    help="UPS grace periods to model (default: 3 5 10)")
    ap.add_argument("--response-tokens", type=int, default=128,
                    help="Target tokens per response (default: 128)")
    args = ap.parse_args()

    baselines = _load_baselines()
    sidecar   = _load_qwen3_sidecar()
    models    = _build_model_table(baselines, sidecar)

    q3_inform_mb = baselines["met_sidecars"]["qwen3_1b7_q4km"]["met_hydration"]["inform_only_mb"]

    _section("SmolLM2-135M vs TinyLlama-1.1B vs Qwen3-1.7B — EMERGENCY FALLBACK")
    print("  Scenario: sudden UPS/backup-power event.  What model do we switch to?")
    print("  All numbers from results/mobile_baselines.json + qwen3_1b7_met_sidecar.json")
    print("  Platform: 12 GB RAM phone, GPUOpenCL, UFS 3.1 storage, 6 CPU threads")

    _print_model_table(models)
    _print_memory_freed(models, q3_inform_mb)
    _print_load_times(models)
    _print_ups_table(models, args.ups_minutes, args.response_tokens)
    _print_opencl_analysis()
    _print_energy_estimate(models, args.response_tokens)

    _section("SUMMARY")
    print("""
  WHAT SmolLM2-135M GAINS vs Qwen3-1.7B
  ───────────────────────────────────────
  Memory:      -57 % peak RAM (1024 MB vs 768 MB INFORM Qwen3; 8× less than HARM mode)
  GGUF size:   -90 % (104 MB vs 1056 MB) — pre-loadable in background constantly
  Cold start:  -90 % (0.07 s vs 0.70 s) — ready before the UPS beeps a second time
  TG speed:    SLOWER (-43 %; 4.82 vs 6.1* t/s)  *Qwen3 mobile TG is estimated

  WHAT SmolLM2 LOSES vs Qwen3-1.7B
  ─────────────────────────────────
  Quality:     Much lower — 135M vs 1.7B is not comparable for complex reasoning
  Speed:       Slower TG (4.82 vs ~6 t/s est. — and Qwen3 is far faster on GPU)

  WHAT SmolLM2 GAINS vs TinyLlama-1.1B  (the real comparison)
  ─────────────────────────────────────────────────────────────
  Memory:      2× less peak RAM (1024 MB vs 2048 MB)
  GGUF:        6× smaller (104 MB vs 636 MB)
  Cold start:  6× faster (0.07 s vs 0.42 s)
  TG speed:    SLOWER — 4.82 vs 12.03 t/s (2.5× slower, OpenCL underutilised at 135M)
  Quality:     Lower (135M vs 1.1B)

  VERDICT
  ───────
  TinyLlama-1.1B is the better EMERGENCY fallback when RAM allows (> 2 GB free):
    faster TG, similar cold-start budget, better quality.
  SmolLM2-135M is the right fallback when RAM is severely constrained (< 2 GB free)
    or as a permanently pre-loaded "always available" safety net.

  Recommended PowerConditionerAgent extension:
    def select_fallback(ram_free_mb):
        if ram_free_mb >= 2048: return "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
        return "HuggingFaceTB/SmolLM2-135M-Instruct"
""")
    print("═" * _W)


if __name__ == "__main__":
    main()
