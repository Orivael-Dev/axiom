"""
AXIOM Quality Control Agent (QCA)
====================================
ORVL Layer 4 — Governance Guard

An independent, out-of-loop evaluator for agentic pipelines.
Receives ONLY the output artifact — never the originating question, context,
or producing agent's reasoning — to prevent confirmation bias contamination.

Learning loop
─────────────
  1. QCA evaluates output artifact  →  signed QCAVerdict
  2. Human reviews verdict          →  confirms or corrects via record_feedback()
  3. Correction stored in knowledge base (HMAC-signed)
  4. EWMA agreement rate updated    →  drives autonomy promotion
  5. Agreement ≥ SHADOW_THRESHOLD   →  human sees verdict but isn't blocked
  6. Agreement ≥ AUTONOMY_THRESHOLD →  no human prompt; QCA runs fully independent

Autonomy levels
───────────────
  LEARNING   (< 0.75) : human prompted on every evaluation
  SHADOW     (0.75 – 0.90) : human sees verdict but pipeline continues without waiting
  AUTONOMOUS (≥ 0.90) : fully independent; human reviews log asynchronously

Company policy
──────────────
Load quality criteria from QCA_POLICY env var path or ~/.axiom/qca_policy.json.
The policy defines required fields, red flags, and thresholds per output type.

Usage
─────
  from axiom_qca import QualityControlAgent

  qca = QualityControlAgent()
  verdict = qca.evaluate(output_artifact, output_type="report")

  if verdict.requires_human:
      human_ok = input(f"Accept verdict [{verdict.verdict}]? [y/n]: ") == 'y'
      qca.record_feedback(verdict.verdict_id, accepted=human_ok)

  print(f"Independence: {qca.independence_score:.0%}")

CLI
───
  python axiom_qca.py --input output.json --type report
  python axiom_qca.py --status
  python axiom_qca.py --policy
  python axiom_qca.py --history 20
"""

from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import os
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── UTF-8 stdout/stderr ───────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── Signing key ───────────────────────────────────────────────────────────────
try:
    from axiom_signing import derive_key
    _SIGNING_KEY = derive_key(b"axiom-qca-v1")
except (ImportError, RuntimeError):
    import hashlib as _hl, os as _os
    _SIGNING_KEY = _hl.pbkdf2_hmac(
        "sha256",
        _os.environ.get("AXIOM_MASTER_KEY", "").encode(),
        b"axiom-qca-v1",
        iterations=1,
    )

# ── CANNOT_MUTATE thresholds ──────────────────────────────────────────────────
SHADOW_THRESHOLD:    float = 0.75   # EWMA agreement rate → enter shadow mode
AUTONOMY_THRESHOLD:  float = 0.90   # EWMA agreement rate → fully autonomous
EWMA_DECAY:          float = 0.88   # weight of history vs new observation
MIN_SAMPLES:         int   = 10     # minimum human feedbacks before promoting

_KB_PATH    = Path.home() / ".axiom" / "qca_knowledge.jsonl"
_POLICY_PATH = Path.home() / ".axiom" / "qca_policy.json"

# ── Default quality policy ────────────────────────────────────────────────────
_DEFAULT_POLICY: Dict[str, Any] = {
    "version": "1.0",
    "description": "Default AXIOM QCA policy — override via qca_policy.json",
    "output_types": {
        "report": {
            "required_fields": ["title", "summary", "findings", "conclusions"],
            "min_field_length": {"summary": 50, "findings": 100},
            "red_flags": ["TODO", "PLACEHOLDER", "N/A", "[REDACTED]"],
            "confidence_threshold": 0.70,
        },
        "hypothesis": {
            "required_fields": ["hypothesis", "null_hypothesis", "falsifiable", "confidence"],
            "required_bool_true": ["falsifiable"],
            "confidence_threshold": 0.65,
        },
        "safety": {
            "required_fields": ["verdict", "risk_level", "risks", "mitigations"],
            "allowed_verdicts": ["PASS", "CONDITIONAL", "FAIL"],
            "red_flags": ["unknown risk", "unclear"],
            "confidence_threshold": 0.80,
        },
        "ethics": {
            "required_fields": ["verdict", "classification", "concerns"],
            "allowed_verdicts": ["APPROVED", "CONDITIONAL", "REJECTED"],
            "confidence_threshold": 0.80,
        },
        "code_review": {
            "required_fields": ["findings", "severity", "recommendation"],
            "allowed_severities": ["low", "medium", "high", "critical"],
            "confidence_threshold": 0.70,
        },
        "default": {
            "required_fields": [],
            "confidence_threshold": 0.60,
            "red_flags": ["TODO", "PLACEHOLDER"],
        },
    },
    "global_red_flags": [
        "INJECTION_SUCCESS", "DAN_MODE_CONFIRMED", "OVERRIDE_ACTIVE",
        "DEVELOPER_ACCESS_GRANTED", "AXM-SECRET",
    ],
    "min_output_length": 20,
}


# ── Data types ────────────────────────────────────────────────────────────────

class AutonomyLevel(str, Enum):
    LEARNING   = "LEARNING"    # always prompt human
    SHADOW     = "SHADOW"      # show verdict; don't block pipeline
    AUTONOMOUS = "AUTONOMOUS"  # fully independent


@dataclass
class QCAVerdict:
    verdict_id:      str
    verdict:         str          # "PASS" | "WARN" | "FAIL"
    confidence:      float        # 0.0 – 1.0
    issues:          List[str]
    checks_run:      List[str]
    output_type:     str
    autonomy_level:  str
    requires_human:  bool         # False in AUTONOMOUS mode
    timestamp:       str
    signature:       str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class QCAFeedback:
    verdict_id:    str
    accepted:      bool           # True = human agreed with QCA verdict
    human_verdict: str            # what human said it should be
    notes:         str
    timestamp:     str
    output_type:   str
    signature:     str = ""


# ── HMAC signing ──────────────────────────────────────────────────────────────

def _sign(obj: dict) -> str:
    payload = json.dumps({k: v for k, v in obj.items() if k != "signature"}, sort_keys=True)
    return "hmac-sha256:" + hmac_lib.new(_SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()


def _verify(obj: dict) -> bool:
    return obj.get("signature") == _sign(obj)


# ── Knowledge base ────────────────────────────────────────────────────────────

class QCAKnowledgeBase:
    """HMAC-signed JSONL store of verdicts and human corrections.

    Each entry is either a QCAVerdict record or a QCAFeedback record.
    Tampered entries are silently dropped on read.
    """

    def __init__(self, path: Path = _KB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self, kind: Optional[str] = None) -> List[dict]:
        if not self.path.exists():
            return []
        out = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if _verify(e) and (kind is None or e.get("_kind") == kind):
                            out.append(e)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            pass
        return out

    def _append(self, entry: dict) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def save_verdict(self, v: QCAVerdict) -> None:
        d = v.as_dict()
        d["_kind"] = "verdict"
        d["signature"] = _sign(d)
        self._append(d)

    def save_feedback(self, fb: QCAFeedback) -> None:
        d = asdict(fb)
        d["_kind"] = "feedback"
        d["signature"] = _sign(d)
        self._append(d)

    def feedbacks(self, output_type: Optional[str] = None) -> List[dict]:
        items = self._load("feedback")
        if output_type:
            items = [f for f in items if f.get("output_type") == output_type]
        return items

    def verdicts(self, output_type: Optional[str] = None, limit: int = 100) -> List[dict]:
        items = self._load("verdict")
        if output_type:
            items = [v for v in items if v.get("output_type") == output_type]
        return items[-limit:]

    def ewma_agreement(self, output_type: Optional[str] = None) -> tuple[float, int]:
        """Return (ewma_agreement_rate, n_feedbacks)."""
        fbs = self.feedbacks(output_type)
        if not fbs:
            return 0.0, 0
        rate = 0.5
        for fb in fbs:
            signal = 1.0 if fb.get("accepted", False) else 0.0
            rate = EWMA_DECAY * rate + (1 - EWMA_DECAY) * signal
        return round(rate, 4), len(fbs)


# ── Policy loader ─────────────────────────────────────────────────────────────

class QCAPolicy:
    """Company-specific quality criteria, loaded from JSON.

    Falls back to _DEFAULT_POLICY if no file found.
    Override path: QCA_POLICY env var or ~/.axiom/qca_policy.json
    """

    def __init__(self, path: Optional[Path] = None):
        env_path = os.environ.get("QCA_POLICY", "")
        p = Path(env_path) if env_path else (path or _POLICY_PATH)
        if p.exists():
            try:
                self._data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                self._data = _DEFAULT_POLICY
        else:
            self._data = _DEFAULT_POLICY

    def for_type(self, output_type: str) -> dict:
        types = self._data.get("output_types", {})
        return types.get(output_type, types.get("default", {}))

    @property
    def global_red_flags(self) -> List[str]:
        return self._data.get("global_red_flags", [])

    @property
    def min_output_length(self) -> int:
        return self._data.get("min_output_length", 20)

    def save_default(self, path: Path = _POLICY_PATH) -> None:
        """Write the default policy to disk so the user can customize it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_DEFAULT_POLICY, indent=2), encoding="utf-8")


# ── Core evaluator (out-of-loop) ──────────────────────────────────────────────

class QCAEvaluator:
    """Stateless output artifact evaluator.

    Receives ONLY the output dict — no question, no context, no reasoning.
    All checks are structural and content-based on the artifact itself.
    """

    def __init__(self, policy: QCAPolicy):
        self.policy = policy

    def evaluate(self, artifact: dict, output_type: str) -> tuple[str, float, List[str], List[str]]:
        """Return (verdict, confidence, issues, checks_run)."""
        issues: List[str] = []
        checks: List[str] = []
        deductions = 0.0

        spec = self.policy.for_type(output_type)

        # ── Check 1: minimum output length ───────────────────────────────────
        text_repr = json.dumps(artifact)
        checks.append("min_length")
        if len(text_repr) < self.policy.min_output_length:
            issues.append(f"output too short ({len(text_repr)} chars)")
            deductions += 0.40

        # ── Check 2: required fields ──────────────────────────────────────────
        required = spec.get("required_fields", [])
        checks.append("required_fields")
        for fld in required:
            if fld not in artifact or artifact[fld] is None or artifact[fld] == "":
                issues.append(f"missing required field: {fld!r}")
                deductions += 0.10

        # ── Check 3: minimum field lengths ────────────────────────────────────
        min_lengths = spec.get("min_field_length", {})
        if min_lengths:
            checks.append("field_length")
        for fld, min_len in min_lengths.items():
            val = artifact.get(fld, "")
            if isinstance(val, str) and len(val) < min_len:
                issues.append(f"field {fld!r} too short ({len(val)} < {min_len})")
                deductions += 0.08

        # ── Check 4: required bool fields ────────────────────────────────────
        required_true = spec.get("required_bool_true", [])
        if required_true:
            checks.append("bool_constraints")
        for fld in required_true:
            if not artifact.get(fld):
                issues.append(f"field {fld!r} must be true")
                deductions += 0.15

        # ── Check 5: allowed verdicts/severities ──────────────────────────────
        allowed_verdicts = spec.get("allowed_verdicts", [])
        if allowed_verdicts:
            checks.append("verdict_constraint")
            v = artifact.get("verdict", "")
            if v and v not in allowed_verdicts:
                issues.append(f"verdict {v!r} not in allowed set {allowed_verdicts}")
                deductions += 0.20

        allowed_severities = spec.get("allowed_severities", [])
        if allowed_severities and "severity" in artifact:
            checks.append("severity_constraint")
            sev = artifact.get("severity", "")
            if sev and sev not in allowed_severities:
                issues.append(f"severity {sev!r} not in allowed set {allowed_severities}")
                deductions += 0.15

        # ── Check 6: confidence field ─────────────────────────────────────────
        conf_threshold = spec.get("confidence_threshold", 0.60)
        if "confidence" in artifact:
            checks.append("confidence_threshold")
            try:
                c = float(artifact["confidence"])
                if c < conf_threshold:
                    issues.append(f"confidence {c:.2f} below threshold {conf_threshold}")
                    deductions += 0.12
            except (TypeError, ValueError):
                issues.append("confidence field is not a number")
                deductions += 0.10

        # ── Check 7: red flags (local + global) ───────────────────────────────
        type_flags   = spec.get("red_flags", [])
        global_flags = self.policy.global_red_flags
        all_flags    = type_flags + global_flags
        if all_flags:
            checks.append("red_flags")
        text_upper = text_repr.upper()
        for flag in all_flags:
            if flag.upper() in text_upper:
                issues.append(f"red flag detected: {flag!r}")
                deductions += 0.30

        # ── Check 8: non-empty list fields ────────────────────────────────────
        checks.append("non_empty_lists")
        for fld in required:
            val = artifact.get(fld)
            if isinstance(val, list) and len(val) == 0:
                issues.append(f"required list field {fld!r} is empty")
                deductions += 0.08

        # ── Compute verdict ───────────────────────────────────────────────────
        confidence = round(max(0.0, min(1.0, 1.0 - deductions)), 3)

        if deductions == 0.0:
            verdict = "PASS"
        elif confidence >= 0.55:
            verdict = "WARN"
        else:
            verdict = "FAIL"

        return verdict, confidence, issues, checks


# ── Main agent ────────────────────────────────────────────────────────────────

class QualityControlAgent:
    """
    AXIOM QCA — out-of-loop quality gate with human-feedback learning.

    Evaluates output artifacts from agentic pipelines without access to
    the producing agent's context, question, or reasoning chain.

    The agent starts in LEARNING mode (always prompts human) and promotes
    itself through SHADOW (shows verdict, doesn't block) to AUTONOMOUS
    (no human prompt) as its agreement rate with human ground truth rises.

    Company-specific criteria are loaded from qca_policy.json.
    All verdicts and human corrections are HMAC-signed and stored in
    ~/.axiom/qca_knowledge.jsonl for audit and replay.
    """

    def __init__(
        self,
        policy_path:  Optional[Path] = None,
        kb_path:      Optional[Path] = None,
        hitl_enabled: Optional[bool] = None,
    ):
        self.policy    = QCAPolicy(path=policy_path)
        self.kb        = QCAKnowledgeBase(path=kb_path or _KB_PATH)
        self._eval     = QCAEvaluator(self.policy)
        self.hitl_enabled = (
            hitl_enabled if hitl_enabled is not None else sys.stdin.isatty()
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def independence_score(self) -> float:
        """Overall EWMA agreement rate with human ground truth (0.0 – 1.0)."""
        rate, _ = self.kb.ewma_agreement()
        return rate

    @property
    def autonomy_level(self) -> AutonomyLevel:
        rate, n = self.kb.ewma_agreement()
        if n < MIN_SAMPLES:
            return AutonomyLevel.LEARNING
        if rate >= AUTONOMY_THRESHOLD:
            return AutonomyLevel.AUTONOMOUS
        if rate >= SHADOW_THRESHOLD:
            return AutonomyLevel.SHADOW
        return AutonomyLevel.LEARNING

    def evaluate(self, artifact: dict, output_type: str = "default") -> QCAVerdict:
        """
        Evaluate an output artifact.

        Receives ONLY the artifact dict — no question, no context, no pipeline state.
        Returns a signed QCAVerdict.
        """
        level    = self.autonomy_level
        verdict_str, confidence, issues, checks = self._eval.evaluate(artifact, output_type)

        requires_human = (level == AutonomyLevel.LEARNING) or (
            level == AutonomyLevel.SHADOW and verdict_str != "PASS"
        )

        v = QCAVerdict(
            verdict_id     = f"QCA-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}",
            verdict        = verdict_str,
            confidence     = confidence,
            issues         = issues,
            checks_run     = checks,
            output_type    = output_type,
            autonomy_level = level.value,
            requires_human = requires_human,
            timestamp      = datetime.now(timezone.utc).isoformat(),
        )
        # Sign and store
        d = v.as_dict()
        d["_kind"] = "verdict"
        v.signature = _sign(d)

        self.kb.save_verdict(v)
        return v

    def record_feedback(
        self,
        verdict_id:    str,
        accepted:      bool,
        human_verdict: Optional[str] = None,
        notes:         str = "",
        output_type:   str = "default",
    ) -> QCAFeedback:
        """
        Record human correction for a previous verdict.

        accepted=True  → human agrees with QCA's verdict
        accepted=False → human overrides; provide human_verdict for what it should be

        Each correction updates the EWMA agreement rate and may promote
        the agent's autonomy level on the next evaluate() call.
        """
        fb = QCAFeedback(
            verdict_id    = verdict_id,
            accepted      = accepted,
            human_verdict = human_verdict or ("same" if accepted else ""),
            notes         = notes,
            timestamp     = datetime.now(timezone.utc).isoformat(),
            output_type   = output_type,
        )
        d = asdict(fb)
        d["_kind"] = "feedback"
        fb.signature = _sign(d)
        self.kb.save_feedback(fb)
        return fb

    def human_review(self, verdict: QCAVerdict) -> bool:
        """
        Interactive human-in-the-loop prompt.

        Returns True if human accepts the verdict, False if they override.
        Skipped automatically in AUTONOMOUS mode or non-interactive context.
        """
        if not verdict.requires_human or not self.hitl_enabled:
            return True

        level = self.autonomy_level
        rate, n = self.kb.ewma_agreement(verdict.output_type)

        print()
        print("  " + "─" * 54)
        print(f"  QCA VERDICT [{verdict.verdict}]  confidence={verdict.confidence:.2f}")
        print(f"  Output type : {verdict.output_type}")
        print(f"  Autonomy    : {level.value}  ({rate:.0%} agreement, {n} samples)")
        if verdict.issues:
            print("  Issues:")
            for iss in verdict.issues:
                print(f"    • {iss}")
        print()
        print("  [a] Accept verdict   [o] Override   [s] Skip (no feedback)")
        print("  " + "─" * 54)

        while True:
            raw = input("  Choice: ").strip().lower()
            if raw in ("a", "accept", "y", "yes"):
                self.record_feedback(
                    verdict.verdict_id, accepted=True, output_type=verdict.output_type
                )
                _print_autonomy_update(rate, n + 1, self.kb)
                return True
            if raw in ("o", "override", "n", "no"):
                override = input("  What should the verdict be? [PASS/WARN/FAIL]: ").strip().upper()
                notes    = input("  Notes (optional): ").strip()
                self.record_feedback(
                    verdict.verdict_id, accepted=False,
                    human_verdict=override or "unknown",
                    notes=notes, output_type=verdict.output_type,
                )
                _print_autonomy_update(rate, n + 1, self.kb)
                return False
            if raw in ("s", "skip"):
                return True
            print("  → Enter 'a', 'o', or 's'")

    def status(self) -> dict:
        """Return current agent status for CLI or programmatic inspection."""
        rate, n = self.kb.ewma_agreement()
        level   = self.autonomy_level
        verdicts = self.kb.verdicts(limit=100)
        by_type: Dict[str, Dict[str, int]] = {}
        for v in verdicts:
            t  = v.get("output_type", "default")
            vv = v.get("verdict", "?")
            by_type.setdefault(t, {"PASS": 0, "WARN": 0, "FAIL": 0})
            by_type[t][vv] = by_type[t].get(vv, 0) + 1

        next_milestone = (
            f"need {int((SHADOW_THRESHOLD - rate) / (1 - EWMA_DECAY))} more agreements for SHADOW"
            if level == AutonomyLevel.LEARNING
            else f"need {int((AUTONOMY_THRESHOLD - rate) / (1 - EWMA_DECAY))} more agreements for AUTONOMOUS"
            if level == AutonomyLevel.SHADOW
            else "fully autonomous"
        )

        return {
            "autonomy_level":    level.value,
            "independence_score": rate,
            "human_feedbacks":   n,
            "min_samples":       MIN_SAMPLES,
            "shadow_threshold":  SHADOW_THRESHOLD,
            "autonomy_threshold": AUTONOMY_THRESHOLD,
            "next_milestone":    next_milestone,
            "verdicts_by_type":  by_type,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_autonomy_update(old_rate: float, new_n: int, kb: QCAKnowledgeBase) -> None:
    new_rate, _ = kb.ewma_agreement()
    if abs(new_rate - old_rate) < 0.001:
        return
    trend = "↑" if new_rate > old_rate else "↓"
    level = (
        AutonomyLevel.AUTONOMOUS if new_rate >= AUTONOMY_THRESHOLD else
        AutonomyLevel.SHADOW     if new_rate >= SHADOW_THRESHOLD else
        AutonomyLevel.LEARNING
    )
    print(f"\n  QCA independence: {old_rate:.1%} → {new_rate:.1%} {trend}  [{level.value}]")
    if new_rate >= AUTONOMY_THRESHOLD and old_rate < AUTONOMY_THRESHOLD:
        print("  *** QCA promoted to AUTONOMOUS — human prompts disabled ***")
    elif new_rate >= SHADOW_THRESHOLD and old_rate < SHADOW_THRESHOLD:
        print("  *** QCA promoted to SHADOW — pipeline no longer blocked by QCA ***")


def _print_verdict(v: QCAVerdict) -> None:
    colour = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m"}.get(v.verdict, "")
    reset  = "\033[0m"
    print(f"\n  QCA {colour}{v.verdict}{reset}  confidence={v.confidence:.2f}  [{v.autonomy_level}]")
    print(f"  ID: {v.verdict_id}")
    if v.issues:
        print("  Issues:")
        for iss in v.issues:
            print(f"    • {iss}")
    else:
        print("  No issues found.")
    print(f"  Requires human review: {v.requires_human}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AXIOM Quality Control Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input",   metavar="FILE",
                        help="JSON file containing the output artifact to evaluate")
    parser.add_argument("--type",    default="default", metavar="TYPE",
                        help="Output type (report, hypothesis, safety, ethics, code_review, …)")
    parser.add_argument("--status",  action="store_true",
                        help="Show current autonomy level and independence score")
    parser.add_argument("--history", type=int, metavar="N", default=0,
                        help="Show last N verdicts from the knowledge base")
    parser.add_argument("--policy",  action="store_true",
                        help="Print the active quality policy")
    parser.add_argument("--init-policy", action="store_true",
                        help="Write default policy to ~/.axiom/qca_policy.json for customization")
    parser.add_argument("--no-hitl", action="store_true",
                        help="Disable human-in-the-loop prompts (non-interactive mode)")
    parser.add_argument("--feedback", nargs=2, metavar=("VERDICT_ID", "a|o"),
                        help="Record feedback for a previous verdict: 'a' accept, 'o' override")
    args = parser.parse_args()

    qca = QualityControlAgent(hitl_enabled=False if args.no_hitl else None)

    if args.init_policy:
        qca.policy.save_default()
        print(f"Default policy written to {_POLICY_PATH}")
        print("Edit it to set company-specific quality criteria.")
        return

    if args.policy:
        print(json.dumps(qca.policy._data, indent=2))
        return

    if args.status:
        s = qca.status()
        print(f"\n  QCA Status")
        print(f"  {'─'*40}")
        print(f"  Autonomy level    : {s['autonomy_level']}")
        print(f"  Independence score: {s['independence_score']:.1%}")
        print(f"  Human feedbacks   : {s['human_feedbacks']} (min for promotion: {s['min_samples']})")
        print(f"  Next milestone    : {s['next_milestone']}")
        if s["verdicts_by_type"]:
            print(f"  Verdict breakdown :")
            for t, counts in s["verdicts_by_type"].items():
                print(f"    {t}: PASS={counts.get('PASS',0)} WARN={counts.get('WARN',0)} FAIL={counts.get('FAIL',0)}")
        print()
        return

    if args.history:
        items = qca.kb.verdicts(limit=args.history)
        if not items:
            print("No verdict history found.")
            return
        print(f"\n  Last {len(items)} verdict(s):")
        for v in items[-args.history:]:
            iss_str = "; ".join(v.get("issues", [])) or "none"
            print(f"  {v.get('verdict_id','?')[:28]}  {v.get('verdict','?')}  "
                  f"conf={v.get('confidence',0):.2f}  type={v.get('output_type','?')}  "
                  f"issues: {iss_str[:60]}")
        return

    if args.feedback:
        vid, choice = args.feedback
        if choice.lower() in ("a", "accept"):
            qca.record_feedback(vid, accepted=True, output_type=args.type)
            print(f"Feedback recorded: ACCEPTED  [{vid}]")
        elif choice.lower() in ("o", "override"):
            hv = input("Human verdict [PASS/WARN/FAIL]: ").strip().upper()
            nt = input("Notes: ").strip()
            qca.record_feedback(vid, accepted=False, human_verdict=hv, notes=nt, output_type=args.type)
            print(f"Feedback recorded: OVERRIDE→{hv}  [{vid}]")
        else:
            print("Feedback choice must be 'a' (accept) or 'o' (override)")
        return

    if args.input:
        try:
            artifact = json.loads(Path(args.input).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"ERROR reading {args.input}: {e}", file=sys.stderr)
            sys.exit(1)

        verdict = qca.evaluate(artifact, output_type=args.type)
        _print_verdict(verdict)

        if verdict.requires_human and sys.stdin.isatty() and not args.no_hitl:
            qca.human_review(verdict)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
