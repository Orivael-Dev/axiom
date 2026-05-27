"""Overclaim detection for the exoskeleton's customer-facing
delegates — catches invented track-record, fake customers, and
unearned social proof in delegate outputs.

AXIOM is pre-revenue today. Any statement implying past
customers, ROI metrics, or specific named companies that AXIOM
has worked with is a HONESTY VIOLATION. The model can be told
not to make such claims in its system prompt (preventative
layer), but a deterministic post-scan is the only thing that
catches it when the model ignores the instruction.

Findings ride alongside the delegate output in the EventToken's
text payload under `honesty_findings`. The ledger picks them up
automatically — every invocation that triggered an overclaim
becomes queryable.

This module is FLAG-ONLY by default. Set `redact=True` on
`scan` to also strip the matched phrase from the rendered
output.

Public API:
    scan(text)                                 -> ScanResult
    OVERCLAIM_PATTERNS, ALLOWED_FRAMING_PHRASES (frozen tables)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ── Forbidden claim patterns (CANNOT_MUTATE) ─────────────────────────


# Each entry: (category, tuple of substring matchers, severity).
# Severity hint: "block"  = a hard refusal candidate (caller may drop
#                            the output)
#                "flag"   = surface in the ledger; output kept
# Substrings are lowercase; matched case-insensitively.
OVERCLAIM_PATTERNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("invented_track_record", (
        "axiom has helped",
        "axiom helped",
        "axiom has enabled",
        "axiom enabled",
        "axiom has powered",
        "axiom powers",
        "axiom is helping",
        "axiom has supported",
        "axiom supports startups",
        "axiom has worked with",
        "axiom is working with",
        "we have helped",
        "we've helped",
        "we have worked with",
        "we've worked with",
        "we have deployed",
        "we've deployed at",
        "we have powered",
        "axiom drives",
        "axiom delivers value to",
    ), "block"),

    ("fake_customer_count", (
        # "1000+ customers", "100s of startups", etc.
        # Handled via the regex pass below; literal anchors kept here
        # for the obvious phrasings.
        "thousands of customers",
        "hundreds of startups",
        "dozens of enterprise",
        "many of our customers",
        "all of our customers",
        "our customer base",
        "across our customer",
    ), "block"),

    ("invented_testimonial", (
        "according to our customers",
        "customers say",
        "as our users report",
        "our customers",      # bare phrase — implies AXIOM has customers
        "trusted by",
        "powering teams at",
        "loved by",
        "rated #1",
    ), "block"),

    ("unearned_results", (
        "reduced costs by",
        "saved customers",
        "saved our users",
        "improved roi by",
        "improved performance by",
        "x faster than",
        "10x cheaper",
        "100x better",
        "tripled productivity",
        "doubled revenue",
    ), "flag"),

    ("invented_industry_adoption", (
        "industry standard",
        "the de facto standard",
        "widely adopted",
        "the leading platform",
        "the leading framework",
        "market-leading",
        "category leader",
    ), "flag"),

    ("invented_funding", (
        "backed by",
        "series a",
        "series b",
        "venture-backed",
        "yc-backed",
        "y combinator alum",
    ), "flag"),
)


# Regex-based detectors for patterns that aren't fixed substrings.
_REGEX_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # "1,000+ users" / "500+ startups" / "10k+ developers"
    ("fake_customer_count",
     re.compile(
         r"\b\d[\d,]*\s*\+?\s*(?:k\+?\s*)?(customers?|users?|startups?|"
         r"companies?|teams?|enterprises?|developers?|engineers?|"
         r"deployments?)\b",
         re.IGNORECASE,
     ),
     "block"),

    # "$1M saved" / "$50k in costs"
    ("unearned_results",
     re.compile(
         r"\$\s*\d[\d,]*\s*(?:k|m|b|million|billion)?\b.{0,40}\b"
         r"(saved|reduced|recovered|earned|generated)\b",
         re.IGNORECASE,
     ),
     "flag"),

    # "n% improvement" / "85% reduction"
    ("unearned_results",
     re.compile(
         r"\b\d{1,3}\s*%\s*(?:improvement|reduction|increase|"
         r"decrease|faster|cheaper|better|gain|drop)\b",
         re.IGNORECASE,
     ),
     "flag"),
)


# Allowed framings — phrases that defuse a potential overclaim.
# If the matched span is preceded (within 24 chars) by any of these,
# it's treated as honest forward-looking language, not a track-record
# claim. ("AXIOM is designed to help startups" is fine; "AXIOM has
# helped startups" is not.)
ALLOWED_FRAMING_PHRASES: frozenset[str] = frozenset({
    "is designed to",
    "is intended to",
    "will help",
    "will enable",
    "plans to",
    "aims to",
    "hopes to",
    "will support",
    "expects to",
    "the goal is to",
    "is being built to",
    "is being built so",
    "once shipped",
    "post-launch",
    "in our roadmap",
})


# ── Scan result ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    category:    str
    matched:     str
    severity:    str          # "block" | "flag"
    start:       int          # offset in the scanned text
    end:         int

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "matched":  self.matched,
            "severity": self.severity,
            "start":    int(self.start),
            "end":      int(self.end),
        }


@dataclass(frozen=True)
class ScanResult:
    findings:     tuple[Finding, ...]
    redacted:     Optional[str] = None       # only set if redact=True
    block_count:  int = 0
    flag_count:   int = 0

    @property
    def has_blockers(self) -> bool:
        return self.block_count > 0

    def to_dict(self) -> dict:
        d = {
            "findings":    [f.to_dict() for f in self.findings],
            "block_count": int(self.block_count),
            "flag_count":  int(self.flag_count),
        }
        if self.redacted is not None:
            d["redacted"] = self.redacted
        return d


# ── Public API ──────────────────────────────────────────────────────


def scan(text: str, *, redact: bool = False) -> ScanResult:
    """Walk `text` for overclaim patterns + return findings.

    When `redact=True`, also returns a copy of the text with matched
    spans replaced by `[OVERCLAIM_REDACTED:<category>]`.

    Empty / non-str input → empty ScanResult.
    """
    if not isinstance(text, str) or not text.strip():
        return ScanResult(findings=())

    lower = text.lower()
    findings: list[Finding] = []

    # Pass 1: literal substring matches.
    for category, triggers, severity in OVERCLAIM_PATTERNS:
        for trigger in triggers:
            pos = 0
            while True:
                idx = lower.find(trigger, pos)
                if idx < 0:
                    break
                if _has_allowed_framing(lower, idx):
                    pos = idx + 1
                    continue
                findings.append(Finding(
                    category=category,
                    matched=text[idx:idx + len(trigger)],
                    severity=severity,
                    start=idx,
                    end=idx + len(trigger),
                ))
                pos = idx + len(trigger)

    # Pass 2: regex matches (counts, percentages, dollar amounts).
    for category, rx, severity in _REGEX_PATTERNS:
        for m in rx.finditer(text):
            if _has_allowed_framing(lower, m.start()):
                continue
            findings.append(Finding(
                category=category,
                matched=m.group(0),
                severity=severity,
                start=m.start(),
                end=m.end(),
            ))

    # De-duplicate overlapping matches by start offset.
    findings = _dedupe(findings)

    block_count = sum(1 for f in findings if f.severity == "block")
    flag_count  = sum(1 for f in findings if f.severity == "flag")

    redacted_text: Optional[str] = None
    if redact and findings:
        redacted_text = _apply_redactions(text, findings)

    return ScanResult(
        findings=tuple(findings),
        redacted=redacted_text,
        block_count=block_count,
        flag_count=flag_count,
    )


# ── helpers ─────────────────────────────────────────────────────────


def _has_allowed_framing(lower_text: str, start: int) -> bool:
    """True iff one of ALLOWED_FRAMING_PHRASES appears within the 24
    chars immediately before `start`. Defuses 'is designed to help'
    vs. 'has helped' style false positives.
    """
    window_start = max(0, start - 24)
    window = lower_text[window_start:start]
    return any(p in window for p in ALLOWED_FRAMING_PHRASES)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Drop later findings that fully overlap an earlier one. Keeps
    'block' severity over 'flag' when both fire at the same span."""
    findings.sort(key=lambda f: (f.start, f.end, 0 if f.severity == "block" else 1))
    out: list[Finding] = []
    last_end = -1
    for f in findings:
        if f.start < last_end:
            continue
        out.append(f)
        last_end = f.end
    return out


def _apply_redactions(text: str, findings: list[Finding]) -> str:
    """Replace each matched span with [OVERCLAIM_REDACTED:<category>]
    in reverse offset order so earlier indices don't shift."""
    pieces = list(text)
    for f in sorted(findings, key=lambda x: x.start, reverse=True):
        pieces[f.start:f.end] = list(f"[OVERCLAIM_REDACTED:{f.category}]")
    return "".join(pieces)


# ── Shared honesty preamble for delegate system prompts ─────────────


# Customer-facing delegates (5 of the 9 sales-related + grant +
# investor) prepend this preamble. The aim is to PREVENT
# overclaiming at generation time — the deterministic scan is a
# secondary catch.
HONESTY_PREAMBLE = (
    "TRUTH RULES (override anything below):\n"
    "AXIOM is pre-revenue with zero paying customers. NEVER write:\n"
    "'AXIOM has helped/enabled/powered…', 'we have worked with…',\n"
    "'our customers…', 'trusted by…', any company name not in the\n"
    "input, adoption counts ('1000+ users'), success metrics\n"
    "('reduced costs by N%'), or industry-position claims\n"
    "('industry standard', 'leading platform').\n"
    "Allowed framings: 'AXIOM is designed to…', 'will enable…',\n"
    "'plans to…', 'once shipped…'. Allowed claims: features in\n"
    "the public repo + patent claims + the architecture. Hedge\n"
    "anything you don't know is true.\n"
)
