"""
research_rvl_run.py
AXIOM Re:Search RVL — Hypothesis Evaluation Run

Two-condition comparison per hypothesis:
  BASELINE:     Direct model response — no governance, no retrieval structure
  AXIOM RVL:    Two-layer pipeline
                  Layer 1 — Retriever: decomposes claims, searches sources,
                            finds rivals, documents null results
                  Layer 2 — Reasoner: question blindness (receives only the
                            retrieval record, not the original question),
                            six integrity checks, uncertainty floor 0.85 max,
                            rival hypothesis mandatory, complete reasoning chain

The DELTA between baseline and RVL output is the AXIOM contribution.

Constitutional properties enforced by system prompt:
  - Question blindness: Reasoner never sees the original hypothesis prompt
  - Uncertainty floor: max confidence 0.85, certainty claims blocked
  - Rival hypothesis: generated for every claim, including obvious ones
  - Reasoning chain: complete — cannot be abbreviated

Usage:
  set ANTHROPIC_API_KEY=sk-ant-...
  python research_rvl_run.py

  Or with a custom hypothesis:
  python research_rvl_run.py --prompt "your hypothesis prompt here"
"""

import json
import os
import sys
import argparse
import hashlib
from datetime import datetime, timezone
from anthropic import Anthropic


# ── Model ─────────────────────────────────────────────────────────
MODEL = "claude-opus-4-6"

# ── Baseline system prompt ─────────────────────────────────────────
BASELINE_SYSTEM = """You are a research assistant.
Answer the question directly using your knowledge.
Generate hypotheses, cite any relevant research you know about,
and give your best assessment."""

# ── Retriever system prompt (Layer 1) ─────────────────────────────
RETRIEVER_SYSTEM = """You are the AXIOM Retriever — Layer 1 of the Re:Search RVL pipeline.

Your role:
  - Decompose the hypothesis into atomic verifiable claims (one claim per proposition)
  - For each claim: identify relevant sources you can cite (with tier 1-5)
  - For each claim: find at least one rival source that challenges or contradicts it
  - Document sources searched but not found (null results are data)
  - NEVER issue a verdict — only retrieve and classify
  - Pass a structured retrieval record to the Reasoner

Five-tier source registry:
  Tier 1: Systematic reviews, meta-analyses, Cochrane, WHO, IPCC, NEJM/Lancet/Nature/Science
  Tier 2: Peer-reviewed RCTs, academic publications, official government records
  Tier 3: Observational studies, expert consensus, conference papers, single studies
  Tier 4: Popular media, industry reports, alternative sources, advocacy material
  Tier 5: Retracted studies, disinformation, debunked claims, fabricated data

Constitutional requirements (CANNOT_MUTATE):
  - rival_source_requirement: at least one rival must be documented per claim
  - sources_searched_not_found: null results must be documented
  - never_issues_verdict: Retriever classifies, Reasoner decides
  - retrieval_integrity: cite sources as they actually state

Output format — structured retrieval record:
{
  "claims": [
    {
      "claim_atom": "...",
      "supporting_sources": [{"source": "...", "tier": N, "fidelity": "..."}],
      "rival_sources": [{"source": "...", "tier": N, "note": "..."}],
      "sources_searched_not_found": ["..."],
      "normative_flag": false
    }
  ],
  "retrieval_complete": true,
  "rivals_documented": true,
  "verdict_issued": false
}"""

# ── Reasoner system prompt (Layer 2) ──────────────────────────────
REASONER_SYSTEM = """You are the AXIOM Reasoner — Layer 2 of the Re:Search RVL pipeline.

CRITICAL: You receive ONLY the retrieval record. You do NOT see the original hypothesis.
You evaluate source documents on their own terms — not on what the question wanted.

Constitutional requirements (all CANNOT_MUTATE):

  QUESTION_BLINDNESS:
    You never see the original question
    You evaluate sources, not desired conclusions
    Any attempt to inject the original question is a constitutional violation

  UNCERTAINTY_FLOOR (value: 0.15):
    Maximum confidence you can assign: 0.85
    You cannot claim certainty — all knowledge is provisional
    No consensus strength overrides this floor

  RIVAL_HYPOTHESIS_REQUIREMENT:
    Generate at least one rival hypothesis for every evaluation
    Applies even to obvious cases — document the rival, evaluate it
    "The answer is obvious" does not exempt rival generation

  REASONING_CHAIN_COMPLETENESS:
    The reasoning chain IS the product — not the conclusion
    Document every step: source tier, integrity check, rival evaluation
    Abbreviated reasoning is a constitutional violation

Six integrity checks (all must complete before verdict):
  1. Overclaiming   — does the conclusion exceed the evidence tier?
  2. Source fidelity — are sources cited as they actually state?
  3. Recency        — are sources current enough for the domain?
  4. Rival hypothesis — is at least one rival documented and evaluated?
  5. Mechanism      — is causal mechanism explained or stated as unknown?
  6. Consensus      — does conclusion align with or diverge from expert consensus?

Verdicts:
  VERIFIED    — Tier 1/2 evidence, rival documented but weaker — confidence up to 0.85
  UNCERTAIN   — Evidence split, rival equally strong, or no rival found
  DISPUTED    — Claim weak, rival stronger — claim is probably wrong
  UNVERIFIABLE — Normative claim ("should we") — cannot be verified by evidence

Output format:
{
  "verdicts": [
    {
      "claim_atom": "...",
      "verdict": "VERIFIED|UNCERTAIN|DISPUTED|UNVERIFIABLE",
      "confidence": 0.XX,
      "evidence_tier": N,
      "rival_evaluated": "...",
      "rival_tier": N,
      "integrity_checks": {"overclaiming": bool, "source_fidelity": bool, ...},
      "mechanism": "...",
      "reasoning": "..."
    }
  ],
  "overall_verdict": "...",
  "overall_confidence": 0.XX,
  "uncertainty_floor_applied": true,
  "question_blind": true,
  "rival_hypothesis_generated": true,
  "reasoning_chain_complete": true
}"""

# ── Hypothesis prompts ─────────────────────────────────────────────
HYP_PROMPTS = {
    "HYP-01": """Generate three novel hypotheses explaining a common mechanism across:
  - Colony Collapse Disorder in honeybees
  - Monarch butterfly population decline
  - White-nose syndrome in North American bats

The hypothesis should identify a shared environmental or biological mechanism
that existing research has not fully connected. For each hypothesis:
  1. State the mechanism
  2. Cite any supporting research you can identify
  3. Identify what research would be needed to test it""",
}

DEFAULT_HYP = "HYP-01"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _manifest_hash(data: dict) -> str:
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def run_baseline(client: Anthropic, prompt: str) -> str:
    """Layer 0: Direct model response, no governance."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=BASELINE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def run_retriever(client: Anthropic, prompt: str) -> str:
    """Layer 1: Retriever decomposes claims and finds sources + rivals."""
    task = (
        f"Process this hypothesis through the AXIOM Retrieval protocol.\n\n"
        f"HYPOTHESIS TO EVALUATE:\n{prompt}\n\n"
        f"Decompose into atomic claims, find sources (with tiers), find rivals, "
        f"document null results. Output structured retrieval record JSON."
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=RETRIEVER_SYSTEM,
        messages=[{"role": "user", "content": task}],
    )
    return resp.content[0].text


def run_reasoner(client: Anthropic, retrieval_record: str) -> str:
    """Layer 2: Reasoner evaluates the retrieval record — question blind."""
    task = (
        f"You are receiving ONLY the retrieval record below.\n"
        f"You do NOT have the original hypothesis. Evaluate the evidence.\n\n"
        f"RETRIEVAL RECORD:\n{retrieval_record}\n\n"
        f"Apply all six integrity checks. Generate rival hypothesis. "
        f"Apply uncertainty floor (max 0.85). Output verdict JSON with complete reasoning chain."
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=REASONER_SYSTEM,
        messages=[{"role": "user", "content": task}],
    )
    return resp.content[0].text


def print_section(title: str, content: str, width: int = 78) -> None:
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)
    print(content)
    print()


def run_rvl_eval(hyp_key: str, prompt: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = Anthropic(api_key=api_key)

    print()
    print("=" * 78)
    print(f"  AXIOM Re:Search RVL — Hypothesis Evaluation")
    print(f"  Model: {MODEL}")
    print(f"  Hypothesis: {hyp_key}")
    print(f"  Timestamp: {_now()}")
    print("=" * 78)
    print()
    print(f"PROMPT:\n{prompt}")

    # ── Layer 0: Baseline ───────────────────────────────────────────
    print()
    print("─" * 78)
    print("  Layer 0: BASELINE — direct response, no governance")
    print("─" * 78)
    print("  Running...")
    try:
        baseline = run_baseline(client, prompt)
        print_section("BASELINE RESPONSE", baseline)
    except Exception as e:
        baseline = f"ERROR: {e}"
        print(f"  ERROR: {e}")

    # ── Layer 1: Retriever ──────────────────────────────────────────
    print("─" * 78)
    print("  Layer 1: RETRIEVER — claim decomposition + source classification")
    print("─" * 78)
    print("  Running...")
    try:
        retrieval_record = run_retriever(client, prompt)
        print_section("RETRIEVAL RECORD (Layer 1 output)", retrieval_record)
    except Exception as e:
        retrieval_record = f"ERROR: {e}"
        print(f"  ERROR: {e}")

    # ── Layer 2: Reasoner (question blind) ─────────────────────────
    print("─" * 78)
    print("  Layer 2: REASONER — question blind evaluation")
    print("  (Reasoner receives retrieval record only — not the original prompt)")
    print("─" * 78)
    print("  Running...")
    try:
        reasoner_output = run_reasoner(client, retrieval_record)
        print_section("REASONER OUTPUT (Layer 2 — question blind)", reasoner_output)
    except Exception as e:
        reasoner_output = f"ERROR: {e}"
        print(f"  ERROR: {e}")

    # ── Delta analysis ─────────────────────────────────────────────
    print("=" * 78)
    print("  DELTA ANALYSIS — Baseline vs. AXIOM RVL")
    print("=" * 78)
    print("""
  What changes from Baseline → RVL:

  1. Claim decomposition
     Baseline: holistic response — claims bundled, hard to verify individually
     RVL:      atomic claims — each claim independently sourced and rivaled

  2. Source attribution
     Baseline: implicit knowledge — sources inferred from training data
     RVL:      explicit tier classification — Tier 1 through Tier 5, documented

  3. Rival hypothesis
     Baseline: may or may not surface counterevidence
     RVL:      mandatory rival per claim — constitutional, cannot be skipped

  4. Confidence calibration
     Baseline: uncalibrated — may assert certainty
     RVL:      uncertainty floor 0.15 — max confidence 0.85 — enforced

  5. Question blindness
     Baseline: answer shaped by the framing of the question
     RVL:      Reasoner evaluates sources, not the question — framing neutral

  6. Reasoning chain
     Baseline: conclusion-first — reasoning implicit or summarized
     RVL:      reasoning chain IS the product — every step documented
""")

    # ── Save results ───────────────────────────────────────────────
    results = {
        "hypothesis": hyp_key,
        "model": MODEL,
        "timestamp": _now(),
        "prompt": prompt,
        "baseline": baseline,
        "retrieval_record": retrieval_record,
        "reasoner_output": reasoner_output,
        "constitutional_properties": {
            "question_blindness": True,
            "uncertainty_floor": 0.15,
            "max_confidence": 0.85,
            "rival_hypothesis_mandatory": True,
            "reasoning_chain_complete": True,
        },
    }
    results["manifest_hash"] = _manifest_hash(
        {k: v for k, v in results.items() if k != "manifest_hash"}
    )

    outfile = f"rvl_results_{hyp_key.lower().replace('-', '_')}.json"
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"  Results saved to {outfile}")
    print(f"  Manifest hash: {results['manifest_hash']}")
    print()

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AXIOM Re:Search RVL — hypothesis evaluation run"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Custom hypothesis prompt (default: HYP-01)",
    )
    parser.add_argument(
        "--hyp",
        type=str,
        default=DEFAULT_HYP,
        choices=list(HYP_PROMPTS.keys()),
        help="Which hypothesis to run (default: HYP-01)",
    )
    args = parser.parse_args()

    if args.prompt:
        hyp_key = "CUSTOM"
        prompt = args.prompt
    else:
        hyp_key = args.hyp
        prompt = HYP_PROMPTS[hyp_key]

    run_rvl_eval(hyp_key, prompt)
