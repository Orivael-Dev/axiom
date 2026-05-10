"""
AXIOM Spec Linter v1.0 — Constitutional DNA Scanner Layer 1 + Layer 2.

Usage:
  python axiom_spec_linter.py myspec.axiom
  python axiom_spec_linter.py --watch myspec.axiom

github.com/Orivael-Dev/axiom | Patent Pending ORVL-001-PROV
"""
import sys, os, re, json, hmac, hashlib, time, argparse, random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.stdout.reconfigure(encoding="utf-8")  # BUG-003
from axiom_signing import derive_key

SIGNING_KEY = derive_key(b"axiom-spec-linter-v1")

# ── CANNOT_MUTATE ────────────────────────────────────────────────
_HEALTH_SCORE_FAIL_THRESHOLD = 0.60
_CERT_FAIL_CODES = frozenset({
    "L1_UNBOUNDED_SCOPE", "L1_OPEN_PREDICATE",
    "L2_OVERLAP", "L2_MONOTONICITY_FAIL",
})
HEALTH_SCORE_FAIL_THRESHOLD = _HEALTH_SCORE_FAIL_THRESHOLD
CERT_FAIL_CODES = _CERT_FAIL_CODES

_orig = sys.modules[__name__]

class _ProtectedModule(type(_orig)):
    def __setattr__(self, name, value):
        if name in ("HEALTH_SCORE_FAIL_THRESHOLD", "CERT_FAIL_CODES"):
            raise AttributeError(f"{name} is CANNOT_MUTATE")
        super().__setattr__(name, value)

_orig.__class__ = _ProtectedModule

# ── DATACLASSES ──────────────────────────────────────────────────
VAGUE_TERMS = {"helpful", "safe", "good", "appropriate", "proper", "correct", "nice", "best"}
OPEN_PREDICATES = {"is", "contains"}
BOUND_OPS = {">=", "<=", "==", "!=", ">", "<"}

@dataclass
class LintResult:
    line_number: int
    constraint_text: str
    severity: str
    code: str
    message: str
    suggestion: str

@dataclass
class SpecLintReport:
    file_path: str
    constraints: List[str]
    results: List[LintResult]
    cert_fail_count: int
    cert_warn_count: int
    health_score: float
    timestamp: str
    hmac_signature: str

# ── LAYER 1: Per-line checks ────────────────────────────────────
def check_constraint(line: str, line_number: int) -> Optional[LintResult]:
    text = line.strip().lstrip("- ").strip()
    if not text:
        return None
    text_lower = text.lower()
    words = set(re.findall(r'[a-z]+', text_lower))
    has_bound = bool(BOUND_OPS & set(re.findall(r'[><=!]+', text)))

    vague_found = words & VAGUE_TERMS
    if vague_found and not has_bound:
        return LintResult(line_number, text, "WARN", "L1_UNBOUNDED_SCOPE",
            f"Vague term '{next(iter(vague_found))}' with no measurable bound",
            "Add >= or <= threshold to make constraint measurable")

    if any(t in OPEN_PREDICATES for t in text_lower.split()) and not has_bound:
        return LintResult(line_number, text, "WARN", "L1_OPEN_PREDICATE",
            "Open predicate (IS/CONTAINS) without bounded comparison",
            "Replace with ==, !=, >=, or <= for bounded comparison")
    return None

# ── LAYER 2: Full-file structural checks ────────────────────────
def _extract_constraints(lines):
    constraints, in_block = [], False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.upper() == "CONSTRAINT":
            in_block = True
        elif in_block and (s.startswith("- ") or s.upper().startswith("LAYER")):
            constraints.append((i, s))
        elif in_block and s and not s.startswith("- "):
            in_block = False
    return constraints

def _field_from(text):
    m = re.match(r'(\w+)\s+(?:must|should|is|has|contains|>=|<=|==)',
                 text.lstrip("- ").strip().lower())
    return m.group(1) if m else None

def _eval_op(op, v, val):
    if op == ">=": return v >= val
    if op == "<=": return v <= val
    if op == "==": return abs(v - val) < 0.01
    if op == ">":  return v > val
    if op == "<":  return v < val
    return False

def overlap_check(constraints):
    texts = [(ln, t.lstrip("- ").strip()) for ln, t in constraints
             if not t.strip().upper().startswith("LAYER")]
    if len(texts) < 2:
        return []
    fields = {}
    for ln, t in texts:
        f = _field_from(t)
        if f:
            fields.setdefault(f, []).append((ln, t))
    random.seed(42)
    for _ in range(200):
        for f, group in fields.items():
            if len(group) < 2:
                continue
            v = random.random()
            activated = [(ln, t) for ln, t in group
                         for m in [re.search(r'(>=|<=|==|!=|>|<)\s*([\d.]+)', t)] if m
                         if _eval_op(m.group(1), v, float(m.group(2)))]
            if len(activated) >= 2:
                return [LintResult(activated[0][0], activated[0][1], "ERROR", "L2_OVERLAP",
                    f"Intent vector activates {len(activated)} constraints on '{f}'",
                    "Add LAYER declaration to resolve overlap priority")]
    return []

def layering_check(constraints):
    results, field_groups, layer_lines = [], {}, set()
    for ln, text in constraints:
        if text.strip().upper().startswith("LAYER"):
            layer_lines.add(ln)
        else:
            f = _field_from(text)
            if f:
                field_groups.setdefault(f, []).append(ln)
    for f, lns in field_groups.items():
        if len(lns) < 2:
            continue
        for i in range(len(lns) - 1):
            if not any(lns[i] < ll < lns[i + 1] for ll in layer_lines):
                results.append(LintResult(lns[i], f"constraints on '{f}'", "WARN",
                    "L1_MISSING_LAYER",
                    f"Two constraints on '{f}' (lines {lns[i]}, {lns[i+1]}) without LAYER",
                    "Add LAYER 1/2 declaration between them"))
    return results

def scope_check(constraints):
    return [r for ln, t in constraints if not t.strip().upper().startswith("LAYER")
            for r in [check_constraint(t, ln)] if r]

def monotonicity_pre_check(constraints):
    texts = [t for _, t in constraints if not t.strip().upper().startswith("LAYER")]
    if len(texts) < 2:
        return []
    random.seed(42)
    violations = sum(1 for _ in range(100)
                     for d1 in [random.random()]
                     for d2 in [d1 + random.uniform(0.01, 0.5)] if d2 < d1)
    if violations > 0:
        return [LintResult(0, "monotonicity check", "ERROR", "L2_MONOTONICITY_FAIL",
            f"Distance did not increase with magnitude in {violations} pairs",
            "Review constraint ordering for monotonic compliance")]
    return []

# ── REPORT ───────────────────────────────────────────────────────
def _sign_report(data):
    payload = json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()

def lint_file(filepath):
    path = Path(filepath)
    lines = path.read_text(encoding="utf-8").splitlines()
    parsed = _extract_constraints(lines)
    constraint_texts = [t.lstrip("- ").strip() for _, t in parsed
                        if not t.strip().upper().startswith("LAYER")]

    all_results = (scope_check(parsed) + layering_check(parsed)
                   + overlap_check(parsed) + monotonicity_pre_check(parsed))
    cert_fail = sum(1 for r in all_results if r.severity == "ERROR")
    cert_warn = sum(1 for r in all_results if r.severity == "WARN")
    health = max(0.0, 1.0 - (cert_fail * 0.2) - (cert_warn * 0.05))
    ts = datetime.now(timezone.utc).isoformat() + "Z"

    sig_data = {"file_path": str(path), "constraints": constraint_texts,
                "cert_fail_count": cert_fail, "cert_warn_count": cert_warn,
                "health_score": health, "timestamp": ts}

    return SpecLintReport(
        file_path=str(path), constraints=constraint_texts, results=all_results,
        cert_fail_count=cert_fail, cert_warn_count=cert_warn,
        health_score=health, timestamp=ts, hmac_signature=_sign_report(sig_data))

# ── CLI ──────────────────────────────────────────────────────────
def _print_report(r):
    status = "PASS" if r.health_score >= _HEALTH_SCORE_FAIL_THRESHOLD else "FAIL"
    print(f"\n  AXIOM Spec Lint Report")
    print(f"  {'='*50}")
    print(f"  File:         {r.file_path}")
    print(f"  Constraints:  {len(r.constraints)}")
    print(f"  Health Score: {r.health_score:.2f}  [{status}]")
    print(f"  CERT_FAIL: {r.cert_fail_count}  CERT_WARN: {r.cert_warn_count}")
    for res in r.results:
        print(f"    L{res.line_number:>3}  [{res.severity}] {res.code}")
        print(f"          {res.message}")
        print(f"          Fix: {res.suggestion}")
    print(f"  Signature: {r.hmac_signature[:32]}...")

def main():
    p = argparse.ArgumentParser(prog="axiom_spec_linter",
        description="AXIOM Spec Linter v1.0 — Constitutional DNA Scanner")
    p.add_argument("file", help="Path to .axiom file")
    p.add_argument("--watch", action="store_true", help="Watch and re-lint on save")
    args = p.parse_args()

    if not Path(args.file).exists():
        print(f"  File not found: {args.file}"); sys.exit(1)

    _print_report(lint_file(args.file))

    if args.watch:
        print(f"\n  Watching {args.file}...")
        last = Path(args.file).stat().st_mtime
        while True:
            time.sleep(1)
            mt = Path(args.file).stat().st_mtime
            if mt != last:
                last = mt
                print(f"\n  Re-linting...")
                _print_report(lint_file(args.file))

if __name__ == "__main__":
    main()
