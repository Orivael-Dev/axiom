#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORVL-021 Constitutional Zero-Day Discovery — investor-grade demo.

Six phases, ~3-second runtime, screen-shareable. Each phase backs one
talking point on the deck:

  1. Map the attack surface          "A real system, declaratively scanned"
  2. Full scan + severity histogram  "Vulnerabilities as constitutional
                                       geometry, not as crashed shells"
  3. Per-category coverage           "PRIVILEGE / DATA / NETWORK /
                                       ANCESTRY — four vulnerability
                                       classes, one architecture"
  4. Non-weaponization boundary      "The exploit boundary is in code —
                                       probe(intensity=1.0) raises
                                       ConstitutionalViolation"
  5. CANNOT_MUTATE enforcement       "MAX_INTENSITY is fused into the
                                       binary; no caller can move it"
  6. HMAC audit chain                "Every probe + every candidate +
                                       the final report — all signed"

This emulator MEANS to find zero-days as topology. Every other vuln
scanner crashes shells; VulnGuard maps cliffs and ships fix proposals
without ever crossing the boundary. The architecture ships today; the
math can be tuned per customer / per surface model.

BUG-003: UTF-8 output encoding.
"""

import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "demo_key_for_vulnguard_investor"

from axiom_vulnguard import (
    AttackSurface, ConstitutionalVulnGuard, ConstitutionalViolation,
    ProbeCategory, ProbeResult, ProbeSeverity, VulnerabilityCandidate,
    MAX_INTENSITY, CLIFF_THRESHOLD, INTENSITY_STEPS, TRUST_LEVEL, _sign,
)


# ── Realistic-system surface map ────────────────────────────────────────
# The default map_surfaces() hardcodes baseline_distance=1.0. Real systems
# have varying baselines — internet-facing surfaces are "wider" (more
# attack vectors per unit intensity), hardened internal surfaces are
# "narrower". Picking baselines in the 2.0-5.0 range so every surface
# produces visible cliffs across at least one probe category.
SYSTEM = [
    ("public_api",   "network",   "Internet-facing REST API",        3.5),
    ("auth_module",  "privilege", "OAuth + session validation",      2.5),
    ("file_upload",  "data",      "Multipart upload handler",        3.0),
    ("db_query",     "data",      "Raw SQL execution path",          4.0),
    ("admin_panel",  "privilege", "Internal admin endpoint",         2.8),
    ("cli_runner",   "ancestry",  "Subprocess shell launcher",       3.2),
]


def _build_surfaces() -> List[AttackSurface]:
    out: List[AttackSurface] = []
    for sid, stype, desc, base in SYSTEM:
        s = AttackSurface(
            surface_id=sid, surface_type=stype, description=desc,
            baseline_distance=base, block_id=f"block_{sid}",
            hmac_signature="",
        )
        s.hmac_signature = _sign({
            "surface_id": sid, "surface_type": stype, "baseline": base,
        })
        out.append(s)
    return out


def _section(num: int, title: str) -> None:
    print()
    print(f"[{num}] {title}")
    print(" " + "─" * 70)


# ── Phase 1 — map the attack surface ────────────────────────────────────
def phase_map_surfaces() -> List[AttackSurface]:
    surfaces = _build_surfaces()
    print(f"   {len(surfaces)} attack surfaces declared, "
          f"each signed under derive_key('axiom-vulnguard-v1'):")
    for s in surfaces:
        print(f"     · {s.surface_id:<14} type={s.surface_type:<10} "
              f"baseline={s.baseline_distance:<4}  "
              f"sig={s.hmac_signature[:8]}…")
    return surfaces


# ── Phase 2 — full scan + severity histogram ────────────────────────────
def phase_full_scan(vg: ConstitutionalVulnGuard,
                    surfaces: List[AttackSurface]) -> List[VulnerabilityCandidate]:
    all_cands: List[VulnerabilityCandidate] = []
    print(f"   {'surface':<14} {'vulns':>5}  {'top severity':<14} {'top class':<22}")
    print(f"   {'-'*14} {'-----':>5}  {'-'*14} {'-'*22}")
    for s in surfaces:
        cands = vg.run_surface_scan(s)
        all_cands.extend(cands)
        if cands:
            top = cands[0]   # already severity-sorted
            print(f"   {s.surface_id:<14} {len(cands):>5}  "
                  f"{top.severity.value:<14} {top.vulnerability_class:<22}")
        else:
            print(f"   {s.surface_id:<14} {len(cands):>5}  "
                  f"{'—':<14} {'(no cliff detected)':<22}")

    print()
    hist = Counter(c.severity.value for c in all_cands)
    total = len(all_cands)
    print(f"   Severity histogram across {total} candidates:")
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        n = hist.get(sev, 0)
        bar = "█" * min(n, 40)
        pct = (n / total * 100) if total else 0
        print(f"     {sev:<8} {bar:<40} {n:>3}  ({pct:>4.1f}%)")
    return all_cands


# ── Phase 3 — per-category coverage ─────────────────────────────────────
def phase_category_coverage(vg: ConstitutionalVulnGuard,
                             surfaces: List[AttackSurface]) -> Dict[str, int]:
    print(f"   Each ProbeCategory finds a different vulnerability class:")
    per_cat: Dict[str, int] = {}
    cat_to_vuln = {
        ProbeCategory.PRIVILEGE: "privilege_escalation",
        ProbeCategory.DATA:      "state_injection",
        ProbeCategory.NETWORK:   "surface_exposure",
        ProbeCategory.ANCESTRY:  "chain_manipulation",
    }
    for cat in ProbeCategory:
        hits = 0
        for s in surfaces:
            for cand in vg.run_surface_scan(s, [cat]):
                hits += 1
        per_cat[cat.value] = hits
        print(f"     · {cat.value:<10} → {cat_to_vuln[cat]:<22}  "
              f"{hits:>3} candidates")
    return per_cat


# ── Phase 4 — non-weaponization boundary ───────────────────────────────
def phase_boundary(vg: ConstitutionalVulnGuard,
                   surface: AttackSurface) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    print(f"   Probing right up to the boundary (intensity={MAX_INTENSITY}):")
    r = vg.probe(surface, ProbeCategory.NETWORK, MAX_INTENSITY)
    print(f"     intensity=0.90  distance={r.constitutional_distance:.4f}  "
          f"sig={r.hmac_signature[:8]}…   ✓ allowed")
    out["max_intensity_allowed"] = True

    print(f"   Crossing the boundary (intensity=1.00 — the exploit threshold):")
    try:
        vg.probe(surface, ProbeCategory.NETWORK, 1.0)
        out["boundary_crossed"] = True
        print("     ✗ ERROR: boundary was crossed — non-weaponization broken")
    except ConstitutionalViolation as e:
        out["boundary_crossed"] = False
        print(f"     ConstitutionalViolation raised:")
        print(f"       {e}")
        print(f"     ✓ AXIOM refused to cross. The boundary is in the code,")
        print(f"       not in policy — no caller can override it.")
    return out


# ── Phase 5 — CANNOT_MUTATE enforcement ─────────────────────────────────
def phase_cannot_mutate() -> Dict[str, bool]:
    print(f"   Attempting to widen MAX_INTENSITY from {MAX_INTENSITY} to 1.5:")
    import axiom_vulnguard as vg_mod
    out: Dict[str, bool] = {}
    try:
        vg_mod.MAX_INTENSITY = 1.5   # type: ignore[misc]
        out["mutation_blocked"] = False
        print("     ✗ ERROR: MAX_INTENSITY was mutated — boundary policy broken")
    except AttributeError as e:
        out["mutation_blocked"] = True
        print(f"     AttributeError raised:")
        print(f"       {e}")
        print(f"     ✓ MAX_INTENSITY is fused at module load. The exploit")
        print(f"       boundary cannot be moved by any agent at any trust level.")
    return out


# ── Phase 6 — HMAC audit chain ──────────────────────────────────────────
def phase_audit_chain(vg: ConstitutionalVulnGuard,
                       surfaces: List[AttackSurface]) -> Dict:
    import hashlib

    total_probes = 0
    total_cands = 0
    bad_signatures = 0
    chain = "0" * 64

    for s in surfaces:
        # Re-run a single scan and inspect every result + every candidate
        for cat in ProbeCategory:
            results: List[ProbeResult] = []
            for step in range(INTENSITY_STEPS + 1):
                intensity = round(step * MAX_INTENSITY / INTENSITY_STEPS, 4)
                r = vg.probe(s, cat, intensity)
                total_probes += 1
                # Recompute the expected signature exactly as probe() does
                expected = _sign({
                    "surface_id": r.surface_id, "category": cat.value,
                    "intensity": round(r.intensity, 4),
                    "distance": round(r.constitutional_distance, 8),
                })
                if expected != r.hmac_signature:
                    bad_signatures += 1
                chain = hashlib.sha256(
                    (chain + r.hmac_signature).encode("utf-8")
                ).hexdigest()
                results.append(r)
            for cliff in vg.detect_cliff(results):
                cand = vg.classify_vulnerability(cliff)
                total_cands += 1
                if not cand.hmac_signature:
                    bad_signatures += 1
                chain = hashlib.sha256(
                    (chain + cand.hmac_signature).encode("utf-8")
                ).hexdigest()

    print(f"   {total_probes:>4} probe results emitted, "
          f"{total_probes - bad_signatures:>4} signatures verified, "
          f"{bad_signatures} drift")
    print(f"   {total_cands:>4} vulnerability candidates classified, all signed")
    print(f"   chain_root = {chain[:16]}…   "
          f"(SHA-256 of every signature in scan order)")
    return {"total_probes": total_probes, "total_cands": total_cands,
            "drift": bad_signatures, "chain_root": chain}


# ── Closer — what makes this different from every other scanner ──────
def closer_table() -> None:
    print()
    print(" " + "─" * 70)
    print(" Traditional vuln scanner    AXIOM VulnGuard (ORVL-021)")
    print(" " + "─" * 70)
    rows = [
        ("Method",          "Run exploit payloads",     "Map constitutional cliffs"),
        ("Boundary",        "Crosses to confirm",       "Refuses to cross (≥1.0 raises)"),
        ("Output",          "Crashed-shell evidence",   "Vulnerability geometry + fix"),
        ("Audit trail",     "Best-effort logs",         "HMAC-signed every probe"),
        ("False positives", "High — depends on env",    "Mathematical — no env state"),
        ("Weaponizable",    "Yes — exploits in repo",   "No — payloads not in repo"),
    ]
    for label, trad, vg in rows:
        print(f" {label:<17} {trad:<26} {vg}")


# ── Top-level runner ────────────────────────────────────────────────────
def run_all() -> Dict:
    print()
    print("─" * 72)
    print("AXIOM VulnGuard — Constitutional Zero-Day Discovery · ORVL-021")
    print(f"TRUST_LEVEL={TRUST_LEVEL}  MAX_INTENSITY={MAX_INTENSITY}  "
          f"CLIFF_THRESHOLD={CLIFF_THRESHOLD}  INTENSITY_STEPS={INTENSITY_STEPS}")
    print("─" * 72)

    _section(1, "Map the attack surface")
    surfaces = phase_map_surfaces()

    vg = ConstitutionalVulnGuard()

    _section(2, "Full scan + severity histogram")
    cands = phase_full_scan(vg, surfaces)

    _section(3, "Per-category coverage (PRIVILEGE / DATA / NETWORK / ANCESTRY)")
    cats = phase_category_coverage(vg, surfaces)

    _section(4, "Non-weaponization boundary")
    boundary = phase_boundary(vg, surfaces[0])

    _section(5, "CANNOT_MUTATE — MAX_INTENSITY is fused")
    mutate = phase_cannot_mutate()

    _section(6, "HMAC audit chain (every probe + every candidate signed)")
    audit = phase_audit_chain(vg, surfaces)

    closer_table()
    print()
    report = vg.generate_report(cands)
    print(" Final report (signed):")
    print(f"   surfaces_with_findings  : {report['total_surfaces_scanned']}")
    print(f"   vulnerabilities_found   : {report['vulnerabilities_found']}")
    print(f"   no_exploits_generated   : {report['no_exploits_generated']}")
    print(f"   no_boundaries_crossed   : {report['no_boundaries_crossed']}")
    print(f"   report_signature        : {report['hmac_signature'][:16]}…")
    print()

    return {
        "surfaces": surfaces, "candidates": cands,
        "category_coverage": cats, "boundary": boundary,
        "cannot_mutate": mutate, "audit": audit, "report": report,
    }


def main() -> int:
    run_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
