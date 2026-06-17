"""Cognitive gating simulation — measure the effect of intent typing + semantic
routing on retrieval precision against a deliberately polluted corpus.

The Orivael RAG paper's central claim is "cognitive gating": background noise
(an unrelated personal dataset — dinner recipes, calendar entries) should not
contaminate a technical query's results.  This script measures whether the
gating layers we built actually deliver that isolation.

It builds a mixed corpus of two disjoint domains:
  - engineering  : weight quantization, model architecture, inference (the signal)
  - personal      : dinner recipes, grocery lists, calendar (the "dinner noise")

then runs the same queries three ways and reports contamination:

  1. FLAT BM25    — one index over everything, no gating  (the baseline)
  2. ROUTED       — SemanticRouter picks the domain, BM25 within it only
  3. ROUTED+INTENT— routing + intent_filter on the chunk content type

"Contamination" = fraction of top-k hits that came from the wrong domain.
Lower is better.  A perfectly gated system returns 0% contamination on a
single-domain query.

Usage:
    export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    python3 research/cognitive_gating_sim.py
    python3 research/cognitive_gating_sim.py --k 5 --json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

# Ensure repo root is importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axiom_domain_ingester import DomainIngester
from axiom_domain_pack import DomainPackManifest, DomainPackStore, build_pack
from axiom_research_retriever import LocalRetriever
from axiom_semantic_router import SemanticRouter


# ── synthetic corpus ────────────────────────────────────────────────────────
#
# Each entry is (domain, filename, text).  The engineering docs use dense
# technical vocabulary; the personal docs are everyday "dinner noise".  The
# overlap is deliberately adversarial — both talk about "reducing", "best",
# "how to", "time" — so flat BM25 has a real chance to confuse them.

_CORPUS: List[Tuple[str, str, str]] = [
    # ── engineering domain ──────────────────────────────────────────────────
    ("engineering", "srd_quant.txt",
     "Stochastic Residual Dithering reduces precision loss during weight "
     "quantization by distributing error variance across the residual. The "
     "technique mitigates quantization error when compressing model weights to "
     "4-bit. Best results occur at matched bits-per-weight against Q4_K_M."),
    ("engineering", "kv_cache.txt",
     "The key-value cache stores attention keys and values to avoid recomputing "
     "them on every decode step. Paged attention reduces memory fragmentation in "
     "the KV cache. Reducing cache precision to FP8 lowers VRAM footprint during "
     "long-context inference."),
    ("engineering", "lora_adapter.txt",
     "A LoRA adapter injects low-rank matrices into frozen base model weights to "
     "fine-tune on domain data without updating the full parameter set. The rank "
     "controls adapter capacity. Merging the adapter back into the base weights "
     "removes inference overhead."),
    ("engineering", "distillation.txt",
     "Knowledge distillation trains a small student model to match the output "
     "distribution of a larger teacher. The temperature parameter softens the "
     "teacher logits. Distillation reduces model size while preserving accuracy "
     "on the target task."),
    ("engineering", "sparsity.txt",
     "Structured sparsity prunes entire attention heads or feedforward channels "
     "to reduce compute. Magnitude pruning removes weights below a threshold. "
     "Sparse kernels accelerate inference only when the sparsity pattern aligns "
     "with hardware tiling."),

    # ── personal domain (the "dinner noise") ─────────────────────────────────
    ("personal", "roast_dinner.txt",
     "To roast the vegetables, preheat the oven to 200 degrees. Toss the carrots "
     "and potatoes in olive oil. Roast for 40 minutes until golden. Best served "
     "warm with a side of gravy. Reducing the sauce on the stove adds flavour."),
    ("personal", "grocery_list.txt",
     "Grocery list for the week: milk, eggs, flour, butter, two onions, fresh "
     "basil, parmesan, and a bottle of olive oil. Remember to buy more coffee. "
     "The best tomatoes are at the farmers market on Saturday morning."),
    ("personal", "calendar.txt",
     "Calendar for next week: dentist appointment on Tuesday at 3pm. Dinner with "
     "Sarah on Thursday. Pick up dry cleaning Friday. Reduce screen time in the "
     "evenings. Best time to call mum is Sunday afternoon."),
    ("personal", "pasta_recipe.txt",
     "For the pasta sauce, gently fry garlic in olive oil. Add crushed tomatoes "
     "and simmer to reduce for twenty minutes. Season with salt and basil. Best "
     "tossed with fresh tagliatelle. Serve dinner while hot."),
    ("personal", "workout.txt",
     "Morning workout routine: ten minutes stretching, then a thirty minute run. "
     "Reduce rest time between sets to raise intensity. Best to hydrate before "
     "and after. Track time and distance each day for progress."),
]

# Test queries.  Each is tagged with the domain it SHOULD retrieve from, so we
# can score contamination automatically.
_QUERIES: List[Tuple[str, str]] = [
    ("engineering", "How do I reduce error when quantizing model weights?"),
    ("engineering", "What lowers VRAM during long context inference?"),
    ("engineering", "How to fine-tune a model without updating all parameters?"),
    ("engineering", "best way to shrink a large model while keeping accuracy"),
    ("personal",    "how long do I roast the vegetables for dinner?"),
    ("personal",    "what is the best time to call and what appointments this week"),
    ("personal",    "how do I reduce the pasta sauce?"),
]


# ── result accounting ─────────────────────────────────────────────────────────

@dataclass
class StrategyResult:
    name: str
    contamination_rate: float = 0.0      # mean fraction of off-domain hits in top-k
    hit_rate: float = 0.0                # mean fraction of correct-domain hits in top-k
    per_query: List[dict] = field(default_factory=list)


def _domain_of(uri: str, uri_to_domain: Dict[str, str]) -> str:
    """Map a retrieved chunk URI back to its source domain via its sidecar/meta."""
    return uri_to_domain.get(uri, "unknown")


# ── simulation ─────────────────────────────────────────────────────────────────

class CognitiveGatingSim:
    def __init__(self, workdir: Path, *, k: int = 5) -> None:
        self.workdir = workdir
        self.k = k
        self.store = DomainPackStore(base_dir=workdir / "store")
        # uri (relative chunk path) -> source domain, for contamination scoring
        self._uri_domain: Dict[str, str] = {}
        # flat retriever over the whole mixed corpus
        self._flat: LocalRetriever | None = None
        self._flat_index = workdir / "flat_index"
        self.router: SemanticRouter | None = None

    # ── build phase ──────────────────────────────────────────────────────────

    def build(self) -> None:
        self._flat_index.mkdir(parents=True, exist_ok=True)
        src_dir = self.workdir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        # Ingest per-domain into separate pack indexes AND into one flat index.
        per_domain_index: Dict[str, Path] = {}
        for domain, fname, text in _CORPUS:
            idx = self.workdir / f"{domain}_index"
            per_domain_index.setdefault(domain, idx)

            # write the source file once
            src = src_dir / fname
            src.write_text(text, encoding="utf-8")

            # ingest into the domain-specific index (gets intent + sidecars)
            dom_ing = DomainIngester(domain=domain, index_dir=idx, session_id="sim")
            chunks = dom_ing.ingest_file(src)
            for c in chunks:
                self._uri_domain[f"{c.content_hash}.txt"] = domain

            # also ingest the SAME file into the flat mixed index
            flat_ing = DomainIngester(domain=domain, index_dir=self._flat_index,
                                      session_id="sim")
            flat_ing.ingest_file(src)

        # Package + install each domain as a pack so the router can see them.
        for domain, idx in per_domain_index.items():
            m = DomainPackManifest(
                name=f"{domain}-pack", title=domain.title(), description=domain,
                version="1.0.0", author="sim", license="Apache-2.0", domain=domain,
            )
            pack_dir = build_pack(manifest=m, index_dir=idx,
                                  output_dir=self.workdir / "packs")
            self.store.install(pack_dir)

        # flat retriever over everything
        self._flat = LocalRetriever(roots=[self._flat_index])
        self._flat.build()

        # router over the installed packs
        self.router = SemanticRouter(self.store)
        self.router.build_indexes()

    # ── retrievers per strategy ────────────────────────────────────────────────

    def _retriever_for_domain(self, domain: str) -> LocalRetriever:
        idx = self.workdir / f"{domain}_index"
        r = LocalRetriever(roots=[idx])
        r.build()
        return r

    def _route(self, query: str) -> str:
        """Return the top-routed domain for a query (or '' if undecided)."""
        assert self.router is not None
        packs = self.router.route(query, top_k=1)
        return packs[0].domain if packs else ""

    # ── run ────────────────────────────────────────────────────────────────────

    def run(self) -> List[StrategyResult]:
        assert self._flat is not None and self.router is not None
        flat = StrategyResult("FLAT BM25 (no gating)")
        routed = StrategyResult("ROUTED (semantic router)")
        routed_intent = StrategyResult("ROUTED + INTENT filter")

        for want_domain, query in _QUERIES:
            # 1. flat baseline — score the whole mixed corpus
            flat_hits = self._flat.retrieve(query, k=self.k)
            self._account(flat, want_domain, query, flat_hits, routed_domain="(none)")

            # 2. routed — pick a domain, search only that index
            picked = self._route(query)
            r = self._retriever_for_domain(picked) if picked else self._flat
            routed_hits = r.retrieve(query, k=self.k)
            self._account(routed, want_domain, query, routed_hits, routed_domain=picked)

            # 3. routed + intent filter — also gate by query's content type
            from axiom_semantic_router import _detect_query_intent
            q_intent = _detect_query_intent(query)
            ri_hits = r.retrieve(query, k=self.k, intent_filter=None)
            # only apply the intent filter when the query has a specific intent
            if q_intent != "general":
                filtered = r.retrieve(query, k=self.k, intent_filter=q_intent)
                if filtered:           # fall back to unfiltered if filter empties it
                    ri_hits = filtered
            self._account(routed_intent, want_domain, query, ri_hits,
                          routed_domain=picked, intent=q_intent)

        for res in (flat, routed, routed_intent):
            n = len(res.per_query)
            res.contamination_rate = sum(q["contamination"] for q in res.per_query) / n
            res.hit_rate = sum(q["hit_rate"] for q in res.per_query) / n
        return [flat, routed, routed_intent]

    def _account(self, res: StrategyResult, want_domain: str, query: str,
                 hits, *, routed_domain: str, intent: str = "") -> None:
        domains = [_domain_of(h.uri, self._uri_domain) for h in hits]
        n = len(domains) or 1
        wrong = sum(1 for d in domains if d != want_domain)
        right = sum(1 for d in domains if d == want_domain)
        res.per_query.append({
            "query": query,
            "want_domain": want_domain,
            "routed_to": routed_domain,
            "intent": intent,
            "hit_domains": domains,
            "contamination": wrong / n,
            "hit_rate": right / n,
        })


# ── reporting ──────────────────────────────────────────────────────────────────

def print_report(results: List[StrategyResult], k: int) -> None:
    print(f"\nCognitive Gating Simulation  (top-k = {k}, "
          f"{len(_QUERIES)} queries, {len(_CORPUS)} docs across 2 domains)\n")
    print(f"{'Strategy':<30}{'Contamination↓':>16}{'On-target↑':>14}")
    print("─" * 60)
    for r in results:
        print(f"{r.name:<30}{r.contamination_rate*100:>14.1f}%{r.hit_rate*100:>12.1f}%")

    print("\nPer-query detail (contamination = off-domain hits in top-k):\n")
    base = results[0]
    best = results[-1]
    for bq, gq in zip(base.per_query, best.per_query):
        print(f"  Q: {bq['query']}")
        print(f"     want={bq['want_domain']:<12} "
              f"flat={bq['contamination']*100:>5.0f}% contam   "
              f"gated[routed→{gq['routed_to']}, intent={gq['intent']}]="
              f"{gq['contamination']*100:>5.0f}% contam")
        print(f"     flat hits : {bq['hit_domains']}")
        print(f"     gated hits: {gq['hit_domains']}")
        print()


# ── within-domain intent scenario ──────────────────────────────────────────────
#
# Routing isolates *across* domains, but cannot help *within* one domain where
# every chunk shares the same vocabulary cluster.  This second corpus is all
# one domain (legal) with mixed content types — the intent filter is the only
# layer that can separate "what the rule IS" (definition) from "what the court
# DID" (ruling).  This is where intent typing earns its keep.

_LEGAL_CORPUS: List[Tuple[str, str]] = [
    ("def_negligence.txt",
     "Negligence is defined as the failure to exercise the standard of care that "
     "a reasonably prudent person would exercise in like circumstances. The "
     "doctrine refers to a breach of a duty owed to another party."),
    ("def_consideration.txt",
     "Consideration is defined as the bargained-for exchange of value that "
     "renders a promise enforceable. It refers to the benefit or detriment that "
     "each party gives to form a binding contract."),
    ("rule_palsgraf.txt",
     "The court held that the defendant railroad owed no duty to the plaintiff "
     "because the harm was not foreseeable. The court ruled that liability "
     "extends only to the orbit of foreseeable risk. Judgment for the defendant."),
    ("rule_carbolic.txt",
     "The court held that the advertisement constituted a unilateral offer that "
     "was accepted by performance. The court decided that the deposit showed "
     "intent to be bound. Verdict entered for the plaintiff."),
    ("proc_filing.txt",
     "To file a civil complaint, first draft the pleading stating the cause of "
     "action. Then, pay the filing fee at the clerk's office. Next, serve the "
     "defendant within the statutory period. Follow these steps in order."),
]

_LEGAL_QUERIES: List[Tuple[str, str]] = [
    # (target_intent, query)
    ("ruling",     "what did the court hold about foreseeable duty and liability"),
    ("definition", "what is the definition of negligence and duty of care"),
    ("procedure",  "how do I file a complaint step by step"),
]


def run_intent_scenario(workdir: Path, k: int) -> Tuple[float, float]:
    """All-legal corpus; compare BM25 alone vs BM25+intent_filter.

    Returns (precision_without_intent, precision_with_intent) — fraction of
    top-k hits whose chunk intent matches the query's target intent.
    """
    from axiom_semantic_router import _detect_query_intent

    idx = workdir / "legal_only_index"
    src = workdir / "legal_src"
    src.mkdir(parents=True, exist_ok=True)
    ing = DomainIngester(domain="legal", index_dir=idx, session_id="sim")
    uri_intent: Dict[str, str] = {}
    for fname, text in _LEGAL_CORPUS:
        p = src / fname
        p.write_text(text, encoding="utf-8")
        for c in ing.ingest_file(p):
            uri_intent[f"{c.content_hash}.txt"] = c.intent_type

    r = LocalRetriever(roots=[idx])
    r.build()

    plain_correct = plain_total = filt_correct = filt_total = 0
    detail: List[dict] = []
    for want_intent, query in _LEGAL_QUERIES:
        q_intent = _detect_query_intent(query)
        plain = r.retrieve(query, k=k)
        filt = r.retrieve(query, k=k, intent_filter=q_intent) or plain

        pi = [uri_intent.get(h.uri, "?") for h in plain]
        fi = [uri_intent.get(h.uri, "?") for h in filt]
        plain_correct += sum(1 for x in pi if x == want_intent)
        plain_total += len(pi) or 1
        filt_correct += sum(1 for x in fi if x == want_intent)
        filt_total += len(fi) or 1
        detail.append({"query": query, "want": want_intent, "detected": q_intent,
                       "plain": pi, "filtered": fi})

    print("\n── Within-domain intent scenario (all legal, no routing possible) ──\n")
    print(f"  {'Query':<52}{'plain':>16}{'+intent':>16}")
    print("  " + "─" * 82)
    for d in detail:
        pc = sum(1 for x in d["plain"] if x == d["want"]) / (len(d["plain"]) or 1)
        fc = sum(1 for x in d["filtered"] if x == d["want"]) / (len(d["filtered"]) or 1)
        print(f"  {d['query'][:50]:<52}{pc*100:>14.0f}% {fc*100:>14.0f}%")
        print(f"     want={d['want']:<12} detected={d['detected']:<12} "
              f"plain={d['plain']}  filtered={d['filtered']}")
    pw = plain_correct / plain_total
    fw = filt_correct / filt_total
    print(f"\n  Mean on-intent precision:  plain {pw*100:.0f}%   +intent {fw*100:.0f}%")
    return pw, fw


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cognitive gating retrieval simulation")
    ap.add_argument("--k", type=int, default=5, help="top-k results per query")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="axiom_gating_sim_") as d:
        sim = CognitiveGatingSim(Path(d), k=args.k)
        sim.build()
        results = sim.run()

        if args.json:
            pw, fw = run_intent_scenario(Path(d) / "intent", args.k)
            print(json.dumps({
                "cross_domain": [{
                    "strategy": r.name,
                    "contamination_rate": round(r.contamination_rate, 4),
                    "hit_rate": round(r.hit_rate, 4),
                    "per_query": r.per_query,
                } for r in results],
                "within_domain_intent": {
                    "precision_plain": round(pw, 4),
                    "precision_with_intent": round(fw, 4),
                },
            }, indent=2))
        else:
            print_report(results, args.k)
            run_intent_scenario(Path(d) / "intent", args.k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
