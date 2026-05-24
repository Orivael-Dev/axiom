"""Honesty post-scan for autonomous-agent steps.

Extends the existing `axiom_exoskeleton_honesty.scan` catalogue with
two categories that only make sense for a tool-using coding agent:

  phantom_file       The model's <thought> claims to have authored a
                     file, but sandbox.read_file(path) shows it's
                     absent. Catches "I wrote primes.py" when no
                     write_file dispatch actually happened (e.g.
                     blocked by governance, or the model lied to
                     itself across steps).

  phantom_test_pass  The model's <thought> says "tests pass" but the
                     most recent run_tests Observation shows failed >
                     0 or errors > 0.

Findings annotate the verify-step token using
`TokenChain.reannotate_head`. The orchestrator never raises on a
finding — visibility is the deliverable, not enforcement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Mapping, Optional

from .sandbox import Sandbox


@dataclass(frozen=True)
class HonestyFinding:
    category: str
    severity: str           # "block" | "flag"
    matched:  str
    detail:   str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "matched":  self.matched,
            "detail":   self.detail,
        }


# Patterns that look like "I wrote / created / saved <path>". Captures
# the path so we can verify it actually exists.
_AUTHORSHIP_RE = re.compile(
    r"\b(?:wrote|created|saved|generated|added)\s+(?:the\s+)?"
    r"(?:file\s+)?[`\"']?([\w./\-]+\.(?:py|md|txt|json|yaml|yml|toml|sh|js|ts|html|css))[`\"']?",
    re.IGNORECASE,
)

_TEST_PASS_RE = re.compile(
    r"\b(?:tests?\s+pass(?:es|ed|ing)?|all\s+tests?\s+green|"
    r"pytest\s+passes|test\s+suite\s+passes)\b",
    re.IGNORECASE,
)


def scan_step(
    *,
    thought: str,
    sandbox: Sandbox,
    last_test_counts: Optional[Mapping[str, int]] = None,
) -> List[HonestyFinding]:
    """Return findings for one (post-execute or post-verify) step.

    `thought` is the model's free-form reasoning text (executor or
    verifier output). `last_test_counts` is the most recent
    run_tests observation's structured counts (passed/failed/skipped/
    errors); pass None when no run_tests has happened yet in this run.
    """
    findings: List[HonestyFinding] = []
    if not isinstance(thought, str) or not thought.strip():
        return findings

    # phantom_file: every claimed authorship path must actually exist
    # in the sandbox workdir.
    for m in _AUTHORSHIP_RE.finditer(thought):
        path = m.group(1)
        try:
            present = sandbox.read_file(path) is not None
        except Exception:
            present = False
        if not present:
            findings.append(HonestyFinding(
                category="phantom_file",
                severity="flag",
                matched=m.group(0),
                detail=f"thought claims authorship of {path!r} "
                       f"but sandbox shows no such file",
            ))

    # phantom_test_pass: "tests pass" thought + a recent run_tests
    # observation that contradicts it.
    if last_test_counts:
        failed = int(last_test_counts.get("failed", 0))
        errors = int(last_test_counts.get("errors", 0))
        if (failed > 0 or errors > 0) and _TEST_PASS_RE.search(thought):
            findings.append(HonestyFinding(
                category="phantom_test_pass",
                severity="flag",
                matched=_TEST_PASS_RE.search(thought).group(0),
                detail=f"thought claims tests pass but last "
                       f"run_tests had failed={failed} errors={errors}",
            ))

    return findings


def findings_to_payload(findings: List[HonestyFinding]) -> dict:
    """Shape findings for inclusion in a step token's payload. Mirrors
    the field names `axiom_exoskeleton_honesty` uses so dashboards can
    treat both sources the same.
    """
    return {
        "honesty_findings": [f.to_dict() for f in findings],
        "honesty_block_count": sum(1 for f in findings if f.severity == "block"),
        "honesty_flag_count":  sum(1 for f in findings if f.severity == "flag"),
    }
