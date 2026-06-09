"""Power Conditioner Agent — runnable demonstration.

Simulates a session where battery drains from 85 % → 35 % → 12 %,
then a sudden UPS/backup-power event fires.  Shows how the inference
profile, α parameter, context window, and input compression adapt at
each stage.

Also prints a cross-model RAM budget table comparing Qwen3-1.7B and
TinyLlama-1.1B across all five profiles.

Usage:
    python3 research/simulation/power_conditioner_sim.py
    python3 research/simulation/power_conditioner_sim.py --quiet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import os
os.environ.setdefault("AXIOM_MASTER_KEY", "0" * 64)   # demo key if not set

from axiom_agent_fabric.power_conditioner import (
    PowerConditionerAgent, PowerState, PowerProfile,
    PROFILE_CONFIGS, RAM_BUDGET_MB,
)

_W = 78


def _section(title: str) -> None:
    print(f"\n{'═' * _W}")
    print(f"  {title}")
    print("═" * _W)


def _bar(value: int, maximum: int, width: int = 20) -> str:
    filled = int(value / maximum * width) if maximum > 0 else 0
    return f"[{'█' * filled}{'░' * (width - filled)}] {value:>4}"


# ── Demo scenarios ─────────────────────────────────────────────────────────────

_SCENARIOS = [
    {
        "label": "Normal use — battery comfortable",
        "state": PowerState(battery_pct=85, is_charging=False, is_backup=False,
                            thermal_ok=True, source="mock"),
        "text": (
            "Explain the history of Byzantine architecture and its influence on "
            "Eastern European church design, covering the period from 330 AD through "
            "the fall of Constantinople in 1453.  Include details on the Hagia Sophia, "
            "dome construction techniques, mosaic programmes, and the transmission of "
            "these forms through Bulgarian, Serbian, and Russian church building until "
            "the 17th century.  Also discuss how Ottoman architects absorbed and "
            "transformed Byzantine elements after the conquest."
        ),
        "query": "Byzantine architecture Eastern Europe",
    },
    {
        "label": "Battery dipping — moderation kicks in",
        "state": PowerState(battery_pct=52, is_charging=False, is_backup=False,
                            thermal_ok=True, source="mock"),
        "text": (
            "You are an AI assistant helping with quarterly financial reporting.  "
            "The company operates in three segments: retail (42 % of revenue), "
            "wholesale (35 %), and licensing (23 %).  Q2 revenue was $142M, up 8 % "
            "YoY.  EBITDA margin improved 120 bps to 18.4 %.  Free cash flow was "
            "$23M, up from $17M in Q2 last year.  Net debt stands at $88M, leverage "
            "ratio 1.6×.  Please draft the earnings call script opening paragraph, "
            "the financial highlights section, and suggested analyst Q&A talking "
            "points covering revenue mix, margin trajectory, and capital allocation."
        ),
        "query": "quarterly financial report earnings",
    },
    {
        "label": "Low battery — power-save mode",
        "state": PowerState(battery_pct=31, is_charging=False, is_backup=False,
                            thermal_ok=True, source="mock"),
        "text": (
            "Summarise the following research paper abstract and key findings, then "
            "identify the three most important open questions the paper leaves "
            "unanswered.  The paper studies the effect of temperature on lithium-ion "
            "battery degradation in electric vehicles under fast-charging conditions.  "
            "It shows that charging at 10 °C increases capacity fade by 23 % per 500 "
            "cycles versus charging at 25 °C.  The authors attribute this to lithium "
            "plating on the anode surface.  They propose a thermal pre-conditioning "
            "protocol that reduces fade to 9 % at 10 °C by heating the pack to 20 °C "
            "before fast-charge initiation.  The study uses a fleet of 40 vehicles "
            "over 18 months.  Limitations include single-chemistry scope and no "
            "highway driving cycles."
        ),
        "query": "battery degradation temperature fast charging",
    },
    {
        "label": "Critical battery — short context only",
        "state": PowerState(battery_pct=8, is_charging=False, is_backup=False,
                            thermal_ok=True, source="mock"),
        "text": (
            "What is the fastest route from London Paddington to Oxford, and what "
            "time does the next Great Western Railway service depart?  I also need "
            "to know whether there are any planned engineering works this weekend "
            "affecting this route, and if so what the rail replacement bus schedule "
            "looks like and where the buses depart from."
        ),
        "query": "London Paddington Oxford train",
    },
    {
        "label": "Power outage — UPS/backup power detected  ⚡",
        "state": PowerState(battery_pct=72, is_charging=False, is_backup=True,
                            thermal_ok=True, source="linux_sys"),
        "text": (
            "Process the attached patient intake form and extract the chief complaint, "
            "current medications, allergies, and vital signs.  Then cross-reference "
            "the medication list against the known interactions database and flag any "
            "high-priority interactions.  Finally, generate a structured SOAP note "
            "template pre-filled with the extracted information for the attending "
            "physician to review."
        ),
        "query": "patient intake medications allergies",
    },
]


def _print_scenario(
    idx:    int,
    label:  str,
    result,
    quiet:  bool,
) -> None:
    state  = result.power_state
    config = result.config
    prof   = result.profile

    batt_bar = _bar(state.battery_pct, 100, width=15)
    backup_tag = "  ⚡ UPS/BACKUP" if state.is_backup else ""
    print(f"\n{'─' * _W}")
    print(f"  [{idx}] {label}")
    print(f"{'─' * _W}")
    print(f"  Battery  {batt_bar}  charging={state.is_charging}  "
          f"thermal_ok={state.thermal_ok}  source={state.source}{backup_tag}")
    print(f"  Profile  {prof.value:<10}  α={config.alpha:.1f}  "
          f"ctx={config.context_window:<5}  MET={config.met_policy}")
    print(f"  Reason:  {config.reason}")

    if not quiet:
        print(f"\n  Input ({result.original_tokens} tokens, first 80 chars):")
        print(f"    \"{result.original_text[:80].replace(chr(10), ' ')}…\"")

    if result.compressed:
        saved_pct = 100 * (1 - result.conditioned_tokens / result.original_tokens)
        print(f"\n  Compression:  {result.original_tokens} → "
              f"{result.conditioned_tokens} tokens  "
              f"(-{saved_pct:.0f} %)")
        if not quiet:
            print(f"  Compressed (first 80 chars):")
            print(f"    \"{result.conditioned_text[:80].replace(chr(10), ' ')}…\"")
    else:
        print(f"\n  Compression:  none (input ≤ budget)")

    if config.model_fallback:
        print(f"\n  ⚠  Fallback model: {config.model_fallback}")

    # RAM budget
    q3_mb = RAM_BUDGET_MB[prof]["qwen3_1b7"]
    tl_mb = RAM_BUDGET_MB[prof]["tinyllama_1b"]
    print(f"\n  RAM budget:  Qwen3-1.7B {q3_mb} MB  |  TinyLlama-1.1B {tl_mb} MB")


def _print_budget_table() -> None:
    _section("CROSS-MODEL RAM BUDGET BY PROFILE")
    print(f"\n  {'Profile':<10}  {'α':>4}  {'Context':>7}  {'MET':>7}  "
          f"{'Qwen3-1.7B':>11}  {'TinyLlama-1B':>13}  {'vs FULL (Q)':>11}")
    print(f"  {'─'*10}  {'─'*4}  {'─'*7}  {'─'*7}  {'─'*11}  {'─'*13}  {'─'*11}")
    full_q = RAM_BUDGET_MB[PowerProfile.FULL]["qwen3_1b7"]
    for prof in PowerProfile:
        cfg   = PROFILE_CONFIGS[prof]
        q_mb  = RAM_BUDGET_MB[prof]["qwen3_1b7"]
        tl_mb = RAM_BUDGET_MB[prof]["tinyllama_1b"]
        diff  = 100 * (q_mb - full_q) / full_q
        diff_s = f"{diff:+.0f} %" if diff else "baseline"
        print(f"  {prof.value:<10}  {cfg.alpha:>4.1f}  {cfg.context_window:>7}  "
              f"{cfg.met_policy:>7}  {q_mb:>9} MB  {tl_mb:>11} MB  {diff_s:>11}")

    print(f"\n  Notes:")
    print(f"    Qwen3-1.7B embedding is 593.5 MB F16 (large vocab — always pinned).")
    print(f"    EMERGENCY fallback to SmolLM2-135M: ~25 MB floor.")
    print(f"    >1B models (e.g. Mistral-7B SRD): CONSERVE α=0 → 4.5 bpw → ~4 GB")
    print(f"    vs FULL α=1 → 13 bpw → ~11 GB.  α is the primary >1B power lever.")


def _print_large_model_note() -> None:
    _section("LARGE MODEL (>1B) POWER NOTES")
    rows = [
        ("Mistral-7B  α=1.0", "13.0", "~11 GB", "needs 12+ GB GPU"),
        ("Mistral-7B  α=0.5", "13.0", "~11 GB", "partial residual — same storage"),
        ("Mistral-7B  α=0.0", " 4.5", " ~4 GB", "fits 6 GB VRAM with headroom"),
        ("Qwen3-1.7B  α=1.0", "13.0", " ~2 GB", "fits 6 GB (small model)"),
        ("Qwen3-1.7B  α=0.0", " 4.5", "~1.1 GB","fits mobile 4 GB RAM"),
    ]
    print(f"\n  {'Config':<22}  {'bpw':>5}  {'Weights':>8}  Note")
    print(f"  {'─'*22}  {'─'*5}  {'─'*8}  {'─'*30}")
    for name, bpw, mem, note in rows:
        print(f"  {name:<22}  {bpw:>5}  {mem:>8}  {note}")
    print(f"\n  Context window also matters for KV cache:")
    print(f"    Mistral-7B (32 layers, 8 KV heads, head_dim=128):")
    print(f"    KV cache ≈ 2 × 32 × (8×128) × 2 bytes × ctx = 128 KB/token")
    print(f"    ctx 4096 → ~512 MB KV.  ctx 512 → ~64 MB KV.  (~8× saving)")
    print(f"    Combined: CONSERVE on Mistral-7B SRD = 4 GB weights + 64 MB KV")
    print(f"    vs FULL = 11 GB weights + 512 MB KV — fits vs doesn't fit on 6 GB.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Power Conditioner Agent Demo")
    ap.add_argument("--quiet", action="store_true", help="Suppress input/output text")
    args = ap.parse_args()

    agent = PowerConditionerAgent()

    _section("AXIOM POWER CONDITIONER AGENT")
    print(f"  Scenarios : {len(_SCENARIOS)} (battery drain + UPS event)")
    print(f"  Sensor    : mock states (no live hardware needed for demo)")
    print(f"  Models    : Qwen3-1.7B Q4_K_M + TinyLlama-1.1B Q4_K_M (reference)")

    _section("POWER SCENARIOS")

    for idx, scenario in enumerate(_SCENARIOS, start=1):
        result = agent.condition(
            text  = scenario["text"],
            query = scenario["query"],
            state = scenario["state"],
        )
        _print_scenario(idx, scenario["label"], result, args.quiet)

    _print_budget_table()
    _print_large_model_note()

    _section("SENSOR PLATFORMS")
    print(f"  linux_sys  : /sys/class/power_supply/* — laptop, Pi, desktop+UPS")
    print(f"  termux     : termux-battery-status JSON — Android / Termux")
    print(f"  jetson     : /etc/nv_tegra_release detect — always FULL (mains)")
    print(f"  mock_full  : fallback — safe on GPU servers, cloud, desktop")
    print(f"\n  UPS detection (linux_sys):")
    print(f"    /sys/class/power_supply/UPS*/status == 'Discharging'")
    print(f"    OR Mains/AC online == 0 with no battery present")
    print(f"    Covers: APC, Eaton, CyberPower via kernel UPS driver")
    print(f"    apcupsd events: /var/run/apcupsd.status (if daemon running)")
    print("═" * _W)


if __name__ == "__main__":
    main()
