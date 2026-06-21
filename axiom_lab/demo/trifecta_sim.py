"""Memory Trifecta — Full Three-Pillar Simulation

Demonstrates all three pillars back-to-back in a single run:

  Pillar 1 — Meaning Swapping     (domain palette hot-swap + BLT cost)
  Pillar 2 — Delta Memory Map     (session state advancing across turns)
  Pillar 3 — Multi-Resolution     (LOD 0/1/2 selection by intent + domain)

No Ollama required. All metrics are analytic / in-process measurements.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

from axiom_delta_memory import DeltaMemoryMap, DeltaMemoryStore, DeltaState
from axiom_multiresolution_memory import MemoryLOD, MultiResolutionMemory
from axiom_blt_bench import BLTBenchmark, BLTConfig

W = 72

def _bar(label: str, char: str = "═") -> None:
    print(f"\n{char * W}")
    print(f"  {label}")
    print(char * W)

def _sub(label: str) -> None:
    print(f"\n  ── {label}")

# ─────────────────────────────────────────────────────────────────────────────
# PILLAR 1 — MEANING SWAPPING
# ─────────────────────────────────────────────────────────────────────────────

PALETTES = {
    "legal":      {"frag_total": 50, "preamble_cov": 0.90,
                   "tools": ["gdpr_check", "contract_parser"],
                   "compliance": 0.95},
    "healthcare": {"frag_total": 25, "preamble_cov": 0.80,
                   "tools": ["drug_lookup", "hipaa_gate"],
                   "compliance": 0.90},
    "finance":    {"frag_total": 20, "preamble_cov": 0.75,
                   "tools": ["risk_scorer", "pci_gate"],
                   "compliance": 0.85},
    "general":    {"frag_total": 10, "preamble_cov": 0.00,
                   "tools": ["bm25_search"],
                   "compliance": 0.30},
}

def _delta_frags(p: dict) -> int:
    return max(1, round(p["frag_total"] * (1 - p["preamble_cov"])))

def run_pillar1() -> None:
    _bar("PILLAR 1 — MEANING SWAPPING  (one model · swappable domain palettes)")

    _sub("Palette inventory")
    print(f"  {'Domain':<12} {'Compliance':>10} {'KV coverage':>12} {'Tools'}")
    print(f"  {'──────':<12} {'──────────':>10} {'───────────':>12} {'─────'}")
    for domain, p in PALETTES.items():
        tools = ", ".join(p["tools"])
        print(f"  {domain:<12} {p['compliance']:>9.0%}  {p['preamble_cov']:>11.0%}  {tools}")

    print(f"\n  Model in VRAM : gemma3-1b-srd4  (unchanged across all domains)")
    print(f"  Swapped/query : compliance_weight · tools · system_tone · preamble_ptr")

    _sub("BLT cost  —  Naive injection vs Preamble-filtered delta")
    print(f"  {'Domain':<12} {'Naive frags':>11}  {'Delta frags':>11}  {'Token Δ':>8}  {'Cost Δ/1M':>10}")
    print(f"  {'──────':<12} {'───────────':>11}  {'───────────':>11}  {'───────':>8}  {'──────────':>10}")

    total_tok_savings = 0
    for domain, p in PALETTES.items():
        naive_n = p["frag_total"]
        delta_n = _delta_frags(p)
        cfg_n = BLTConfig(fragment_counts=[naive_n], reps=1)
        cfg_d = BLTConfig(fragment_counts=[delta_n],  reps=1)
        r_naive = BLTBenchmark(config=cfg_n).run()[0]
        r_delta = BLTBenchmark(config=cfg_d).run()[0]
        tok_saved = r_naive.tokens_added - r_delta.tokens_added
        total_tok_savings += tok_saved
        pct = tok_saved / max(r_naive.tokens_added, 1)
        cost_n_sign = "+" if r_naive.net_cost_per_1m_calls >= 0 else "-"
        cost_d_sign = "+" if r_delta.net_cost_per_1m_calls >= 0 else "-"
        print(
            f"  {domain:<12}"
            f" {naive_n:>8} frags "
            f" {delta_n:>8} frags "
            f" {pct:>7.0%} "
            f"  {cost_n_sign}${abs(r_naive.net_cost_per_1m_calls):>5,.0f}"
            f" → {cost_d_sign}${abs(r_delta.net_cost_per_1m_calls):>5,.0f}"
        )

    usd_saved = total_tok_savings / 1000 * 0.002 * 1_000_000
    print(f"\n  Total token savings : {total_tok_savings:,} toks/1M calls  "
          f"→ ${usd_saved:,.0f} USD at $0.002/1k")


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR 2 — DELTA MEMORY MAP
# ─────────────────────────────────────────────────────────────────────────────

TURNS = [
    # (query, simulated_output)
    ("What is GDPR Article 9?",
     "Article 9 restricts processing of special categories of personal data."),
    ("Is biometric data covered?",
     "Objective: Assess GDPR Article 9 scope for biometric data. Yes, biometric data is covered."),
    ("Are there exceptions?",
     "Resolved: biometric data confirmed covered under Art 9. There are narrow exceptions in Art 9(2)."),
    ("Can we process it for employment?",
     "Objective: Evaluate Art 9 exceptions for employment context. Employment exception exists under Art 9(2)(b)."),
    ("Summarise what we have established.",
     "Resolved: employment exception confirmed. Done: GDPR Art 9 biometric scope fully mapped."),
]

def run_pillar2() -> None:
    _bar("PILLAR 2 — DELTA MEMORY MAP  (O(1) session state · dirty-rect updates)")

    dm  = DeltaMemoryMap()

    with tempfile.TemporaryDirectory() as tmp:
        store = DeltaMemoryStore(path=Path(tmp) / "delta.jsonl")
        state = DeltaState(session_id="sim-legal", domain="legal")

        # Show what standard context growth looks like vs O(1) delta
        std_chars_running = 0

        print(f"\n  {'Turn':>4}  {'Query (truncated)':36}  "
              f"{'Δ fields':>9}  {'Ctx tokens':>10}  {'Std tokens':>10}")
        print(f"  {'────':>4}  {'──────────────────────────────────────':36}  "
              f"{'────────':>9}  {'──────────':>10}  {'──────────':>10}")

        for i, (query, output) in enumerate(TURNS, 1):
            t0 = time.perf_counter()
            dirty = dm.extract_delta(output, query, state)
            state = dm.apply_delta(state, **dirty)
            store.save("sim-legal", state)
            elapsed = (time.perf_counter() - t0) * 1000

            ctx_str   = dm.to_context_string(state)
            ctx_toks  = len(ctx_str) // 4

            # Standard: cumulative history (all turns concatenated)
            std_chars_running += len(query) + len(output)
            std_toks = std_chars_running // 4

            delta_fields = [k for k in dirty if k not in ("turn_count", "last_updated")]
            field_str    = ",".join(delta_fields)[:9] or "turn"

            print(
                f"  {i:>4}  {query[:36]:<36}  "
                f"{field_str:>9}  "
                f"{ctx_toks:>8} tok  "
                f"{std_toks:>8} tok"
            )

        print(f"\n  Delta state at turn {len(TURNS)}:")
        ctx = dm.to_context_string(state)
        parsed = json.loads(ctx)
        for k, v in parsed.items():
            val = v if not isinstance(v, list) else f"[{len(v)} items]"
            print(f"    {k}: {val}")

        final_ctx_toks = len(ctx) // 4
        print(f"\n  Context tokens  —  Delta: {final_ctx_toks}  vs  Standard: {std_chars_running // 4}")
        reduction = 1.0 - final_ctx_toks / max(std_chars_running // 4, 1)
        print(f"  Context reduction: {reduction:.0%}  ({final_ctx_toks} vs {std_chars_running // 4} toks)")


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR 3 — MULTI-RESOLUTION MEMORY
# ─────────────────────────────────────────────────────────────────────────────

MR_SCENARIOS = [
    # (query, intent_class, domain, why_chosen)
    ("Route this quickly",            "UNCERTAIN", None,      "no reliable context → LOD 0 only"),
    ("What is GDPR Art 9?",           "INFORM",    "general", "standard task → LOD 1 summary"),
    ("Analyse NDA clause 9 liability","INFORM",    "legal",   "compliance domain → LOD 2 full JSON"),
    ("Check SEC 10-K filing risk",    "CLARIFY",   "finance", "compliance domain → LOD 2 full JSON"),
    ("Blocked harmful query",         "HARM",      None,      "blocked → LOD 0 (minimal overhead)"),
]

def run_pillar3() -> None:
    _bar("PILLAR 3 — MULTI-RESOLUTION MEMORY  (LOD 0 / 1 / 2 by intent + domain)")

    mr    = MultiResolutionMemory()
    state = DeltaState(
        session_id        = "sim-multi",
        current_objective = "Analyse GDPR Art 9 scope for biometric data",
        active_constraints= ("cite Article numbers", "HIPAA compatible"),
        unresolved_questions = ("Are there employment exceptions?",),
        domain            = "legal",
        turn_count        = 3,
    )

    print(f"\n  Session state injected:")
    print(f"    objective   : {state.current_objective}")
    print(f"    constraints : {state.active_constraints}")
    print(f"    open        : {state.unresolved_questions}")

    _sub("LOD selection per query")
    print(f"  {'Query (truncated)':38}  {'Intent':10}  {'Domain':8}  {'LOD':>4}  {'Tokens':>7}")
    print(f"  {'──────────────────────────────────────':38}  {'──────':10}  {'──────':8}  {'───':>4}  {'──────':>7}")

    for query, intent, domain, note in MR_SCENARIOS:
        view = mr.view(state, intent, domain)
        lod_name = f"LOD{int(view.lod)}"
        print(
            f"  {query[:38]:<38}  {intent:<10}  {domain or 'general':<8}"
            f"  {lod_name:>4}  {view.token_estimate:>5} tok"
        )

    _sub("LOD content examples")
    lod0 = mr.to_lod0(state, "legal")
    lod1 = mr.to_lod1(state)
    lod2 = mr.to_lod2(state)

    print(f"\n  LOD 0 — token pointer  ({lod0.token_estimate} tok):")
    print(f"    {lod0.content}")

    print(f"\n  LOD 1 — text summary   ({lod1.token_estimate} tok):")
    for line in lod1.content.split(". "):
        if line.strip():
            print(f"    {line.strip()}.")

    print(f"\n  LOD 2 — full JSON      ({lod2.token_estimate} tok, first 200 chars):")
    print(f"    {lod2.content[:200]}…")

    _sub("LOD overhead vs always-injecting LOD 2")
    lod2_toks = lod2.token_estimate
    for intent, domain, label in [
        ("UNCERTAIN", None,    "Routing query (LOD 0)"),
        ("INFORM",    None,    "Standard task (LOD 1)"),
        ("INFORM",    "legal", "Legal code gen (LOD 2)"),
    ]:
        view = mr.view(state, intent, domain)
        saving = lod2_toks - view.token_estimate
        pct    = saving / max(lod2_toks, 1)
        print(f"  {label:<25} → LOD{int(view.lod)}  {view.token_estimate:>4} tok  "
              f"(saves {saving:>3} tok vs LOD 2  = {pct:.0%} overhead reduction)")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * W)
    print("  THE MEMORY TRIFECTA — Full Simulation")
    print("  90s Hardware Survival Techniques × Axiom Inference OS")
    print("=" * W)

    run_pillar1()
    run_pillar2()
    run_pillar3()

    _bar("SUMMARY", char="═")
    print("""
  Pillar 1  Meaning Swapping    One model in VRAM · palettes hot-swapped per query
                                  Preamble cache collapses BLT cost 75–90% for structured domains

  Pillar 2  Delta Memory Map    O(1) session context regardless of conversation length
                                  Only dirty registers written per turn · full history discarded

  Pillar 3  Multi-Resolution    LOD 0 → routing (1 token) · LOD 1 → task (~60 tok)
                                  LOD 2 → compliance code gen · auto-selected from intent+domain
""")


if __name__ == "__main__":
    main()
