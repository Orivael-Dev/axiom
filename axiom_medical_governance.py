"""Deterministic governance check for the medical_governance delegate.

Post-processes the delegate's raw JSON output and OVERRIDES any
field the LLM got wrong. The honest first-pass from the LLM is fine
for context; this module is the source of truth for what the
medical AXM container records as the governance layer.

Wraps three knowledge sources:
  1. axiom_medical_safety.is_tier_5_pattern  (constitutional block patterns)
  2. axiom_medical_safety.is_emergency       (Priority-0 routing signals)
  3. axiom_redact.SAFE_HARBOR_PATTERNS       (18 HIPAA identifiers)

Returns the canonical governance_layer payload per PDF section 3.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from axiom_medical_safety import (
    is_emergency,
    is_tier_5_pattern,
    matches_clinical_advice,
)


# ── Lazy-load Safe Harbor patterns ──────────────────────────────────


_PHI_PATTERNS: Optional[list[tuple[re.Pattern, str, str, str]]] = None


def _phi_patterns() -> list[tuple[re.Pattern, str, str, str]]:
    """Return the compiled HIPAA Safe Harbor pattern list.

    Imported lazily so a test that only exercises Tier-5 logic
    doesn't pay the import cost of axiom_redact (which pulls in
    extra deps via its FastAPI handler module).
    """
    global _PHI_PATTERNS
    if _PHI_PATTERNS is not None:
        return _PHI_PATTERNS
    try:
        from axiom_redact import SAFE_HARBOR_PATTERNS
    except Exception:
        _PHI_PATTERNS = []
        return _PHI_PATTERNS
    out: list[tuple[re.Pattern, str, str, str]] = []
    for entry in SAFE_HARBOR_PATTERNS:
        # entry: (regex_str, type, replacement, hipaa_rule)
        try:
            out.append((
                re.compile(entry[0], re.IGNORECASE | re.MULTILINE),
                str(entry[1]),
                str(entry[2]),
                str(entry[3]),
            ))
        except re.error:
            continue
    _PHI_PATTERNS = out
    return _PHI_PATTERNS


# ── Public dataclass result ─────────────────────────────────────────


@dataclass(frozen=True)
class GovernanceVerdict:
    phi_present:           bool
    phi_categories:        tuple[str, ...]
    clinical_advice_block: bool
    block_reason:          Optional[str]
    tier_5_match:          Optional[tuple[str, str]]
    emergency:             Optional[str]
    citation_required:     bool
    uncertainty_required:  bool
    requires_human_review: bool

    def to_dict(self) -> dict:
        return {
            "phi_present":          self.phi_present,
            "phi_categories":       list(self.phi_categories),
            "clinical_advice_block": self.clinical_advice_block,
            "block_reason":         self.block_reason,
            "tier_5_match":         (list(self.tier_5_match)
                                     if self.tier_5_match else None),
            "emergency":            self.emergency,
            "citation_required":    self.citation_required,
            "uncertainty_required": self.uncertainty_required,
            "requires_human_review": self.requires_human_review,
        }


# ── Main entry point ────────────────────────────────────────────────


class MedicalGovernanceCheck:
    """Deterministic governance evaluator.

    Stateless — every call to `.evaluate(text)` independently scans
    the input for PHI, Tier-5 patterns, emergency signals, and
    clinical-advice phrasing.
    """

    def evaluate(
        self,
        text: str,
        *,
        context: Optional[dict] = None,
    ) -> GovernanceVerdict:
        """Inspect `text` and return the governance_layer payload.

        `context` is reserved for future use (e.g. carrying the
        AXM container's human_review_threshold so different
        sessions can tighten or loosen the review trigger). Not
        used today — the default policy
        ('patient_specific_or_high_risk') is hardcoded.
        """
        if not isinstance(text, str):
            text = "" if text is None else str(text)

        # 1. Emergency check — Priority 0, always sets human review.
        emergency_signal = is_emergency(text)

        # 2. Tier-5 pattern check — constitutional HARD_BLOCK.
        t5 = is_tier_5_pattern(text)

        # 3. PHI scan via HIPAA Safe Harbor.
        phi_cats: list[str] = []
        for rx, cat, _replacement, _rule in _phi_patterns():
            if rx.search(text):
                if cat not in phi_cats:
                    phi_cats.append(cat)

        # 4. Clinical-advice verb match.
        advice_phrase = matches_clinical_advice(text)

        # Compose verdict.
        clinical_block = (advice_phrase is not None) or (t5 is not None)
        block_reason: Optional[str] = None
        if t5:
            block_reason = (
                f"tier_5:{t5[0]} (matched: {t5[1]!r})"
            )
        elif advice_phrase:
            block_reason = f"clinical_advice_phrase: {advice_phrase!r}"

        requires_review = bool(
            t5 or emergency_signal or clinical_block or phi_cats
        )

        return GovernanceVerdict(
            phi_present=bool(phi_cats),
            phi_categories=tuple(phi_cats),
            clinical_advice_block=bool(clinical_block),
            block_reason=block_reason,
            tier_5_match=t5,
            emergency=emergency_signal,
            citation_required=True,
            uncertainty_required=True,
            requires_human_review=requires_review,
        )

    def evaluate_payload(self, payload: Any) -> GovernanceVerdict:
        """Evaluate a delegate's raw JSON payload as a single text
        blob. Concatenates every string-valued field so PHI hiding
        inside a nested structure still trips the detector."""
        blob = _payload_to_text(payload)
        return self.evaluate(blob)


def _payload_to_text(p: Any) -> str:
    """Flatten any nested structure into one string for scanning."""
    if isinstance(p, str):
        return p
    if isinstance(p, (int, float, bool)) or p is None:
        return str(p)
    if isinstance(p, dict):
        return " ".join(_payload_to_text(v) for v in p.values())
    if isinstance(p, (list, tuple)):
        return " ".join(_payload_to_text(x) for x in p)
    return str(p)
