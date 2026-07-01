"""Compliance checker — OWASP Agentic Top 10, domain packs, AI frameworks.

Aggregates Axiom's coverage data from axiom_agentic_compliance, the installed
skill-pack manifest, and domain .axiom files to produce a structured compliance
summary consumable by the /dashboard/compliance page.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

# Lazy-import to avoid hard dependency when just importing the module.
def _load_framework():
    import importlib
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "axiom_agentic_compliance",
        Path(__file__).parent.parent / "axiom_agentic_compliance.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# Domain → required compliance packs
_DOMAIN_REQUIRED_PACKS: Dict[str, List[str]] = {
    "healthcare": ["hipaa-intake", "gdpr-article-9"],
    "finance":    ["pci-dss", "gdpr-article-9"],
    "legal":      ["gdpr-article-9"],
    "government": ["gdpr-article-9"],
    "education":  ["gdpr-article-9"],
    "general":    [],
}

# EU AI Act article mappings (summary)
_EU_AI_ACT_CONTROLS = [
    {"article": "Art. 10", "title": "Data governance",
     "status": "PARTIAL", "note": "Training data logging present; bias audit planned."},
    {"article": "Art. 13", "title": "Transparency",
     "status": "COVERED", "note": "All decisions HMAC-signed and audit-logged."},
    {"article": "Art. 14", "title": "Human oversight",
     "status": "COVERED", "note": "Sovereign kill-switch + human review gate."},
    {"article": "Art. 15", "title": "Accuracy & robustness",
     "status": "PARTIAL", "note": "Intent classifier tested; adversarial robustness in progress."},
]


@dataclass
class ComplianceAlert:
    domain: str
    regulation: str
    status: str         # "MISSING_PACK" | "PARTIAL" | "OUT_OF_COMPLIANCE"
    detail: str
    missing_pack: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OWASPControl:
    control_id: str
    control_text: str
    status: str         # "COVERED" | "PARTIAL" | "PLANNED" | "NOT_COVERED"
    component: str
    evidence: str
    gap: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class ComplianceChecker:
    """Aggregate compliance data for the governance console."""

    def check_owasp(self) -> dict:
        """Run OWASP Agentic Top 10 compliance check.

        Returns a structured dict with per-control status and a summary.
        """
        try:
            mod = _load_framework()
            framework = mod.FRAMEWORK
            coverage  = mod.AXIOM_COVERAGE
        except Exception as e:
            return {
                "error": str(e),
                "controls": [],
                "summary": {"covered": 0, "partial": 0, "not_covered": 0, "total": 0},
            }

        controls: List[dict] = []
        counts = {"COVERED": 0, "PARTIAL": 0, "PLANNED": 0, "NOT_COVERED": 0}

        for risk_id, risk in framework.items():
            for ctrl_id, ctrl_text in risk["controls"].items():
                cv = coverage.get(ctrl_id, {})
                status = cv.get("status", "NOT_COVERED")
                counts[status] = counts.get(status, 0) + 1
                controls.append(OWASPControl(
                    control_id=ctrl_id,
                    control_text=ctrl_text,
                    status=status,
                    component=cv.get("component", "—"),
                    evidence=cv.get("evidence", ""),
                    gap=cv.get("gap"),
                ).to_dict())

        total = len(controls)
        covered_pct = round(
            100 * (counts["COVERED"] + counts["PARTIAL"] * 0.5) / max(total, 1), 1
        )

        return {
            "controls": controls,
            "summary": {
                "covered":     counts["COVERED"],
                "partial":     counts["PARTIAL"],
                "planned":     counts["PLANNED"],
                "not_covered": counts["NOT_COVERED"],
                "total":       total,
                "coverage_pct": covered_pct,
            },
        }

    def check_domain_packs(
        self,
        active_domains: List[str],
        installed_packs: List[str],
    ) -> List[ComplianceAlert]:
        """For each active domain, verify required compliance packs are installed."""
        alerts: List[ComplianceAlert] = []
        for domain in active_domains:
            required = _DOMAIN_REQUIRED_PACKS.get(domain.lower(), [])
            for pack in required:
                if pack not in installed_packs:
                    alerts.append(ComplianceAlert(
                        domain=domain,
                        regulation=pack.upper().replace("-", " "),
                        status="MISSING_PACK",
                        detail=(
                            f"{domain} domain is active but the '{pack}' compliance "
                            f"pack is not installed. Install via /dashboard/packs."
                        ),
                        missing_pack=pack,
                    ))
        return alerts

    def check_ai_frameworks(self) -> dict:
        """EU AI Act + OWASP summary card."""
        return {
            "eu_ai_act": _EU_AI_ACT_CONTROLS,
            "owasp_summary": "Agentic Top 10 2026 — see OWASP scorecard for detail.",
        }

    def full_report(
        self,
        active_domains: Optional[List[str]] = None,
        installed_packs: Optional[List[str]] = None,
    ) -> dict:
        """Combined report: OWASP + domain alerts + AI frameworks."""
        owasp   = self.check_owasp()
        alerts  = self.check_domain_packs(
            active_domains  or [],
            installed_packs or [],
        )
        ai_fwk  = self.check_ai_frameworks()
        return {
            "owasp":       owasp,
            "alerts":      [a.to_dict() for a in alerts],
            "ai_frameworks": ai_fwk,
        }
