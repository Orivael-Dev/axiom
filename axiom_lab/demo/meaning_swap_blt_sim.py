"""Domain Palette × BLT Simulation

Demonstrates "Meaning Swapping" — one model, swappable domain routing vectors —
and its effect on BLT (Bloat / Latency / Tokens) cost.

Key insight: the preamble cache pre-loads the high-frequency domain docs into
Ollama's KV cache at startup. Queries only inject DELTA (novel) docs via the
Knowledge Cookie. This collapses BLT cost from "inject everything" to "inject
only the residual."

Modes compared
--------------
  Naive          — always inject all domain RAG fragments (current baseline)
  Palette+Cache  — inject only delta fragments not covered by the preamble

Run:
    python3 axiom_lab/demo/meaning_swap_blt_sim.py
"""
from __future__ import annotations

import os
import sys
import time
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

from axiom_blt_bench import BLTBenchmark, BLTConfig, BLTResult, _compute_derived

# ── Domain palette definitions ────────────────────────────────────────────────
# Each palette is the "routing vector" for one domain. Instead of loading a new
# model, InferenceOS swaps this dict — domain vocabulary, compliance gates,
# tool access, and preamble coverage fraction.

DOMAIN_PALETTES: Dict[str, dict] = {
    "legal": {
        "compliance_weight": 0.95,
        "citation_required": True,
        "tools":             ["gdpr_check", "contract_parser", "liability_scorer"],
        "system_tone":       "precise, cite-evidence, conservative",
        "fragment_total":    50,   # fragments naive approach injects
        "preamble_coverage": 0.90, # fraction already in Ollama KV from preamble
    },
    "healthcare": {
        "compliance_weight": 0.90,
        "citation_required": True,
        "tools":             ["drug_lookup", "icd10_mapper", "hipaa_gate"],
        "system_tone":       "clinical, evidence-based, cautious",
        "fragment_total":    25,
        "preamble_coverage": 0.80,
    },
    "finance": {
        "compliance_weight": 0.85,
        "citation_required": True,
        "tools":             ["risk_scorer", "sec_lookup", "pci_gate"],
        "system_tone":       "analytical, quantitative, conservative",
        "fragment_total":    20,
        "preamble_coverage": 0.75,
    },
    "general": {
        "compliance_weight": 0.30,
        "citation_required": False,
        "tools":             ["bm25_search"],
        "system_tone":       "helpful, concise",
        "fragment_total":    10,
        "preamble_coverage": 0.00,  # no preamble for general
    },
}


def _delta_fragments(palette: dict) -> int:
    """Fragment count after preamble filtering."""
    total    = palette["fragment_total"]
    coverage = palette["preamble_coverage"]
    return max(1, round(total * (1 - coverage)))


# ── Visual palette display ────────────────────────────────────────────────────

def _print_palette_swap() -> None:
    print("\n" + "═" * 68)
    print("  MEANING SWAPPING — one model, four domain palettes")
    print("═" * 68)
    print(f"  {'Domain':<12} {'Compliance':<12} {'Tools':<32} {'Coverage'}")
    print(f"  {'──────':<12} {'──────────':<12} {'─────':<32} {'────────'}")
    for domain, p in DOMAIN_PALETTES.items():
        tools_str = ", ".join(p["tools"])[:30]
        print(
            f"  {domain:<12} "
            f"{p['compliance_weight']:.0%}        "
            f"{tools_str:<32} "
            f"{p['preamble_coverage']:.0%} in KV"
        )
    print()
    print("  Model in VRAM: unchanged (gemma3-1b-srd4)")
    print("  Swapped per query: compliance_weight, tools, system_tone, preamble")
    print("═" * 68 + "\n")


# ── BLT comparison ───────────────────────────────────────────────────────────

@dataclass
class PaletteComparison:
    domain:             str
    naive_fragments:    int
    delta_fragments:    int
    naive_tokens:       int
    delta_tokens:       int
    naive_break_even:   float
    delta_break_even:   float
    naive_cost_1m:      float
    delta_cost_1m:      float
    preamble_coverage:  float


def _run_blt_for(fragment_count: int, avg_chars: int = 1200) -> BLTResult:
    cfg = BLTConfig(
        fragment_counts=[fragment_count],
        avg_fragment_chars=avg_chars,
        reps=3,
    )
    bench = BLTBenchmark(config=cfg)
    return bench.run()[0]


def _compare_domain(domain: str, palette: dict) -> PaletteComparison:
    naive_n = palette["fragment_total"]
    delta_n = _delta_fragments(palette)

    naive_r = _run_blt_for(naive_n)
    delta_r = _run_blt_for(delta_n)

    return PaletteComparison(
        domain=domain,
        naive_fragments=naive_n,
        delta_fragments=delta_n,
        naive_tokens=naive_r.tokens_added,
        delta_tokens=delta_r.tokens_added,
        naive_break_even=naive_r.break_even_sessions,
        delta_break_even=delta_r.break_even_sessions,
        naive_cost_1m=naive_r.net_cost_per_1m_calls,
        delta_cost_1m=delta_r.net_cost_per_1m_calls,
        preamble_coverage=palette["preamble_coverage"],
    )


def _print_blt_comparison(comparisons: List[PaletteComparison]) -> None:
    print("═" * 76)
    print("  BLT COMPARISON — Naive vs Palette+PreambleCache")
    print("═" * 76)

    # Header
    print(f"  {'Domain':<12} │ {'Naive frags':>11} │ {'Delta frags':>11} │ "
          f"{'Token Δ':>9} │ {'Cost Δ/1M':>10}")
    print(f"  {'──────':<12}─┼─{'───────────':>11}─┼─{'───────────':>11}─┼─"
          f"{'───────':>9}─┼─{'──────────':>10}")

    for c in comparisons:
        tok_reduction = 1.0 - (c.delta_tokens / max(c.naive_tokens, 1))
        cost_naive  = c.naive_cost_1m
        cost_delta  = c.delta_cost_1m
        cost_delta_sign = "+" if cost_delta >= 0 else "-"
        cost_naive_sign = "+" if cost_naive >= 0 else "-"

        print(
            f"  {c.domain:<12} │"
            f" {c.naive_fragments:>5} frags    │"
            f" {c.delta_fragments:>5} frags    │"
            f" {tok_reduction:>8.0%}  │"
            f"  {cost_naive_sign}${abs(cost_naive):>6,.0f} → {cost_delta_sign}${abs(cost_delta):>6,.0f}"
        )

    print()
    print("  Fragment counts (Naive): always inject all domain RAG docs")
    print("  Fragment counts (Delta): only inject docs NOT in the preamble KV cache")
    print()

    # Summary
    total_tok_savings = sum(c.naive_tokens - c.delta_tokens for c in comparisons)
    print(f"  Total token savings across all domains (per M calls):")
    print(f"    {total_tok_savings:,} tokens → "
          f"${total_tok_savings / 1000 * 0.002 * 1_000_000:,.0f} USD at $0.002/1k")
    print("═" * 76 + "\n")


def _print_routing_sim() -> None:
    """Show what a live palette swap looks like per query."""
    print("═" * 68)
    print("  LIVE ROUTING SIMULATION")
    print("═" * 68)

    queries = [
        ("What are GDPR Article 9 restrictions on health data?", "legal"),
        ("Symptoms of hantavirus pulmonary syndrome?",           "healthcare"),
        ("What is the P/E ratio for valuation?",                "finance"),
        ("How does BM25 rank documents?",                       "general"),
    ]

    for query, domain in queries:
        p = DOMAIN_PALETTES[domain]
        delta_n = _delta_fragments(p)
        print(f"\n  Query : {query[:58]}")
        print(f"  Domain: {domain}  (swapped palette — no model reload)")
        print(f"  Tools : {', '.join(p['tools'])}")
        print(f"  Preamble coverage: {p['preamble_coverage']:.0%} → inject "
              f"{delta_n}/{p['fragment_total']} fragments")
        time.sleep(0.05)  # visual pacing

    print("\n" + "═" * 68 + "\n")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    _print_palette_swap()
    _print_routing_sim()

    print("  Running BLT measurements (naive vs palette+cache) …\n")
    comparisons: List[PaletteComparison] = []
    for domain, palette in DOMAIN_PALETTES.items():
        sys.stdout.write(f"  [{domain}] … ")
        sys.stdout.flush()
        c = _compare_domain(domain, palette)
        comparisons.append(c)
        tok_saved = c.naive_tokens - c.delta_tokens
        print(f"{tok_saved:+,} tokens ({1 - c.delta_tokens/max(c.naive_tokens,1):.0%} reduction)")

    print()
    _print_blt_comparison(comparisons)


if __name__ == "__main__":
    main()
