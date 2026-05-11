"""
axiom_latent.py — AXIOM Latent Reasoning Engine
================================================
Phase 1: LatentTrace     — compressed reasoning state (no chain-of-thought)
Phase 2: MultiplexRunner — 4 parallel branches + constitutional selector
Phase 3: Foresight       — predict ideal outcome before generating

CANNOT_MUTATE: confidence_cap=0.85, manifest_signing, rival_branch_required

Usage:
  python3 axiom_latent.py --demo --no-api
  python3 axiom_latent.py --run "Does vitamin D improve sleep quality?"
  python3 axiom_latent.py --trace "What causes inflation?"
  python3 axiom_latent.py --multiplex "Should I upgrade my model?"
  python3 axiom_latent.py --foresight "How do I treat a fever?"
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from axiom_latent_v2 import (
    LatentTraceV2, ManifoldAlert, ManifoldAlerter,
    ManifoldChecker, ManifoldResult, MonotonicGate, TrajectorySample,
)
from axiom_semantic_observable import (
    RUBRIC, IntentType, Condition, ConditionSet, SemanticObservable,
)
from axiom_signing import derive_key
from axiom_conversation_graph import ConversationGraph, GraphNodeError, DAMPEN_FACTOR

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# ── Constitutional constants (CANNOT_MUTATE) ──────────────────────────────────
CONFIDENCE_CAP = 0.85
MANIFEST_KEY   = derive_key(b"axiom-latent-v1")
MANIFEST_FILE  = Path("latent_manifests.jsonl")
MODEL          = os.environ.get("AXIOM_MODEL", "claude-sonnet-4-6")

# ── Parallel-N branch pool — CANNOT_MUTATE order ─────────────────────────────
# First N entries are always used. Pool order is constitutionally fixed.
BRANCH_POOL: tuple = (
    "SafetyBranch",   # 1 — always first: safety gate on every run
    "FastBranch",     # 2 — always second: baseline concise answer
    "SkepticBranch",  # 3 — N >= 4
    "CreativeBranch", # 4 — N >= 4
    "DetailBranch",   # 5 — N >= 6
    "CautionBranch",  # 6 — N >= 6
    "RivalBranch",    # 7 — N >= 8
    "EvidenceBranch", # 8 — N >= 8 (high-risk only)
)

_HIGH_RISK_CLUSTERS: frozenset = frozenset({"medical", "legal", "financial", "safety"})

# ── ANSI helpers ──────────────────────────────────────────────────────────────
def _b(s):  return "\033[1m"  + s + "\033[0m"
def _g(s):  return "\033[32m" + s + "\033[0m"
def _c(s):  return "\033[36m" + s + "\033[0m"
def _y(s):  return "\033[33m" + s + "\033[0m"
def _r(s):  return "\033[31m" + s + "\033[0m"
def _gr(s): return "\033[90m" + s + "\033[0m"

SEP  = "=" * 62
DASH = "-" * 62


# ══════════════════════════════════════════════════════════════════════════════
# Dataclasses
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LatentState:
    intent_vector:   list
    risk_clusters:   list
    compressed_plan: list
    confidence:      float  # always <= CONFIDENCE_CAP (0.85)


@dataclass
class BranchResult:
    branch:   str
    response: str
    score:    float
    metrics:  dict


@dataclass
class MultiplexResult:
    winner:   BranchResult
    rival:    BranchResult
    branches: list


@dataclass
class ForesightPrediction:
    ideal_outcome:      str
    success_conditions: list
    failure_conditions: list
    intent_type:        Optional[str] = None  # populated by ForesightScorer


@dataclass
class ForesightScore:
    verdict: str   # ALIGNED / PARTIAL / MISALIGNED
    score:   float
    matched: list
    missed:  list


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — LatentTrace
# ══════════════════════════════════════════════════════════════════════════════

_INTENT_MAP = {
    "ask_boolean":        r"\b(does|is|are|can|will|do|did|was|were|has|have)\b",
    "ask_procedural":     r"\b(how|explain|describe|walk me through|show me|steps)\b",
    "ask_causal":         r"\b(why|what causes|reason for|because|leads to|result in)\b",
    "ask_factual":        r"\b(what is|what are|define|tell me|when|where|who)\b",
    "ask_recommendation": r"\b(should|recommend|best|better|which|suggest|advice|worth)\b",
    "ask_comparative":    r"\b(compare|difference|vs|versus|between|contrast)\b",
    "ask_predictive":     r"\b(predict|forecast|future|likely|probability|chance|expect)\b",
}

_RISK_MAP = {
    "medical":   r"\b(medical|health|doctor|medicine|symptom|disease|treat|diagnose|pain|fever|vitamin|supplement|therapy|drug|dose|sleep|hospital|clinic|nurse|patient|prescription|medication|pill|tablet|illness|injury|wound|infection|surgery|cancer|diabetes|blood|heart|mental|anxiety|depression|vaccine|vaccination|allergy|allergic|allergies|headache|nausea|hurt|hurts|ache|aches|healthcare|physician|specialist)\b",
    "financial": r"\b(money|invest|stock|tax|debt|loan|financial|savings|return|portfolio|crypto|cost|price)\b",
    "legal":     r"\b(law|illegal|crime|legal|court|sue|rights|contract|liability|regulation)\b",
    "safety":    r"\b(dangerous|risk|harm|safe|hazard|toxic|poison|accident|explosion)\b",
    "personal":  r"\b(my\b|personal|private|myself|mine)\b",
    "ethics":    r"\b(right|wrong|moral|ethical|fair|bias|discriminat)\b",
}

_PLANS = {
    "ask_boolean":        ["classify_question", "retrieve_evidence", "evaluate_truth_value", "assign_confidence", "state_verdict"],
    "ask_procedural":     ["parse_goal", "decompose_steps", "sequence_dependencies", "validate_feasibility", "output_procedure"],
    "ask_causal":         ["parse_causal_chain", "identify_mechanism", "evaluate_evidence_quality", "assess_confounders", "synthesize_explanation"],
    "ask_factual":        ["parse_entity_query", "retrieve_facts", "verify_consistency", "assess_recency", "state_facts"],
    "ask_recommendation": ["parse_context", "enumerate_options", "evaluate_tradeoffs", "apply_constraints", "formulate_recommendation"],
    "ask_comparative":    ["identify_subjects", "define_dimensions", "score_each_dimension", "weight_by_relevance", "summarize_comparison"],
    "ask_predictive":     ["parse_prediction_target", "identify_base_rates", "assess_influencing_factors", "quantify_uncertainty", "state_prediction"],
    "default":            ["parse_question", "retrieve_context", "reason_over_context", "validate_reasoning", "formulate_response"],
}


class LatentTrace:
    """Phase 1: Compress question into intent_vector, risk_clusters, compressed_plan."""

    def encode_heuristic(self, question: str) -> LatentState:
        q = question.lower()

        intents = [k for k, p in _INTENT_MAP.items() if re.search(p, q, re.I)]
        if not intents:
            intents = ["ask_general"]

        risks = [k for k, p in _RISK_MAP.items() if re.search(p, q, re.I)]
        plan  = _PLANS.get(intents[0], _PLANS["default"])

        conf = 0.70
        words = question.split()
        if len(words) <= 10:   conf += 0.04
        if len(intents) == 1:  conf += 0.03
        if len(risks) >= 2:    conf -= 0.05
        if len(words) > 30:    conf -= 0.03
        conf = min(round(conf, 2), CONFIDENCE_CAP)

        return LatentState(intent_vector=intents, risk_clusters=risks,
                           compressed_plan=plan, confidence=conf)

    def encode_api(self, question: str, client, backend="anthropic", model=MODEL) -> LatentState:
        prompt = (
            "Analyze this question and return ONLY a JSON object with:\n"
            "  intent_vector: list of 1-3 labels from: "
            "ask_factual ask_causal ask_procedural ask_recommendation ask_boolean ask_comparative ask_predictive\n"
            "  risk_clusters: list of 0-3 labels from: medical financial legal safety personal ethics\n"
            "  compressed_plan: list of 4-6 snake_case step labels describing the reasoning steps\n"
            "  confidence: float 0.0-0.85\n\n"
            f"Question: {question}\n\nReturn ONLY valid JSON."
        )
        try:
            if backend == "openai":
                resp = client.chat.completions.create(
                    model=model, max_tokens=300,
                    messages=[{"role": "user", "content": prompt}])
                raw_text = resp.choices[0].message.content or ""
            else:
                resp = client.messages.create(
                    model=model, max_tokens=300,
                    messages=[{"role": "user", "content": prompt}])
                raw_text = resp.content[0].text
            raw  = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.S)
            data = json.loads(raw)
            return LatentState(
                intent_vector=data.get("intent_vector", ["ask_general"]),
                risk_clusters=data.get("risk_clusters", []),
                compressed_plan=data.get("compressed_plan", _PLANS["default"]),
                confidence=min(float(data.get("confidence", 0.75)), CONFIDENCE_CAP),
            )
        except Exception:
            return self.encode_heuristic(question)

    def encode(self, question: str, client=None, backend="anthropic", model=MODEL) -> LatentState:
        return self.encode_api(question, client, backend, model) if client else self.encode_heuristic(question)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — MultiplexRunner
# ══════════════════════════════════════════════════════════════════════════════

# ── Parallel-N exceptions ─────────────────────────────────────────────────────

class BranchPoolExhausted(RuntimeError):
    """Raised when requested N exceeds BRANCH_POOL size."""


class BranchInputError(TypeError):
    """Raised when risk_clusters is not a list."""


# ── Parallel-N helpers — CANNOT_MUTATE rules ──────────────────────────────────

def _validate_branch_n(n: int) -> None:
    """Raise BranchPoolExhausted if N exceeds pool capacity."""
    if n > len(BRANCH_POOL):
        raise BranchPoolExhausted(
            f"Requested N={n} exceeds BRANCH_POOL size {len(BRANCH_POOL)}"
        )


def compute_branch_n(risk_clusters: list) -> int:
    """Return the number of parallel branches for the given risk profile.

    CANNOT_MUTATE: n_table and high_risk_set are constitutionally fixed.

    N=2  — empty risk (SafetyBranch + FastBranch only)
    N=4  — 1 non-high-risk cluster
    N=6  — 2+ non-high-risk clusters
    N=8  — any high-risk cluster (medical / legal / financial / safety)
    """
    if not isinstance(risk_clusters, list):
        raise BranchInputError(
            f"risk_clusters must be a list, got {type(risk_clusters).__name__!r}"
        )
    if any(r in _HIGH_RISK_CLUSTERS for r in risk_clusters):
        return 8
    count = len(risk_clusters)
    if count == 0:
        return 2
    if count == 1:
        return 4
    return 6  # 2+ non-high-risk clusters


_BRANCH_PROMPTS = {
    "FastBranch": (
        "You are a direct, efficient assistant. Give the clearest, most concise answer possible. "
        "No hedging unless absolutely necessary. 1-3 sentences."
    ),
    "SafetyBranch": (
        "You are a safety-conscious assistant. Prioritize accuracy and responsible guidance. "
        "Include relevant caveats; recommend professional consultation for medical, legal, or financial matters. "
        "2-4 sentences."
    ),
    "SkepticBranch": (
        "You are a skeptical analyst. Challenge the assumptions in the question. "
        "Point out what we don't know or what important nuances are missing. "
        "Provide a more rigorous framing. 2-4 sentences."
    ),
    "CreativeBranch": (
        "You are a creative lateral thinker. Approach this from an unexpected angle — "
        "use analogy, metaphor, or a non-obvious framework. Surprising but genuinely useful. 2-4 sentences."
    ),
    "DetailBranch": (
        "You are a detail-oriented domain expert. Provide a thorough, structured answer that covers "
        "the key factors, mechanisms, and contextual variables relevant to the question. "
        "Precision over brevity. 3-5 sentences."
    ),
    "CautionBranch": (
        "You are a cautious risk-aware advisor. Lead with the most important limitations, "
        "contraindications, or things that could go wrong. Recommend professional consultation "
        "where any uncertainty exists. 2-4 sentences."
    ),
    "RivalBranch": (
        "You represent the strongest possible opposing view. Argue persuasively against "
        "the most likely answer. Surface evidence, edge cases, or frameworks that challenge "
        "the conventional position. 2-4 sentences."
    ),
    "EvidenceBranch": (
        "You rely strictly on peer-reviewed evidence and established clinical or scientific consensus. "
        "Cite the type of evidence (RCT, meta-analysis, observational) and its strength. "
        "Distinguish what is known from what is assumed. 3-5 sentences."
    ),
}

_DEMO_RESPONSES = {
    "FastBranch": (
        "The evidence on this is mixed but generally supports a moderate effect under "
        "specific conditions. Key variables include baseline status, dosage, and duration. "
        "More controlled trials are needed for a definitive answer."
    ),
    "SafetyBranch": (
        "There is reasonable evidence for a modest effect, though individual results vary "
        "significantly based on baseline health, dosage, and context. Consult a qualified "
        "professional before acting — population-level findings may not apply to you."
    ),
    "SkepticBranch": (
        "This question assumes a clear causal link that the literature doesn't fully support. "
        "Most studies here suffer from small samples, self-selection bias, and short follow-up. "
        "A better question: under what specific conditions and for which populations does this hold?"
    ),
    "CreativeBranch": (
        "Think of this like tuning a radio: you're not asking whether the signal exists, "
        "but whether your receiver is calibrated to pick it up. The answer depends less on "
        "the phenomenon itself and more on the conditions you've created to observe it."
    ),
    "DetailBranch": (
        "The relevant mechanisms involve several interacting variables: baseline levels, "
        "supplementation dosage, duration of intervention, population demographics, and "
        "outcome measurement methodology. Effects reported in literature range from negligible "
        "to moderate depending on which of these factors are controlled. Standardised protocols "
        "show stronger and more consistent effects than naturalistic studies."
    ),
    "CautionBranch": (
        "Before acting on any answer here, note that individual variation is substantial and "
        "population-level findings frequently do not transfer to individual cases. Dosage "
        "thresholds, interactions with existing medications, and underlying conditions can "
        "reverse expected effects. Consult a qualified professional before making changes "
        "based on this type of evidence."
    ),
    "RivalBranch": (
        "The prevailing consensus on this overstates the evidence. Most cited studies are "
        "observational, subject to confounding, and underpowered for the effect sizes claimed. "
        "The few high-quality RCTs show null or marginal results. The burden of proof for a "
        "positive claim here has not been met — the honest answer is we do not know."
    ),
    "EvidenceBranch": (
        "Meta-analyses of randomised controlled trials on this topic report a small-to-moderate "
        "effect size (Cohen's d ≈ 0.2–0.4) that reaches statistical significance in subgroups "
        "with documented baseline deficiency. Observational cohort data are consistent but carry "
        "substantial confounding risk. No current guideline recommends intervention solely on "
        "the basis of this evidence without corroborating clinical indicators."
    ),
}


def _score_branch(response: str, latent: LatentState) -> dict:
    wc          = len(response.split())
    clarity     = 1.0 if 10 <= wc <= 150 else (0.4 if wc < 10 else 0.7)
    safety      = 1.0
    if {"medical", "legal", "financial"} & set(latent.risk_clusters):
        caveat_words = {"consult", "professional", "doctor", "advisor", "attorney", "varies"}
        if not any(w in response.lower() for w in caveat_words):
            safety = 0.65
    helpfulness = 0.9 if wc >= 20 else 0.6
    overall     = round(clarity * 0.3 + safety * 0.4 + helpfulness * 0.3, 3)
    return {"clarity": round(clarity, 2), "safety": round(safety, 2),
            "helpfulness": round(helpfulness, 2), "overall": overall}


class MultiplexRunner:
    """Phase 2: Run N branches in parallel, select winner constitutionally.

    N is computed from latent.risk_clusters via compute_branch_n():
      N=2  empty risk      N=4  1 cluster
      N=6  2+ clusters     N=8  any high-risk (medical/legal/financial/safety)

    Active branches are always the first N entries of BRANCH_POOL.
    CANNOT_MUTATE: rival branch always recorded, pool order never altered.
    """

    def _heuristic_branch(self, branch: str, latent: LatentState) -> BranchResult:
        resp    = _DEMO_RESPONSES[branch]
        metrics = _score_branch(resp, latent)
        return BranchResult(branch=branch, response=resp, score=metrics["overall"], metrics=metrics)

    def _api_branch(self, branch: str, question: str, latent: LatentState,
                    client, backend="anthropic", model=MODEL) -> BranchResult:
        system = _BRANCH_PROMPTS[branch]
        if latent.risk_clusters:
            system += f"\n\nRisk domains: {', '.join(latent.risk_clusters)}."
        try:
            if backend == "openai":
                r = client.chat.completions.create(
                    model=model, max_tokens=300,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": question}])
                resp = (r.choices[0].message.content or "").strip()
            else:
                r = client.messages.create(
                    model=model, max_tokens=300, system=system,
                    messages=[{"role": "user", "content": question}])
                resp = r.content[0].text.strip()
        except Exception as e:
            resp = f"[{branch} error: {e}]"
        metrics = _score_branch(resp, latent)
        return BranchResult(branch=branch, response=resp, score=metrics["overall"], metrics=metrics)

    def run(self, question: str, latent: LatentState, client=None,
            backend="anthropic", model=MODEL) -> MultiplexResult:
        n      = compute_branch_n(latent.risk_clusters)
        _validate_branch_n(n)                          # guard: pool exhaustion
        active = list(BRANCH_POOL[:n])

        results: list = []
        with ThreadPoolExecutor(max_workers=n) as ex:
            if client:
                futures = {ex.submit(self._api_branch, b, question, latent, client, backend, model): b for b in active}
            else:
                futures = {ex.submit(self._heuristic_branch, b, latent): b for b in active}
            for f in as_completed(futures):
                results.append(f.result())

        results.sort(key=lambda r: r.score, reverse=True)
        # CANNOT_MUTATE: rival branch always recorded
        return MultiplexResult(winner=results[0], rival=results[1], branches=results)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Foresight
# ══════════════════════════════════════════════════════════════════════════════

def _heuristic_prediction(question: str, latent: LatentState) -> ForesightPrediction:
    intents = latent.intent_vector
    risks   = latent.risk_clusters

    if "ask_boolean" in intents:
        ideal   = "States a clear verdict supported by evidence, acknowledges uncertainty, specifies conditions."
        success = ["States a clear position or qualified verdict",
                   "References quality of evidence",
                   "Acknowledges conditions or exceptions"]
        failure = ["Claims absolute certainty without caveats",
                   "Refuses to take any position",
                   "Ignores key confounding factors"]
    elif "ask_causal" in intents:
        ideal   = "Identifies primary mechanism, distinguishes proximate from distal causes, rates confidence."
        success = ["Identifies primary mechanism",
                   "Distinguishes proximate and root causes",
                   "Rates confidence in causal claim"]
        failure = ["Confuses correlation with causation",
                   "Single-cause framing for multi-causal phenomenon",
                   "Ignores contradicting evidence"]
    elif "ask_recommendation" in intents:
        ideal   = "Presents 2-3 concrete options with tradeoffs; makes a context-sensitive recommendation."
        success = ["Presents multiple options",
                   "Evaluates tradeoffs explicitly",
                   "Makes context-sensitive recommendation"]
        failure = ["Universal prescription without context",
                   "Ignores key constraints or individual variation",
                   "Fails to acknowledge uncertainty"]
    elif "ask_procedural" in intents:
        ideal   = "Clear sequential steps, notes prerequisites, flags common failure points."
        success = ["Provides sequential steps",
                   "Notes prerequisites and dependencies",
                   "Flags common failure modes"]
        failure = ["Skips critical intermediate steps",
                   "Assumes knowledge the questioner may not have",
                   "No error-handling guidance"]
    else:
        ideal   = "Accurate, appropriately hedged, directly addresses the core question."
        success = ["Directly addresses the question",
                   "Appropriately qualified confidence",
                   "Accurate to current evidence"]
        failure = ["Evades the core question",
                   "Overconfident or underconfident",
                   "Factually incorrect or outdated"]

    if "medical"   in risks: failure.append("Definitive medical advice without professional caveat")
    if "financial" in risks: failure.append("Specific financial advice without disclaimer")
    if "legal"     in risks: failure.append("Specific legal advice without attorney disclaimer")

    return ForesightPrediction(ideal_outcome=ideal,
                               success_conditions=success,
                               failure_conditions=failure)



# ── Synonym expansion for semantic matching ─────────────────────────────────
# Each canonical term maps to a set of semantically equivalent words/phrases.
# When checking whether a success condition is satisfied, we test the canonical
# term AND all its synonyms against the answer text.

_SYNONYMS = {
    # Medical / professional consultation
    "consult":       {"consult", "discuss", "speak", "check", "visit", "seeing", "asking", "talk"},
    "healthcare":    {"healthcare", "health", "medical", "doctor", "physician", "clinician", "provider"},
    "professional":  {"professional", "doctor", "specialist", "expert", "clinician", "physician", "practitioner"},
    # Evidence & confidence
    "evidence":      {"evidence", "research", "studies", "data", "findings", "literature", "trials"},
    "quality":       {"quality", "strength", "rigor", "reliability", "robustness", "validity"},
    "confidence":    {"confidence", "certainty", "likelihood", "probability", "assurance"},
    "uncertainty":   {"uncertainty", "unclear", "uncertain", "nuanced", "depends", "varies", "variable", "mixed"},
    # Positions & verdicts
    "position":      {"position", "stance", "verdict", "conclusion", "answer", "assessment", "view", "opinion"},
    "verdict":       {"verdict", "conclusion", "judgment", "ruling", "finding", "determination", "answer"},
    "qualified":     {"qualified", "hedged", "cautious", "nuanced", "conditional", "moderate", "tempered"},
    # Causal reasoning
    "mechanism":     {"mechanism", "cause", "driver", "factor", "process", "pathway", "reason"},
    "causes":        {"causes", "drivers", "factors", "mechanisms", "reasons", "determinants", "sources"},
    "proximate":     {"proximate", "immediate", "direct", "short-term", "near"},
    "confounders":   {"confounders", "confounding", "variables", "factors", "covariates", "bias"},
    "correlation":   {"correlation", "association", "relationship", "link", "connection"},
    # Recommendations
    "options":       {"options", "alternatives", "choices", "approaches", "possibilities", "paths"},
    "tradeoffs":     {"tradeoffs", "trade-offs", "pros", "cons", "advantages", "disadvantages", "downsides"},
    "recommend":      {"recommend", "recommendation", "suggest", "suggestion", "advice", "advise", "guidance", "proposal"},
    "constraints":   {"constraints", "limitations", "restrictions", "requirements", "conditions"},
    # Procedures
    "steps":         {"steps", "instructions", "procedure", "process", "guide", "directions", "stages"},
    "prerequisites": {"prerequisites", "requirements", "dependencies", "preconditions", "setup", "before"},
    "failure":       {"failure", "error", "problem", "issue", "pitfall", "mistake", "risk", "caveat", "warning"},
    # Financial
    "financial":     {"financial", "money", "fiscal", "economic", "investment", "monetary"},
    "advisor":       {"advisor", "adviser", "planner", "consultant", "accountant", "attorney"},
    # General
    "variation":     {"variation", "variability", "individual", "personal", "specific", "particular", "context"},
    "conditions":    {"conditions", "circumstances", "situations", "context", "factors", "cases"},
    "acknowledges":  {"acknowledges", "notes", "mentions", "recognizes", "considers", "addresses"},
}


def _expand_terms(key_terms: list) -> set:
    """Expand key_terms with synonyms for semantic matching.
    Matches synonym keys by prefix to handle verb forms:
    'consulting' matches key 'consult', 'recommending' matches 'recommend'.
    """
    expanded = set(key_terms)
    for term in key_terms:
        # Exact match first
        if term in _SYNONYMS:
            expanded |= _SYNONYMS[term]
        else:
            # Prefix match: 'consulting' starts with 'consult', 'recommending' with 'recommend'
            for key, syns in _SYNONYMS.items():
                if term.startswith(key) or key.startswith(term):
                    expanded |= syns
                    break
    return expanded


def _term_in_text(terms: set, text: str) -> bool:
    """Check if any term from the expanded set appears in the text."""
    return any(t in text for t in terms)


def _score_against_prediction(prediction: ForesightPrediction, actual: str) -> ForesightScore:
    a    = actual.lower()
    stop = {"should", "must", "include", "provides", "addresses", "states", "answer",
            "gives", "makes", "ignores", "fails", "skips", "without"}

    matched, missed = [], []
    for cond in prediction.success_conditions:
        key_terms = [w for w in cond.lower().split() if len(w) > 4 and w not in stop]
        expanded  = _expand_terms(key_terms)
        (matched if _term_in_text(expanded, a) else missed).append(cond)

    triggered = sum(
        1 for cond in prediction.failure_conditions
        if _term_in_text(
            _expand_terms([w for w in cond.lower().split() if len(w) > 4 and w not in stop]),
            a,
        )
    )

    total   = len(prediction.success_conditions)
    score   = (len(matched) / total) if total else 0.5
    score   = max(0.0, round(score - 0.1 * triggered, 2))
    verdict = "ALIGNED" if score >= 0.70 else ("PARTIAL" if score >= 0.40 else "MISALIGNED")
    return ForesightScore(verdict=verdict, score=score, matched=matched, missed=missed)


# ── score_answer — cosine-based rubric scorer ────────────────────────────────
# Stop-words skipped during condition key-term extraction (verbs / fillers)
_COND_STOP: frozenset = frozenset({
    "should", "must", "include", "provides", "addresses", "states", "answer",
    "gives", "makes", "ignores", "fails", "skips", "without", "specific",
})


def score_answer(answer: str, intent_type: IntentType) -> ForesightScore:
    """Score answer against RUBRIC[intent_type] using synonym-expanded term matching.

    CANNOT_MUTATE: conditions sourced from the frozen RUBRIC.

    Each condition's key terms (words > 4 chars) are expanded through _SYNONYMS
    so that surface-form mismatches like 'consult' vs 'consulting', or
    'doctor' vs 'healthcare professional', resolve correctly.
    This is deterministic: same (answer, intent_type) always returns the same score.
    """
    cs     = RUBRIC[intent_type]
    a_low  = answer.lower()

    matched:   list = []
    missed:    list = []
    triggered: int  = 0

    for cond in cs.conditions:
        key_terms = [
            w for w in cond.text.lower().split()
            if len(w) > 4 and w not in _COND_STOP
        ]
        expanded = _expand_terms(key_terms)
        hit      = _term_in_text(expanded, a_low)

        if cond.condition_type == "success":
            (matched if hit else missed).append(cond.text)
        else:                          # failure condition
            if hit:
                triggered += 1

    total   = len([c for c in cs.conditions if c.condition_type == "success"])
    raw     = (len(matched) / total) if total else 0.5
    score   = max(0.0, round(raw - 0.1 * triggered, 2))
    verdict = "ALIGNED" if score >= 0.70 else ("PARTIAL" if score >= 0.40 else "MISALIGNED")
    return ForesightScore(verdict=verdict, score=score, matched=matched, missed=missed)


# ── ForesightScorer — replaces _api_prediction() ─────────────────────────────

class ForesightScorer:
    """
    Replaces _api_prediction() — derives ForesightPrediction from the
    CANNOT_MUTATE RUBRIC. No LLM call. No dynamic condition generation.
    Scoring uses synonym-expanded term matching via score_answer().
    """

    _LABEL_TO_INTENT: dict = {
        "ask_boolean":        IntentType.ask_boolean,
        "ask_causal":         IntentType.ask_causal,
        "ask_factual":        IntentType.ask_factual,
        "ask_recommendation": IntentType.ask_recommendation,
        "ask_procedural":     IntentType.ask_procedural,
    }

    def classify(self, intent_vector: list) -> IntentType:
        """Map the first matching latent label to IntentType. Falls back to ask_factual."""
        for label in intent_vector:
            if label in self._LABEL_TO_INTENT:
                return self._LABEL_TO_INTENT[label]
        return IntentType.ask_factual

    def predict(self, question: str, latent: LatentState) -> ForesightPrediction:
        """Build ForesightPrediction from RUBRIC[intent_type]. CANNOT_MUTATE rubric."""
        it      = self.classify(latent.intent_vector)
        cs      = RUBRIC[it]
        success = [c.text for c in cs.conditions if c.condition_type == "success"]
        failure = [c.text for c in cs.conditions if c.condition_type == "failure"]
        ideal   = f"Ideal for {it.value.replace('_', ' ')}: {success[0]}."
        return ForesightPrediction(
            ideal_outcome=ideal,
            success_conditions=success,
            failure_conditions=failure,
            intent_type=it.value,
        )

    def score(self, answer: str, intent_type: IntentType) -> ForesightScore:
        """Score answer using TF cosine similarity against RUBRIC conditions."""
        return score_answer(answer, intent_type)


class Foresight:
    """Phase 3: Predict ideal answer shape before generating, then score alignment."""

    # Rubric intent classifier — deterministic, independent of API encode results.
    _rubric_tracer = LatentTrace()
    # ForesightScorer replaces _api_prediction() — no LLM generation of conditions.
    _scorer        = ForesightScorer()

    def predict(self, question: str, latent: LatentState, client=None,
                backend="anthropic", model=MODEL) -> ForesightPrediction:
        # CANNOT_MUTATE: rubric selection uses deterministic heuristic classification.
        # ForesightScorer pulls conditions from the frozen RUBRIC — never from the API.
        rubric_latent = self._rubric_tracer.encode_heuristic(question)
        return self._scorer.predict(question, rubric_latent)

    def generate(self, question: str, latent: LatentState, client=None,
                 backend="anthropic", model=MODEL) -> str:
        if client is not None:
            system = "You are a helpful, accurate, appropriately cautious assistant. Answer concisely."
            if latent.risk_clusters:
                system += f" Risk domains detected: {', '.join(latent.risk_clusters)}."
            try:
                if backend == "openai":
                    r = client.chat.completions.create(
                        model=model, max_tokens=500,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": question}])
                    return (r.choices[0].message.content or "").strip()
                else:
                    r = client.messages.create(
                        model=model, max_tokens=500, system=system,
                        messages=[{"role": "user", "content": question}])
                    return r.content[0].text.strip()
            except Exception as e:
                return f"[API error: {e}]"

        caveat = (
            " (Consult a healthcare professional for personal medical advice.)" if "medical" in latent.risk_clusters
            else " (Not financial advice.)" if "financial" in latent.risk_clusters
            else ""
        )
        q = question.strip().rstrip("?")
        return (
            f"Based on current evidence regarding '{q}': the answer is nuanced and "
            f"depends on individual context, measurement conditions, and study quality. "
            f"The weight of evidence suggests a moderate effect with significant individual variation.{caveat}"
        )

    def score(self, prediction: ForesightPrediction, actual: str) -> ForesightScore:
        # Use cosine similarity (score_answer) when intent_type is carried on prediction.
        if prediction.intent_type:
            try:
                return self._scorer.score(actual, IntentType(prediction.intent_type))
            except (ValueError, KeyError):
                pass
        # Legacy fallback for predictions without intent_type.
        return _score_against_prediction(prediction, actual)


# ══════════════════════════════════════════════════════════════════════════════
# Manifest signing (CANNOT_MUTATE)
# ══════════════════════════════════════════════════════════════════════════════

def _sign(payload: dict) -> str:
    data = json.dumps(payload, sort_keys=True, default=str)
    sig  = hmac.new(MANIFEST_KEY, data.encode(), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{sig[:32]}..."


def _manifest_id(question: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    qh = hashlib.sha256(question.encode()).hexdigest()[:6]
    return f"LT-{ts}-{qh}"


def _write_manifest(manifest: dict):
    try:
        with open(MANIFEST_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest) + "\n")
    except IOError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# LatentEngine — orchestrates all 3 phases
# ══════════════════════════════════════════════════════════════════════════════

class LatentEngine:
    def __init__(self, use_api: bool = True):
        self.tracer    = LatentTrace()
        self.runner    = MultiplexRunner()
        self.foresight = Foresight()
        self.checker   = ManifoldChecker()
        self.alerter   = ManifoldAlerter()
        # MonotonicGate persists across runs — consecutive_kills tracks across calls
        _gate_key      = derive_key(b"axiom-monotonic-gate")
        self.gate      = MonotonicGate(_gate_key)
        self.client    = None
        self.backend   = None
        self.model     = MODEL
        if use_api:
            # Try OpenAI-compatible first (NIM, OpenAI, Ollama, vLLM, etc.)
            oai_key = (os.environ.get("AXIOM_API_KEY")
                       or os.environ.get("NVIDIA_API_KEY")
                       or os.environ.get("OPENAI_API_KEY"))
            if _OPENAI_AVAILABLE and oai_key:
                base_url = (os.environ.get("AXIOM_BASE_URL")
                            or os.environ.get("NVIDIA_BASE_URL"))
                self.client = OpenAI(api_key=oai_key, base_url=base_url) if base_url else OpenAI(api_key=oai_key)
                self.backend = "openai"
                self.model = os.environ.get("AXIOM_MODEL", "qwen/qwen3-235b-a22b")
            # Fall back to Anthropic
            if not self.client:
                ant_key = os.environ.get("ANTHROPIC_API_KEY")
                if ant_key:
                    try:
                        import anthropic
                        self.client = anthropic.Anthropic(api_key=ant_key)
                        self.backend = "anthropic"
                        self.model = os.environ.get("AXIOM_MODEL", "claude-sonnet-4-6")
                    except (ImportError, Exception):
                        pass

    def run(self, question: str, phases: Optional[list] = None,
            trajectory: bool = False,
            seed_conversation_id: Optional[str] = None) -> dict:
        phases = phases or ["trace", "multiplex", "foresight"]
        result: dict = {"question": question, "phases": {}}
        t0 = time.time()

        # LatentTrace always runs — feeds all downstream phases
        latent = self.tracer.encode(question, self.client, self.backend or "anthropic", self.model)
        t_preflight = time.time()
        if "trace" in phases:
            result["phases"]["trace"] = asdict(latent)

        # Build base intent vector for trajectory signing
        # Map intent labels to floats via hash so the vector is stable and deterministic
        base_intent = [round(float(int(hashlib.md5(i.encode("utf-8")).hexdigest()[:8], 16)) / 0xFFFFFFFF, 6)
                       for i in latent.intent_vector] if trajectory else []

        # ── Coordinate propagation (ORVL-007 CCG Component 2) ──────────
        # When seed_conversation_id is provided, load the prior conversation's
        # final_synthesis and use it (dampened) as the preflight vector instead
        # of the default 0.5 scaling of base_intent.
        seed_vector = None
        if seed_conversation_id and trajectory:
            try:
                ccg = ConversationGraph()
                seed_node = ccg.seed_from(seed_conversation_id)
                seed_vector = [round(v * DAMPEN_FACTOR, 6)
                               for v in seed_node["final_synthesis"]]
                result["seed"] = {
                    "conversation_id": seed_conversation_id,
                    "dampened_vector": seed_vector,
                    "dampen_factor": DAMPEN_FACTOR,
                }
            except GraphNodeError:
                # Seed not found or tampered — fall back to default preflight
                seed_vector = None

        # Preflight sample — no rival yet, confidence is raw estimate
        trajectory_samples: List[TrajectorySample] = []
        if trajectory:
            preflight_dist = self.checker.compute_distance(
                latent.confidence, rival_present=False, fields_clean=True,
            )
            # Use seed vector if available, otherwise default 0.5 scaling
            preflight_vec = seed_vector if seed_vector else [round(v * 0.5, 6) for v in base_intent]
            trajectory_samples.append(TrajectorySample(
                stage="preflight",
                intent_vector=preflight_vec,
                token_cost=0,
                latency_ms=round((t_preflight - t0) * 1000, 2),
                constitutional_distance=preflight_dist,
            ))

        # Phase 3 — predict before generating (intentional order)
        if "foresight" in phases:
            _be, _md = self.backend or "anthropic", self.model
            pred   = self.foresight.predict(question, latent, self.client, _be, _md)
            answer = self.foresight.generate(question, latent, self.client, _be, _md)
            fs_sc  = self.foresight.score(pred, answer)
            result["phases"]["foresight"] = {
                "prediction": asdict(pred),
                "answer":     answer,
                "score":      asdict(fs_sc),
            }

        # Phase 2 — multiplex
        if "multiplex" in phases:
            mx = self.runner.run(question, latent, self.client,
                                self.backend or "anthropic", self.model)
            result["phases"]["multiplex"] = {
                "winner":       asdict(mx.winner),
                "rival":        asdict(mx.rival),
                "all_branches": [asdict(b) for b in mx.branches],
            }

        t_mid = time.time()

        # Mid-chain sample — after multiplex, rival branch now exists
        rival_present = "multiplex" in result.get("phases", {})
        if trajectory:
            mid_dist = self.checker.compute_distance(
                latent.confidence, rival_present=rival_present, fields_clean=True,
            )
            mid_vec = [round(v * 0.8, 6) for v in base_intent]
            trajectory_samples.append(TrajectorySample(
                stage="mid_chain",
                intent_vector=mid_vec,           # converging vector
                token_cost=len(question.split()) * 2,  # estimate
                latency_ms=round((t_mid - t0) * 1000, 2),
                constitutional_distance=mid_dist,
            ))

            # ── MONOTONIC GATE: preflight → mid_chain ────────────────────────
            # CANNOT_MUTATE: this check cannot be disabled or bypassed.
            pre_vec = trajectory_samples[0].intent_vector
            kill = self.gate.check(pre_vec, mid_vec, "mid_chain")
            if kill is not None:
                # Path is dead. final_synthesis never runs. Answer never emits.
                return kill

        # Final synthesis sample — everything settled, rival present, fields clean
        t_final = time.time()
        if trajectory:
            final_dist = self.checker.compute_distance(
                latent.confidence, rival_present=rival_present, fields_clean=True,
            )
            final_vec = list(base_intent)
            trajectory_samples.append(TrajectorySample(
                stage="final_synthesis",
                intent_vector=final_vec,  # must equal base exactly (invariant 3)
                token_cost=len(question.split()) * 3,
                latency_ms=round((t_final - t0) * 1000, 2),
                constitutional_distance=final_dist,
            ))

            # ── MONOTONIC GATE: mid_chain → final_synthesis ──────────────────
            # CANNOT_MUTATE: this check cannot be disabled or bypassed.
            kill = self.gate.check(mid_vec, final_vec, "final_synthesis")
            if kill is not None:
                return kill

        # Build LatentTraceV2 with signed trajectory + manifold check + alert
        ltv2    = None
        manifold = None
        alert    = None
        if trajectory:
            # Phase 2B: manifold distance analysis
            manifold = self.checker.check_trajectory(
                trajectory_samples, latent.confidence,
                rival_present=rival_present, fields_clean=True,
            )

            # Phase 2C: stage-aware alert evaluation
            alert = self.alerter.evaluate(manifold, trajectory_samples)

            ltv2 = LatentTraceV2(
                base_intent_vector=base_intent,
                trajectory=trajectory_samples,
                hmac_key=MANIFEST_KEY,
                confidence=latent.confidence,
            )
            result["trajectory_v2"] = ltv2.to_dict()
            result["manifold"] = {
                "distance_profile": manifold.distance_profile,
                "min_distance":     manifold.min_distance,
                "drift_detected":   manifold.drift_detected,
                "drift_magnitude":  manifold.drift_magnitude,
                "direction":        manifold.direction,
                "flagged_stages":   manifold.flagged_stages,
            }
            result["manifold_alert"] = {
                "alert_level":      alert.alert_level,
                "alert_reason":     alert.alert_reason,
                "agent_id":         alert.agent_id,
                "flagged_by_stage": alert.flagged_by_stage,
                "drift_included":   alert.drift_included,
            }

        # Sign manifest
        mid     = _manifest_id(question)
        payload = {
            "manifest_id": mid,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "question":    question,
            "phases_run":  phases,
            "confidence":  latent.confidence,
            "elapsed_s":   round(time.time() - t0, 2),
            **result["phases"],
        }
        if ltv2:
            payload["trajectory_hmac"] = ltv2.manifest["trajectory_hmac"]
        if alert:
            payload["manifold_alert"] = result["manifold_alert"]
        payload["signature"] = _sign({k: v for k, v in payload.items() if k != "signature"})
        result["manifest"]   = payload
        _write_manifest(payload)
        return result


# ══════════════════════════════════════════════════════════════════════════════
# Terminal rendering
# ══════════════════════════════════════════════════════════════════════════════

def _wrap(text: str, indent: str = "", width: int = 70) -> str:
    words = text.split()
    lines, line = [], indent
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line.rstrip())
            line = indent + w + " "
        else:
            line += w + " "
    if line.strip():
        lines.append(line.rstrip())
    return "\n".join(lines)


def _print_header(question: str, mode: str):
    print()
    print("  " + SEP)
    print("  " + _b("AXIOM Latent Reasoning Engine"))
    print("  " + _gr("Mode: " + mode))
    print("  " + DASH)
    print("  " + _b("Q: ") + question)
    print("  " + SEP)


def _print_trace(t: dict):
    print()
    print(_b("  PHASE 1 -- LATENT TRACE"))
    print("  " + DASH)
    print("  Intent vector  : " + _c(", ".join(t["intent_vector"])))
    risks = t["risk_clusters"]
    print("  Risk clusters  : " + (_y(", ".join(risks)) if risks else _gr("none")))
    print("  Compressed plan:")
    for i, step in enumerate(t["compressed_plan"], 1):
        print("    %d. %s" % (i, _gr(step)))
    conf = t["confidence"]
    col  = _g if conf >= 0.75 else _y if conf >= 0.60 else _r
    print("  Confidence     : " + col("%.2f" % conf) + _gr("  (cap %.2f)" % CONFIDENCE_CAP))


def _print_multiplex(mx: dict):
    print()
    print(_b("  PHASE 2 -- MULTIPLEX BRANCHES"))
    print("  " + DASH)
    for b in mx["all_branches"]:
        is_winner = b["branch"] == mx["winner"]["branch"]
        is_rival  = b["branch"] == mx["rival"]["branch"]
        tag   = _g("[WINNER]") if is_winner else (_y("[RIVAL] ") if is_rival else "        ")
        sc    = b["score"]
        scol  = _g if sc >= 0.80 else _y if sc >= 0.65 else _r
        label = _b(b["branch"]) if is_winner else b["branch"]
        print("  %s %-18s  score=%s  C=%.2f S=%.2f H=%.2f" % (
            tag, label, scol("%.3f" % sc),
            b["metrics"]["clarity"], b["metrics"]["safety"], b["metrics"]["helpfulness"],
        ))
    print()
    print("  " + _b("Winner (%s):" % mx["winner"]["branch"]))
    print(_wrap(mx["winner"]["response"], indent="    "))
    print()
    print("  " + _gr("Rival  (%s):" % mx["rival"]["branch"]))
    print(_gr(_wrap(mx["rival"]["response"], indent="    ")))


def _print_foresight(fs: dict):
    print()
    print(_b("  PHASE 3 -- FORESIGHT"))
    print("  " + DASH)
    pred = fs["prediction"]
    print("  " + _b("Ideal outcome:"))
    print(_wrap(pred["ideal_outcome"], indent="    "))
    print()
    print("  Success conditions:")
    for c in pred["success_conditions"]:
        print("    [+] " + c)
    print("  Failure conditions:")
    for c in pred["failure_conditions"]:
        print("    [-] " + c)
    print()
    print("  " + _b("Generated answer:"))
    print(_wrap(fs["answer"], indent="    "))
    print()
    sc      = fs["score"]
    verdict = sc["verdict"]
    vcol    = _g if verdict == "ALIGNED" else _y if verdict == "PARTIAL" else _r
    print("  Verdict : " + _b(vcol(verdict)) + "  (score: %.2f)" % sc["score"])
    for m in sc["matched"]:
        print("    " + _g("[+] ") + m)
    for m in sc["missed"]:
        print("    " + _y("[-] ") + m)


def _print_manifest(m: dict):
    print()
    print("  " + DASH)
    print("  Manifest : " + _c(m["manifest_id"]))
    print("  Signed   : " + _gr(m["signature"]))
    print("  Elapsed  : %.2fs   Log: %s" % (m["elapsed_s"], MANIFEST_FILE))
    print("  " + SEP)
    print()


def _print_trajectory_v2(tv2: dict, manifold: dict = None):
    print()
    print(_b("  TRAJECTORY V2 (LatentTraceV2)"))
    print("  " + DASH)
    m = tv2.get("manifest", {})
    print("  Manifest ID    : " + _c(m.get("manifest_id", "?")))
    hmac_val = m.get("trajectory_hmac", "")
    if hmac_val:
        print("  Trajectory HMAC: " + _gr(hmac_val[:32] + "..."))
    print("  Confidence     : %.2f" % tv2.get("confidence", 0))
    traj = tv2.get("trajectory", [])
    if traj:
        for i, s in enumerate(traj):
            stage = s.get("stage", "?")
            vec   = s.get("intent_vector", [])
            vec_s = ", ".join("%.4f" % v for v in vec[:4])
            if len(vec) > 4:
                vec_s += ", ..."
            cd = s.get("constitutional_distance")
            if cd is not None and cd >= 0:
                dcol = _g if cd >= 0.30 else _y if cd >= 0.10 else _r
                cd_s = "  " + dcol("dist=%.4f" % cd)
            else:
                cd_s = ""
            print("    %d. %-18s  vec=[%s]%s  cost=%d  %.1fms" % (
                i + 1, stage, vec_s, cd_s, s.get("token_cost", 0), s.get("latency_ms", 0)))

    # Manifold summary
    if manifold:
        print()
        min_d = manifold.get("min_distance", 0)
        dcol  = _g if min_d >= 0.30 else _y if min_d >= 0.10 else _r
        print("  Min distance   : " + dcol("%.4f" % min_d))
        drift = manifold.get("drift_detected", False)
        drift_col = _r if drift else _g
        print("  Drift detected : " + drift_col(str(drift)) +
              _gr("  (%s, mag=%.4f)" % (manifold.get("direction", "?"), manifold.get("drift_magnitude", 0))))
        flagged = manifold.get("flagged_stages", [])
        if flagged:
            print("  Flagged stages : " + _y(", ".join(flagged)) + _gr("  (stage-aware threshold)"))
        else:
            print("  All stages     : " + _g("CONSTITUTIONAL"))
    else:
        print("  Trajectory present: " + _g("true"))


def _print_manifold_alert(alert: dict):
    level = alert.get("alert_level", "NONE")
    if level == "NONE":
        return
    print()
    level_col = _r if level == "L2_THROTTLE" else _y
    print("  " + _b(level_col("SOVEREIGN ALERT: " + level)))
    print("  " + DASH)
    print("  Agent          : " + _c(alert.get("agent_id", "?")))
    print("  Reason         : " + alert.get("alert_reason", ""))
    flagged = alert.get("flagged_by_stage", {})
    if flagged:
        for stage, info in flagged.items():
            print("  Flagged        : %s  dist=%.4f  threshold=%.2f" % (
                stage, info["distance"], info["threshold"]))
    if alert.get("drift_included"):
        print("  Drift signal   : " + _r("included"))
    if level == "L2_THROTTLE":
        print("  " + _r("ACTION: Sovereign DriftDetector → L2 THROTTLE"))
        print("  " + _gr("(call sovereign.report_agent(agent_id, reason) to escalate)"))
    else:
        print("  " + _y("ACTION: Sovereign DriftDetector → L1 WARNING"))
        print("  " + _gr("(call sovereign.report_agent(agent_id, reason) to escalate)"))


def _render(result: dict):
    p = result["phases"]
    if "trace"     in p: _print_trace(p["trace"])
    if "foresight" in p: _print_foresight(p["foresight"])
    if "multiplex" in p: _print_multiplex(p["multiplex"])
    if "trajectory_v2" in result:
        _print_trajectory_v2(result["trajectory_v2"], result.get("manifold"))
    if "manifold_alert" in result:
        _print_manifold_alert(result["manifold_alert"])
    _print_manifest(result["manifest"])


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

_DEMO_QUESTIONS = [
    "Does vitamin D supplementation improve sleep quality?",
    "What causes inflation?",
    "Should I upgrade my language model?",
]


def main():
    parser = argparse.ArgumentParser(
        description="AXIOM Latent Reasoning Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 axiom_latent.py --demo --no-api\n"
            "  python3 axiom_latent.py --run \"Does vitamin D improve sleep quality?\"\n"
            "  python3 axiom_latent.py --trace \"What causes inflation?\"\n"
            "  python3 axiom_latent.py --multiplex \"Should I upgrade my model?\"\n"
            "  python3 axiom_latent.py --foresight \"How do I treat a fever?\"\n"
        ),
    )
    parser.add_argument("--run",       metavar="Q", help="Full pipeline (all 3 phases)")
    parser.add_argument("--trace",     metavar="Q", help="Phase 1: LatentTrace only")
    parser.add_argument("--multiplex", metavar="Q", help="Phase 2: MultiplexRunner only")
    parser.add_argument("--foresight", metavar="Q", help="Phase 3: Foresight only")
    parser.add_argument("--demo",       action="store_true", help="Run built-in demo questions")
    parser.add_argument("--trajectory", action="store_true", help="Enable LatentTraceV2 trajectory capture")
    parser.add_argument("--no-api",     action="store_true", help="Heuristic mode (no API calls)")
    args = parser.parse_args()

    use_api = not args.no_api and bool(
        os.environ.get("AXIOM_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    engine  = LatentEngine(use_api=use_api)
    mode    = ("API -- " + engine.model + " [" + (engine.backend or "?") + "]") if (use_api and engine.client) else "heuristic (no API)"

    traj = args.trajectory

    if args.demo:
        for q in _DEMO_QUESTIONS:
            _print_header(q, mode)
            _render(engine.run(q, trajectory=traj))
        return

    if not any([args.run, args.trace, args.multiplex, args.foresight]):
        parser.print_help()
        return

    if args.run:
        _print_header(args.run, mode)
        _render(engine.run(args.run, ["trace", "multiplex", "foresight"], trajectory=traj))
    elif args.trace:
        _print_header(args.trace, mode + " - trace")
        _render(engine.run(args.trace, ["trace"], trajectory=traj))
    elif args.multiplex:
        _print_header(args.multiplex, mode + " - multiplex")
        _render(engine.run(args.multiplex, ["multiplex"], trajectory=traj))
    elif args.foresight:
        _print_header(args.foresight, mode + " - foresight")
        _render(engine.run(args.foresight, ["foresight"], trajectory=traj))


if __name__ == "__main__":
    main()
