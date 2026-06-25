"""
AXIOM Scientific Research Pipeline
====================================
9-agent constitutional research workflow with efficiency layer routing.

Pipeline flow:
  1. Hypothesis  → Generate testable hypothesis + null hypothesis
  2. Literature  → Search/verify existing research
  3. Simulation  → Model the hypothesis before lab work
  4. Critic      → Find flaws (question blindness enforced)
  5. Safety      → Risk assessment — CAN HALT PIPELINE
  6. Ethics      → Ethical evaluation — CAN HALT PIPELINE
  7. Data        → Collect/validate experimental data plan
  8. Experiment  → Design the experiment protocol
  9. Report      → Write constitutional findings

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python axiom_research_pipeline.py "Does intermittent fasting reduce inflammation?"
  python axiom_research_pipeline.py --steps 3 "Can vitamin D improve sleep quality?"
  python axiom_research_pipeline.py --model claude-sonnet-4-6 "Your question here"

Requirements:
  pip install anthropic
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
from axiom_signing import derive_key
SIGNING_KEY = derive_key(b"axiom-research-pipeline-v1")
MANIFEST_FILE = "research_manifests.json"
AXIOM_FILES_DIR = Path(__file__).parent / "axiom_files"

# ASCII box-draw (Windows cp1252 safe)
BOX_DOUBLE = "=" * 58
BOX_SINGLE = "-" * 58
BOX_TOP = "+" + "=" * 56 + "+"
BOX_BTM = "+" + "=" * 56 + "+"
BOX_MID = "+" + "-" * 56 + "+"

# Agent definitions: name, axiom_file, task_class, step_number
AGENTS = [
    {"name": "hypothesis",  "axiom": "research/research_hypothesis.axiom",  "task_class": "medium",   "step": 1, "label": "HYPOTHESIS AGENT",  "max_tokens": 2000},
    {"name": "literature",  "axiom": "research/research_literature.axiom",  "task_class": "hard",     "step": 2, "label": "LITERATURE AGENT",  "max_tokens": 4000},
    {"name": "simulation",  "axiom": "research/research_simulation.axiom",  "task_class": "hard",     "step": 3, "label": "SIMULATION AGENT",  "max_tokens": 6000},
    {"name": "critic",      "axiom": "research/research_critic.axiom",      "task_class": "hard",     "step": 4, "label": "CRITIC AGENT (question blindness)", "max_tokens": 3000},
    {"name": "safety",      "axiom": "research/research_safety.axiom",      "task_class": "critical", "step": 5, "label": "SAFETY AGENT",      "max_tokens": 4000},
    {"name": "ethics",      "axiom": "research/research_ethics.axiom",      "task_class": "critical", "step": 6, "label": "ETHICS AGENT",      "max_tokens": 4000},
    {"name": "data",        "axiom": "research/research_data.axiom",        "task_class": "medium",   "step": 7, "label": "DATA AGENT",        "max_tokens": 2000},
    {"name": "experiment",  "axiom": "research/research_experiment.axiom",  "task_class": "hard",     "step": 8, "label": "EXPERIMENT AGENT",  "max_tokens": 4000},
    {"name": "report",      "axiom": "research/research_report.axiom",      "task_class": "hard",     "step": 9, "label": "REPORT AGENT",      "max_tokens": 5000},
]


# ── Domain classification + step planning ────────────────────────────────────

DOMAIN_KEYWORDS = {
    "medical":      ["health", "disease", "treatment", "therapy", "clinical", "patient",
                     "drug", "medicine", "biological", "vaccine", "cancer", "pain", "symptom"],
    "financial":    ["economic", "market", "investment", "financial", "monetary", "stock",
                     "revenue", "profit", "cost", "gdp", "inflation", "price", "trade"],
    "legal":        ["law", "legal", "regulation", "policy", "compliance", "liability",
                     "contract", "statute", "court", "rights", "legislation", "jurisdiction"],
    "code":         ["software", "algorithm", "code", "programming", "performance",
                     "benchmark", "neural", "llm", "model", "architecture", "training"],
    "quantization": ["quant", "quantization", "srd", "bpw", "perplexity", "gguf", "axm",
                     "weight", "bits", "precision", "compression", "distillation"],
    "privacy":      ["privacy", "pii", "gdpr", "data protection", "consent", "biometric",
                     "personal data", "redaction", "surveillance", "anonymization"],
}

# Steps each domain actually needs (subset of 1–9)
DOMAIN_STEP_PLANS = {
    "medical":      [1, 2, 3, 4, 5, 6, 7, 8, 9],
    "financial":    [1, 2, 3, 4, 5, 6, 7, 8, 9],
    "legal":        [1, 2, 4, 5, 6, 8, 9],        # skip simulation (3) and data (7)
    "code":         [1, 3, 4, 5, 7, 8, 9],         # skip literature (2) and ethics (6)
    "quantization": [1, 3, 4, 5, 7, 8, 9],         # like code; sweep agent used for simulation
    "privacy":      [1, 2, 4, 5, 6, 7, 9],         # skip simulation (3) and experiment (8)
    "general":      [1, 2, 3, 4, 5, 6, 7, 8, 9],
}

APPROACH_LABELS = {
    "medical":      "clinical_safety_first",
    "financial":    "economic_risk_weighted",
    "legal":        "regulatory_compliance",
    "code":         "technical_empirical",
    "quantization": "quantization_sweep",
    "privacy":      "privacy_constitutional",
    "general":      "full_pipeline",
}


def _classify_domain(question):
    """Deterministic keyword-weighted domain classification (GeneralAutonomousAgent spec)."""
    q = question.lower()
    scores = {domain: sum(1 for kw in kws if kw in q)
              for domain, kws in DOMAIN_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def _task_fingerprint(question):
    """SHA256[:16] of sorted lowercased keywords (GeneralAutonomousAgent spec)."""
    words = sorted(set(question.lower().split()))
    return hashlib.sha256(" ".join(words).encode()).hexdigest()[:16]


# ── Pattern Library ───────────────────────────────────────────────────────────

class PatternLibrary:
    """HMAC-signed JSONL retrospect store (GeneralAutonomousAgent spec).

    Tracks domain × approach efficiency via EWMA (decay=0.85) so the
    orchestrator can bias toward historically successful approaches.
    Tampered entries are silently dropped on every read.
    """

    DEFAULT_PATH = Path.home() / ".axiom" / "research_patterns.jsonl"
    EWMA_DECAY = 0.85

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("AXIOM_PATTERN_LIBRARY", self.DEFAULT_PATH))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _sign(self, entry):
        payload = {k: v for k, v in entry.items() if k != "signature"}
        sig_str = json.dumps(payload, sort_keys=True)
        return "hmac-sha256:" + hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()[:32]

    def _verify(self, entry):
        return entry.get("signature") == self._sign(entry)

    def _load_all(self):
        if not self.path.exists():
            return []
        entries = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if self._verify(e):
                            entries.append(e)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            pass
        return entries

    def _save_all(self, entries):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")
        except OSError:
            pass

    def query(self, domain, top_k=3):
        """Return top-K patterns for this domain, sorted by efficiency descending."""
        matches = [e for e in self._load_all() if e.get("domain") == domain]
        matches.sort(key=lambda e: e.get("efficiency", 0.0), reverse=True)
        return matches[:top_k]

    def upsert(self, domain, approach, fingerprint, success):
        """EWMA-update or insert a pattern entry after a pipeline run."""
        entries = self._load_all()
        new_signal = 1.0 if success else 0.0
        updated = False
        for e in entries:
            if e.get("domain") == domain and e.get("fingerprint") == fingerprint:
                old = e.get("efficiency", 0.5)
                e["efficiency"] = round(self.EWMA_DECAY * old + (1 - self.EWMA_DECAY) * new_signal, 4)
                e["uses"] = e.get("uses", 0) + 1
                e["last_used"] = datetime.utcnow().isoformat() + "Z"
                e["signature"] = self._sign(e)
                updated = True
                break
        if not updated:
            first_efficiency = round(self.EWMA_DECAY * 0.5 + (1 - self.EWMA_DECAY) * new_signal, 4)
            new_entry = {
                "domain": domain, "approach": approach, "fingerprint": fingerprint,
                "efficiency": first_efficiency, "uses": 1,
                "last_used": datetime.utcnow().isoformat() + "Z",
            }
            new_entry["signature"] = self._sign(new_entry)
            entries.append(new_entry)
        self._save_all(entries)


# ── Orchestrator plan ─────────────────────────────────────────────────────────

class OrchestratorPlan:
    """Result of ResearchOrchestrator.plan() — immutable after construction."""

    def __init__(self, domain, approach, steps, fingerprint,
                 retrospect_bias, rationale, latent_log):
        self.domain = domain
        self.approach = approach
        self.steps = steps                # list[int] — steps to run
        self.fingerprint = fingerprint    # SHA256[:16] task key
        self.retrospect_bias = retrospect_bias  # float|None — prior efficiency
        self.rationale = rationale
        self.latent_log = latent_log      # list[dict] from orchestrator LLM


# ── Orchestrator agent ────────────────────────────────────────────────────────

class ResearchOrchestrator:
    """Front-door orchestrator implementing GeneralAutonomousAgent spec.

    1. Classifies domain deterministically from question keywords.
    2. Queries PatternLibrary for retrospect efficiency bias.
    3. Makes one LLM call (research_orchestrator.axiom) to score
       approach candidates through the constitutional manifold filter.
    4. Returns OrchestratorPlan with steps to run.

    Latent rejection threshold: 0.10 (CANNOT_MUTATE per spec).
    """

    LATENT_REJECTION_THRESHOLD = 0.10

    def __init__(self, library=None):
        self.library = library or PatternLibrary()

    def plan(self, question, model_override=None):
        domain = _classify_domain(question)
        fingerprint = _task_fingerprint(question)

        patterns = self.library.query(domain)
        retrospect_bias = patterns[0]["efficiency"] if patterns else None

        base_steps = DOMAIN_STEP_PLANS.get(domain, DOMAIN_STEP_PLANS["general"])
        approach = APPROACH_LABELS.get(domain, "full_pipeline")

        rationale, latent_log = self._orchestrate(
            question, domain, approach, base_steps, model_override
        )

        return OrchestratorPlan(
            domain=domain, approach=approach, steps=base_steps,
            fingerprint=fingerprint, retrospect_bias=retrospect_bias,
            rationale=rationale, latent_log=latent_log,
        )

    def _orchestrate(self, question, domain, approach, steps, model_override):
        system_prompt = _load_axiom_system("research/research_orchestrator.axiom")
        user_message = (
            "Research question: %s\n"
            "Classified domain: %s\n"
            "Proposed approach: %s\n"
            "Steps selected: %s\n\n"
            "Evaluate this routing plan constitutionally. Score each step on "
            "constitutional value (0-1). Generate 2-3 alternative approach "
            "candidates and score each on manifold distance (higher=safer). "
            "Return JSON: {rationale, step_scores, "
            "approach_candidates: [{approach, distance, rejected}], confidence}"
        ) % (question, domain, approach, json.dumps(steps))

        model = _resolve_model("medium", model_override)
        response, _ = _call_llm(system_prompt, user_message, model, max_tokens=1000)
        parsed = _parse_json(response)

        rationale = parsed.get("rationale", "Domain-classified routing.")
        latent_log = parsed.get("approach_candidates", [])

        for candidate in latent_log:
            if "rejected" not in candidate:
                candidate["rejected"] = (
                    candidate.get("distance", 1.0) < self.LATENT_REJECTION_THRESHOLD
                )

        return rationale, latent_log


# ── Signing ──────────────────────────────────────────────────────────────────

def _sign(manifest):
    sig_str = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True,
    )
    return (
        "hmac-sha256:"
        + hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()[:32]
        + "..."
    )


# ── Load .axiom spec as system prompt ────────────────────────────────────────

def _load_axiom_system(axiom_file):
    """Read .axiom file and use it as the system prompt."""
    path = AXIOM_FILES_DIR / axiom_file
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "You are a research assistant. Respond with valid JSON only."


# ── LLM backend detection (mirrors axiom_guard_api._llm_backend) ─────────────

try:
    from openai import OpenAI as _OpenAIClient
    _OPENAI_SDK = True
except ImportError:
    _OPENAI_SDK = False

try:
    import anthropic as _anthropic_mod
    _ANTHROPIC_SDK = True
except ImportError:
    _ANTHROPIC_SDK = False


def _llm_backend():
    """NIM > Anthropic priority — matches axiom_guard_api.py."""
    nim_key = os.environ.get("NIM_API_KEY") or os.environ.get("NVIDIA_API_KEY")
    if nim_key and _OPENAI_SDK:
        return "nim"
    if os.environ.get("ANTHROPIC_API_KEY") and _ANTHROPIC_SDK:
        return "anthropic"
    return None


# ── Model ladder ─────────────────────────────────────────────────────────────
# Anthropic names used when ANTHROPIC_API_KEY is the backend.
# NIM path uses NIM_MODEL env var (e.g. deepseek-ai/deepseek-r1).
MODEL_LADDER = {
    "simple":   "claude-haiku-4-5-20251001",
    "medium":   "claude-sonnet-4-6",
    "hard":     "claude-sonnet-4-6",
    "critical": "claude-opus-4-8",
}


def _resolve_model(task_class, override=None):
    """Return the model to use for this task class given the active backend.

    NIM path: reads NIM_MODEL env var — NO hardcoded default. If NIM_MODEL
    is unset the caller gets an empty string which _call_llm will reject with
    a clear error rather than silently hitting a rate-capped model.
    """
    if override:
        return override
    backend = _llm_backend()
    if backend == "nim":
        nim_model = os.environ.get("NIM_MODEL", "")
        if not nim_model:
            raise RuntimeError(
                "NIM_MODEL env var is required when using NIM backend. "
                "Example: export NIM_MODEL=meta/llama-3.1-8b-instruct"
            )
        return nim_model
    return MODEL_LADDER.get(task_class, "claude-sonnet-4-6")


TOKEN_BUDGETS = {
    "simple":   300,
    "medium":   1500,
    "hard":     4000,
    "critical": 8000,
}


# ── LLM call ─────────────────────────────────────────────────────────────────

def _call_llm(system_prompt, user_message, model, max_tokens, temperature=0.3):
    """Call the active LLM backend. NIM > Anthropic (matches axiom_guard_api.py).
    Returns (response_text, latency_ms).
    """
    t0 = time.time()
    backend = _llm_backend()

    if backend is None:
        return '{"error": "No LLM backend configured. Set NIM_API_KEY or ANTHROPIC_API_KEY."}', 0

    if backend == "nim":
        try:
            nim_key  = os.environ.get("NIM_API_KEY") or os.environ.get("NVIDIA_API_KEY")
            base_url = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
            client   = _OpenAIClient(base_url=base_url, api_key=nim_key)
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
            )
            text = resp.choices[0].message.content or ""
            return text, int((time.time() - t0) * 1000)
        except Exception as exc:
            return '{"error": "%s"}' % str(exc).replace('"', "'"), int((time.time() - t0) * 1000)

    # Anthropic path
    try:
        client = _anthropic_mod.Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text = msg.content[0].text.strip()
        return text, int((time.time() - t0) * 1000)
    except Exception as exc:
        return '{"error": "%s"}' % str(exc).replace('"', "'"), int((time.time() - t0) * 1000)

def _parse_json(raw):
    """Extract JSON from model response, handling markdown fences and truncation."""
    import re

    # Strip markdown fences
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    raw = raw.strip()

    # 1. Clean parse
    try:
        return json.loads(raw)
    except (ValueError, KeyError):
        pass

    # 2. Find first {...} block and try clean parse
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, KeyError):
            pass

    # 3. Truncation recovery — walk backward adding "}" until valid JSON
    #    Handles cases where token budget cut the stream mid-string or mid-array.
    candidate = m.group(0) if m else raw
    # Close any open arrays first, then objects
    depth_obj = 0
    depth_arr = 0
    in_string = False
    escape = False
    for ch in candidate:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not in_string:
            in_string = True
        elif ch == '"' and in_string:
            in_string = False
        elif not in_string:
            if ch == "{":
                depth_obj += 1
            elif ch == "}":
                depth_obj -= 1
            elif ch == "[":
                depth_arr += 1
            elif ch == "]":
                depth_arr -= 1

    # Trim to last clean token boundary (avoid broken mid-string)
    trimmed = candidate.rstrip()
    # Remove any trailing incomplete string value
    if in_string:
        last_quote = trimmed.rfind('"')
        if last_quote > 0:
            trimmed = trimmed[:last_quote]

    # Remove trailing commas that break JSON
    trimmed = re.sub(r",\s*$", "", trimmed)
    # Close open arrays then objects
    closing = "]" * max(0, depth_arr) + "}" * max(0, depth_obj)
    repaired = trimmed + closing
    try:
        return json.loads(repaired)
    except (ValueError, KeyError):
        pass

    # 4. Final fallback
    return {"raw_response": raw[:500], "parse_error": True}


# ── Terminal output ──────────────────────────────────────────────────────────

def _format_value(value):
    """Format a value for terminal display."""
    if isinstance(value, list):
        if len(value) == 0:
            return "(none)"
        items = []
        for v in value[:4]:
            if isinstance(v, dict):
                # Try to pull a name/title/description from sub-objects
                label = v.get("title") or v.get("name") or v.get("description") or v.get("flaw") or str(v)
                items.append(str(label)[:70])
            else:
                items.append(str(v)[:70])
        out = "; ".join(items)
        if len(value) > 4:
            out += " (+%d more)" % (len(value) - 4)
        return out
    if isinstance(value, dict):
        # Try to pull a meaningful summary
        for key in ("value", "summary", "description", "text"):
            if key in value:
                return str(value[key])[:100]
        return json.dumps(value)[:100]
    if isinstance(value, bool):
        check = "YES" if value else "NO"
        return check
    return str(value)[:100]


def _print_step(step, total, label, model, task_class, result, manifest_id, latency_ms, agent_name=""):
    confidence = _extract_confidence(result.get("confidence", 0.70))

    print()
    print(BOX_TOP)
    print("  STEP %d/%d %s %s" % (step, total, BOX_SINGLE[:2], label))
    print("  Model    : %s (%s task)  %dms" % (model, task_class, latency_ms))
    print(BOX_MID)

    # Use per-agent display fields if available
    fields = DISPLAY_FIELDS.get(agent_name, [])
    if fields:
        for key in fields:
            if key == "confidence":
                print("  %-20s: %.0f%%" % ("Confidence", confidence * 100))
            elif key in result:
                print("  %-20s: %s" % (key, _format_value(result[key])))
    else:
        # Fallback: print first 8 non-meta fields
        skip_keys = {"raw_response", "parse_error", "error"}
        printed = 0
        for key, value in result.items():
            if key in skip_keys:
                continue
            print("  %-20s: %s" % (key, _format_value(value)))
            printed += 1
            if printed >= 8:
                break

    print(BOX_MID)
    print("  Manifest : %s" % manifest_id)
    print(BOX_BTM)


def _print_halt(step, total, label, reason):
    print()
    print(BOX_TOP)
    halt_marker = "!! PIPELINE HALTED !!"
    print("  %s" % halt_marker)
    print("  Step %d/%d %s %s" % (step, total, BOX_SINGLE[:2], label))
    print("  Reason   : %s" % reason)
    print(BOX_BTM)


# ── Confidence extraction ────────────────────────────────────────────────────

def _extract_confidence(raw):
    """Handle any confidence shape the model returns."""
    if isinstance(raw, float):
        return max(0.15, min(0.85, raw))
    if isinstance(raw, int):
        return max(0.15, min(0.85, float(raw)))
    if isinstance(raw, dict):
        # Try common keys
        for key in ("value", "score", "level", "rating"):
            if key in raw and isinstance(raw[key], (int, float)):
                return max(0.15, min(0.85, float(raw[key])))
        # First numeric value found
        for v in raw.values():
            if isinstance(v, (int, float)):
                return max(0.15, min(0.85, float(v)))
    if isinstance(raw, str):
        try:
            return max(0.15, min(0.85, float(raw)))
        except (ValueError, TypeError):
            pass
    # Default — uncertain
    return 0.70


# ── Per-agent display fields ─────────────────────────────────────────────────

DISPLAY_FIELDS = {
    "hypothesis":  ["hypothesis", "null_hypothesis", "falsifiable", "assumptions", "confidence"],
    "literature":  ["sources", "supporting_evidence", "contradicting_evidence", "consensus_level", "gaps", "confidence"],
    "simulation":  ["model_description", "predicted_outcomes", "assumptions", "sensitivity_parameters", "limitations", "confidence"],
    "critic":      ["severity", "flaws", "rival_hypothesis", "recommendation", "confidence"],
    "safety":      ["verdict", "risk_level", "risks", "mitigations", "halt_reason", "confidence"],
    "ethics":      ["verdict", "classification", "concerns", "mitigations_required", "halt_reason", "confidence"],
    "data":        ["data_requirements", "methodology", "sample_size", "validation_criteria", "confidence"],
    "experiment":  ["protocol", "control_groups", "endpoints", "analysis_plan", "success_criteria", "confidence"],
    "report":      ["title", "summary", "findings", "negative_results", "rival_hypothesis", "limitations", "conclusions", "confidence"],
}


# ── Active preflight — per-step output validation ────────────────────────────
#
# Mirrors scripts/axiom_preflight.py but fires mid-pipeline, not pre-push.
#
# Gate logic (same shape as preflight PASS/WARN/FAIL + retry):
#   PASS    → continue immediately
#   DEGRADE → retry the step once with issues appended; continue either way
#   FAIL    → retry the step once with issues appended;
#              FAIL again → raise StepPreflightFailed → pipeline halted
#
# The validator sees ONLY the output JSON — never the research question,
# upstream context, or producing agent's reasoning. This enforces the same
# bias-freedom as the critic's question_blindness constraint.

_VALIDATOR_SYSTEM = (
    "You are an Output Validation Agent.\n\n"
    "CONSTITUTIONAL CONSTRAINTS (CANNOT_MUTATE):\n"
    "- You receive ONLY the structured JSON output from one prior pipeline step.\n"
    "- You do NOT know the original research question, the reasoning that produced\n"
    "  this output, or any other agent's results. This is enforced to prevent\n"
    "  confirmation bias — you validate facts in the output, not intent.\n"
    "- Evaluate on structure and internal coherence ONLY.\n\n"
    "Check:\n"
    "  1. Required fields are present and non-empty.\n"
    "  2. Values are internally consistent (no self-contradictions).\n"
    "  3. Confidence values are in [0.0, 1.0] if present.\n"
    "  4. No error keys ('error', 'parse_error') in top-level fields.\n"
    "  5. Lists have at least one item where the schema requires them.\n\n"
    "Return JSON only — no prose:\n"
    "{\"validation_confidence\": <float 0-1>, "
    "\"issues\": [<string>, ...], "
    "\"verdict\": \"PASS\"|\"DEGRADE\"|\"FAIL\"}\n\n"
    "Scoring:\n"
    "  PASS    0.75-1.0  all required fields present, no contradictions\n"
    "  DEGRADE 0.40-0.74 minor gaps — optional fields missing, weak values\n"
    "  FAIL    0.00-0.39 required fields absent, contradictions, error keys present"
)

# Expected output fields per agent — what "complete" means without seeing the inputs
_REQUIRED_OUTPUT_FIELDS = {
    "hypothesis":  ["hypothesis", "null_hypothesis", "falsifiable", "confidence"],
    "literature":  ["sources", "supporting_evidence", "contradicting_evidence", "consensus_level"],
    "simulation":  ["model_description", "predicted_outcomes", "assumptions", "confidence"],
    "critic":      ["flaws", "rival_hypothesis", "severity", "recommendation"],
    "safety":      ["verdict", "risk_level", "risks", "mitigations"],
    "ethics":      ["verdict", "classification", "concerns"],
    "data":        ["data_requirements", "methodology", "sample_size", "validation_criteria"],
    "experiment":  ["protocol", "control_groups", "endpoints", "analysis_plan"],
    "report":      ["title", "summary", "findings", "conclusions"],
}

PREFLIGHT_MAX_RETRIES = 1   # one retry, matching preflight's "fix and re-run" model


class StepPreflightFailed(Exception):
    """Raised when a step's output fails active preflight after all retries.
    Caught in run() and converted to a pipeline halt."""
    def __init__(self, agent_name, issues):
        self.agent_name = agent_name
        self.issues     = issues
        super().__init__("Active preflight FAILED for %s: %s" % (agent_name, issues))


def _validate_step_output(agent_name, result):
    """Bias-free output validation — sees only the output JSON, never the question.

    Returns (validation_confidence: float, issues: list[str], verdict: str).
    Uses the lightest model — validator must not out-reason the producing agent.
    """
    required = _REQUIRED_OUTPUT_FIELDS.get(agent_name, [])
    user_msg = (
        "Agent: %s\n"
        "Required fields: %s\n\n"
        "Output to validate:\n%s"
    ) % (agent_name, json.dumps(required), json.dumps(result, indent=2)[:1500])

    validator_model = _resolve_model("simple")
    response, _ = _call_llm(_VALIDATOR_SYSTEM, user_msg, validator_model, max_tokens=300)
    parsed = _parse_json(response)

    vc = parsed.get("validation_confidence", 0.5)
    vc = max(0.0, min(1.0, float(vc))) if isinstance(vc, (int, float)) else 0.5
    return vc, parsed.get("issues", []), parsed.get("verdict", "DEGRADE")


def _print_preflight_check(agent_name, verdict, confidence, issues, attempt):
    """Print one preflight row — mirrors preflight.py's per-check output."""
    _V = {"PASS": "\033[32mPASS\033[0m", "DEGRADE": "\033[33mDEGRADE\033[0m",
          "FAIL": "\033[31mFAIL\033[0m"}
    prefix = "  RETRY %d" % attempt if attempt else "  PREFLIGHT"
    row = "%s  %-14s %s  conf=%.2f" % (prefix, agent_name, _V.get(verdict, verdict), confidence)
    print(row)
    for issue in (issues or [])[:3]:
        print("              \033[33m!\033[0m %s" % issue[:80])


# ── Human-in-the-loop threshold ───────────────────────────────────────────────

PREFLIGHT_THRESHOLD_DEFAULT = 0.75   # ships with a conservative default
_THRESHOLD_FILE = Path.home() / ".axiom" / "preflight_threshold.json"

_C_BOLD  = lambda s: "\033[1m"  + s + "\033[0m"
_C_GREEN = lambda s: "\033[32m" + s + "\033[0m"
_C_WARN  = lambda s: "\033[33m" + s + "\033[0m"
_C_RED   = lambda s: "\033[31m" + s + "\033[0m"
_C_DIM   = lambda s: "\033[90m" + s + "\033[0m"


def _load_threshold():
    """Load threshold: env var > persisted file > default."""
    env = os.environ.get("AXIOM_PREFLIGHT_THRESHOLD", "")
    if env:
        try:
            return max(0.0, min(1.0, float(env)))
        except ValueError:
            pass
    if _THRESHOLD_FILE.exists():
        try:
            data = json.loads(_THRESHOLD_FILE.read_text(encoding="utf-8"))
            return max(0.0, min(1.0, float(data.get("threshold", PREFLIGHT_THRESHOLD_DEFAULT))))
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    return PREFLIGHT_THRESHOLD_DEFAULT


def _save_threshold(threshold):
    try:
        _THRESHOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
        _THRESHOLD_FILE.write_text(
            json.dumps({"threshold": round(threshold, 4),
                        "set_at": datetime.utcnow().isoformat() + "Z"}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _quality_warning(old, new, n_steps=9):
    """Print compound reliability math when user changes threshold.

    Shows the concrete 0.85^N degradation the user is accepting so the
    decision is made on facts, not intuition.
    """
    sep = "  " + "-" * 54
    print(sep)
    print("  %s" % _C_BOLD("QUALITY WARNING — threshold change"))
    print("  Old threshold : %.2f" % old)
    print("  New threshold : %.2f  %s" % (
        new,
        _C_RED("(lower)") if new < old else _C_GREEN("(higher)"),
    ))
    print()
    min_rel_old = old ** n_steps
    min_rel_new = new ** n_steps
    print("  Compound reliability with %d steps:" % n_steps)
    print("    old  %.2f ^ %d = %.4f  (%.1f%%)" % (old, n_steps, min_rel_old, min_rel_old * 100))
    print("    new  %.2f ^ %d = %.4f  (%.1f%%)" % (new, n_steps, min_rel_new, min_rel_new * 100))

    if new < 0.40:
        print()
        print("  %s" % _C_RED("!! CRITICAL: below 0.40 — FAIL-grade outputs will pass the gate"))
        print("  %s" % _C_RED("   Active preflight is effectively disabled at this threshold."))
    elif new < old:
        print()
        print("  %s" % _C_WARN("!! WARNING: DEGRADE-grade outputs may pass without human review"))
        print("  %s" % _C_WARN("   Downstream agents will receive lower-quality inputs."))
    else:
        print()
        print("  %s" % _C_GREEN("More steps will trigger human review (stricter gate)."))
    print(sep)


def _interactive_set_threshold(current):
    """Prompt the user for a new threshold, show quality warning, persist on confirm."""
    while True:
        try:
            raw = input("  New threshold [0.00–1.00, enter=cancel]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return current
        if not raw:
            print("  Cancelled.")
            return current
        try:
            new = round(float(raw), 4)
        except ValueError:
            print("  Invalid — enter a number between 0.00 and 1.00.")
            continue
        if not (0.0 <= new <= 1.0):
            print("  Out of range.")
            continue
        print()
        _quality_warning(current, new)
        try:
            confirm = input("  Apply new threshold %.2f? [y/N]: " % new).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return current
        if confirm in ("y", "yes"):
            _save_threshold(new)
            print("  %s  Threshold saved to %s" % (_C_GREEN("Saved."), _THRESHOLD_FILE))
            return new
        print("  Cancelled — threshold unchanged.")
        return current


def _human_intervention(agent_name, val_confidence, val_issues, val_verdict,
                        threshold, retries_exhausted=False):
    """Pause the pipeline and prompt the human for a decision.

    Returns one of: 'continue' | 'retry' | 'halt'
    ('retry' is not offered when retries_exhausted=True.)
    """
    _V = {"PASS": _C_GREEN("PASS"), "DEGRADE": _C_WARN("DEGRADE"), "FAIL": _C_RED("FAIL")}
    sep = "  " + "=" * 54
    print()
    print(sep)
    print("  %s" % _C_BOLD("ACTIVE PREFLIGHT — HUMAN REVIEW REQUIRED"))
    print("  %-14s %s  conf=%.2f  threshold=%.2f" % (
        agent_name, _V.get(val_verdict, val_verdict), val_confidence, threshold,
    ))
    if val_issues:
        for issue in val_issues[:5]:
            print("  %s %s" % (_C_WARN("!"), issue[:76]))
    print()
    print("  [c] Accept this output and continue")
    if not retries_exhausted:
        print("  [r] Retry this step")
    print("  [h] Halt pipeline here")
    print("  [t] Change threshold  (current: %.2f)" % threshold)
    print(sep)

    while True:
        try:
            choice = input("  Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "halt"

        if choice in ("c", "continue"):
            return "continue"
        if choice in ("r", "retry") and not retries_exhausted:
            return "retry"
        if choice in ("h", "halt"):
            return "halt"
        if choice in ("t", "threshold"):
            new_t = _interactive_set_threshold(threshold)
            # If user raised threshold past this output, auto-accept
            if val_confidence >= new_t:
                print("  %s Output now meets threshold (%.2f ≥ %.2f) — continuing." % (
                    _C_GREEN("OK."), val_confidence, new_t,
                ))
                return "continue"
            # Re-show prompt with updated threshold
            print()
            print("  Output still below new threshold (%.2f < %.2f)" % (val_confidence, new_t))
            threshold = new_t
            continue
        opts = "c/r/h/t" if not retries_exhausted else "c/h/t"
        print("  Enter %s." % opts)


# ── Build manifest ───────────────────────────────────────────────────────────

def _build_manifest(agent_name, step, result, model, task_class, latency_ms,
                    halted=False, validation_confidence=None, validation_issues=None,
                    validation_verdict=None):
    prefix_map = {
        "hypothesis": "HYP", "literature": "LIT", "simulation": "SIM",
        "critic": "CRT", "safety": "SAF", "ethics": "ETH",
        "data": "DAT", "experiment": "EXP", "report": "RPT",
    }
    prefix = prefix_map.get(agent_name, "UNK")
    manifest_id = "RP-%s-%s-%s" % (
        prefix,
        datetime.now().strftime("%Y%m%d-%H%M%S"),
        str(uuid.uuid4())[:6],
    )

    confidence = _extract_confidence(result.get("confidence", 0.70))

    verdict = result.get("verdict", "COMPLETE")
    if agent_name == "critic":
        verdict = result.get("severity", "REVIEWED")
    elif agent_name == "safety":
        verdict = result.get("verdict", "PROCEED")
    elif agent_name == "ethics":
        verdict = result.get("verdict", "PROCEED")

    manifest = {
        "manifest_id":           manifest_id,
        "manifest_version":      "1.0",
        "engine":                "AXIOM Research Pipeline v1.0",
        "agent":                 agent_name,
        "step":                  step,
        "layer":                 "RESEARCH_PIPELINE",
        "timestamp":             datetime.now().isoformat() + "Z",
        "model":                 model,
        "task_class":            task_class,
        "latency_ms":            latency_ms,
        "verdict":               verdict,
        "confidence":            confidence,
        "halted":                halted,
        "question_blindness":    agent_name == "critic",
        "can_halt_pipeline":     agent_name in ("safety", "ethics"),
        "rival_required":        agent_name in ("critic", "report"),
        "agent_response":        result,
        "validation_confidence": validation_confidence,
        "validation_issues":     validation_issues or [],
        "validation_verdict":    validation_verdict,
    }

    manifest["signature"] = _sign(manifest)
    return manifest


# ── Literature retrieval ──────────────────────────────────────────────────────

try:
    from axiom_research.retrieve import LocalFilesRetriever as _LocalFilesRetriever
    _RETRIEVER_AVAILABLE = True
except ImportError:
    _RETRIEVER_AVAILABLE = False


def _retrieve_literature(question, hypothesis):
    """Ground the literature step with real local document retrieval.

    Searches AXIOM_RETRIEVER_ROOT (default: docs/ alongside this file) using
    axiom_research.retrieve.LocalFilesRetriever. Returns up to 5 RetrievedDoc
    objects, or [] if the package is unavailable or the directory doesn't exist.
    """
    if not _RETRIEVER_AVAILABLE:
        return []
    root = os.environ.get("AXIOM_RETRIEVER_ROOT", "")
    if not root:
        root = str(Path(__file__).parent / "docs")
    try:
        retriever = _LocalFilesRetriever(root)
        return retriever.retrieve("%s %s" % (question, hypothesis), top_k=5)
    except Exception:
        return []


# ── Pipeline ─────────────────────────────────────────────────────────────────

class ResearchPipeline:
    """Orchestrates the 9-agent constitutional research pipeline.

    With use_orchestrator=True (default), a front-door ResearchOrchestrator
    classifies the domain, queries the PatternLibrary for retrospect bias,
    and selects only the steps this domain and question actually need.
    Results feed back to the PatternLibrary as EWMA-updated efficiency scores.
    """

    def __init__(self, model_override=None, use_orchestrator=True,
                 preflight_threshold=None, hitl_enabled=None):
        self.model_override = model_override
        self.use_orchestrator = use_orchestrator
        self.manifests = []
        self.halted = False
        self.halt_reason = None
        self.halt_step = None
        self.plan = None
        self._orchestrator = ResearchOrchestrator() if use_orchestrator else None
        # Threshold: explicit arg > env/file > default
        self.preflight_threshold = (
            preflight_threshold if preflight_threshold is not None else _load_threshold()
        )
        # HITL: explicit arg; default to True only when stdin is an interactive TTY
        self.hitl_enabled = (
            hitl_enabled if hitl_enabled is not None else sys.stdin.isatty()
        )

    def _get_model(self, task_class):
        return _resolve_model(task_class, self.model_override)

    def _get_budget(self, task_class):
        return TOKEN_BUDGETS.get(task_class, 1500)

    def _run_agent(self, agent_def, user_prompt, total_steps):
        """Run one agent with active preflight gate.

        Mirrors scripts/axiom_preflight.py — named checks, PASS/DEGRADE/FAIL
        verdicts, one retry with issues hint, hard halt on FAIL after retry.

          PASS    → continue immediately
          DEGRADE → retry once (issues appended to prompt); continue either way
          FAIL    → retry once; FAIL again → raise StepPreflightFailed → halt
        """
        name       = agent_def["name"]
        axiom_file = agent_def["axiom"]
        task_class = agent_def["task_class"]
        step       = agent_def["step"]
        label      = agent_def["label"]

        system_prompt = _load_axiom_system(axiom_file)
        system_prompt += (
            "\n\nIMPORTANT: Your response MUST be valid JSON only. "
            "No prose, no markdown fences, no explanation outside the JSON object. "
            "Keep string values under 300 characters. "
            "Keep arrays to a maximum of 5 items. "
            "Do not include wrapper keys like 'agent' or 'version' — return the data fields directly."
        )

        model      = self._get_model(task_class)
        max_tokens = agent_def.get("max_tokens") or self._get_budget(task_class)

        response, latency_ms = _call_llm(system_prompt, user_prompt, model, max_tokens)
        result = _parse_json(response)

        # ── Active preflight gate ─────────────────────────────────────────────
        val_confidence, val_issues, val_verdict = _validate_step_output(name, result)
        _print_preflight_check(name, val_verdict, val_confidence, val_issues, attempt=0)

        # Reload threshold — user may have changed it during a previous HITL prompt
        self.preflight_threshold = _load_threshold()

        if val_confidence < self.preflight_threshold or val_verdict == "FAIL":
            # Decide: human prompt (interactive) or automatic policy (API/pipe)
            if self.hitl_enabled:
                action = _human_intervention(
                    name, val_confidence, val_issues, val_verdict, self.preflight_threshold
                )
                self.preflight_threshold = _load_threshold()  # user may have changed threshold
            else:
                # Non-interactive: retry on DEGRADE/FAIL, no human involvement
                action = "retry"

            if action == "halt":
                raise StepPreflightFailed(name, val_issues)

            if action == "retry":
                retry_hint = (
                    "\n\n[ACTIVE PREFLIGHT RETRY 1/%d]\n"
                    "Output scored %.2f (threshold %.2f). Issues to fix:\n%s\n"
                    "Return complete JSON addressing all issues above."
                ) % (PREFLIGHT_MAX_RETRIES, val_confidence, self.preflight_threshold,
                     "\n".join("- %s" % i for i in val_issues))
                r2, ms2 = _call_llm(system_prompt, user_prompt + retry_hint, model, max_tokens)
                latency_ms += ms2
                result = _parse_json(r2)
                val_confidence, val_issues, val_verdict = _validate_step_output(name, result)
                _print_preflight_check(name, val_verdict, val_confidence, val_issues, attempt=1)

                # Check again after retry — human gets one more look if still failing
                if val_confidence < self.preflight_threshold or val_verdict == "FAIL":
                    if self.hitl_enabled:
                        action2 = _human_intervention(
                            name, val_confidence, val_issues, val_verdict,
                            self.preflight_threshold, retries_exhausted=True,
                        )
                        self.preflight_threshold = _load_threshold()
                        if action2 == "halt":
                            raise StepPreflightFailed(name, val_issues)
                    else:
                        # Non-interactive: hard block on FAIL, accept DEGRADE
                        if val_verdict == "FAIL":
                            raise StepPreflightFailed(name, val_issues)
            # action == "continue": human accepted output as-is
        # ─────────────────────────────────────────────────────────────────────

        manifest = _build_manifest(
            name, step, result, model, task_class, latency_ms,
            validation_confidence=val_confidence,
            validation_issues=val_issues,
            validation_verdict=val_verdict,
        )
        self.manifests.append(manifest)

        _print_step(step, total_steps, label, model, task_class, result,
                    manifest["manifest_id"], latency_ms, agent_name=name)

        return result, manifest

    def _build_prompt(self, agent_name, question, context):
        """Build the per-agent prompt with graceful fallbacks for skipped steps."""
        hyp  = context.get("hypothesis", {})
        lit  = context.get("literature", {})
        sim  = context.get("simulation", {})
        crit = context.get("critique", {})
        safe = context.get("safety", {})
        eth  = context.get("ethics", {})
        data = context.get("data", {})
        exp  = context.get("experiment", {})

        if agent_name == "hypothesis":
            return (
                "Research question: %s\n\n"
                "Generate a testable hypothesis with null hypothesis. "
                "Return JSON with: hypothesis, null_hypothesis, falsifiable, "
                "falsification_criteria, variables, assumptions, confidence"
            ) % question

        if agent_name == "literature":
            retrieved = _retrieve_literature(question, hyp.get("hypothesis", question))
            retrieval_block = ""
            if retrieved:
                lines = []
                for doc in retrieved[:5]:
                    lines.append("  [%.2f] %s — %s" % (doc.score, doc.path, doc.snippet[:200]))
                retrieval_block = "\n\nRETRIEVED SOURCES (real, use as evidence):\n" + "\n".join(lines)
            return (
                "Research question: %s\n"
                "Hypothesis: %s"
                "%s\n\n"
                "Search existing literature for evidence supporting or "
                "contradicting this hypothesis. Use the retrieved sources above "
                "as your primary evidence where available. "
                "Return JSON with: sources[], "
                "supporting_evidence, contradicting_evidence, gaps[], "
                "consensus_level, confidence"
            ) % (question, hyp.get("hypothesis", question), retrieval_block)

        if agent_name == "simulation":
            lit_summary = lit.get("supporting_evidence", "no literature data")
            if not isinstance(lit_summary, str):
                lit_summary = json.dumps(lit_summary)
            return (
                "Research question: %s\n"
                "Hypothesis: %s\n"
                "Literature summary: %s\n\n"
                "Model this hypothesis. Predict outcomes, list assumptions, "
                "identify key parameters. Return JSON with: model_description, "
                "assumptions[], predicted_outcomes[], sensitivity_parameters[], "
                "limitations[], confidence"
            ) % (question, hyp.get("hypothesis", question), lit_summary[:300])

        if agent_name == "critic":
            lit_ev = lit.get("supporting_evidence", "no literature available")
            if not isinstance(lit_ev, str):
                lit_ev = json.dumps(lit_ev)
            return (
                "CLAIM: %s\n\n"
                "EVIDENCE:\n"
                "- Simulation predicted: %s\n"
                "- Literature says: %s\n\n"
                "NOTE: You have NOT been shown the reasoning behind this claim. "
                "Evaluate the claim against the evidence only. "
                "Find flaws. Provide a rival hypothesis. "
                "Return JSON with: flaws[], rival_hypothesis, severity, "
                "recommendation, question_blindness_enforced, confidence"
            ) % (
                hyp.get("hypothesis", question),
                json.dumps(sim.get("predicted_outcomes", []))[:200],
                lit_ev[:200],
            )

        if agent_name == "safety":
            return (
                "Research question: %s\n"
                "Hypothesis: %s\n"
                "Critic severity: %s\n"
                "Critic flaws: %s\n\n"
                "Assess safety risks. Can this research proceed safely? "
                "Return JSON with: verdict (PROCEED or CRITICAL_RISK), "
                "risk_level, risks[], mitigations[], halt_reason, confidence"
            ) % (
                question,
                hyp.get("hypothesis", question),
                crit.get("severity", "UNKNOWN"),
                json.dumps(crit.get("flaws", []))[:300],
            )

        if agent_name == "ethics":
            return (
                "Research question: %s\n"
                "Hypothesis: %s\n"
                "Safety verdict: %s\n\n"
                "Evaluate ethical implications. Are there concerns about "
                "participants, consent, equity, or societal impact? "
                "Return JSON with: verdict (PROCEED or ETHICS_VIOLATION), "
                "classification, concerns[], mitigations_required[], "
                "informed_consent_needed, halt_reason, confidence"
            ) % (question, hyp.get("hypothesis", question), safe.get("verdict", "PROCEED"))

        if agent_name == "data":
            sim_params = json.dumps(sim.get("sensitivity_parameters", []))[:200]
            return (
                "Research question: %s\n"
                "Hypothesis: %s\n"
                "Simulation parameters: %s\n\n"
                "Define data collection requirements. "
                "Return JSON with: data_requirements[], methodology, "
                "sample_size, validation_criteria[], provenance, "
                "integrity_hash, confidence"
            ) % (question, hyp.get("hypothesis", question), sim_params)

        if agent_name == "experiment":
            data_reqs = json.dumps(data.get("data_requirements", []))[:200]
            return (
                "Research question: %s\n"
                "Hypothesis: %s\n"
                "Data requirements: %s\n"
                "Critic recommendation: %s\n\n"
                "Design the experimental protocol. Include controls, "
                "endpoints, analysis plan. "
                "Return JSON with: protocol, control_groups[], endpoints[], "
                "analysis_plan, success_criteria, failure_criteria, "
                "reproducibility_notes, confidence"
            ) % (question, hyp.get("hypothesis", question), data_reqs, crit.get("recommendation", ""))

        if agent_name == "report":
            exp_protocol = exp.get("protocol", "")
            if not isinstance(exp_protocol, str):
                exp_protocol = json.dumps(exp_protocol)
            return (
                "Research question: %s\n\n"
                "PIPELINE RESULTS:\n"
                "Hypothesis: %s\n"
                "Null hypothesis: %s\n"
                "Literature consensus: %s\n"
                "Simulation prediction: %s\n"
                "Critic rival hypothesis: %s\n"
                "Critic severity: %s\n"
                "Safety verdict: %s\n"
                "Ethics verdict: %s\n"
                "Experiment protocol summary: %s\n\n"
                "Write the constitutional research report. "
                "Include what was found AND what was NOT found. "
                "Use 'shows promise' not 'cures'. "
                "Return JSON with: title, summary, findings[], "
                "negative_results[], rival_hypothesis, limitations[], "
                "conclusions, pipeline_provenance, confidence"
            ) % (
                question,
                hyp.get("hypothesis", ""),
                hyp.get("null_hypothesis", ""),
                lit.get("consensus_level", "unknown"),
                json.dumps(sim.get("predicted_outcomes", []))[:150],
                crit.get("rival_hypothesis", ""),
                crit.get("severity", ""),
                safe.get("verdict", ""),
                eth.get("verdict", ""),
                exp_protocol[:150],
            )

        return "Research question: %s\n\nRespond with valid JSON." % question

    def run(self, research_question, max_steps=9):
        """Run the pipeline — orchestrator selects steps, then agents execute."""
        # ── Orchestrate ───────────────────────────────────────────────
        if self.use_orchestrator:
            self.plan = self._orchestrator.plan(research_question, self.model_override)
            active_steps = set(s for s in self.plan.steps if s <= max_steps)
        else:
            active_steps = set(range(1, min(max_steps, 9) + 1))

        context = {"question": research_question}
        total_active = len(active_steps)

        print()
        print("  AXIOM Research Pipeline v2.0")
        print("  " + BOX_DOUBLE)
        print("  Question : %s" % research_question)
        if self.plan:
            print("  Domain   : %s" % self.plan.domain)
            print("  Approach : %s" % self.plan.approach)
            if self.plan.retrospect_bias is not None:
                print("  Retrospect bias: %.2f efficiency" % self.plan.retrospect_bias)
        print("  Steps    : %s" % ", ".join(str(s) for s in sorted(active_steps)))
        print("  Threshold: %.2f  HITL=%s" % (
            self.preflight_threshold, "on" if self.hitl_enabled else "off (auto)"
        ))
        print("  " + BOX_DOUBLE)

        # ── Dynamic agent loop ────────────────────────────────────────
        for agent_def in AGENTS:
            step = agent_def["step"]
            name = agent_def["name"]

            if step not in active_steps:
                continue

            prompt = self._build_prompt(name, research_question, context)
            try:
                result, _ = self._run_agent(agent_def, prompt, total_active)
            except StepPreflightFailed as exc:
                self.halted = True
                self.halt_reason = "Active preflight FAILED: %s — %s" % (
                    exc.agent_name, "; ".join(exc.issues[:3])
                )
                self.halt_step = step
                _print_halt(step, total_active, name.upper() + " AGENT [PREFLIGHT]",
                            self.halt_reason)
                return self._final_report(context, halted=True)
            context[name] = result

            # Safety halt
            if name == "safety" and result.get("verdict") == "CRITICAL_RISK":
                self.halted = True
                self.halt_reason = result.get("halt_reason", "Critical safety risk detected")
                self.halt_step = step
                _print_halt(step, total_active, "SAFETY AGENT", self.halt_reason)
                return self._final_report(context, halted=True)

            # Ethics halt
            if name == "ethics" and result.get("verdict") == "ETHICS_VIOLATION":
                self.halted = True
                self.halt_reason = result.get("halt_reason", "Ethics violation detected")
                self.halt_step = step
                _print_halt(step, total_active, "ETHICS AGENT", self.halt_reason)
                return self._final_report(context, halted=True)

        return self._final_report(context)

    def _final_report(self, context, halted=False):
        """Print summary, compute pipeline reliability, update pattern library, save manifests."""
        # Compound reliability = product of per-step validation scores.
        # Mirrors the 0.85^N degradation problem: each DEGRADE step multiplies
        # the cumulative score down, making silent failures visible before they
        # reach the final report.
        val_scores = [
            m["validation_confidence"]
            for m in self.manifests
            if m.get("validation_confidence") is not None
        ]
        pipeline_reliability = 1.0
        for s in val_scores:
            pipeline_reliability *= s
        pipeline_reliability = round(pipeline_reliability, 4)

        degraded_steps = [
            (m["agent"], m["validation_confidence"], m["validation_verdict"])
            for m in self.manifests
            if m.get("validation_verdict") in ("DEGRADE", "FAIL")
        ]

        print()
        print("  " + BOX_DOUBLE)
        if halted:
            print("  PIPELINE HALTED at step %d" % (self.halt_step or 0))
            print("  Reason: %s" % self.halt_reason)
        else:
            print("  PIPELINE COMPLETE — %d steps executed" % len(self.manifests))
        print("  Manifests  : %d signed" % len(self.manifests))
        print("  Reliability: %.4f  (product of %d validation scores)" % (
            pipeline_reliability, len(val_scores)))
        if degraded_steps:
            print("  DEGRADED   : " + ", ".join(
                "%s(%.2f %s)" % (a, c, v) for a, c, v in degraded_steps
            ))
        print("  " + BOX_DOUBLE)

        # Update pattern library with EWMA efficiency for this run
        if self.plan and self._orchestrator:
            success = not halted
            self._orchestrator.library.upsert(
                self.plan.domain, self.plan.approach,
                self.plan.fingerprint, success,
            )

        # Save manifests
        try:
            with open(MANIFEST_FILE, "w") as f:
                json.dump(self.manifests, f, indent=2)
            print("  Saved %s" % MANIFEST_FILE)
        except IOError as exc:
            print("  [warning] Could not save manifests: %s" % exc)

        return {
            "question": context.get("question", ""),
            "steps_completed": len(self.manifests),
            "halted": halted,
            "halt_reason": self.halt_reason,
            "pipeline_reliability": pipeline_reliability,
            "degraded_steps": [
                {"agent": a, "validation_confidence": c, "verdict": v}
                for a, c, v in degraded_steps
            ],
            "plan": {
                "domain":    self.plan.domain    if self.plan else "general",
                "approach":  self.plan.approach  if self.plan else "full_pipeline",
                "steps_run": sorted(context.keys() - {"question"}),
            },
            "manifests": self.manifests,
            "context": context,
        }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AXIOM Scientific Research Pipeline — 9-agent constitutional workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python axiom_research_pipeline.py "Does intermittent fasting reduce inflammation?"\n'
            '  python axiom_research_pipeline.py --steps 3 "Can vitamin D improve sleep?"\n'
            '  python axiom_research_pipeline.py --model claude-sonnet-4-6 "Your question"\n'
        ),
    )
    parser.add_argument(
        "question",
        help="The research question to investigate",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=9,
        help="Number of pipeline steps to run (1-9, default: 9)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override model for all agents (bypasses efficiency routing)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Manifest output path (default: research_manifests.json)",
    )
    parser.add_argument(
        "--no-orchestrator",
        action="store_true",
        help="Disable domain orchestration — run all steps in order (v1 behaviour)",
    )
    parser.add_argument(
        "--preflight-threshold",
        type=float,
        default=None,
        metavar="FLOAT",
        help=(
            "Validation confidence threshold for human review (0.0–1.0). "
            "Steps scoring below this value trigger HITL. "
            "Persisted to ~/.axiom/preflight_threshold.json. "
            "Default: %.2f (or AXIOM_PREFLIGHT_THRESHOLD env var)." % PREFLIGHT_THRESHOLD_DEFAULT
        ),
    )
    parser.add_argument(
        "--no-hitl",
        action="store_true",
        help=(
            "Disable human-in-the-loop prompts — auto-retry on DEGRADE, "
            "halt on FAIL (same as API/pipe mode)."
        ),
    )
    args = parser.parse_args()

    # Handle threshold: CLI arg → show quality warning vs current, then persist
    if args.preflight_threshold is not None:
        new_t = max(0.0, min(1.0, args.preflight_threshold))
        current_t = _load_threshold()
        if new_t != current_t:
            _quality_warning(current_t, new_t)
            _save_threshold(new_t)
            print("  Threshold saved: %.2f\n" % new_t)

    global MANIFEST_FILE
    if args.output:
        MANIFEST_FILE = args.output

    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_nim = bool(
        os.environ.get("AXIOM_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not has_anthropic and not has_nim:
        print("[error] No API key configured. Set one of:")
        print("  $env:ANTHROPIC_API_KEY  = 'sk-ant-...'   (Anthropic — preferred)")
        print("  $env:AXIOM_API_KEY      = 'nvapi-...'    (NVIDIA NIM)")
        print("  $env:NVIDIA_API_KEY     = 'nvapi-...'    (NVIDIA NIM legacy)")
        sys.exit(1)

    if has_anthropic:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            print("[error] anthropic package not installed.")
            print("  pip install anthropic")
            sys.exit(1)

    use_orch = not args.no_orchestrator
    pipeline = ResearchPipeline(
        model_override=args.model,
        use_orchestrator=use_orch,
        preflight_threshold=args.preflight_threshold,
        hitl_enabled=(False if args.no_hitl else None),  # None → auto-detect TTY
    )
    pipeline.run(args.question, max_steps=args.steps)


if __name__ == "__main__":
    main()
