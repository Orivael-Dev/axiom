#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AXIOM Dev Agent v2 — the four-layer constitutional code agent.

The CPI work for ORVL-022 (PRs #6–#9) proved a developmental pattern
that lifts cleanly into software engineering. v1 of the dev agent
(axiom_dev_agent.py) is a single-loop LLM caller; v2 wraps the same
work in the four layers from CPI:

  Layer 0 — Reflex     : pure-Python AST + forbidden-pattern checks on
                          the proposed diff. Sub-millisecond, no LLM
                          call. Equivalent of "don't fall over".
  Layer 1 — Reviewer   : per-task-class competence + forecast of
                          "will this PR survive code review?" Emits
                          PASS / SOFTEN / VETO with reasons. Mirror
                          of CPI's SupervisoryGuard.
  Layer 2 — Curriculum : AXM-backed memory of past task trajectories.
                          Persists competence across sessions, transfers
                          trust between similar task classes via
                          AXM-derived similarity, suggests next task
                          in the zone of proximal development. Mirror
                          of CPI's DevelopmentalCurriculum (PR #8).
  Layer 3 — Examiner   : sealed CI suite, signed under an independent
                          derived key. The agent under test cannot
                          forge a passing certificate. Mirror of
                          CPI's MotionExaminer (PR #9).

Trust levels (lower = more advisory, higher = more authoritative):

  Reflex     TRUST_LEVEL = 4   (the floor — refusal IS sacred)
  Reviewer   TRUST_LEVEL = 3
  Curriculum TRUST_LEVEL = 3
  Examiner   TRUST_LEVEL = 2

Four independent derived keys so no layer can forge another's output:

  derive_key(b"axiom-dev-reflex-v1")
  derive_key(b"axiom-dev-reviewer-v1")
  derive_key(b"axiom-dev-curriculum-v1")
  derive_key(b"axiom-dev-examiner-v1")

Spec : axiom_files/core/axiom_dev_agent_v2.axiom
HMAC : SHA-256 over canonical JSON, hex digest.
BUG-003: UTF-8 output encoding.
"""

from __future__ import annotations

import ast
import hashlib
import hmac as hmac_lib
import json
import math
import os
import re
import sys
import types as _types
from collections import deque
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── CANNOT_MUTATE constants ──────────────────────────────────────────────
TRUST_LEVEL: int = 3                       # facade trust; layers vary
FLOOR_PASSING_LIKELIHOOD: float = 0.20     # below this → VETO
HIGH_THRESHOLD: float = 0.80               # above this → unconditional PASS
COMPETENCE_BUILD_PER_SUCCESS: float = 0.05
COMPETENCE_DROP_ON_VETO: float = 0.30
COMPETENCE_DROP_ON_CI_FAIL: float = 0.40
TRANSFER_CAP_PER_CALL: float = 0.40
SIMILARITY_FLOOR: float = 0.30
SUITE_ID: str = "axiom-dev-agent-baseline-v1"
SUITE_VERSION: str = "1.0"

TASK_CLASSES: Tuple[str, ...] = (
    "FEATURE", "BUG_FIX", "EFFICIENCY", "SPEC_WRITING", "DOCUMENTATION",
)

# Forbidden code patterns the reflex layer refuses outright.
_FORBIDDEN_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\beval\s*\(",                      "eval() — refuses"),
    (r"\bexec\s*\(",                      "exec() — refuses"),
    (r"\bos\.system\s*\(",                "os.system() — refuses"),
    (r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True", "subprocess shell=True — refuses"),
    (r"\bassert\s+False\b",               "assert False — refuses"),
    # Master-key-like 64-hex strings should never appear in diffs.
    (r"\b[A-Fa-f0-9]{64}\b",              "looks like a master key — refuses"),
)


_FROZEN_NAMES = frozenset({
    "TRUST_LEVEL", "FLOOR_PASSING_LIKELIHOOD", "HIGH_THRESHOLD",
    "COMPETENCE_BUILD_PER_SUCCESS", "COMPETENCE_DROP_ON_VETO",
    "COMPETENCE_DROP_ON_CI_FAIL", "TRANSFER_CAP_PER_CALL",
    "SIMILARITY_FLOOR", "SUITE_ID", "SUITE_VERSION", "TASK_CLASSES",
})


# ── Exceptions ────────────────────────────────────────────────────────────
class DevAgentError(Exception):
    """Base for AxiomDevAgentV2 errors."""


class ReflexRefusal(DevAgentError):
    """Layer 0 refused the proposed diff outright."""


class TransferCapExceeded(DevAgentError):
    """Curriculum transfer would breach the per-call cap."""


# ── Signing helpers ───────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac_lib.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def _reflex_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-dev-reflex-v1")


def _reviewer_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-dev-reviewer-v1")


def _curriculum_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-dev-curriculum-v1")


def _examiner_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-dev-examiner-v1")


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN_NAMES:
        raise AttributeError(
            f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,),
                      {"__setattr__": _module_setattr})


# ── Frozen dataclasses ────────────────────────────────────────────────────
@dataclass(frozen=True)
class DevTask:
    """A unit of dev work the agent is asked to perform. The agent
    never sees a raw user prompt — every task is pre-classified."""
    id:               str
    description:      str
    task_class:       str
    artifact_path:    str
    proposed_diff:    str               # the patch (text) the agent wants to apply
    cited_patterns:   Tuple[str, ...]   # IDs of AXM TrajectoryBlocks the diff invokes


@dataclass(frozen=True)
class ReflexResult:
    """Layer 0 output. ok=False blocks the rest of the pipeline."""
    task_id:    str
    ok:         bool
    reasons:    Tuple[str, ...]
    signature:  str = ""


@dataclass(frozen=True)
class ReviewVerdict:
    """Layer 1 output. PASS / SOFTEN / VETO."""
    task_id:           str
    verdict:           str
    task_class:        str
    competence:        float
    forecast_passing:  float
    min_safe:          float
    softening_advice:  Tuple[str, ...]   # what to do if SOFTEN
    reasons:           Tuple[str, ...]
    signature:         str = ""


@dataclass(frozen=True)
class CurriculumSuggestion:
    """Layer 2 output. The next task class to attempt + its rationale."""
    task_class:           str
    target_difficulty:    str            # "low" | "moderate" | "high"
    current_competence:   float
    rationale:            str
    signature:            str = ""


@dataclass(frozen=True)
class TransferEvent:
    src_class:    str
    dst_class:    str
    similarity:   float
    old_dst:      float
    new_dst:      float
    raise_delta:  float
    signature:    str = ""


@dataclass(frozen=True)
class CICheck:
    """One sealed CI check. Hardcoded in the SUITE tuple."""
    id:          str
    description: str


@dataclass(frozen=True)
class CIResult:
    suite_id:         str
    suite_version:    str
    checks_run:       int
    checks_passed:    int
    checks_failed:    int
    failure_summary:  Tuple[str, ...]
    issued_at:        str
    signature:        str = ""


@dataclass(frozen=True)
class DevHandleOutcome:
    """The four-layer pipeline outcome for one task."""
    task_id:        str
    reflex:         ReflexResult
    review:         Optional[ReviewVerdict]
    ci:             Optional[CIResult]
    final_verdict:  str        # "MERGED" | "SOFTEN_REQUESTED" | "VETO" | "REFLEX_REFUSED"


# ── Layer 0 — Reflex ────────────────────────────────────────────────────
class CodeReflex:
    """Pure-Python static checks on the proposed diff. No LLM call,
    no network, sub-millisecond. Equivalent of "don't fall over"."""

    def check(self, task: DevTask) -> ReflexResult:
        reasons: List[str] = []
        diff = task.proposed_diff

        # 1. AST parse (best-effort — diffs are partial, so try as
        # a Python module first, then bail to pattern-only if syntax
        # genuinely fails on the snippet alone).
        try:
            ast.parse(diff)
        except SyntaxError as exc:
            # Allow if it's clearly a unified-diff snippet (has + or
            # - line prefixes). Otherwise this is a real syntax issue.
            if not _looks_like_unified_diff(diff):
                reasons.append(f"syntax error: {exc.msg}")

        # 2. Forbidden patterns.
        for pat, label in _FORBIDDEN_PATTERNS:
            if re.search(pat, diff):
                reasons.append(label)

        payload = {
            "task_id": task.id,
            "ok":      not reasons,
            "reasons": tuple(reasons),
        }
        sig = _sign(_reflex_key(), payload)
        return ReflexResult(**payload, signature=sig)


def _looks_like_unified_diff(text: str) -> bool:
    """Heuristic — unified diffs have +/- line prefixes and @@ hunks."""
    lines = text.splitlines()
    if not lines:
        return False
    has_prefix = any(L.startswith(("+", "-")) for L in lines[:20])
    has_hunk = any("@@" in L for L in lines[:20])
    return has_prefix or has_hunk


# ── Layer 1 — Reviewer ──────────────────────────────────────────────────
class PullRequestReviewer:
    """Per-task-class competence + forecast of PR survival. Emits
    PASS / SOFTEN / VETO. Mirror of CPI's SupervisoryGuard at the
    architectural level; specialised to code review heuristics."""

    def __init__(self):
        self._competence: Dict[str, float] = {c: 0.0 for c in TASK_CLASSES}
        self._reviews_total: int = 0

    def get(self, task_class: str) -> float:
        return self._competence.get(task_class, 0.0)

    def set(self, task_class: str, value: float) -> None:
        if not 0.0 <= value <= 1.0:
            raise ValueError("competence must be in [0, 1]")
        self._competence[task_class] = float(value)

    def set_all(self, value: float) -> None:
        for k in list(self._competence.keys()):
            self.set(k, value)

    def review(self, task: DevTask, *,
               available_patterns: Optional[Sequence[str]] = None
               ) -> ReviewVerdict:
        """Forecast pass-likelihood from three signals:
          - per-class competence (heaviest weight)
          - diff size (larger diffs → more risk)
          - citation grounding (cited_patterns in available_patterns)
        """
        cls = task.task_class
        c = self.get(cls)
        diff_lines = len(task.proposed_diff.splitlines())
        size_penalty = min(0.30, diff_lines / 1000.0)
        novelty_penalty = 0.50 * (1.0 - c)
        if available_patterns and task.cited_patterns:
            hits = sum(1 for p in task.cited_patterns
                        if p in available_patterns)
            citation_penalty = 0.0 if hits >= 1 else 0.20
        elif not task.cited_patterns:
            citation_penalty = 0.20
        else:
            citation_penalty = 0.0
        min_pred = max(0.0, 1.0 - novelty_penalty - size_penalty
                              - citation_penalty)
        min_safe = (FLOOR_PASSING_LIKELIHOOD
                     + (HIGH_THRESHOLD - FLOOR_PASSING_LIKELIHOOD)
                     * (1.0 - c))

        reasons: List[str] = []
        advice: List[str] = []
        if min_pred >= min_safe:
            verdict = "PASS"
            reasons.append(f"forecast {min_pred:.2f} ≥ min_safe {min_safe:.2f}")
        elif min_pred >= FLOOR_PASSING_LIKELIHOOD:
            verdict = "SOFTEN"
            reasons.append(f"forecast {min_pred:.2f} < min_safe {min_safe:.2f}")
            if size_penalty >= 0.15:
                advice.append("split the diff into smaller commits "
                              f"(currently {diff_lines} lines)")
            if citation_penalty > 0:
                advice.append("cite an AXM TrajectoryBlock pattern for "
                              "this change (cited_patterns is empty or "
                              "doesn't match the corpus)")
            if novelty_penalty >= 0.30:
                advice.append(f"competence on {cls} is {c:.2f} — pair "
                              "with a reviewer who has a longer track "
                              "record on this class")
        else:
            verdict = "VETO"
            reasons.append(
                f"forecast {min_pred:.2f} < floor "
                f"{FLOOR_PASSING_LIKELIHOOD:.2f} — "
                "diff is too far from the corpus baseline"
            )

        self._reviews_total += 1
        payload = {
            "task_id":          task.id,
            "verdict":          verdict,
            "task_class":       cls,
            "competence":       round(c, 4),
            "forecast_passing": round(min_pred, 4),
            "min_safe":         round(min_safe, 4),
            "softening_advice": tuple(advice),
            "reasons":          tuple(reasons),
        }
        sig = _sign(_reviewer_key(), payload)
        return ReviewVerdict(**payload, signature=sig)

    def on_outcome(self, task_class: str, *,
                   ci_passed: Optional[bool] = None,
                   review_vetoed: bool = False) -> None:
        """Update competence based on the resolved outcome of a task.
        Asymmetric updates mirror CPI's CompetenceTracker:
          - clean CI pass : +COMPETENCE_BUILD_PER_SUCCESS
          - review VETO   : −COMPETENCE_DROP_ON_VETO
          - CI fail       : −COMPETENCE_DROP_ON_CI_FAIL
        """
        if task_class not in self._competence:
            self._competence[task_class] = 0.0
        if review_vetoed:
            self._competence[task_class] = max(
                0.0, self._competence[task_class] - COMPETENCE_DROP_ON_VETO)
            return
        if ci_passed is True:
            self._competence[task_class] = min(
                1.0, self._competence[task_class]
                      + COMPETENCE_BUILD_PER_SUCCESS)
        elif ci_passed is False:
            self._competence[task_class] = max(
                0.0, self._competence[task_class]
                      - COMPETENCE_DROP_ON_CI_FAIL)

    def snapshot(self) -> dict:
        return {
            "competence":      dict(self._competence),
            "reviews_total":   self._reviews_total,
        }


# ── Layer 2 — Curriculum ────────────────────────────────────────────────
class DevCurriculum:
    """AXM-backed memory + per-task-class similarity + persistence.
    Mirror of CPI's DevelopmentalCurriculum (PR #8). When an AXM
    container is supplied, similarity is derived from the cosine of
    bag-of-words over TrajectoryBlock task_pattern strings (one
    bag per task_class cluster). With no AXM, the curriculum still
    runs — transfer just defaults to no-op."""

    def __init__(self, reviewer: PullRequestReviewer,
                 axm_container: Any = None,
                 persistence_path: Optional[str] = None):
        self.reviewer = reviewer
        self.axm = axm_container
        self.persistence_path = (
            Path(persistence_path) if persistence_path else None
        )
        self._similarity_graph: Dict[frozenset, float] = {}
        self._consolidation_count = 0
        self._loaded_from_disk = False
        if self.axm is not None:
            self._build_similarity_graph()
        if self.persistence_path is not None and self.persistence_path.exists():
            self._load_persisted_state()

    def _build_similarity_graph(self) -> None:
        """Bag-of-words cosine over AXM TrajectoryBlock task_pattern
        strings. Trajectories are grouped by task_class via the
        prefix convention used by axiom_training_to_axm.py
        (traj-axiom-agent-<type>)."""
        per_class: Dict[str, Dict[str, int]] = {}
        for traj in self.axm.trajectories:
            tcls = self._classify_trajectory(traj.id)
            if tcls is None:
                continue
            bag = per_class.setdefault(tcls, {})
            for tok in re.findall(r"[a-z_]+", traj.task_pattern.lower()):
                bag[tok] = bag.get(tok, 0) + 1
        classes = list(per_class.keys())
        for i, ca in enumerate(classes):
            for cb in classes[i + 1:]:
                sim = _cosine_bow(per_class[ca], per_class[cb])
                self._similarity_graph[frozenset((ca, cb))] = sim

    @staticmethod
    def _classify_trajectory(tid: str) -> Optional[str]:
        """Map an axiom_training_to_axm-style trajectory id onto a
        TASK_CLASSES bucket. Falls through to None for unknowns."""
        m = re.match(r"traj-axiom-agent-(\w+)", tid)
        if not m:
            return None
        kind = m.group(1)
        if kind in {"bug_fix", "bug_knowledge", "bug_pattern_detection"}:
            return "BUG_FIX"
        if kind in {"spec_writing", "spec_explanation", "spec_authoring"}:
            return "SPEC_WRITING"
        if kind in {"implementation_pattern", "test_first_implementation",
                    "guard_writing"}:
            return "FEATURE"
        if kind in {"benchmark_knowledge", "pattern_knowledge"}:
            return "EFFICIENCY"
        if kind in {"language_knowledge", "orvl_knowledge"}:
            return "DOCUMENTATION"
        return None

    def similarity(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        return self._similarity_graph.get(frozenset((a, b)), 0.0)

    def transfer(self, src: str, dst: str, *,
                 force_similarity: Optional[float] = None
                 ) -> TransferEvent:
        sim = (force_similarity if force_similarity is not None
                else self.similarity(src, dst))
        old_dst = self.reviewer.get(dst)
        new_dst = old_dst
        raise_delta = 0.0
        if sim >= SIMILARITY_FLOOR and src != dst:
            src_c = self.reviewer.get(src)
            seeded = min(1.0, src_c * sim)
            if seeded > old_dst:
                raise_delta = seeded - old_dst
                if raise_delta > TRANSFER_CAP_PER_CALL:
                    raise TransferCapExceeded(
                        f"transfer {src}→{dst} would raise by "
                        f"{raise_delta:.3f} > cap "
                        f"{TRANSFER_CAP_PER_CALL:.3f}"
                    )
                self.reviewer.set(dst, seeded)
                new_dst = seeded

        payload = {
            "src_class":   src,
            "dst_class":   dst,
            "similarity":  round(sim, 4),
            "old_dst":     round(old_dst, 4),
            "new_dst":     round(new_dst, 4),
            "raise_delta": round(raise_delta, 4),
        }
        sig = _sign(_curriculum_key(), payload)
        return TransferEvent(**payload, signature=sig)

    def suggest_next_task_class(self) -> CurriculumSuggestion:
        scores = dict(self.reviewer.snapshot()["competence"])
        ripe = [(c, s) for c, s in scores.items() if 0.10 <= s <= 0.85]
        if ripe:
            ripe.sort(key=lambda cs: cs[1])  # lowest first = most ripe
            cls, score = ripe[0]
            difficulty = "low" if score < 0.30 else (
                "moderate" if score < 0.60 else "high")
            rationale = (f"competence {score:.2f} is in the SOFTEN-zone; "
                         f"good zone-of-proximal-development pick")
        else:
            cls, score = min(scores.items(), key=lambda cs: cs[1])
            difficulty = "low"
            rationale = (f"all classes saturated or fresh; build "
                         f"{cls} from {score:.2f}")
        payload = {
            "task_class":         cls,
            "target_difficulty":  difficulty,
            "current_competence": round(score, 4),
            "rationale":          rationale,
        }
        sig = _sign(_curriculum_key(), payload)
        return CurriculumSuggestion(**payload, signature=sig)

    def consolidate(self) -> bool:
        if self.persistence_path is None:
            return False
        snap = self.reviewer.snapshot()
        payload = {
            "format_version":      "0.1",
            "consolidation_count": self._consolidation_count + 1,
            "competence":          dict(snap["competence"]),
            "reviews_total":       int(snap["reviews_total"]),
            "timestamp":           datetime.now(timezone.utc).isoformat(),
        }
        payload["signature"] = _sign(_curriculum_key(), payload)
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self.persistence_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        self._consolidation_count += 1
        return True

    def _load_persisted_state(self) -> None:
        raw = self.persistence_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        sig = data.pop("signature", None)
        if sig is None:
            raise ValueError("curriculum persistence: missing signature")
        expected = _sign(_curriculum_key(), data)
        if not hmac_lib.compare_digest(sig, expected):
            raise ValueError("curriculum persistence: signature mismatch")
        for cls, score in data.get("competence", {}).items():
            if 0.0 <= float(score) <= 1.0:
                self.reviewer.set(cls, float(score))
        self._consolidation_count = int(data.get("consolidation_count", 0))
        self._loaded_from_disk = True


def _cosine_bow(a: Mapping[str, int], b: Mapping[str, int]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[t] * b.get(t, 0) for t in a)
    na = math.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (na * nb)


# ── Layer 3 — Examiner (sealed CI suite) ─────────────────────────────────
#
# Black-box. Knows nothing about the agent's internal state. Sees only
# the (task, reflex, review) triple plus its own sealed checks.
CI_SUITE: Tuple[CICheck, ...] = (
    CICheck("reflex_did_not_refuse",
            "Layer-0 reflex did not refuse the proposed diff"),
    CICheck("reviewer_did_not_veto",
            "Layer-1 reviewer did not VETO the task"),
    CICheck("task_class_is_recognized",
            "task.task_class is one of TASK_CLASSES"),
    CICheck("diff_under_size_ceiling",
            "Proposed diff is under 500 lines (architectural slice limit)"),
    CICheck("cited_pattern_present_on_bugfix",
            "If task_class=BUG_FIX, at least one cited_pattern is set"),
)


class CIExaminer:
    """Sealed CI suite. The examiner sees only:
      - the (task, reflex, review) triple
      - its own sealed check list (CANNOT_MUTATE)
    It NEVER reads reviewer._competence, curriculum state, or any
    other internal collaborator. Signs under its own derived key
    so the rest of the stack cannot forge a passing certificate."""

    def __init__(self, suite: Sequence[CICheck] = CI_SUITE):
        self._suite: Tuple[CICheck, ...] = tuple(suite)

    @property
    def suite(self) -> Tuple[CICheck, ...]:
        return self._suite

    def evaluate(self, task: DevTask, reflex: ReflexResult,
                 review: Optional[ReviewVerdict]) -> CIResult:
        passed = 0
        failures: List[str] = []
        for check in self._suite:
            ok, reason = self._run_check(check, task, reflex, review)
            if ok:
                passed += 1
            else:
                failures.append(f"{check.id}: {reason}")
        body: dict = {
            "suite_id":         SUITE_ID,
            "suite_version":    SUITE_VERSION,
            "checks_run":       len(self._suite),
            "checks_passed":    passed,
            "checks_failed":    len(self._suite) - passed,
            "failure_summary":  tuple(failures),
            "issued_at":        datetime.now(timezone.utc).isoformat(),
        }
        sig = _sign(_examiner_key(), body)
        return CIResult(**body, signature=sig)

    def verify_certificate(self, cert: CIResult) -> bool:
        body = {k: v for k, v in asdict(cert).items() if k != "signature"}
        body["failure_summary"] = tuple(body.get("failure_summary", ()))
        expected = _sign(_examiner_key(), body)
        return hmac_lib.compare_digest(cert.signature, expected)

    @staticmethod
    def _run_check(check: CICheck, task: DevTask, reflex: ReflexResult,
                   review: Optional[ReviewVerdict]) -> Tuple[bool, str]:
        if check.id == "reflex_did_not_refuse":
            return reflex.ok, ("; ".join(reflex.reasons)
                                if reflex.reasons else "")
        if check.id == "reviewer_did_not_veto":
            if review is None:
                return False, "no review provided"
            return review.verdict != "VETO", (
                f"verdict={review.verdict}: {'; '.join(review.reasons)}"
            )
        if check.id == "task_class_is_recognized":
            return task.task_class in TASK_CLASSES, (
                f"unknown task_class={task.task_class!r}")
        if check.id == "diff_under_size_ceiling":
            lines = len(task.proposed_diff.splitlines())
            return lines <= 500, f"diff has {lines} lines (>500)"
        if check.id == "cited_pattern_present_on_bugfix":
            if task.task_class != "BUG_FIX":
                return True, ""
            return bool(task.cited_patterns), "BUG_FIX missing cited_patterns"
        return False, f"unknown check {check.id!r}"


# ── Facade — AxiomDevAgentV2 ─────────────────────────────────────────────
class AxiomDevAgentV2:
    """Four-layer constitutional dev agent. The facade ties every
    layer together and exposes one method: handle_task(task)."""

    def __init__(self, *, axm_container: Any = None,
                 persistence_path: Optional[str] = None):
        self.reflex     = CodeReflex()
        self.reviewer   = PullRequestReviewer()
        self.curriculum = DevCurriculum(
            reviewer=self.reviewer,
            axm_container=axm_container,
            persistence_path=persistence_path,
        )
        self.examiner   = CIExaminer()

    def handle_task(self, task: DevTask) -> DevHandleOutcome:
        # Layer 0 — refuses outright if the diff trips reflex checks.
        reflex_result = self.reflex.check(task)
        if not reflex_result.ok:
            return DevHandleOutcome(
                task_id=task.id, reflex=reflex_result,
                review=None, ci=None,
                final_verdict="REFLEX_REFUSED",
            )

        # Layer 1 — reviewer forecast.
        review = self.reviewer.review(task)
        if review.verdict == "VETO":
            self.reviewer.on_outcome(task.task_class, review_vetoed=True)
            return DevHandleOutcome(
                task_id=task.id, reflex=reflex_result,
                review=review, ci=None,
                final_verdict="VETO",
            )
        if review.verdict == "SOFTEN":
            return DevHandleOutcome(
                task_id=task.id, reflex=reflex_result,
                review=review, ci=None,
                final_verdict="SOFTEN_REQUESTED",
            )

        # Layer 3 — sealed CI suite (the teacher).
        ci = self.examiner.evaluate(task, reflex_result, review)
        self.reviewer.on_outcome(task.task_class,
                                  ci_passed=(ci.checks_failed == 0))
        final = "MERGED" if ci.checks_failed == 0 else "VETO"
        return DevHandleOutcome(
            task_id=task.id, reflex=reflex_result, review=review, ci=ci,
            final_verdict=final,
        )

    def status(self) -> dict:
        rev = self.reviewer.snapshot()
        return {
            "trust_level":         TRUST_LEVEL,
            "competence":          rev["competence"],
            "reviews_total":       rev["reviews_total"],
            "consolidation_count": self.curriculum._consolidation_count,
            "loaded_from_disk":    self.curriculum._loaded_from_disk,
            "similarity_pairs":    len(self.curriculum._similarity_graph),
            "suite_id":            SUITE_ID,
            "suite_version":       SUITE_VERSION,
        }

    # ── LLM-backed proposal loop ─────────────────────────────────────
    def propose(self, *,
                description: str,
                task_class: str,
                artifact_path: str = "unknown",
                context: str = "",
                backend: Any = None,
                max_retries: int = 2,
                task_id: Optional[str] = None
                ) -> "DevHandleOutcome":
        """Generate a diff via the LLM backend, build a DevTask, run
        it through the four-layer pipeline. On REFLEX_REFUSED, retry
        up to `max_retries` with the refusal reason fed back to the
        backend as a hint. Never silently accepts a refused diff —
        if all retries refuse, returns the last REFLEX_REFUSED
        outcome so the caller sees what went wrong.

        The LLM is just another diff source: the same four gates
        apply. Generating a diff is NOT the same as merging it."""
        if backend is None:
            from axiom_dev_agent_v2_backends import select_backend
            backend = select_backend(prefer="auto")

        task_id = task_id or f"propose-{int(datetime.now(timezone.utc).timestamp())}"
        retry_hint: Optional[str] = None
        last_outcome: Optional["DevHandleOutcome"] = None

        for attempt in range(max_retries + 1):
            resp = backend.generate_diff(
                description=description, task_class=task_class,
                context=context, retry_hint=retry_hint,
            )
            task = DevTask(
                id=f"{task_id}-attempt{attempt}",
                description=description,
                task_class=task_class,
                artifact_path=artifact_path,
                proposed_diff=resp.diff,
                cited_patterns=tuple(resp.cited_patterns),
            )
            outcome = self.handle_task(task)
            last_outcome = outcome
            if outcome.final_verdict != "REFLEX_REFUSED":
                return outcome
            # Reflex refusal — feed the reason back to the LLM.
            retry_hint = "; ".join(outcome.reflex.reasons) or "unspecified"

        return last_outcome  # type: ignore[return-value]


# ── CLI ──────────────────────────────────────────────────────────────────
def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="axiom_dev_agent_v2",
        description="Constitutional code agent — four-layer pipeline.",
    )
    parser.add_argument("--axm",
                        help="Optional path to an AXM container "
                             "(provides curriculum similarity).")
    parser.add_argument("--persistence",
                        help="Optional path for the curriculum sidecar "
                             "JSON (default: ./axiom_dev_agent_v2.json)")
    parser.add_argument("--status", action="store_true",
                        help="Print the agent status and exit.")
    # `propose` subcommand — generate a diff via the LLM backend and
    # run it through the four-layer pipeline.
    parser.add_argument("--propose", action="store_true",
                        help="Generate a diff via the LLM backend for "
                             "--description / --task-class, then run it "
                             "through the four-layer pipeline.")
    parser.add_argument("--description",
                        help="Task description (used with --propose).")
    parser.add_argument("--task-class",
                        choices=list(TASK_CLASSES),
                        help="Task class (used with --propose).")
    parser.add_argument("--prefer-backend",
                        choices=("auto", "anthropic", "openai", "simulator"),
                        default="auto",
                        help="LLM backend preference (default: auto).")
    args = parser.parse_args(argv)

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("AXIOM_MASTER_KEY not set", file=sys.stderr)
        return 2

    container = None
    if args.axm:
        from axiom_axm import AXMContainer
        container = AXMContainer.from_path(args.axm)

    agent = AxiomDevAgentV2(
        axm_container=container,
        persistence_path=args.persistence or "./axiom_dev_agent_v2.json",
    )
    if args.status:
        print(json.dumps(agent.status(), indent=2, ensure_ascii=True,
                          sort_keys=True))
        return 0

    if args.propose:
        if not args.description or not args.task_class:
            print("--propose requires --description and --task-class",
                  file=sys.stderr)
            return 2
        from axiom_dev_agent_v2_backends import select_backend
        backend = select_backend(prefer=args.prefer_backend)
        outcome = agent.propose(description=args.description,
                                  task_class=args.task_class,
                                  backend=backend)
        print(f"backend       : {backend.name}")
        print(f"final_verdict : {outcome.final_verdict}")
        if outcome.reflex.reasons:
            print(f"reflex reasons: {'; '.join(outcome.reflex.reasons)}")
        if outcome.review:
            print(f"review        : {outcome.review.verdict}  "
                  f"forecast={outcome.review.forecast_passing}  "
                  f"competence={outcome.review.competence}")
        if outcome.ci:
            print(f"ci            : passed={outcome.ci.checks_passed}/"
                  f"{outcome.ci.checks_run}  "
                  f"sig={outcome.ci.signature[:16]}…")
        return 0 if outcome.final_verdict in ("MERGED", "SOFTEN_REQUESTED") else 1

    # Demo: run three representative tasks through the pipeline.
    demos = [
        DevTask(id="demo-1",
                description="add a docstring to the gate module",
                task_class="DOCUMENTATION",
                artifact_path="axiom_intent_gate.py",
                proposed_diff="+ \"\"\"Intent gate — guards model output.\"\"\"\n",
                cited_patterns=()),
        DevTask(id="demo-2",
                description="fix BUG-001 in this regex",
                task_class="BUG_FIX",
                artifact_path="axiom_intent_gate.py",
                proposed_diff="- pattern = r'foo'\n+ pattern = r'foo\\b'\n",
                cited_patterns=("traj-axiom-agent-bug_fix",)),
        DevTask(id="demo-3",
                description="add eval() for dynamic config",
                task_class="FEATURE",
                artifact_path="axiom_config.py",
                proposed_diff="+ config = eval(user_input)\n",
                cited_patterns=()),
    ]
    for task in demos:
        outcome = agent.handle_task(task)
        print(f"{task.id} ({task.task_class}): {outcome.final_verdict}")
        if outcome.reflex.reasons:
            print(f"  reflex: {'; '.join(outcome.reflex.reasons)}")
        if outcome.review:
            print(f"  review: {outcome.review.verdict}  "
                  f"forecast={outcome.review.forecast_passing}  "
                  f"competence={outcome.review.competence}")
            for advice in outcome.review.softening_advice:
                print(f"    advise: {advice}")
        if outcome.ci:
            print(f"  ci    : passed={outcome.ci.checks_passed}/"
                  f"{outcome.ci.checks_run}  "
                  f"sig={outcome.ci.signature[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
