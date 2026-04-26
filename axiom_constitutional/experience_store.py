"""
axiom/experience_store.py
Spec: axiom_files/skill_builder.axiom  (VERSION 1.1)

Experience-driven skill promotion for the SkillBuilder agent.

Common sense is not learned from datasets — it is learned from consequences.
This module stores pattern-action-outcome triples and scores them using a
formula that weighs consequences (outcome) above repetition (seen_count).

ExperienceScore formula (from RULES block — CANNOT_MUTATE: experience_formula):
    score = outcome_value × 0.60 + confidence × 0.25 + min(seen_count / 5, 1.0) × 0.15

Adjusted score (applies category weight):
    adjusted_score = experience_score × category_weight

Promotion criteria (all three must be met):
    seen_count >= 2
    confidence >= 0.75
    adjusted_score >= 0.70

Category weights:
    survival    = 1.0   (survival lessons harden fastest)
    navigation  = 0.8
    reward      = 0.6
    exploration = 0.4

Outcome values:
    positive = 1.0
    neutral  = 0.5
    negative = 0.0

Score bands (from RULES block):
    0.80–1.00 = strong rule
    0.60–0.79 = useful tendency
    0.40–0.59 = weak hypothesis
    0.00–0.39 = do not trust yet
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

# ── Immutable formula constants (CANNOT_MUTATE: experience_formula) ───────────

_OUTCOME_WEIGHT:    float = 0.60
_CONFIDENCE_WEIGHT: float = 0.25
_REPETITION_WEIGHT: float = 0.15
_REPETITION_CAP:    int   = 5        # seen_count / this, capped at 1.0

_PROMOTE_MIN_SEEN:        int   = 2
_PROMOTE_MIN_CONFIDENCE:  float = 0.75
_PROMOTE_MIN_SCORE:       float = 0.70

# ── Type aliases ───────────────────────────────────────────────────────────────

OutcomeType  = Literal["positive", "neutral", "negative"]
CategoryType = Literal["survival", "navigation", "reward", "exploration"]
BiasLevel    = Literal["high", "medium", "low", "none"]

# ── Outcome and category tables ───────────────────────────────────────────────

OUTCOME_VALUES: dict[str, float] = {
    "positive": 1.0,
    "neutral":  0.5,
    "negative": 0.0,
}

CATEGORY_WEIGHTS: dict[str, float] = {
    "survival":    1.0,
    "navigation":  0.8,
    "reward":      0.6,
    "exploration": 0.4,
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ExperienceRecord:
    """
    One pattern-action-outcome triple with derived scores.

    Fields match the EMITS and PROCESS blocks in skill_builder.axiom:
      pattern_id, context_signature, action, outcome, confidence,
      seen_count, experience_score, adjusted_score, last_outcome,
      decision_bias, category.
    """
    pattern_id:        str
    context_signature: str
    action:            str
    outcome:           OutcomeType
    confidence:        float
    category:          CategoryType = "survival"
    seen_count:        int   = 1
    experience_score:  float = 0.0
    adjusted_score:    float = 0.0
    last_outcome:      OutcomeType = "neutral"
    decision_bias:     BiasLevel   = "none"
    outcome_history:   list[str]   = field(default_factory=list)
    promoted:          bool  = False
    promoted_at:       float | None = None
    created_at:        float = field(default_factory=time.time)
    updated_at:        float = field(default_factory=time.time)

    def score_band(self) -> str:
        """Human-readable classification from RULES block score bands."""
        s = self.adjusted_score
        if s >= 0.80:
            return "strong_rule"
        if s >= 0.60:
            return "useful_tendency"
        if s >= 0.40:
            return "weak_hypothesis"
        return "untrusted"


# ── Pure scoring functions ─────────────────────────────────────────────────────

def compute_experience_score(
    outcome_value: float,
    confidence: float,
    seen_count: int,
) -> float:
    """
    ExperienceScore formula from RULES block (CANNOT_MUTATE: experience_formula).

        score = outcome_value × 0.60 + confidence × 0.25 + min(seen_count / 5, 1.0) × 0.15

    All three inputs clamped to [0, 1] before weighting.
    """
    ov = max(0.0, min(1.0, outcome_value))
    cf = max(0.0, min(1.0, confidence))
    rep = min(seen_count / _REPETITION_CAP, 1.0)
    return round(
        ov * _OUTCOME_WEIGHT + cf * _CONFIDENCE_WEIGHT + rep * _REPETITION_WEIGHT,
        4,
    )


def compute_adjusted_score(experience_score: float, category: str) -> float:
    """
    adjusted_score = experience_score × category_weight.
    Unknown categories default to lowest weight (exploration = 0.4).
    """
    weight = CATEGORY_WEIGHTS.get(category, 0.4)
    return round(experience_score * weight, 4)


def meets_promotion_criteria(record: ExperienceRecord) -> bool:
    """
    All three promotion gates from RULES block:
      seen_count >= 2, confidence >= 0.75, adjusted_score >= 0.70
    """
    return (
        record.seen_count >= _PROMOTE_MIN_SEEN
        and record.confidence >= _PROMOTE_MIN_CONFIDENCE
        and record.adjusted_score >= _PROMOTE_MIN_SCORE
    )


def derive_decision_bias(record: ExperienceRecord) -> BiasLevel:
    """
    Map score band to decision_bias:
      strong_rule        → high
      useful_tendency    → medium
      weak_hypothesis    → low
      untrusted          → none
    Also activates CautiousResponse if confidence is high but outcomes are mixed.
    """
    band = record.score_band()
    if band == "strong_rule":
        # CautiousResponse: high confidence but negative outcomes present
        has_negatives = "negative" in record.outcome_history
        if record.confidence >= 0.75 and has_negatives:
            return "low"    # CautiousResponse activates
        return "high"
    if band == "useful_tendency":
        return "medium"
    if band == "weak_hypothesis":
        return "low"
    return "none"


# ── ExperienceStore ───────────────────────────────────────────────────────────

class ExperienceStore:
    """
    Stores and manages ExperienceRecords for SkillBuilder.

    Wraps history_store semantics — keeps a keyed dict of records indexed by
    pattern_id and calls compute_experience_score on every observation.

    Usage:
        store = ExperienceStore()

        # Observe an outcome
        rec = store.observe(
            pattern_id="ghost_approaching_left",
            context_signature="ghost_left_close",
            action="move_right",
            outcome="positive",
            confidence=0.80,
            category="survival",
        )

        # Check whether it should be promoted
        if store.should_promote("ghost_approaching_left"):
            skill = store.to_skill("ghost_approaching_left")
    """

    def __init__(self, decay_not_seen: int = 10):
        """
        decay_not_seen: evict records not updated within this many frames.
                        Matches HISTORY: decay not_seen after 10 frames.
        """
        self._records: dict[str, ExperienceRecord] = {}
        self._decay_not_seen = decay_not_seen
        self._frame: int = 0               # monotonic frame counter
        self._last_seen: dict[str, int] = {}  # pattern_id → frame last observed

    # ── Write ─────────────────────────────────────────────────────────────────

    def observe(
        self,
        pattern_id: str,
        context_signature: str,
        action: str,
        outcome: OutcomeType,
        confidence: float,
        category: CategoryType = "survival",
    ) -> ExperienceRecord:
        """
        Record one observation of a pattern-action-outcome triple.
        Updates experience_score, adjusted_score, and decision_bias on every call.
        Returns the updated ExperienceRecord.
        """
        outcome_value = OUTCOME_VALUES.get(outcome, 0.5)

        if pattern_id in self._records:
            rec = self._records[pattern_id]
            rec.seen_count += 1
            rec.confidence = confidence          # caller supplies current confidence
            rec.last_outcome = outcome
            rec.context_signature = context_signature
            rec.action = action
            rec.category = category
            rec.outcome_history.append(outcome)
        else:
            rec = ExperienceRecord(
                pattern_id=pattern_id,
                context_signature=context_signature,
                action=action,
                outcome=outcome,
                confidence=confidence,
                category=category,
                last_outcome=outcome,
                outcome_history=[outcome],
            )
            self._records[pattern_id] = rec

        rec.experience_score = compute_experience_score(
            outcome_value=outcome_value,
            confidence=confidence,
            seen_count=rec.seen_count,
        )
        rec.adjusted_score = compute_adjusted_score(rec.experience_score, category)
        rec.decision_bias = derive_decision_bias(rec)
        rec.updated_at = time.time()

        self._last_seen[pattern_id] = self._frame
        return rec

    def tick(self) -> None:
        """Advance the frame counter. Call once per game frame."""
        self._frame += 1

    def decay(self) -> list[str]:
        """
        Evict records not seen within decay_not_seen frames.
        Returns list of evicted pattern_ids.
        Matches HISTORY: decay not_seen after 10 frames.
        """
        evicted = []
        for pid, last_frame in list(self._last_seen.items()):
            if self._frame - last_frame >= self._decay_not_seen:
                evicted.append(pid)
                self._records.pop(pid, None)
                self._last_seen.pop(pid, None)
        return evicted

    # ── Query ─────────────────────────────────────────────────────────────────

    def get(self, pattern_id: str) -> ExperienceRecord | None:
        return self._records.get(pattern_id)

    def should_promote(self, pattern_id: str) -> bool:
        """True when all three promotion criteria from RULES block are met."""
        rec = self._records.get(pattern_id)
        return rec is not None and meets_promotion_criteria(rec)

    def promote(self, pattern_id: str) -> ExperienceRecord | None:
        """Mark a record as promoted. Returns the record or None if not found."""
        rec = self._records.get(pattern_id)
        if rec is not None:
            rec.promoted = True
            rec.promoted_at = time.time()
        return rec

    def top_by_score(self, n: int = 10, category: str | None = None) -> list[ExperienceRecord]:
        """Return top-N records sorted by adjusted_score descending."""
        records = list(self._records.values())
        if category:
            records = [r for r in records if r.category == category]
        return sorted(records, key=lambda r: r.adjusted_score, reverse=True)[:n]

    def promotable(self) -> list[ExperienceRecord]:
        """All records that currently meet promotion criteria."""
        return [r for r in self._records.values() if meets_promotion_criteria(r)]

    def to_skill(self, pattern_id: str) -> dict | None:
        """
        Emit a skill dict from an experience record.
        Matches the EMITS block in skill_builder.axiom.
        """
        rec = self._records.get(pattern_id)
        if rec is None:
            return None
        return {
            "skill_id":          rec.pattern_id,
            "name":              _plain_name(rec),
            "trigger":           f"When you see {rec.context_signature}",
            "action":            rec.action,
            "category":          rec.category,
            "confidence":        rec.confidence,
            "experience_score":  rec.experience_score,
            "adjusted_score":    rec.adjusted_score,
            "decision_bias":     rec.decision_bias,
            "last_outcome":      rec.last_outcome,
            "score_band":        rec.score_band(),
            "seen_count":        rec.seen_count,
            "promoted":          rec.promoted,
        }

    def summary(self) -> dict:
        total = len(self._records)
        promoted = sum(1 for r in self._records.values() if r.promoted)
        return {
            "total_experiences":    total,
            "promoted_count":       promoted,
            "promotable_count":     len(self.promotable()),
            "frame":                self._frame,
            "top_survival":         [r.pattern_id for r in self.top_by_score(3, "survival")],
        }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _plain_name(rec: ExperienceRecord) -> str:
    """Generate a plain-language skill name from context and action."""
    ctx = rec.context_signature.replace("_", " ").title()
    act = rec.action.replace("_", " ").title()
    return f"{act} on {ctx}"
