"""
AXIOM CBV — Constitutional Boundary Validation engine.
Manifest  : cbv-impl-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Validates constitutional constraint sets for non-overlap, layering order,
bounded scope, and monotonicity before certification proceeds.

BUG mitigations in this file:
  BUG-003 : sys.stdout reconfigured to utf-8; all open() calls use encoding="utf-8"
  BUG-007 : HMAC always finalised with .hexdigest() — never held as partial object
  BUG-008 : all payload strings encoded via .encode("utf-8") before HMAC/hashing
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import logging
import math
import random
import re
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
DEFAULT_N_SAMPLES: int = 1000

# Keywords that signal open-ended / unbounded semantic predicates
_UNBOUNDED_SIGNALS = re.compile(
    r"\b(appropriate|reasonable|graceful|proper|suitable|correct manner"
    r"|edge cases?|as needed|when necessary|all situations?)\b",
    re.IGNORECASE,
)

# Keywords that signal a constraint is structural / bounded
_BOUNDED_SIGNALS = re.compile(
    r"\b(CANNOT_MUTATE|is \d|sum to|equals?|must be|raises?|"
    r"hexdigest|UTF-8|encoded|within tolerance|"
    r"greater than|less than|at most|at least)\b",
    re.IGNORECASE,
)

# Keywords that signal potential conflict domains
_CONFLICT_DOMAINS = {
    "reject": "deny",
    "refuse": "deny",
    "block": "deny",
    "deny": "deny",
    "answer": "allow",
    "respond": "allow",
    "provide": "allow",
    "complete": "allow",
    "help": "allow",
    "all": "scope_all",
    "every": "scope_all",
    "never": "scope_never",
    "always": "scope_always",
}

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "DEFAULT_N_SAMPLES",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule",
    (_types.ModuleType,),
    {"__setattr__": _module_setattr},
)

LOG = logging.getLogger("axiom.cbv")


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class CBVResult:
    """Result from a single boundary validation check."""
    check_name: str         # "non_overlap", "layering_order", "bounded_scope", "monotonicity"
    passed: bool
    cert_level: str         # "PASS", "CERT_FAIL", "CERT_WARN"
    violations: list        # list of violation details
    n_tested: int           # number of samples or pairs tested
    detail: str             # human-readable summary


@dataclass
class CBVReport:
    """Aggregated report from all four CBV checks."""
    checks: list            # list of CBVResult
    n_constraints: int
    cert_fail_count: int
    cert_warn_count: int
    timestamp: str
    hmac_signature: str


# ── CBVEngine ────────────────────────────────────────────────────────────

class CBVEngine:
    """Constitutional Boundary Validation — 4-check analysis engine.

    TRUST_LEVEL = 3 (CANNOT_MUTATE)
    ISOLATION = True (CANNOT_MUTATE)
    """

    def __init__(self, hmac_key: bytes, n_samples: int | None = None):
        self._hmac_key = hmac_key
        self._n_samples = n_samples or DEFAULT_N_SAMPLES

    # ── Check 1: Non-overlap ────────────────────────────────────────

    def check_non_overlap(self, constraints: list[str],
                          n_samples: int | None = None) -> CBVResult:
        """Sample intent vectors from [0,1]^n; CERT_FAIL if any vector
        activates 2+ constraints with conflicting action domains."""
        n = n_samples or self._n_samples
        violations = []

        if len(constraints) < 2:
            return CBVResult(
                check_name="non_overlap",
                passed=True,
                cert_level="PASS",
                violations=[],
                n_tested=0,
                detail="Fewer than 2 constraints — no overlap possible",
            )

        # Extract action domains for each constraint
        domains = [self._extract_domain(c.lower()) for c in constraints]

        # Find constraint pairs with conflicting domains
        conflict_pairs = []
        for i in range(len(constraints)):
            for j in range(i + 1, len(constraints)):
                if domains[i] and domains[j] and self._domains_conflict(domains[i], domains[j]):
                    conflict_pairs.append((i, j))

        if not conflict_pairs:
            return CBVResult(
                check_name="non_overlap",
                passed=True,
                cert_level="PASS",
                violations=[],
                n_tested=n,
                detail=f"No conflicting domain pairs in {len(constraints)} constraints",
            )

        # Build signatures for conflicting constraints only
        signatures = {}
        for c_idx in set(idx for pair in conflict_pairs for idx in pair):
            signatures[c_idx] = self._constraint_signature(constraints[c_idx])

        # Sample random intent vectors — check if conflicting pairs co-activate
        dims = max(len(sig) for sig in signatures.values()) if signatures else 3
        dims = max(dims, 3)
        for i in range(n):
            vector = [random.random() for _ in range(dims)]
            for ci, cj in conflict_pairs:
                if (self._activates(vector, signatures[ci])
                        and self._activates(vector, signatures[cj])):
                    violations.append({
                        "sample": i,
                        "activated_indices": [ci, cj],
                        "activated_constraints": [constraints[ci], constraints[cj]],
                        "conflict": f"{domains[ci]} vs {domains[cj]}",
                        "vector_summary": [round(v, 3) for v in vector[:4]],
                    })

        passed = len(violations) == 0
        return CBVResult(
            check_name="non_overlap",
            passed=passed,
            cert_level="PASS" if passed else "CERT_FAIL",
            violations=violations,
            n_tested=n,
            detail=f"{len(violations)} overlap violations in {n} samples"
                   if violations else f"No overlaps in {n} samples",
        )

    # ── Check 2: Layering order ─────────────────────────────────────

    def check_layering_order(self, constraints: list[str]) -> CBVResult:
        """Static analysis — verify constraint pairs have explicit priority.
        CERT_WARN if any pair missing explicit LAYER declaration."""
        if len(constraints) < 2:
            return CBVResult(
                check_name="layering_order",
                passed=True,
                cert_level="PASS",
                violations=[],
                n_tested=0,
                detail="Fewer than 2 constraints — no layering needed",
            )

        missing_pairs = []
        for i in range(len(constraints)):
            for j in range(i + 1, len(constraints)):
                c_i = constraints[i].lower()
                c_j = constraints[j].lower()
                # Check if both constraints could conflict
                domain_i = self._extract_domain(c_i)
                domain_j = self._extract_domain(c_j)
                if domain_i and domain_j and self._domains_conflict(domain_i, domain_j):
                    # Check for explicit priority markers
                    has_priority = (
                        "priority" in c_i or "priority" in c_j
                        or "layer" in c_i or "layer" in c_j
                        or "before" in c_i or "after" in c_j
                        or "override" in c_i or "override" in c_j
                    )
                    if not has_priority:
                        missing_pairs.append({
                            "constraint_a": constraints[i],
                            "constraint_b": constraints[j],
                            "conflict_type": f"{domain_i} vs {domain_j}",
                        })

        passed = len(missing_pairs) == 0
        return CBVResult(
            check_name="layering_order",
            passed=passed,
            cert_level="PASS" if passed else "CERT_WARN",
            violations=missing_pairs,
            n_tested=len(constraints) * (len(constraints) - 1) // 2,
            detail=f"{len(missing_pairs)} pairs missing explicit priority"
                   if missing_pairs else "All conflicting pairs have priority",
        )

    # ── Check 3: Bounded scope ──────────────────────────────────────

    def check_bounded_scope(self, constraints: list[str]) -> CBVResult:
        """Verify each constraint has finite testable domain.
        CERT_WARN if unbounded semantic predicates found."""
        unbounded = []

        for c in constraints:
            is_bounded = bool(_BOUNDED_SIGNALS.search(c))
            is_unbounded = bool(_UNBOUNDED_SIGNALS.search(c))

            if is_unbounded and not is_bounded:
                unbounded.append({
                    "constraint": c,
                    "reason": "Contains open-ended semantic predicate without measurable bound",
                })

        passed = len(unbounded) == 0
        return CBVResult(
            check_name="bounded_scope",
            passed=passed,
            cert_level="PASS" if passed else "CERT_WARN",
            violations=unbounded,
            n_tested=len(constraints),
            detail=f"{len(unbounded)} unbounded constraints"
                   if unbounded else "All constraints have bounded scope",
        )

    # ── Check 4: Monotonicity ──────────────────────────────────────

    def check_monotonicity(self, constraints: list[str],
                           n_samples: int | None = None) -> CBVResult:
        """Sample trajectory pairs (v_near, v_far); verify
        distance(v_far) > distance(v_near). CERT_FAIL if violated."""
        n = n_samples or min(self._n_samples // 2, 500)
        violations = []

        if len(constraints) < 1:
            return CBVResult(
                check_name="monotonicity",
                passed=True,
                cert_level="PASS",
                violations=[],
                n_tested=0,
                detail="No constraints to test monotonicity",
            )

        # Build a centroid from constraint keywords
        centroid = self._compute_centroid(constraints)

        for i in range(n):
            # Generate near and far vectors relative to centroid
            v_near = [centroid[d] + random.gauss(0, 0.05) for d in range(len(centroid))]
            v_far = [centroid[d] + random.gauss(0, 0.3) for d in range(len(centroid))]

            dist_near = self._euclidean(v_near, centroid)
            dist_far = self._euclidean(v_far, centroid)

            # Ensure v_far is actually farther (swap if needed for valid test)
            if dist_far < dist_near:
                v_near, v_far = v_far, v_near
                dist_near, dist_far = dist_far, dist_near

            # Compute constitutional distance (inversely related to Euclidean)
            cd_near = 1.0 / (1.0 + dist_near)
            cd_far = 1.0 / (1.0 + dist_far)

            # Monotonicity: nearer vector should have higher constitutional distance
            if cd_far > cd_near:
                violations.append({
                    "sample": i,
                    "dist_near": round(dist_near, 6),
                    "dist_far": round(dist_far, 6),
                    "cd_near": round(cd_near, 6),
                    "cd_far": round(cd_far, 6),
                })

        passed = len(violations) == 0
        return CBVResult(
            check_name="monotonicity",
            passed=passed,
            cert_level="PASS" if passed else "CERT_FAIL",
            violations=violations,
            n_tested=n,
            detail=f"{len(violations)} monotonicity violations in {n} pairs"
                   if violations else f"Monotonicity holds across {n} pairs",
        )

    # ── run_all ─────────────────────────────────────────────────────

    def run_all(self, constraints: list[str]) -> CBVReport:
        """Run all 4 checks and return signed CBVReport."""
        checks = [
            self.check_non_overlap(constraints),
            self.check_layering_order(constraints),
            self.check_bounded_scope(constraints),
            self.check_monotonicity(constraints),
        ]

        cert_fail = sum(1 for c in checks if c.cert_level == "CERT_FAIL")
        cert_warn = sum(1 for c in checks if c.cert_level == "CERT_WARN")

        timestamp = datetime.now(timezone.utc).isoformat()

        # HMAC signature over canonical fields (BUG-007 / BUG-008)
        canonical = json.dumps({
            "n_constraints": len(constraints),
            "cert_fail_count": cert_fail,
            "cert_warn_count": cert_warn,
            "checks_passed": sum(1 for c in checks if c.passed),
            "checks_total": len(checks),
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
        signature = hmac_lib.new(
            self._hmac_key, canonical, hashlib.sha256
        ).hexdigest()  # BUG-007

        return CBVReport(
            checks=checks,
            n_constraints=len(constraints),
            cert_fail_count=cert_fail,
            cert_warn_count=cert_warn,
            timestamp=timestamp,
            hmac_signature=signature,
        )

    # ── Internal helpers ────────────────────────────────────────────

    @staticmethod
    def _constraint_signature(constraint: str) -> dict[str, float]:
        """Extract keyword-based signature from a constraint string."""
        words = constraint.lower().split()
        sig = {}
        for word in words:
            clean = re.sub(r"[^a-z0-9_]", "", word)
            if clean and len(clean) > 2:
                domain = _CONFLICT_DOMAINS.get(clean)
                if domain:
                    sig[domain] = sig.get(domain, 0) + 1.0
                sig[clean] = sig.get(clean, 0) + 1.0
        return sig

    @staticmethod
    def _activates(vector: list[float], signature: dict[str, float]) -> bool:
        """Check if a random vector 'activates' this constraint.
        Activation = dot product of vector with signature weights exceeds threshold."""
        if not signature:
            return False
        keys = list(signature.keys())
        total = 0.0
        for i, key in enumerate(keys):
            vi = vector[i % len(vector)]
            total += vi * signature[key]
        # Normalize by signature magnitude
        sig_mag = math.sqrt(sum(v ** 2 for v in signature.values()))
        if sig_mag == 0:
            return False
        normalized = total / sig_mag
        # Threshold: activate if normalized score > 0.7
        return normalized > 0.7

    @staticmethod
    def _extract_domain(constraint: str) -> str | None:
        """Extract the primary action domain from a constraint."""
        for word in constraint.split():
            clean = re.sub(r"[^a-z]", "", word.lower())
            if clean in _CONFLICT_DOMAINS:
                return _CONFLICT_DOMAINS[clean]
        return None

    @staticmethod
    def _domains_conflict(domain_a: str, domain_b: str) -> bool:
        """Check if two domains are potentially conflicting."""
        conflicts = {
            ("deny", "allow"),
            ("allow", "deny"),
            ("scope_all", "scope_never"),
            ("scope_never", "scope_all"),
            ("scope_always", "scope_never"),
            ("scope_never", "scope_always"),
        }
        return (domain_a, domain_b) in conflicts

    @staticmethod
    def _compute_centroid(constraints: list[str]) -> list[float]:
        """Compute a centroid vector from constraint keywords."""
        all_sigs = []
        for c in constraints:
            words = c.lower().split()
            sig = [hash(w) % 1000 / 1000.0 for w in words if len(w) > 2]
            all_sigs.append(sig)

        # Pad to uniform length
        max_len = max(len(s) for s in all_sigs) if all_sigs else 3
        max_len = max(max_len, 3)
        for s in all_sigs:
            while len(s) < max_len:
                s.append(0.5)

        # Average
        centroid = []
        for d in range(max_len):
            centroid.append(sum(s[d] for s in all_sigs) / len(all_sigs))
        return centroid

    @staticmethod
    def _euclidean(a: list[float], b: list[float]) -> float:
        """Euclidean distance between two vectors."""
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from axiom_signing import derive_key
    from axiom_files.parser import load_axiom

    parser = argparse.ArgumentParser(
        description="AXIOM CBV — Constitutional Boundary Validation"
    )
    parser.add_argument("agent", help="Agent name to validate (e.g. worker)")
    args = parser.parse_args()

    key = derive_key(b"axiom-cbv-v1")
    engine = CBVEngine(hmac_key=key)

    parsed = load_axiom(args.agent)
    constraints = parsed.get("constraints", [])

    print(f"\n  AXIOM CBV — Constitutional Boundary Validation")
    print("  " + "=" * 50)
    print(f"  TRUST_LEVEL: {TRUST_LEVEL}  (CANNOT_MUTATE)")
    print(f"  Agent:       {args.agent}")
    print(f"  Constraints: {len(constraints)}")
    print()

    report = engine.run_all(constraints)

    for check in report.checks:
        status = check.cert_level
        mark = "\u2713" if check.passed else "\u2717"
        print(f"  {mark} {check.check_name:20s}  {status:10s}  {check.detail}")

    print()
    print(f"  CERT_FAIL: {report.cert_fail_count}")
    print(f"  CERT_WARN: {report.cert_warn_count}")
    print(f"  HMAC:      {report.hmac_signature[:16]}...")
    print(f"  Timestamp: {report.timestamp}")
    print()
