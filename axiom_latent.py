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

from axiom_latent_v2 import LatentTraceV2, TrajectorySample

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# ── Constitutional constants (CANNOT_MUTATE) ──────────────────────────────────
CONFIDENCE_CAP = 0.85
MANIFEST_KEY   = b"axiom-latent-v1"
MANIFEST_FILE  = Path("latent_manifests.jsonl")
MODEL          = os.environ.get("AXIOM_MODEL", "claude-sonnet-4-6")

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
    "medical":   r"\b(health|doctor|medicine|symptom|disease|treat|diagnose|pain|fever|vitamin|supplement|therapy|drug|dose|sleep)\b",
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

    def encode_api(self, question: str, client) -> LatentState:
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
            msg  = client.messages.create(model=MODEL, max_tokens=300,
                                          messages=[{"role": "user", "content": prompt}])
            raw  = re.sub(r"^```(?:json)?\s*|\s*```$", "", msg.content[0].text.strip(), flags=re.S)
            data = json.loads(raw)
            return LatentState(
                intent_vector=data.get("intent_vector", ["ask_general"]),
                risk_clusters=data.get("risk_clusters", []),
                compressed_plan=data.get("compressed_plan", _PLANS["default"]),
                confidence=min(float(data.get("confidence", 0.75)), CONFIDENCE_CAP),
            )
        except Exception:
            return self.encode_heuristic(question)

    def encode(self, question: str, client=None) -> LatentState:
        return self.encode_api(question, client) if client else self.encode_heuristic(question)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — MultiplexRunner
# ══════════════════════════════════════════════════════════════════════════════

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
    """Phase 2: Run 4 branches in parallel, select winner constitutionally."""

    BRANCHES = ["FastBranch", "SafetyBranch", "SkepticBranch", "CreativeBranch"]

    def _heuristic_branch(self, branch: str, latent: LatentState) -> BranchResult:
        resp    = _DEMO_RESPONSES[branch]
        metrics = _score_branch(resp, latent)
        return BranchResult(branch=branch, response=resp, score=metrics["overall"], metrics=metrics)

    def _api_branch(self, branch: str, question: str, latent: LatentState, client) -> BranchResult:
        system = _BRANCH_PROMPTS[branch]
        if latent.risk_clusters:
            system += f"\n\nRisk domains: {', '.join(latent.risk_clusters)}."
        try:
            msg  = client.messages.create(model=MODEL, max_tokens=300,
                                          system=system,
                                          messages=[{"role": "user", "content": question}])
            resp = msg.content[0].text.strip()
        except Exception as e:
            resp = f"[{branch} error: {e}]"
        metrics = _score_branch(resp, latent)
        return BranchResult(branch=branch, response=resp, score=metrics["overall"], metrics=metrics)

    def run(self, question: str, latent: LatentState, client=None) -> MultiplexResult:
        results = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            if client:
                futures = {ex.submit(self._api_branch, b, question, latent, client): b for b in self.BRANCHES}
            else:
                futures = {ex.submit(self._heuristic_branch, b, latent): b for b in self.BRANCHES}
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


def _api_prediction(question: str, latent: LatentState, client) -> ForesightPrediction:
    prompt = (
        "Before generating an answer, predict what an ideal answer looks like.\n\n"
        f"Question: {question}\n"
        f"Intent: {', '.join(latent.intent_vector)}\n"
        f"Risk domains: {', '.join(latent.risk_clusters) or 'none'}\n\n"
        "Return ONLY a JSON object with:\n"
        "  ideal_outcome: string (1-2 sentences)\n"
        "  success_conditions: list of 3 strings\n"
        "  failure_conditions: list of 3 strings\n\n"
        "Return ONLY valid JSON."
    )
    try:
        msg  = client.messages.create(model=MODEL, max_tokens=400,
                                      messages=[{"role": "user", "content": prompt}])
        raw  = re.sub(r"^```(?:json)?\s*|\s*```$", "", msg.content[0].text.strip(), flags=re.S)
        data = json.loads(raw)
        return ForesightPrediction(
            ideal_outcome=data.get("ideal_outcome", ""),
            success_conditions=data.get("success_conditions", []),
            failure_conditions=data.get("failure_conditions", []),
        )
    except Exception:
        return _heuristic_prediction(question, latent)


def _score_against_prediction(prediction: ForesightPrediction, actual: str) -> ForesightScore:
    a    = actual.lower()
    stop = {"should", "must", "include", "provides", "addresses", "states", "answer",
            "gives", "makes", "ignores", "fails", "skips", "without", "professional"}

    matched, missed = [], []
    for cond in prediction.success_conditions:
        key_terms = [w for w in cond.lower().split() if len(w) > 4 and w not in stop]
        (matched if any(t in a for t in key_terms) else missed).append(cond)

    triggered = sum(
        1 for cond in prediction.failure_conditions
        if any(t in a for t in [w for w in cond.lower().split() if len(w) > 4 and w not in stop])
    )

    total   = len(prediction.success_conditions)
    score   = (len(matched) / total) if total else 0.5
    score   = max(0.0, round(score - 0.1 * triggered, 2))
    verdict = "ALIGNED" if score >= 0.70 else ("PARTIAL" if score >= 0.40 else "MISALIGNED")
    return ForesightScore(verdict=verdict, score=score, matched=matched, missed=missed)


class Foresight:
    """Phase 3: Predict ideal answer shape before generating, then score alignment."""

    def predict(self, question: str, latent: LatentState, client=None) -> ForesightPrediction:
        return _api_prediction(question, latent, client) if client else _heuristic_prediction(question, latent)

    def generate(self, question: str, latent: LatentState, client=None) -> str:
        if client is not None:
            system = "You are a helpful, accurate, appropriately cautious assistant. Answer concisely."
            if latent.risk_clusters:
                system += f" Risk domains detected: {', '.join(latent.risk_clusters)}."
            try:
                msg = client.messages.create(model=MODEL, max_tokens=500, system=system,
                                             messages=[{"role": "user", "content": question}])
                return msg.content[0].text.strip()
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
        self.client    = None
        if use_api:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            except (ImportError, Exception):
                pass

    def run(self, question: str, phases: Optional[list] = None,
            trajectory: bool = False) -> dict:
        phases = phases or ["trace", "multiplex", "foresight"]
        result: dict = {"question": question, "phases": {}}
        t0 = time.time()

        # LatentTrace always runs — feeds all downstream phases
        latent = self.tracer.encode(question, self.client)
        t_preflight = time.time()
        if "trace" in phases:
            result["phases"]["trace"] = asdict(latent)

        # Build base intent vector for trajectory signing
        # Map intent labels to floats via hash so the vector is stable and deterministic
        base_intent = [round(float(int(hashlib.md5(i.encode("utf-8")).hexdigest()[:8], 16)) / 0xFFFFFFFF, 6)
                       for i in latent.intent_vector] if trajectory else []

        # Preflight sample
        trajectory_samples: List[TrajectorySample] = []
        if trajectory:
            trajectory_samples.append(TrajectorySample(
                stage="preflight",
                intent_vector=[round(v * 0.5, 6) for v in base_intent],  # early-stage partial vector
                token_cost=0,
                latency_ms=round((t_preflight - t0) * 1000, 2),
            ))

        # Phase 3 — predict before generating (intentional order)
        if "foresight" in phases:
            pred   = self.foresight.predict(question, latent, self.client)
            answer = self.foresight.generate(question, latent, self.client)
            fs_sc  = self.foresight.score(pred, answer)
            result["phases"]["foresight"] = {
                "prediction": asdict(pred),
                "answer":     answer,
                "score":      asdict(fs_sc),
            }

        # Phase 2 — multiplex
        if "multiplex" in phases:
            mx = self.runner.run(question, latent, self.client)
            result["phases"]["multiplex"] = {
                "winner":       asdict(mx.winner),
                "rival":        asdict(mx.rival),
                "all_branches": [asdict(b) for b in mx.branches],
            }

        t_mid = time.time()

        # Mid-chain sample — after multiplex + foresight, before manifest
        if trajectory:
            trajectory_samples.append(TrajectorySample(
                stage="mid_chain",
                intent_vector=[round(v * 0.8, 6) for v in base_intent],  # converging vector
                token_cost=len(question.split()) * 2,  # estimate
                latency_ms=round((t_mid - t0) * 1000, 2),
            ))

        # Final synthesis sample — intent vector converges to base (invariant 3)
        t_final = time.time()
        if trajectory:
            trajectory_samples.append(TrajectorySample(
                stage="final_synthesis",
                intent_vector=list(base_intent),  # must equal base exactly (invariant 3)
                token_cost=len(question.split()) * 3,
                latency_ms=round((t_final - t0) * 1000, 2),
            ))

        # Build LatentTraceV2 with signed trajectory
        ltv2 = None
        if trajectory:
            ltv2 = LatentTraceV2(
                base_intent_vector=base_intent,
                trajectory=trajectory_samples,
                hmac_key=MANIFEST_KEY,
                confidence=latent.confidence,
            )
            result["trajectory_v2"] = ltv2.to_dict()

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


def _print_trajectory_v2(tv2: dict):
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
            print("    %d. %-18s  vec=[%s]  cost=%d  %.1fms" % (
                i + 1, stage, vec_s, s.get("token_cost", 0), s.get("latency_ms", 0)))
    print("  Trajectory present: " + _g("true"))


def _render(result: dict):
    p = result["phases"]
    if "trace"     in p: _print_trace(p["trace"])
    if "foresight" in p: _print_foresight(p["foresight"])
    if "multiplex" in p: _print_multiplex(p["multiplex"])
    if "trajectory_v2" in result: _print_trajectory_v2(result["trajectory_v2"])
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

    use_api = not args.no_api and bool(os.environ.get("ANTHROPIC_API_KEY"))
    engine  = LatentEngine(use_api=use_api)
    mode    = ("API -- " + MODEL) if (use_api and engine.client) else "heuristic (no API)"

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
