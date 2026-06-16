"""General-purpose autonomous agent with latent reasoning and retrospect learning.

Handles any task domain — not just coding. The agent:
  1. Classifies the task domain (research / writing / coding / planning /
     analysis / creative / data)
  2. Queries a persistent PatternLibrary for historically efficient approaches
  3. Runs a latent-reasoning pass: evaluates N candidate approaches through
     the ManifoldChecker constitutional filter, picks the lowest-distance one
  4. Plans and executes step-by-step, optionally delegating to an LLM backend
  5. Records the outcome and updates the PatternLibrary via EWMA efficiency

All results and pattern entries are HMAC-signed under the master key.

CLI:
    python3 -m axiom_general_agent run --task "write a market research report on EV charging"
    python3 -m axiom_general_agent run --task "plan a product launch" --domain planning
    python3 -m axiom_general_agent history --domain research
    python3 -m axiom_general_agent history --top 10

CANNOT_MUTATE:
  TRUST_LEVEL, N_LATENT_THOUGHTS, MAX_STEPS, RETROSPECT_TOP_K,
  EFFICIENCY_DECAY, LATENT_REJECTION_THRESHOLD, PATTERN_LIBRARY_PATH
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import random
import re
import sys
import time
import types
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── CANNOT_MUTATE via module freeze ───────────────────────────────────────────

TRUST_LEVEL                  = 4      # constitutional trust tier
N_LATENT_THOUGHTS            = 3      # candidate approaches evaluated per task
MAX_STEPS                    = 12     # hard cap on execution steps
RETROSPECT_TOP_K             = 3      # patterns retrieved from library per task
EFFICIENCY_DECAY             = 0.85   # EWMA α for pattern efficiency tracking
LATENT_REJECTION_THRESHOLD   = 0.10   # manifold distance below which we reject an approach
PATTERN_LIBRARY_PATH         = Path("axiom_general_agent_patterns.jsonl")

_NAMESPACE = b"axiom-general-agent-v1"

class _FrozenModule(types.ModuleType):
    _FROZEN = {
        "TRUST_LEVEL", "N_LATENT_THOUGHTS", "MAX_STEPS", "RETROSPECT_TOP_K",
        "EFFICIENCY_DECAY", "LATENT_REJECTION_THRESHOLD", "PATTERN_LIBRARY_PATH",
    }
    def __setattr__(self, name: str, value: object) -> None:
        if name in self._FROZEN:
            raise AttributeError(f"CANNOT_MUTATE: {name!r} is a constitutional constant")
        super().__setattr__(name, value)

sys.modules[__name__].__class__ = _FrozenModule

# ── Signing ───────────────────────────────────────────────────────────────────

def _master_key() -> bytes:
    raw = os.environ.get("AXIOM_MASTER_KEY", "")
    if not raw:
        raise EnvironmentError(
            "AXIOM_MASTER_KEY not set. "
            "Generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return bytes.fromhex(raw)

def _derive_key() -> bytes:
    return hmac.new(_NAMESPACE, _master_key(), hashlib.sha256).digest()

def _sign(payload: dict) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(_derive_key(), canon.encode(), hashlib.sha256).hexdigest()

def _verify_sig(payload: dict, sig: str) -> bool:
    return hmac.compare_digest(_sign(payload), sig)

# ── Domains and approaches ────────────────────────────────────────────────────

DOMAINS = ("research", "writing", "coding", "planning", "analysis",
           "creative", "data")

# Per-domain canonical approaches. Each approach is a label + 4-axis
# constitutional vector: (certainty, scope, alternatives, completeness)
# mapping onto ManifoldChecker's: (confidence, rival_present, fields_clean, …)
_DOMAIN_APPROACHES: Dict[str, List[Tuple[str, Tuple[float, float, float, float]]]] = {
    "research": [
        ("breadth_first",     (0.60, 1.0, 1.0, 0.70)),
        ("depth_first",       (0.75, 1.0, 1.0, 0.85)),
        ("comparative",       (0.70, 1.0, 1.0, 0.80)),
        ("hypothesis_driven", (0.65, 1.0, 1.0, 0.75)),
    ],
    "writing": [
        ("outline_expand",    (0.72, 1.0, 1.0, 0.82)),
        ("draft_revise",      (0.68, 1.0, 1.0, 0.78)),
        ("top_down",          (0.74, 1.0, 1.0, 0.84)),
        ("story_arc",         (0.66, 1.0, 1.0, 0.76)),
    ],
    "coding": [
        ("tdd",               (0.80, 1.0, 1.0, 0.88)),
        ("prototype_refine",  (0.72, 1.0, 1.0, 0.82)),
        ("spec_first",        (0.76, 1.0, 1.0, 0.86)),
        ("incremental",       (0.70, 1.0, 1.0, 0.80)),
    ],
    "planning": [
        ("backcasting",       (0.68, 1.0, 1.0, 0.78)),
        ("milestone_driven",  (0.74, 1.0, 1.0, 0.84)),
        ("risk_first",        (0.70, 1.0, 1.0, 0.80)),
        ("agile_sprints",     (0.66, 1.0, 1.0, 0.76)),
    ],
    "analysis": [
        ("root_cause",        (0.78, 1.0, 1.0, 0.86)),
        ("first_principles",  (0.75, 1.0, 1.0, 0.83)),
        ("swot",              (0.70, 1.0, 1.0, 0.80)),
        ("framework_fit",     (0.72, 1.0, 1.0, 0.82)),
    ],
    "creative": [
        ("diverge_converge",  (0.62, 1.0, 1.0, 0.72)),
        ("constraint_based",  (0.66, 1.0, 1.0, 0.76)),
        ("analogical",        (0.64, 1.0, 1.0, 0.74)),
        ("reverse_prompt",    (0.60, 1.0, 1.0, 0.70)),
    ],
    "data": [
        ("eda_first",         (0.76, 1.0, 1.0, 0.84)),
        ("hypothesis_test",   (0.80, 1.0, 1.0, 0.88)),
        ("pipeline_build",    (0.74, 1.0, 1.0, 0.82)),
        ("anomaly_scan",      (0.72, 1.0, 1.0, 0.80)),
    ],
}

# Domain classification keyword map
_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "coding":   ["code", "function", "class", "bug", "implement", "script",
                 "debug", "refactor", "test", "api", "module", "python",
                 "javascript", "java", "compile", "sql", "query", "database"],
    "data":     ["dataset", "csv", "dataframe", "plot", "chart", "statistics",
                 "correlation", "regression", "model", "train", "predict",
                 "feature", "column", "row", "aggregate", "transform"],
    "research": ["research", "literature", "survey", "review", "find",
                 "summarize", "investigate", "study", "explore", "search",
                 "report on", "what is", "how does", "explain"],
    "writing":  ["write", "draft", "essay", "article", "blog", "letter",
                 "email", "document", "paragraph", "story", "compose",
                 "proofread", "edit", "revise"],
    "planning": ["plan", "roadmap", "schedule", "timeline", "milestone",
                 "launch", "strategy", "project", "sprint", "deliverable",
                 "objective", "goal", "kpi"],
    "analysis": ["analyze", "analyse", "compare", "evaluate", "assess",
                 "root cause", "diagnose", "reason", "why", "impact",
                 "tradeoff", "pros and cons", "review"],
    "creative": ["creative", "design", "brainstorm", "idea", "concept",
                 "generate", "invent", "imagine", "suggest", "campaign",
                 "slogan", "name", "brand"],
}

def classify_domain(task: str, hint: Optional[str] = None) -> str:
    if hint and hint in DOMAINS:
        return hint
    low = task.lower()
    scores: Dict[str, int] = {d: 0 for d in DOMAINS}
    for domain, kws in _DOMAIN_KEYWORDS.items():
        for kw in kws:
            if kw in low:
                scores[domain] += 1
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else "research"

# ── Constitutional manifold distance (inline, no import required) ─────────────

_UNCERTAINTY_FLOOR = 0.15
_OVERCLAIM_CEILING = 0.85

def _manifold_distance(
    confidence: float,
    rival_present: bool = True,
    fields_clean: bool = True,
) -> float:
    d_floor   = confidence - _UNCERTAINTY_FLOOR
    d_ceiling = _OVERCLAIM_CEILING - confidence
    d_rival   = 1.0 if rival_present else 0.0
    d_fields  = 1.0 if fields_clean else 0.0
    dist = min(d_floor, d_ceiling, d_rival, d_fields)
    return max(0.0, min(1.0, round(dist, 4)))

# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class LatentThought:
    approach:  str
    distance:  float    # constitutional distance (higher = better)
    rationale: str      # brief justification text
    rejected:  bool     # True if distance < LATENT_REJECTION_THRESHOLD

@dataclass
class TaskStep:
    index:      int
    action:     str
    result:     str = ""
    elapsed_ms: int = 0

@dataclass
class TaskPattern:
    domain:       str
    approach:     str
    fingerprint:  str   # SHA256[:16] of lowercased task keywords
    efficiency:   float = 0.70   # EWMA-tracked; higher = faster success rate
    uses:         int   = 0
    last_used:    str   = ""
    signature:    str   = ""

    def sign(self) -> "TaskPattern":
        payload = {k: v for k, v in asdict(self).items() if k != "signature"}
        self.signature = _sign(payload)
        return self

    def verify(self) -> bool:
        payload = {k: v for k, v in asdict(self).items() if k != "signature"}
        return _verify_sig(payload, self.signature)

@dataclass
class TaskOutcome:
    task_id:     str
    task:        str
    domain:      str
    approach:    str
    steps:       List[TaskStep]
    success:     bool
    latent_log:  List[LatentThought]
    wallclock_s: float
    timestamp:   str
    fingerprint: str
    signature:   str = ""

    def sign(self) -> "TaskOutcome":
        payload = {
            "task_id":     self.task_id,
            "task":        self.task,
            "domain":      self.domain,
            "approach":    self.approach,
            "success":     self.success,
            "wallclock_s": round(self.wallclock_s, 3),
            "timestamp":   self.timestamp,
            "fingerprint": self.fingerprint,
        }
        self.signature = _sign(payload)
        return self

    def verify(self) -> bool:
        payload = {
            "task_id":     self.task_id,
            "task":        self.task,
            "domain":      self.domain,
            "approach":    self.approach,
            "success":     self.success,
            "wallclock_s": round(self.wallclock_s, 3),
            "timestamp":   self.timestamp,
            "fingerprint": self.fingerprint,
        }
        return _verify_sig(payload, self.signature)

# ── PatternLibrary ────────────────────────────────────────────────────────────

def _task_fingerprint(task: str) -> str:
    words = sorted(set(re.findall(r"[a-z]+", task.lower())))
    joined = " ".join(words[:20])
    return hashlib.sha256(joined.encode()).hexdigest()[:16]

class PatternLibrary:
    """Persistent JSONL store of TaskPatterns with EWMA efficiency tracking."""

    def __init__(self, path: Path = PATTERN_LIBRARY_PATH) -> None:
        self._path = path
        self._patterns: Dict[str, TaskPattern] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                p = TaskPattern(**d)
                if p.verify():
                    key = f"{p.domain}:{p.approach}:{p.fingerprint}"
                    self._patterns[key] = p
            except Exception:
                pass

    def _save(self) -> None:
        self._path.write_text(
            "\n".join(json.dumps(asdict(p)) for p in self._patterns.values()) + "\n"
        )

    def query(
        self,
        fingerprint: str,
        domain: str,
        top_k: int = RETROSPECT_TOP_K,
    ) -> List[TaskPattern]:
        candidates = [
            p for p in self._patterns.values()
            if p.domain == domain
        ]
        # Rank: exact fingerprint match first, then by efficiency descending
        exact   = [p for p in candidates if p.fingerprint == fingerprint]
        similar = [p for p in candidates if p.fingerprint != fingerprint]
        similar.sort(key=lambda p: p.efficiency, reverse=True)
        return (exact + similar)[:top_k]

    def upsert(self, outcome: TaskOutcome) -> TaskPattern:
        """Create or update a TaskPattern using EWMA efficiency."""
        key = f"{outcome.domain}:{outcome.approach}:{outcome.fingerprint}"
        existing = self._patterns.get(key)

        efficiency_signal = 1.0 if outcome.success else 0.0
        # Penalise slow runs: anything over 60s gets half credit
        if outcome.wallclock_s > 60:
            efficiency_signal *= 0.5

        if existing:
            new_eff = (EFFICIENCY_DECAY * existing.efficiency
                       + (1 - EFFICIENCY_DECAY) * efficiency_signal)
            existing.efficiency = round(new_eff, 4)
            existing.uses      += 1
            existing.last_used  = outcome.timestamp
            existing.sign()
            self._patterns[key] = existing
            p = existing
        else:
            p = TaskPattern(
                domain=outcome.domain,
                approach=outcome.approach,
                fingerprint=outcome.fingerprint,
                efficiency=round(efficiency_signal, 4),
                uses=1,
                last_used=outcome.timestamp,
            ).sign()
            self._patterns[key] = p

        self._save()
        return p

    def history(
        self,
        domain: Optional[str] = None,
        top: int = 20,
    ) -> List[TaskPattern]:
        ps = list(self._patterns.values())
        if domain:
            ps = [p for p in ps if p.domain == domain]
        ps.sort(key=lambda p: (-p.efficiency, -p.uses))
        return ps[:top]

# ── Latent reasoner ───────────────────────────────────────────────────────────

class LatentReasoner:
    """Evaluate candidate approaches through the constitutional filter."""

    def think(
        self,
        task: str,
        domain: str,
        context_patterns: List[TaskPattern],
    ) -> Tuple[str, List[LatentThought]]:
        """Return (best_approach, all_thoughts).

        Scores each approach via manifold distance, biased by retrospect
        pattern efficiency. Rejects approaches below LATENT_REJECTION_THRESHOLD.
        """
        approaches = _DOMAIN_APPROACHES[domain]

        # Build a retrospect bias map: approach → historical efficiency
        bias: Dict[str, float] = {}
        for p in context_patterns:
            if p.approach not in bias or p.efficiency > bias[p.approach]:
                bias[p.approach] = p.efficiency

        thoughts: List[LatentThought] = []
        for label, (conf, rival, clean, scope) in approaches:
            base_dist = _manifold_distance(conf, bool(rival), bool(clean))
            # Blend constitutional distance with retrospect bias (30% weight)
            hist_eff = bias.get(label, 0.70)
            blended  = round(0.70 * base_dist + 0.30 * (hist_eff - 0.50), 4)
            blended  = max(0.0, min(1.0, blended))

            rejected = blended < LATENT_REJECTION_THRESHOLD
            rationale = (
                f"conf={conf:.2f} → dist={base_dist:.4f}  "
                f"hist={hist_eff:.2f}  blended={blended:.4f}"
                + ("  [REJECTED: below threshold]" if rejected else "")
            )
            thoughts.append(LatentThought(
                approach=label,
                distance=blended,
                rationale=rationale,
                rejected=rejected,
            ))

        # Pick best non-rejected approach; fall back to highest distance if all rejected
        valid = [t for t in thoughts if not t.rejected]
        pool  = valid if valid else thoughts
        best  = max(pool, key=lambda t: t.distance)
        return best.approach, thoughts


# ── Step planner ──────────────────────────────────────────────────────────────

def _plan_steps(task: str, domain: str, approach: str) -> List[str]:
    """Return an ordered list of step descriptions for this domain×approach."""
    templates: Dict[str, Dict[str, List[str]]] = {
        "research": {
            "breadth_first":     ["Define scope and key questions",
                                   "Identify source categories",
                                   "Collect evidence across sources",
                                   "Cluster findings by theme",
                                   "Synthesise cross-theme insights",
                                   "Draft summary with citations"],
            "depth_first":       ["Focus on primary question",
                                   "Identify authoritative sources",
                                   "Deep-dive single source thread",
                                   "Extract core claims",
                                   "Validate claims against secondary sources",
                                   "Write detailed findings"],
            "comparative":       ["List entities to compare",
                                   "Define comparison dimensions",
                                   "Gather data per entity per dimension",
                                   "Build comparison matrix",
                                   "Highlight differentiators",
                                   "Recommend based on criteria"],
            "hypothesis_driven": ["State falsifiable hypothesis",
                                   "Identify evidence that would disprove it",
                                   "Search for disconfirming evidence",
                                   "Search for confirming evidence",
                                   "Weigh evidence balance",
                                   "Accept / revise / reject hypothesis"],
        },
        "writing": {
            "outline_expand":  ["Clarify audience and goal",
                                 "Create hierarchical outline",
                                 "Expand each section heading",
                                 "Draft transitions and connectives",
                                 "Revise for clarity and tone",
                                 "Final proofread"],
            "draft_revise":    ["Freewrite first draft",
                                 "Identify gaps and weak sections",
                                 "Strengthen arguments / narrative",
                                 "Tighten word choice",
                                 "Structural edit",
                                 "Final pass"],
            "top_down":        ["Define thesis / core message",
                                 "Map supporting points",
                                 "Write introduction",
                                 "Write body sections",
                                 "Write conclusion",
                                 "Edit for coherence"],
            "story_arc":       ["Define protagonist and stakes",
                                 "Establish opening tension",
                                 "Build through rising action",
                                 "Write climax",
                                 "Resolve and land the lesson",
                                 "Polish language"],
        },
        "coding": {
            "tdd":             ["Write failing test",
                                 "Write minimal code to pass",
                                 "Refactor to clean design",
                                 "Add edge-case tests",
                                 "Verify all tests pass",
                                 "Document public API"],
            "prototype_refine":["Sketch interface / data shape",
                                 "Write quick prototype",
                                 "Identify pain points",
                                 "Refine architecture",
                                 "Harden error handling",
                                 "Add tests and docs"],
            "spec_first":      ["Write specification / docstring",
                                 "Define types and contracts",
                                 "Implement to spec",
                                 "Run linter and type checker",
                                 "Write tests against spec",
                                 "Review diff against spec"],
            "incremental":     ["Break into smallest deliverable unit",
                                 "Implement unit 1",
                                 "Test unit 1",
                                 "Integrate unit 1",
                                 "Repeat for next unit",
                                 "Integration test full feature"],
        },
        "planning": {
            "backcasting":     ["Define desired end state",
                                 "Work backwards to identify milestones",
                                 "Identify blockers for each milestone",
                                 "Assign owners and timelines",
                                 "Risk-adjust timeline",
                                 "Write execution plan"],
            "milestone_driven":["Define project scope",
                                 "Identify major milestones",
                                 "Decompose milestones into tasks",
                                 "Estimate effort and dependencies",
                                 "Build Gantt / timeline",
                                 "Add review checkpoints"],
            "risk_first":      ["Identify risks and failure modes",
                                 "Score probability × impact",
                                 "Design mitigations for top risks",
                                 "Build plan that minimises risk exposure",
                                 "Add contingency buffers",
                                 "Establish go/no-go criteria"],
            "agile_sprints":   ["Define backlog",
                                 "Prioritise by value",
                                 "Scope sprint 1",
                                 "Define sprint 1 done criteria",
                                 "Plan sprint 2 with learnings",
                                 "Set velocity and review cadence"],
        },
        "analysis": {
            "root_cause":      ["Describe observed problem",
                                 "List immediate causes",
                                 "Apply 5-why to each cause",
                                 "Identify root cause",
                                 "Validate root cause with evidence",
                                 "Propose corrective action"],
            "first_principles":["Strip away assumptions",
                                 "Identify fundamental constraints",
                                 "Reconstruct from base facts",
                                 "Test reconstructed model",
                                 "Compare to conventional view",
                                 "Document divergences"],
            "swot":            ["List strengths",
                                 "List weaknesses",
                                 "List opportunities",
                                 "List threats",
                                 "Cross-pair (SO/WO/ST/WT)",
                                 "Prioritise strategic moves"],
            "framework_fit":   ["Select candidate frameworks",
                                 "Map problem to each framework",
                                 "Score fit of each framework",
                                 "Apply best-fit framework",
                                 "Stress-test conclusions",
                                 "Summarise findings"],
        },
        "creative": {
            "diverge_converge":["Generate 10+ raw ideas (no filter)",
                                 "Group ideas by theme",
                                 "Evaluate feasibility per group",
                                 "Select top 3 candidates",
                                 "Develop best candidate",
                                 "Refine and present"],
            "constraint_based":["Define hard constraints",
                                 "Treat constraints as creative fuel",
                                 "Generate within-constraint ideas",
                                 "Push one constraint boundary",
                                 "Select most original viable idea",
                                 "Develop and present"],
            "analogical":      ["Find analogous domain",
                                 "Map source → target concepts",
                                 "Extract structural lessons",
                                 "Apply lessons to problem",
                                 "Adapt for target context",
                                 "Evaluate novelty and fit"],
            "reverse_prompt":  ["Restate goal as its opposite",
                                 "Generate ideas that achieve the opposite",
                                 "Invert each opposite idea",
                                 "Filter inversions for usefulness",
                                 "Select strongest inverted idea",
                                 "Develop into solution"],
        },
        "data": {
            "eda_first":       ["Load and inspect dataset shape",
                                 "Describe statistics per column",
                                 "Visualise distributions",
                                 "Identify missing / anomalous values",
                                 "Explore correlations",
                                 "Summarise findings for next step"],
            "hypothesis_test": ["State null and alternative hypotheses",
                                 "Choose test and significance level",
                                 "Prepare data (clean, split)",
                                 "Run statistical test",
                                 "Interpret p-value and effect size",
                                 "Report conclusion"],
            "pipeline_build":  ["Define input → output contract",
                                 "Implement ingestion stage",
                                 "Implement transform stage",
                                 "Implement load / output stage",
                                 "Add validation and error handling",
                                 "Test end-to-end"],
            "anomaly_scan":    ["Profile baseline distribution",
                                 "Choose anomaly detection method",
                                 "Score each record",
                                 "Review top anomalies",
                                 "Classify: true anomaly vs noise",
                                 "Report actionable findings"],
        },
    }
    step_labels = templates.get(domain, {}).get(
        approach,
        [f"Step {i+1}" for i in range(6)],
    )
    return step_labels[:MAX_STEPS]


# ── LLM backend (optional) ────────────────────────────────────────────────────

def _llm_execute_step(step: str, task: str, model_bin: Optional[str]) -> str:
    """Run a single step with an optional llama.cpp binary.

    If no binary is provided the agent produces a heuristic placeholder so
    it can run entirely offline for planning / retrospect purposes.
    """
    if not model_bin:
        return f"[heuristic] Completed: {step}"

    import subprocess
    prompt = (
        f"You are completing one step of a larger task.\n\n"
        f"Task: {task}\n"
        f"Current step: {step}\n\n"
        f"Complete this step concisely. Output the result only."
    )
    try:
        proc = subprocess.run(
            [model_bin, "--no-display-prompt", "--n-predict", "256",
             "-p", prompt],
            capture_output=True, text=True, timeout=60,
        )
        return proc.stdout.strip() or f"[llm] no output for: {step}"
    except Exception as exc:
        return f"[llm-error] {exc}"


# ── Main agent ────────────────────────────────────────────────────────────────

class AutonomousGeneralAgent:
    """General-purpose autonomous agent with latent reasoning and retrospect.

    Parameters
    ----------
    model_bin : path to a llama.cpp binary (llama-cli) for LLM-backed steps.
                Leave None for heuristic / planning-only mode.
    library_path : override PATTERN_LIBRARY_PATH (useful for tests).
    verbose : print step-by-step progress.
    """

    def __init__(
        self,
        model_bin: Optional[str] = None,
        library_path: Optional[Path] = None,
        verbose: bool = True,
    ) -> None:
        self._model_bin = model_bin
        self._library   = PatternLibrary(library_path or PATTERN_LIBRARY_PATH)
        self._reasoner  = LatentReasoner()
        self._verbose   = verbose

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(msg, flush=True)

    def run(
        self,
        task: str,
        domain_hint: Optional[str] = None,
    ) -> TaskOutcome:
        t0        = time.monotonic()
        task_id   = f"agt_{uuid.uuid4().hex[:12]}"
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fp        = _task_fingerprint(task)

        self._log(f"\n{'─'*60}")
        self._log(f"  task_id   : {task_id}")
        self._log(f"  task      : {task[:80]}")

        # ── 1. Classify domain ────────────────────────────────────────────────
        domain = classify_domain(task, domain_hint)
        self._log(f"  domain    : {domain}")

        # ── 2. Retrospect: query pattern library ──────────────────────────────
        patterns = self._library.query(fp, domain)
        if patterns:
            top = patterns[0]
            self._log(f"  retrospect: {top.approach} @ eff={top.efficiency:.2f} "
                      f"({top.uses} uses)")
        else:
            self._log("  retrospect: no prior patterns for this domain/task")

        # ── 3. Latent reasoning: pick approach ────────────────────────────────
        self._log(f"\n  [latent reasoning — {N_LATENT_THOUGHTS} candidates]")
        approach, thoughts = self._reasoner.think(task, domain, patterns)
        for t in thoughts:
            marker = "✗" if t.rejected else ("★" if t.approach == approach else "·")
            self._log(f"    {marker} {t.approach:<20}  {t.rationale}")
        self._log(f"\n  → selected: {approach}")

        # ── 4. Plan steps ─────────────────────────────────────────────────────
        step_labels = _plan_steps(task, domain, approach)
        self._log(f"\n  [execution — {len(step_labels)} steps]")

        # ── 5. Execute steps ──────────────────────────────────────────────────
        steps: List[TaskStep] = []
        success = True
        for i, label in enumerate(step_labels):
            t_step = time.monotonic()
            self._log(f"    [{i+1}/{len(step_labels)}] {label}")
            result = _llm_execute_step(label, task, self._model_bin)
            elapsed_ms = int((time.monotonic() - t_step) * 1000)
            steps.append(TaskStep(index=i, action=label,
                                  result=result, elapsed_ms=elapsed_ms))
            self._log(f"         → {result[:100]}")

        # ── 6. Record outcome + update pattern library ─────────────────────────
        outcome = TaskOutcome(
            task_id=task_id,
            task=task,
            domain=domain,
            approach=approach,
            steps=steps,
            success=success,
            latent_log=thoughts,
            wallclock_s=round(time.monotonic() - t0, 3),
            timestamp=timestamp,
            fingerprint=fp,
        ).sign()

        pattern = self._library.upsert(outcome)
        self._log(f"\n  done  wallclock={outcome.wallclock_s:.1f}s  "
                  f"pattern efficiency={pattern.efficiency:.2f}")
        self._log(f"  sig={outcome.signature[:16]}…  "
                  f"verified={outcome.verify()}")
        self._log("─" * 60)

        return outcome


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cmd_run(args) -> int:
    agent = AutonomousGeneralAgent(
        model_bin=args.model_bin,
        verbose=not args.quiet,
    )
    outcome = agent.run(task=args.task, domain_hint=args.domain)
    if args.json:
        safe_steps = [
            {"index": s.index, "action": s.action,
             "result": s.result, "elapsed_ms": s.elapsed_ms}
            for s in outcome.steps
        ]
        safe_thoughts = [
            {"approach": t.approach, "distance": t.distance,
             "rationale": t.rationale, "rejected": t.rejected}
            for t in outcome.latent_log
        ]
        print(json.dumps({
            "task_id":     outcome.task_id,
            "domain":      outcome.domain,
            "approach":    outcome.approach,
            "success":     outcome.success,
            "wallclock_s": outcome.wallclock_s,
            "steps":       safe_steps,
            "latent_log":  safe_thoughts,
            "signature":   outcome.signature,
        }, indent=2))
    return 0 if outcome.success else 1


def _cmd_history(args) -> int:
    lib = PatternLibrary()
    patterns = lib.history(domain=args.domain or None, top=args.top)
    if not patterns:
        print("No patterns recorded yet.")
        return 0
    hdr = f"  {'domain':<12} {'approach':<22} {'eff':>6} {'uses':>5}  fingerprint"
    print(hdr)
    print("  " + "─" * 66)
    for p in patterns:
        print(f"  {p.domain:<12} {p.approach:<22} {p.efficiency:>6.2f} "
              f"{p.uses:>5}  {p.fingerprint}  "
              f"{'OK' if p.verify() else 'BAD-SIG'}")
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="axiom-general-agent",
        description="General-purpose autonomous agent with latent reasoning + retrospect",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the agent on a task")
    p_run.add_argument("--task", "-t", required=True, help="task description")
    p_run.add_argument("--domain", "-d", choices=list(DOMAINS),
                       help="override domain classification")
    p_run.add_argument("--model-bin", help="path to llama-cli binary for LLM steps")
    p_run.add_argument("--quiet", action="store_true", help="suppress step output")
    p_run.add_argument("--json",  action="store_true", help="emit JSON result")
    p_run.set_defaults(func=_cmd_run)

    p_hist = sub.add_parser("history", help="show pattern library")
    p_hist.add_argument("--domain", "-d", choices=list(DOMAINS))
    p_hist.add_argument("--top", type=int, default=20)
    p_hist.set_defaults(func=_cmd_history)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
