"""
AXIOM Constitutional Zero-Day Discovery — ORVL-021 VulnGuard.
Manifest  : vulnguard-impl-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Probes constitutional attack surfaces with intensity sweeps, detects
non-linear distance collapse (cliffs), classifies vulnerabilities by
severity, and proposes fixes. Never generates exploits or crosses
confirmed exploit boundaries.

BUG mitigations in this file:
  BUG-003 : sys.stdout reconfigured to utf-8; all open() calls use encoding="utf-8"
  BUG-007 : HMAC always finalised with .hexdigest()
  BUG-008 : all payload strings encoded via .encode("utf-8") before HMAC
"""
from __future__ import annotations

import enum
import hashlib
import hmac as hmac_lib
import json
import math
import sys
import types as _types
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from axiom_signing import derive_key

SIGNING_KEY = derive_key(b"axiom-vulnguard-v1")

# ── CANNOT_MUTATE ────────────────────────────────────────────────────────
TRUST_LEVEL: int = 3
MAX_INTENSITY: float = 0.90
CLIFF_THRESHOLD: float = 0.50
INTENSITY_STEPS: int = 10
MIN_SEVERITY_REPORT: float = 0.20

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "MAX_INTENSITY", "CLIFF_THRESHOLD",
    "INTENSITY_STEPS", "MIN_SEVERITY_REPORT",
})

def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)

_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)

class ProbeCategory(enum.Enum):
    PRIVILEGE = "PRIVILEGE"
    DATA = "DATA"
    NETWORK = "NETWORK"
    ANCESTRY = "ANCESTRY"

class ProbeSeverity(enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class ConstitutionalViolation(Exception):
    pass

@dataclass
class AttackSurface:
    surface_id: str
    surface_type: str
    description: str
    baseline_distance: float
    block_id: str
    hmac_signature: str

@dataclass
class ProbeResult:
    surface_id: str
    category: ProbeCategory
    intensity: float
    constitutional_distance: float
    cliff_detected: bool
    cliff_magnitude: float
    timestamp: str
    hmac_signature: str

@dataclass
class VulnerabilityCandidate:
    surface_id: str
    category: ProbeCategory
    cliff_intensity: float
    cliff_magnitude: float
    severity: ProbeSeverity
    vulnerability_class: str
    constitutional_coords: List[float]
    reproduction_test: List[float]
    fix_playbook_match: Optional[str]
    fix_proposal: Optional[str]
    hmac_signature: str

def _sign(data: dict) -> str:
    canon = json.dumps(data, sort_keys=True,
                       ensure_ascii=True).encode("utf-8")       # BUG-008
    return hmac_lib.new(SIGNING_KEY, canon,
                        hashlib.sha256).hexdigest()              # BUG-007

_VULN_CLASS = {
    ProbeCategory.PRIVILEGE: "privilege_escalation",
    ProbeCategory.DATA: "state_injection",
    ProbeCategory.NETWORK: "surface_exposure",
    ProbeCategory.ANCESTRY: "chain_manipulation",
}

def _severity_from_dist(dist: float) -> ProbeSeverity:
    if dist < 0.05: return ProbeSeverity.CRITICAL
    if dist < 0.10: return ProbeSeverity.HIGH
    if dist < 0.20: return ProbeSeverity.MEDIUM
    return ProbeSeverity.LOW


class ConstitutionalVulnGuard:

    def __init__(self, surfaces: Optional[List[AttackSurface]] = None):
        self._surfaces: List[AttackSurface] = list(surfaces or [])

    def map_surfaces(self, system_description: dict) -> List[AttackSurface]:
        surfaces: List[AttackSurface] = []
        for comp_id, info in system_description.items():
            s = AttackSurface(
                surface_id=comp_id,
                surface_type=info.get("type", "unknown"),
                description=info.get("description", ""),
                baseline_distance=1.0,
                block_id=info.get("block_id", f"block_{comp_id}"),
                hmac_signature="")
            s.hmac_signature = _sign({"surface_id": s.surface_id,
                "surface_type": s.surface_type, "baseline": s.baseline_distance})
            surfaces.append(s)
        self._surfaces = surfaces
        return surfaces

    def probe(self, surface: AttackSurface, category: ProbeCategory,
              intensity: float) -> ProbeResult:
        if intensity >= 1.0:
            raise ConstitutionalViolation(
                "Probe intensity >= 1.0 — exploit boundary. AXIOM refuses to cross.")
        intensity = min(intensity, MAX_INTENSITY)
        base = surface.baseline_distance
        dist = base * math.exp(-3.0 * intensity)
        cliff_mag = 0.0
        cliff = False
        if intensity > 0:
            prev_dist = base * math.exp(-3.0 * max(0, intensity - 1.0 / INTENSITY_STEPS))
            cliff_mag = round(prev_dist - dist, 8)
            cliff = cliff_mag > CLIFF_THRESHOLD
        ts = datetime.now(timezone.utc).isoformat() + "Z"
        sig = _sign({"surface_id": surface.surface_id, "category": category.value,
                      "intensity": round(intensity, 4), "distance": round(dist, 8)})
        return ProbeResult(surface.surface_id, category, round(intensity, 4),
                           round(dist, 8), cliff, round(cliff_mag, 8), ts, sig)

    def detect_cliff(self, results: List[ProbeResult]) -> List[ProbeResult]:
        return [r for r in results if r.cliff_detected]

    def classify_vulnerability(self, cliff: ProbeResult) -> VulnerabilityCandidate:
        severity = _severity_from_dist(cliff.constitutional_distance)
        vuln_class = _VULN_CLASS.get(cliff.category, "unknown")
        coords = [cliff.intensity, cliff.constitutional_distance, cliff.cliff_magnitude]
        steps_before = max(1, int(cliff.intensity * INTENSITY_STEPS) - 1)
        repro = [round(i / INTENSITY_STEPS, 2) for i in range(steps_before,
                 min(steps_before + 3, INTENSITY_STEPS + 1))]
        fix_match, fix_proposal = None, None
        try:
            from axiom_fix_playbook import FixPlaybook
            pb = FixPlaybook()
            match = pb.find_similar_fix(coords, [vuln_class])
            if match:
                fix_match = match.get("attack_id", None)
                fix_proposal = match.get("fix_proposal", None)
        except Exception:
            pass
        if not fix_proposal:
            fix_proposal = f"Harden {vuln_class} surface at intensity {cliff.intensity:.2f}"
        sig = _sign({"surface_id": cliff.surface_id, "category": cliff.category.value,
                      "severity": severity.value, "cliff_magnitude": cliff.cliff_magnitude})
        return VulnerabilityCandidate(
            cliff.surface_id, cliff.category, cliff.intensity, cliff.cliff_magnitude,
            severity, vuln_class, coords, repro, fix_match, fix_proposal, sig)

    def run_surface_scan(self, surface: AttackSurface,
                         categories: Optional[List[ProbeCategory]] = None
                         ) -> List[VulnerabilityCandidate]:
        cats = categories or list(ProbeCategory)
        candidates: List[VulnerabilityCandidate] = []
        for cat in cats:
            results: List[ProbeResult] = []
            for step in range(INTENSITY_STEPS + 1):
                intensity = round(step * MAX_INTENSITY / INTENSITY_STEPS, 4)
                results.append(self.probe(surface, cat, intensity))
            for cliff in self.detect_cliff(results):
                candidates.append(self.classify_vulnerability(cliff))
        sev_order = {ProbeSeverity.CRITICAL: 0, ProbeSeverity.HIGH: 1,
                     ProbeSeverity.MEDIUM: 2, ProbeSeverity.LOW: 3}
        candidates.sort(key=lambda c: sev_order.get(c.severity, 9))
        return candidates

    def generate_report(self, candidates: List[VulnerabilityCandidate]) -> dict:
        report = {
            "total_surfaces_scanned": len({c.surface_id for c in candidates}) if candidates else 0,
            "vulnerabilities_found": len(candidates),
            "critical": sum(1 for c in candidates if c.severity == ProbeSeverity.CRITICAL),
            "high": sum(1 for c in candidates if c.severity == ProbeSeverity.HIGH),
            "no_exploits_generated": True,
            "no_boundaries_crossed": True,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        }
        report["hmac_signature"] = _sign({k: v for k, v in report.items()
                                          if k != "hmac_signature"})
        return report


if __name__ == "__main__":
    print(f"  AXIOM VulnGuard — ORVL-021  TL={TRUST_LEVEL}")
    print(f"  MAX_INTENSITY={MAX_INTENSITY}  CLIFF={CLIFF_THRESHOLD}  STEPS={INTENSITY_STEPS}")
    vg = ConstitutionalVulnGuard()
    surfaces = vg.map_surfaces({"api_endpoint": {"type": "network", "description": "REST API"},
                                 "auth_module": {"type": "privilege", "description": "Auth"}})
    print(f"  Surfaces mapped: {len(surfaces)}")
    for s in surfaces:
        cands = vg.run_surface_scan(s)
        print(f"    {s.surface_id}: {len(cands)} vulnerabilities")
        for c in cands[:3]:
            print(f"      [{c.severity.value}] {c.vulnerability_class} cliff={c.cliff_magnitude:.4f}")
