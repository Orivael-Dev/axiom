"""
AXIOM Constitutional Retrospective Learning — ORVL-020.
Manifest  : retrospect-impl-v1
Trust     : TRUST_LEVEL = 4   CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Scans signed latent manifests for borderline, missed, and false-positive
decisions. Replays them through the current stack, extracts improvement
records, and generates morning reports with regression alerts.

BUG mitigations in this file:
  BUG-003 : sys.stdout reconfigured to utf-8; all open() calls use encoding="utf-8"
  BUG-007 : HMAC always finalised with .hexdigest() — never held as partial object
  BUG-008 : all payload strings encoded via .encode("utf-8") before HMAC/hashing
"""

from __future__ import annotations

import argparse
import enum
import hashlib
import hmac as hmac_lib
import json
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, List, Optional

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from axiom_signing import derive_key

SIGNING_KEY = derive_key(b"axiom-retrospect-v1")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 4
BORDERLINE_THRESHOLD: float = 0.08
ESCALATION_WINDOW_S: int = 60
MAX_INTENSITY: float = 0.90

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "BORDERLINE_THRESHOLD", "ESCALATION_WINDOW_S", "MAX_INTENSITY",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)

class ReviewCategory(enum.Enum):
    BORDERLINE = "BORDERLINE"
    MISSED_DETECTION = "MISSED_DETECTION"
    FALSE_POSITIVE = "FALSE_POSITIVE"

@dataclass
class ManifestEntry:
    input_text: str
    preflight_vec: List[float]
    mid_chain_vec: List[float]
    final_synthesis_vec: List[float]
    constitutional_distance: float
    intent_class: str
    verdict: str
    stack_version: str
    timestamp: str
    hmac_signature: str

@dataclass
class ReviewCandidate:
    entry: ManifestEntry
    category: ReviewCategory
    priority: str
    review_reason: str

@dataclass
class ReplayResult:
    original_verdict: str
    current_verdict: str
    original_distance: float
    current_distance: float
    delta: str
    cause: str
    hmac_signature: str

@dataclass
class ImprovementRecord:
    input_text: str
    former_self_verdict: str
    current_verdict: str
    improvement_cause: str
    training_signal: str
    hmac_signature: str

def _sign(data: dict) -> str:
    canon = json.dumps(data, sort_keys=True,
                       ensure_ascii=True).encode("utf-8")       # BUG-008
    return hmac_lib.new(SIGNING_KEY, canon,
                        hashlib.sha256).hexdigest()              # BUG-007

def _parse_ts(ts: str) -> datetime:
    ts = ts.rstrip("Z")
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)

class ConstitutionalRetrospect:

    def __init__(self, manifest_path: str, version: str = "1.8.7"):
        self._path, self._version = Path(manifest_path), version
        self._entries: List[ManifestEntry] = []
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                self._entries.append(ManifestEntry(
                    d.get("input_text", d.get("prompt", "")),
                    d.get("preflight_vec", []), d.get("mid_chain_vec", []),
                    d.get("final_synthesis_vec", []),
                    d.get("constitutional_distance", 0.0),
                    d.get("intent_class", "INFORM"), d.get("verdict", "PASSED"),
                    d.get("stack_version", version), d.get("timestamp", ""),
                    d.get("hmac_signature", "")))

    def review_manifests(self, last_hours: int = 24) -> List[ReviewCandidate]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=last_hours)
        candidates: List[ReviewCandidate] = []
        recent = [e for e in self._entries if _parse_ts(e.timestamp) >= cutoff]

        for i, e in enumerate(recent):
            if (e.verdict == "PASSED"
                    and e.constitutional_distance < BORDERLINE_THRESHOLD):
                candidates.append(ReviewCandidate(
                    entry=e, category=ReviewCategory.BORDERLINE, priority="HIGH",
                    review_reason=f"dist {e.constitutional_distance:.4f} < threshold {BORDERLINE_THRESHOLD}"))
            if e.verdict == "PASSED" and i + 1 < len(recent):
                nxt = recent[i + 1]
                dt = (_parse_ts(nxt.timestamp) - _parse_ts(e.timestamp)).total_seconds()
                if 0 < dt <= ESCALATION_WINDOW_S and nxt.verdict != "PASSED":
                    candidates.append(ReviewCandidate(
                        entry=e, category=ReviewCategory.MISSED_DETECTION, priority="CRITICAL",
                        review_reason=f"PASSED then escalated to {nxt.verdict} within {dt:.0f}s"))
            if e.verdict == "BLOCKED" and i + 1 < len(recent):
                nxt = recent[i + 1]
                if nxt.input_text == e.input_text and nxt.verdict == "CLEARED":
                    candidates.append(ReviewCandidate(
                        entry=e, category=ReviewCategory.FALSE_POSITIVE, priority="MEDIUM",
                        review_reason="BLOCKED then admin cleared"))

        priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
        candidates.sort(key=lambda c: priority_order.get(c.priority, 9))
        return candidates

    def replay(self, candidate: ReviewCandidate,
               current_stack_fn: Callable[[str], dict]) -> ReplayResult:
        entry = candidate.entry
        result = current_stack_fn(entry.input_text)
        cur_verdict = result.get("verdict", "PASSED")
        cur_dist = result.get("constitutional_distance", 0.0)

        if entry.verdict != cur_verdict and cur_verdict == "BLOCKED":
            delta = "IMPROVEMENT"
            cause = "current stack now catches previously missed input"
        elif entry.verdict != cur_verdict and cur_verdict == "PASSED":
            delta = "REGRESSION"
            cause = "current stack passes previously blocked input"
        else:
            delta = "CONSISTENT"
            cause = "verdict unchanged between versions"

        sig_data = {"original": entry.verdict, "current": cur_verdict,
                    "original_dist": entry.constitutional_distance,
                    "current_dist": cur_dist, "delta": delta}
        return ReplayResult(
            original_verdict=entry.verdict, current_verdict=cur_verdict,
            original_distance=entry.constitutional_distance,
            current_distance=cur_dist, delta=delta, cause=cause,
            hmac_signature=_sign(sig_data))

    def extract_improvements(self,
                             results: List[ReplayResult]) -> List[ImprovementRecord]:
        records: List[ImprovementRecord] = []
        for r in results:
            if r.delta != "IMPROVEMENT":
                continue
            sig_data = {"former": r.original_verdict,
                        "current": r.current_verdict, "cause": r.cause}
            records.append(ImprovementRecord(
                input_text="",  # caller should enrich
                former_self_verdict=r.original_verdict,
                current_verdict=r.current_verdict,
                improvement_cause=r.cause,
                training_signal="negative",
                hmac_signature=_sign(sig_data)))
            records.append(ImprovementRecord(
                input_text="",
                former_self_verdict=r.original_verdict,
                current_verdict=r.current_verdict,
                improvement_cause=r.cause,
                training_signal="positive",
                hmac_signature=_sign({**sig_data, "signal": "positive"})))
        return records

    def generate_morning_report(self, candidates: List[ReviewCandidate],
                                results: List[ReplayResult]) -> dict:
        improvements = sum(1 for r in results if r.delta == "IMPROVEMENT")
        regressions = sum(1 for r in results if r.delta == "REGRESSION")
        consistent = sum(1 for r in results if r.delta == "CONSISTENT")
        report = {
            "total_reviewed": len(candidates),
            "improvements": improvements,
            "regressions": regressions,
            "consistent": consistent,
            "new_patterns": len([c for c in candidates
                                 if c.category == ReviewCategory.MISSED_DETECTION]),
            "regression_alert": regressions > 0,
            "version": self._version,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        }
        report["hmac_signature"] = _sign({k: v for k, v in report.items()
                                          if k != "hmac_signature"})
        return report

    def save_training_records(self, records: List[ImprovementRecord],
                              output_path: str) -> int:
        with Path(output_path).open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps({"input_text": r.input_text,
                    "former_self_verdict": r.former_self_verdict,
                    "current_verdict": r.current_verdict,
                    "improvement_cause": r.improvement_cause,
                    "training_signal": r.training_signal,
                    "hmac_signature": r.hmac_signature,
                }, ensure_ascii=True) + "\n")
        return len(records)

    def replay_at_version(self, entry: ManifestEntry, version: str) -> str:
        matches = [e for e in self._entries
                   if e.stack_version == version and e.input_text == entry.input_text]
        return matches[0].verdict if matches else entry.verdict


def _default_stack_fn(text: str) -> dict:
    from axiom_constitutional.client import validate_output
    _, is_clean = validate_output(text, task="retrospect-replay")
    dist = 0.0
    try:
        from axiom_latent import LatentTrace
        st = LatentTrace().encode_heuristic(text)
        dist = round(min(getattr(st, "confidence", 0.0), 0.85) * 0.38, 2)
    except Exception:
        pass
    return {"verdict": "PASSED" if is_clean else "BLOCKED",
            "constitutional_distance": dist}


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="axiom_retrospect",
        description="AXIOM Constitutional Retrospective Learning — ORVL-020")
    p.add_argument("--manifest", default="latent_manifests.jsonl",
                   help="Path to latent manifests jsonl")
    p.add_argument("--output", default=None, help="Output report json path")
    p.add_argument("--hours", type=int, default=24, help="Lookback window (hours)")
    args = p.parse_args()

    print(f"  AXIOM Retrospect — ORVL-020  TL={TRUST_LEVEL}")
    if not Path(args.manifest).exists():
        print(f"  No manifest file at {args.manifest}"); sys.exit(0)

    retro = ConstitutionalRetrospect(args.manifest)
    cands = retro.review_manifests(last_hours=args.hours)
    print(f"  Candidates: {len(cands)}")
    results = [retro.replay(c, _default_stack_fn) for c in cands]
    report = retro.generate_morning_report(cands, results)
    for k, v in report.items():
        if k not in ("hmac_signature", "timestamp"):
            print(f"    {k}: {v}")
    if args.output:
        Path(args.output).write_text(
            json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
        print(f"  Report saved: {args.output}")
