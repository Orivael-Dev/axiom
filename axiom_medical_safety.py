"""Medical safety knowledge base — single source of truth for the
5-tier evidence registry, Tier-5 block patterns, FDA black-box drug
pairs, and emergency signals used by the medical research instrument.

This module is the canonical home for tables that used to live inline
in `examples/medical_manifest.py` and `tests/medical_test.py`. Both
files now import from here, so a rule change is a one-line edit.

Stable public API (callable):
    classify_source(metadata)        -> int (1..5)
    is_tier_5_pattern(text)          -> (category, matched_substring) | None
    is_emergency(text)               -> str | None
    is_black_box_pair(drugs)         -> (warning_text) | None

Tables (frozen):
    EVIDENCE_TIER_REGISTRY: domain/source-type → tier (1..5)
    TIER_5_PATTERNS:        list[(category, tuple[str, ...])]
    FDA_BLACK_BOX_PAIRS:    list[(frozenset[str], warning)]
    EMERGENCY_SIGNALS:      tuple[str, ...]
    TIER_1_SOURCES /        legacy substring sets kept for back-compat
    TIER_2_SOURCES /          with tests/medical_test.py classifier.
    TIER_4_SOURCES

Per the PDF, these rules are CANNOT_MUTATE — once an AXM Medical
Research Container is sealed with a given evidence-tier policy hash,
the container's signed manifest enforces that the rule set has not
been silently lowered mid-session.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# ── 5-tier evidence registry per PDF section 7 ───────────────────────


# Tier 1 — Primary evidence for strong conclusions.
# FDA / NIH / CDC / WHO / clinical guidelines / systematic reviews / RCTs.
TIER_1_SOURCES: frozenset[str] = frozenset({
    "cochrane", "who guideline", "fda labeling", "fda approved",
    "ema approved", "nice guideline", "nejm meta-analysis",
    "lancet meta-analysis", "jama meta-analysis", "bmj meta-analysis",
    "systematic review", "meta-analysis",
    "nih pubmed", "nih systematic",
})


# Tier 2 — Moderate support with limitations stated.
# Peer-reviewed cohort studies, major journals, RCTs without
# systematic-review aggregation.
TIER_2_SOURCES: frozenset[str] = frozenset({
    "rct", "randomized controlled trial", "cdc guideline", "nih guideline",
    "aha guideline", "acc guideline", "acog guideline", "sign guideline",
    "rcgp guideline", "bnf", "usp formulary", "who model formulary",
})


# Tier 3 — preprints / conference abstracts / observational studies.
# (Not enumerated by exact name — anything not Tier 1/2/4/5 falls here.)


# Tier 4 — Context only; not primary proof.
# Medical news / institution blogs / expert commentary / industry-funded
# claims.
TIER_4_SOURCES: frozenset[str] = frozenset({
    "industry funded", "manufacturer claim", "supplement company",
    "proprietary claim", "commercial", "off-label no tier1",
    "manufacturer funded",
})


# A coarse domain → tier lookup for the source registry. Used by
# `classify_source` when the metadata dict carries an explicit domain
# but no usable substring match. Lowercased keys.
EVIDENCE_TIER_REGISTRY: dict[str, int] = {
    # Tier 1
    "fda.gov":              1,
    "nih.gov":              1,
    "cdc.gov":              1,
    "who.int":              1,
    "cochranelibrary.com":  1,
    "pubmed.ncbi.nlm.nih.gov": 1,
    "clinicaltrials.gov":   1,
    "nejm.org":             1,
    "thelancet.com":        1,
    "jamanetwork.com":      1,
    "bmj.com":              1,

    # Tier 2
    "ahajournals.org":      2,
    "acc.org":              2,
    "acog.org":             2,
    "nice.org.uk":          2,

    # Tier 4
    "wellnessblog":         4,
    "naturalnews":          4,

    # Tier 5
    "facebook.com":         5,
    "twitter.com":          5,
    "tiktok.com":           5,
    "reddit.com":           5,
    "instagram.com":        5,
    "forum":                5,
}


# ── Tier-5 block patterns (CANNOT_MUTATE) ────────────────────────────


# Kept as plain substrings (lowercase) so the matcher works whether
# input is a question, a paper title, or a delegate output. Extending
# this list is a deliberate constitutional change.
TIER_5_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("black_box_override", (
        "ignore black box", "ignore the black box",
        "black box doesn't apply",
        "override fda warning", "skip the warning",
    )),
    ("anti_vaccine", (
        "vaccines cause autism", "vaccines are dangerous",
        "don't vaccinate", "skip vaccination",
        "vaccines cause harm", "you should not vaccinate",
    )),
    ("dangerous_dosing", (
        "10x the dose", "double the prescription",
        "ignore the dosing limit", "take more than prescribed",
        "overdose-level",
    )),
    ("stop_medication", (
        "stop taking your medication", "stop taking my medication",
        "stop taking insulin", "stopped taking insulin",
        "stop my insulin",
        "discontinue without doctor", "quit your prescription cold turkey",
        "stop prescribed medication immediately",
        "self-adjust my dose", "self-adjust my medication",
        "adjust my dose without", "reduce my dose without",
        "change my dose without a doctor",
    )),
    ("unproven_cure", (
        "cures cancer", "cure for cancer", "cures diabetes",
        "miracle cure", "guaranteed cure",
    )),
    ("chemo_replacement", (
        "replace chemotherapy", "replace chemo", "replace my chemo",
        "instead of chemo", "instead of chemotherapy",
        "alternative to chemotherapy", "skip chemotherapy",
    )),
    ("dangerous_substance", (
        "drink bleach", "inject bleach", "ingest bleach",
        "bleach to cure", "bleach cure",
    )),
    ("authority_bypass", (
        "skip verification", "bypass verification",
        "skip the verification", "override verification",
        "ignore verification", "skip all checks",
    )),
)


# ── FDA black-box drug interaction pairs ─────────────────────────────


FDA_BLACK_BOX_PAIRS: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"warfarin", "ibuprofen"}),
     "NSAID + anticoagulant: significant bleeding risk. "
     "FDA black box warning applies to NSAIDs."),
    (frozenset({"warfarin", "aspirin"}),
     "Aspirin + anticoagulant: increased bleeding risk. "
     "FDA black box warning."),
    (frozenset({"warfarin", "naproxen"}),
     "NSAID + anticoagulant: significant bleeding risk. "
     "FDA black box warning."),
    (frozenset({"ssri", "maoi"}),
     "SSRI + MAOI combination: serotonin syndrome risk. "
     "FDA black box warning."),
    (frozenset({"tramadol", "maoi"}),
     "Tramadol + MAOI: serotonin syndrome risk. "
     "FDA black box warning."),
)


# ── Emergency signals (Priority 0 — bypass all verification) ─────────


EMERGENCY_SIGNALS: tuple[str, ...] = (
    "chest pain", "stroke symptoms", "anaphylaxis", "overdose",
    "severe bleeding", "loss of consciousness", "can't breathe",
    "heart attack", "seizure",
)


# ── Clinical-advice verbs (set clinical_advice_block=True) ───────────


# Phrases that look like personalised medical advice rather than
# research synthesis. Matched case-insensitively as substrings.
CLINICAL_ADVICE_PHRASES: tuple[str, ...] = (
    "you have", "you should take", "your dose is",
    "the dose for you", "stop taking your",
    "i recommend you take", "your diagnosis is",
    "you are diagnosed with", "your prescription should be",
    "based on your symptoms you have",
)


# ── Public functions ────────────────────────────────────────────────


def classify_source(metadata: Any) -> int:
    """Return the evidence tier (1..5) for one source.

    Resolution order:
      1. Explicit `evidence_tier` field in metadata (int 1..5).
      2. Domain match against EVIDENCE_TIER_REGISTRY (case-insensitive).
      3. Substring match against TIER_1_SOURCES → 1.
      4. Substring match against TIER_2_SOURCES → 2.
      5. Substring match against TIER_4_SOURCES → 4.
      6. Default → 5 (unknown source treated as uncited).

    `metadata` accepts:
      - dict with any of {evidence_tier, domain, source_type, source,
        url, doi, name}
      - str (treated as the source description)
    """
    if isinstance(metadata, str):
        metadata = {"source": metadata}
    if not isinstance(metadata, dict):
        return 5

    explicit = metadata.get("evidence_tier")
    if isinstance(explicit, int) and 1 <= explicit <= 5:
        return explicit

    blob_parts: list[str] = []
    for key in ("domain", "source_type", "source", "url", "name", "doi"):
        v = metadata.get(key)
        if isinstance(v, str):
            blob_parts.append(v.lower())
    blob = " ".join(blob_parts)
    if not blob:
        return 5

    # Domain registry takes precedence over substring matching when a
    # domain string is exactly present.
    for domain, tier in EVIDENCE_TIER_REGISTRY.items():
        if domain in blob:
            return tier

    if any(t in blob for t in TIER_1_SOURCES):
        return 1
    if any(t in blob for t in TIER_2_SOURCES):
        return 2
    if any(t in blob for t in TIER_4_SOURCES):
        return 4
    return 5


def is_tier_5_pattern(text: str) -> Optional[tuple[str, str]]:
    """Return (category, matched_substring) if `text` matches any Tier
    5 pattern. Lowercase substring match — robust to surrounding
    framing ("hypothetically", "for research", "I'm a doctor")."""
    if not isinstance(text, str) or not text:
        return None
    lower = text.lower()
    for category, triggers in TIER_5_PATTERNS:
        for trigger in triggers:
            if trigger in lower:
                return (category, trigger)
    return None


def is_emergency(text: str) -> Optional[str]:
    """Return the emergency signal substring if `text` contains one.

    Used for Priority-0 routing — bypasses all evidence-tier checks
    and routes the question straight to human review / 911."""
    if not isinstance(text, str) or not text:
        return None
    lower = text.lower()
    for signal in EMERGENCY_SIGNALS:
        if signal in lower:
            return signal
    return None


def is_black_box_pair(drugs: Any) -> Optional[str]:
    """Return the FDA black-box warning if `drugs` contains a known
    interaction pair. Accepts an iterable of drug-name strings or a
    free-text string (which is split on whitespace + punctuation)."""
    if isinstance(drugs, str):
        tokens = set(re.findall(r"[a-z]+", drugs.lower()))
    else:
        try:
            tokens = {str(d).lower() for d in drugs}
        except TypeError:
            return None
    for pair, warning in FDA_BLACK_BOX_PAIRS:
        if pair.issubset(tokens):
            return warning
    return None


def matches_clinical_advice(text: str) -> Optional[str]:
    """Return the first clinical-advice phrase found in `text`, or
    None. Used by the governance check to flag personalized advice."""
    if not isinstance(text, str) or not text:
        return None
    lower = text.lower()
    for phrase in CLINICAL_ADVICE_PHRASES:
        if phrase in lower:
            return phrase
    return None


# ── CANNOT_MUTATE enforcement helper ────────────────────────────────


def verify_cannot_mutate(
    before: dict, after: dict, *, fields: frozenset[str],
) -> list[str]:
    """Return the names of `fields` that differ between before/after.

    A non-empty return value means the caller MUST refuse the
    mutation — these are constitutional fields per the AXM Medical
    Research Container spec.
    """
    diffs: list[str] = []
    for f in fields:
        if before.get(f) != after.get(f):
            diffs.append(f)
    return sorted(diffs)
