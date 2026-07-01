"""Cosmos BM25 Experiment — flat vs. layered retrieval benchmark.

Compares three retrieval strategies on a synthetic tagged corpus:

  Flat BM25      — single retrieve() call, no intent_filter
  Layered        — 3-pass galaxy/planet/star (sequential)
  Layered+Warmup — 3-pass with star query pre-fired in background thread

Outputs a latency + quality table to stdout.  No external deps beyond
the repo itself.

Usage:
    python3 axiom_lab/benchmarks/cosmos_bm25_experiment.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axiom_research_retriever import LocalRetriever
from axiom_semantic_cosmos import (
    CosmosLayeredRetriever,
    cosmos_tag_doc,
    write_cosmos_meta,
)

# ── synthetic corpus ─────────────────────────────────────────────────

# (level, galaxy_tag, content)
_CORPUS: list[tuple[str, str, str]] = [
    # ── GALAXY docs (broad, diverse vocabulary) ──────────────────────
    (
        "galaxy", "medical",
        "Medical science encompasses epidemiology pathology pharmacology immunology "
        "genetics biochemistry physiology anatomy neuroscience cardiology oncology "
        "radiology surgery dermatology endocrinology gastroenterology nephrology "
        "pulmonology infectious disease haematology rheumatology paediatrics "
        "obstetrics gynaecology psychiatry ophthalmology otolaryngology urology "
        "orthopaedics anaesthesiology emergency medicine occupational health "
        "preventive medicine palliative care biostatistics clinical trial design "
        "systematic review meta-analysis evidence-based practice public health "
        "epidemiological surveillance population screening health economics "
        "regulatory compliance drug safety pharmacovigilance medical ethics "
        "informed consent patient autonomy beneficence non-maleficence justice "
        "quality improvement patient safety adverse event reporting root cause "
        "healthcare technology assessment digital health telemedicine wearables",
    ),
    (
        "galaxy", "medical",
        "Healthcare administration policy governance reimbursement insurance "
        "hospital management nursing workforce pharmacy dispensing laboratory "
        "diagnostics imaging pathology microbiology serology virology bacteriology "
        "parasitology mycology public health nutrition dietetics rehabilitation "
        "physiotherapy occupational therapy speech language therapy psychology "
        "social work community health environmental health global health "
        "international health tropical medicine travel medicine refugee health "
        "pandemic preparedness biosecurity infection prevention control hand "
        "hygiene personal protective equipment sterilisation decontamination "
        "waste management clinical governance credentialing accreditation ",
    ),
    (
        "galaxy", "finance",
        "Financial markets encompass equity fixed income derivatives foreign "
        "exchange commodities real estate private equity venture capital hedge "
        "funds mutual funds exchange-traded funds pension funds insurance "
        "asset management wealth management investment banking retail banking "
        "corporate banking central banking monetary policy fiscal policy "
        "macroeconomics microeconomics econometrics quantitative finance "
        "risk management credit risk market risk operational risk liquidity "
        "risk systemic risk Basel III capital adequacy stress testing "
        "financial regulation compliance anti-money laundering know-your-customer "
        "financial crime fraud detection forensic accounting audit taxation "
        "valuation mergers acquisitions corporate restructuring bankruptcy "
        "Islamic finance sustainable finance ESG impact investing fintech "
        "blockchain cryptocurrency decentralised finance payment systems",
    ),
    (
        "galaxy", "ai-governance",
        "Artificial intelligence governance encompasses model safety alignment "
        "interpretability explainability fairness bias mitigation robustness "
        "adversarial attacks data privacy differential privacy federated "
        "learning responsible AI ethics AI regulation EU AI Act algorithmic "
        "accountability transparency audit trail constitutional AI RLHF "
        "reinforcement learning human feedback red teaming model evaluation "
        "benchmark contamination hallucination detection grounding retrieval "
        "augmented generation vector embeddings semantic search knowledge "
        "graphs ontology engineering natural language processing computer "
        "vision speech recognition multimodal learning transfer learning "
        "fine-tuning quantisation pruning distillation edge inference "
        "model serving latency throughput cost optimisation MLOps",
    ),

    # ── PLANET docs (dense concept clusters, medium length) ───────────
    (
        "planet", "medical",
        "Andes hantavirus is a rodent-borne zoonotic pathogen transmitted through "
        "contact with infected rodent excreta. The primary host is Oligoryzomys "
        "longicaudatus, the long-tailed pygmy rice rat. Incubation period ranges "
        "from one to five weeks after exposure. Clinical presentation progresses "
        "from a prodromal fever phase to cardiopulmonary syndrome with acute "
        "respiratory distress, hypoxia and haemodynamic collapse. Case fatality "
        "rate is approximately thirty-five to forty percent. Diagnosis relies on "
        "RT-PCR serology and immunohistochemistry. No licensed antiviral treatment "
        "exists. Management is supportive with intensive care monitoring. Person-to-"
        "person transmission has been documented in Argentina, distinguishing Andes "
        "from North American hantavirus species. Rodent control and avoidance of "
        "endemic areas are the primary prevention strategies.",
    ),
    (
        "planet", "medical",
        "Sepsis is a life-threatening organ dysfunction caused by dysregulated host "
        "response to infection. Septic shock is a subset where profound circulatory "
        "and cellular metabolic abnormalities increase mortality risk. The Surviving "
        "Sepsis Campaign recommends hour-one bundle: blood cultures before "
        "antibiotics, broad-spectrum antibiotics within one hour, thirty mL per kg "
        "crystalloid for hypotension, vasopressors for mean arterial pressure below "
        "sixty-five mmHg, and lactate measurement. Sequential organ failure "
        "assessment SOFA score quantifies dysfunction across six organ systems. "
        "Procalcitonin guides antibiotic de-escalation. Source control remains "
        "critical. Immunosuppression corticosteroids are reserved for refractory "
        "shock. Glucose control targeting one-forty to one-eighty mg per dL reduces "
        "hyperglycaemia harm. Early goal-directed therapy shifted toward bundles.",
    ),
    (
        "planet", "finance",
        "Credit default swap CDS is an over-the-counter derivative where the "
        "protection buyer pays periodic premiums to the seller in exchange for "
        "compensation upon a defined credit event such as default, restructuring "
        "or bankruptcy of the reference entity. CDS spread represents the annual "
        "cost of protection in basis points. During the 2008 financial crisis "
        "CDS on mortgage-backed securities amplified systemic risk because sellers "
        "lacked capital to cover concentrated exposures. Dodd-Frank Act mandated "
        "central clearing for standardised CDS through central counterparties. "
        "ISDA master agreement governs documentation. CDS can be used for hedging "
        "bond exposure or for speculative positioning on credit quality. Basis "
        "between CDS spread and bond spread reflects funding liquidity and "
        "counterparty risk. Sovereign CDS markets price government default risk.",
    ),
    (
        "planet", "ai-governance",
        "Constitutional AI is a training methodology developed to align language "
        "models with a set of principles called the constitution. During supervised "
        "learning the model critiques and revises its own outputs to conform to "
        "constitutional principles. During reinforcement learning a preference model "
        "trained on constitutional comparisons replaces human raters for harmful "
        "content. Key principles include harmlessness helpfulness honesty avoiding "
        "deception non-manipulation and respect for autonomy. Constitutional "
        "distance measures divergence of model behaviour from constitutional "
        "boundaries. CANNOT_MUTATE constraints enforce non-negotiable limits that "
        "cannot be overridden at inference time. Latent traces record constitutional "
        "distance at each reasoning stage preflight mid-chain and final synthesis.",
    ),
    (
        "planet", "medical",
        "Myocardial infarction occurs when coronary artery occlusion causes "
        "ischaemic necrosis of myocardial tissue. STEMI involves full-thickness "
        "infarction with ST-elevation on ECG requiring urgent reperfusion. Primary "
        "percutaneous coronary intervention is preferred within ninety minutes of "
        "first medical contact. Thrombolysis is alternative when PCI unavailable. "
        "Dual antiplatelet therapy with aspirin and P2Y12 inhibitor reduces "
        "re-occlusion risk. Troponin I and T are sensitive biomarkers of myocardial "
        "injury with peak at twelve to twenty-four hours. Left ventricular ejection "
        "fraction guides prognosis and therapy. Beta-blockers ACE inhibitors "
        "statins and aldosterone antagonists reduce mortality post-MI. Cardiac "
        "rehabilitation improves functional capacity and reduces re-admission.",
    ),
    (
        "planet", "finance",
        "Black-Scholes-Merton model prices European options assuming log-normal "
        "distribution of asset returns constant volatility no arbitrage frictionless "
        "markets and risk-free interest rate. Formula: C = S*N(d1) - K*e^(-rT)*N(d2) "
        "where d1 = (ln(S/K) + (r + sigma^2/2)*T) / (sigma*sqrt(T)) and d2 = d1 - "
        "sigma*sqrt(T). Implied volatility is the sigma that equates model price to "
        "market price. Volatility smile shows implied vol varies with strike violating "
        "BSM assumptions. Greeks: delta gamma theta vega rho measure option "
        "sensitivity to underlying price time volatility and interest rates. "
        "Local volatility stochastic volatility and jump-diffusion models extend BSM.",
    ),

    # ── STAR docs (specific facts, short, focused) ────────────────────
    (
        "star", "medical",
        "Andes hantavirus case fatality rate: approximately 35-40 percent.",
    ),
    (
        "star", "medical",
        "Sepsis hour-one bundle: blood cultures, antibiotics within 1 hour, "
        "30 mL/kg crystalloid, vasopressors for MAP < 65 mmHg, lactate.",
    ),
    (
        "star", "medical",
        "Troponin I peaks at 12-24 hours post myocardial infarction onset.",
    ),
    (
        "star", "medical",
        "Primary PCI target door-to-balloon time: 90 minutes for STEMI.",
    ),
    (
        "star", "medical",
        "Hantavirus primary host: Oligoryzomys longicaudatus (long-tailed pygmy rice rat).",
    ),
    (
        "star", "finance",
        "CDS spread: annual cost of credit protection expressed in basis points.",
    ),
    (
        "star", "finance",
        "Dodd-Frank Act 2010: mandated central clearing for standardised CDS contracts.",
    ),
    (
        "star", "finance",
        "Black-Scholes formula: C = S*N(d1) - K*e^(-rT)*N(d2) for European call options.",
    ),
    (
        "star", "finance",
        "Option delta: rate of change of option price with respect to underlying asset price.",
    ),
    (
        "star", "ai-governance",
        "Constitutional distance DRIFT_THRESHOLD = 0.10 in axiom_latent_v2.",
    ),
    (
        "star", "ai-governance",
        "CANNOT_MUTATE: constants that cannot be overridden at inference time.",
    ),
    (
        "star", "ai-governance",
        "QRF domain branch counts: medical=8, financial=6, security=6, hr=4.",
    ),

    # ── CONSTELLATION docs (reasoning patterns) ──────────────────────
    (
        "constellation", "medical",
        "Medical risk explanation pattern: state uncertainty explicitly, frame symptoms "
        "without diagnosis, provide general education, include emergency warning signs, "
        "recommend consulting a clinician. This reasoning pattern applies across vitamin D "
        "deficiency, sleep disorders, hantavirus, medication safety, and blood pressure "
        "management. The protocol strategy is to acknowledge knowledge limits, present "
        "evidence tiers, avoid overconfident claims, and preserve patient autonomy.",
    ),
    (
        "constellation", "finance",
        "Financial uncertainty handling methodology: disclose assumptions explicitly, "
        "present bull and bear scenarios, quantify confidence intervals, cite data "
        "vintage, distinguish model output from investment advice. This reasoning "
        "approach and framework applies to equity valuation credit analysis options "
        "pricing macroeconomic forecasting and portfolio construction. The algorithm "
        "for uncertainty is: state model, state assumptions, compute output, "
        "bound the output with sensitivity analysis, flag material risks.",
    ),
]

# ── benchmark queries with ground-truth level ─────────────────────────

_QUERIES: list[tuple[str, str]] = [
    ("hantavirus case fatality rate rodent transmission", "star"),
    ("sepsis treatment protocol antibiotics hour bundle", "star"),
    ("credit default swap CDS derivative protection buyer", "planet"),
    ("constitutional AI CANNOT_MUTATE training alignment", "planet"),
    ("troponin peak myocardial infarction biomarker", "star"),
    ("Black-Scholes option pricing formula volatility", "star"),
    ("medical science pharmacology epidemiology governance", "galaxy"),
    ("artificial intelligence regulation EU AI Act compliance", "galaxy"),
    ("medical risk explanation uncertainty pattern reasoning", "constellation"),
    ("financial uncertainty methodology scenario analysis", "constellation"),
    ("STEMI primary PCI door-to-balloon reperfusion", "star"),
    ("hantavirus Oligoryzomys host reservoir incubation", "planet"),
    ("CDS Dodd-Frank central clearing counterparty", "planet"),
    ("option delta gamma theta vega Greeks sensitivity", "planet"),
    ("drift threshold constitutional distance latent trace", "star"),
    ("QRF branch count domain forecast", "star"),
    ("sepsis SOFA score organ dysfunction lactate vasopressor", "planet"),
    ("finance markets equity fixed income derivatives hedge", "galaxy"),
    ("Black-Scholes implied volatility smile strike surface", "planet"),
    ("CANNOT_MUTATE constitutional alignment axiom principle", "star"),
]


# ── helpers ───────────────────────────────────────────────────────────

def _hit_at_k(hits: list, ground_truth_level: str, k: int) -> bool:
    for h in hits[:k]:
        if getattr(h, "intent_type", "general") == ground_truth_level:
            return True
    return False


def _level_dist(hits: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for h in hits:
        lvl = getattr(h, "intent_type", "general")
        counts[lvl] = counts.get(lvl, 0) + 1
    return counts


# ── main experiment ───────────────────────────────────────────────────

def run_experiment() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp) / "corpus"
        corpus_dir.mkdir()

        print(f"Building synthetic corpus ({len(_CORPUS)} docs) …")
        doc_paths: list[Path] = []
        for i, (level, galaxy, content) in enumerate(_CORPUS):
            p = corpus_dir / f"doc_{i:03d}_{level}_{galaxy}.txt"
            p.write_text(content, encoding="utf-8")
            write_cosmos_meta(p, level)
            doc_paths.append(p)

        # Verify auto-tagger matches intended levels
        mismatches = 0
        for i, (level, _, content) in enumerate(_CORPUS):
            auto = cosmos_tag_doc(content)
            if auto != level:
                mismatches += 1
        print(f"  Tagger accuracy on corpus: "
              f"{len(_CORPUS) - mismatches}/{len(_CORPUS)} correct\n")

        retriever = LocalRetriever(roots=[corpus_dir])
        retriever.build()
        cosmos = CosmosLayeredRetriever(retriever)

        # Warm up
        retriever.retrieve("warmup query", k=1)

        K = 5
        N = len(_QUERIES)

        flat_latencies:       list[float] = []
        layered_latencies:    list[float] = []
        anticipate_latencies: list[float] = []
        flat_hits, layered_hits, anticipate_hits = 0, 0, 0
        flat_dist:    dict[str, int] = {}
        layered_dist: dict[str, int] = {}

        for query, gt_level in _QUERIES:
            # Flat BM25
            t0 = time.perf_counter()
            flat_results = retriever.retrieve(query, k=K)
            flat_latencies.append((time.perf_counter() - t0) * 1000)
            if _hit_at_k(flat_results, gt_level, K):
                flat_hits += 1
            for lvl, cnt in _level_dist(flat_results).items():
                flat_dist[lvl] = flat_dist.get(lvl, 0) + cnt

            # Layered (no anticipation)
            res = cosmos.retrieve_layered(query, k=K, anticipate=False)
            layered_latencies.append(res.total_latency_ms)
            all_hits = res.all_hits()
            if _hit_at_k(all_hits, gt_level, K):
                layered_hits += 1
            for lvl, cnt in res.level_counts().items():
                layered_dist[lvl] = layered_dist.get(lvl, 0) + cnt

            # Layered + anticipation
            res2 = cosmos.retrieve_layered(query, k=K, anticipate=True)
            anticipate_latencies.append(res2.total_latency_ms)
            all_hits2 = res2.all_hits()
            if _hit_at_k(all_hits2, gt_level, K):
                anticipate_hits += 1

        def avg(xs): return sum(xs) / len(xs) if xs else 0.0
        def pct(n, d): return round(n / d * 100) if d else 0

        total_flat    = sum(flat_dist.values()) or 1
        total_layered = sum(layered_dist.values()) or 1

        print("=" * 72)
        print(f"{'Method':<26} {'Latency(ms)':>12} {'Hit@5':>7}  "
              f"{'galaxy%':>8} {'planet%':>8} {'star%':>8}")
        print("-" * 72)
        print(
            f"{'Flat BM25':<26} "
            f"{avg(flat_latencies):>12.1f} "
            f"{flat_hits/N:>7.2f}  "
            f"{pct(flat_dist.get('galaxy',0), total_flat):>7}% "
            f"{pct(flat_dist.get('planet',0), total_flat):>7}% "
            f"{pct(flat_dist.get('star',0),   total_flat):>7}%"
        )
        print(
            f"{'Layered (no warmup)':<26} "
            f"{avg(layered_latencies):>12.1f} "
            f"{layered_hits/N:>7.2f}  "
            f"{pct(layered_dist.get('galaxy',0), total_layered):>7}% "
            f"{pct(layered_dist.get('planet',0), total_layered):>7}% "
            f"{pct(layered_dist.get('star',0),   total_layered):>7}%"
        )
        print(
            f"{'Layered (anticipate)':<26} "
            f"{avg(anticipate_latencies):>12.1f} "
            f"{anticipate_hits/N:>7.2f}  "
            "          —        —        —"
        )
        print("=" * 72)
        print()
        print("Interpretation:")
        print("  Hit@5  — fraction of queries where top-5 included the ground-truth level doc")
        print("  Layered enforces balanced representation (galaxy+planet+star each ≤ 3 hits)")
        speedup = avg(layered_latencies) / avg(anticipate_latencies) if avg(anticipate_latencies) > 0 else 1
        print(f"  Anticipation speedup vs sequential: {speedup:.2f}x")


if __name__ == "__main__":
    run_experiment()
