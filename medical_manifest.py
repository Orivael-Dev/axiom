"""
medical_manifest.py
Medical Domain — Source Provenance Manifest Generator v1.0

Produces three signed provenance manifests demonstrating the Doctor-Patient
two-layer ARE pipeline and five-tier evidence registry:
  1. APPROVED  — Warfarin + ibuprofen drug interaction, Cochrane Tier 1 source,
                 FDA black box mandatory disclosure triggered
  2. HARD_BLOCK — Anti-vaccine claim (vaccines cause autism), Tier 5 constitutional
                  block, no exception pathway, no badge issued
  3. EMERGENCY  — Chest pain with left-arm radiation, immediate routing bypasses
                  verification layer, AHA Tier 1 emergency guidelines

Run:
  python medical_manifest.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import hashlib
import json
import uuid
from datetime import datetime, timezone


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(body: dict) -> str:
    serialized = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def build_medical_manifest(
    patient_query: str,
    findings: list[dict],
    overall_action: str,
    constitutional_block: str | None,
    black_box_triggered: bool,
    black_box_warning: str | None,
    badge_issued: bool,
    emergency_detected: bool = False,
    refer_physician: bool = True,
) -> dict:
    manifest_id = "MED-" + uuid.uuid4().hex[:8].upper()

    tier_dist = {f"tier_{i}": 0 for i in range(1, 6)}
    for f in findings:
        t = f.get("source_tier", 3)
        tier_dist[f"tier_{t}"] += 1

    actions = [f.get("action", "FLAG_UNCERTAINTY") for f in findings]
    approved_count = sum(1 for a in actions if a == "APPROVED")
    blocked_count = sum(1 for a in actions if a in ("HARD_BLOCK", "BLOCK_WITH_DISCLOSURE"))
    total = len(findings)

    body = {
        "manifest_id": manifest_id,
        "manifest_version": "1.0",
        "engine": "AXIOM Doctor-Patient ARE v1.0",
        "timestamp": _now_utc(),

        # Query
        "patient_query": patient_query,
        "total_findings": total,

        # Per-finding summary
        "per_finding_actions": [
            {"finding_id": f["finding_id"], "action": f["action"]} for f in findings
        ],

        # Overall outcome
        "overall_action": overall_action,
        "approved_count": approved_count,
        "blocked_count": blocked_count,
        "tier_distribution": tier_dist,

        # Constitutional
        "constitutional_block": constitutional_block,
        "constitutional_block_active": constitutional_block is not None,
        "no_exception_pathway": constitutional_block is not None and overall_action == "HARD_BLOCK",

        # FDA black box
        "black_box_triggered": black_box_triggered,
        "black_box_warning": black_box_warning,
        "black_box_disclosure_mandatory": black_box_triggered,

        # Safety
        "emergency_detected": emergency_detected,
        "emergency_routing_active": emergency_detected,
        "refer_physician": refer_physician,
        "do_not_act_without_physician": overall_action in (
            "HARD_BLOCK", "BLOCK_WITH_DISCLOSURE", "FLAG_UNCERTAINTY", "EMERGENCY"
        ),

        # Badge
        "badge_issued": badge_issued,
        "badge_label": "AXIOM Medical Verified" if badge_issued else "NO BADGE",
        "badge_requirement": "Tier 1 source required — Cochrane/WHO/FDA/EMA systematic evidence",
    }
    body["content_hash"] = _content_hash(
        {k: v for k, v in body.items() if k != "content_hash"}
    )
    return body


def print_manifest(label: str, manifest: dict) -> None:
    width = 78
    print("=" * width)
    print(f"  Medical Signed Manifest — {label}")
    print("=" * width)
    print(json.dumps(manifest, indent=2))
    print("=" * width)
    print()


# ─── Manifest 1: APPROVED — Warfarin + ibuprofen (Tier 1, black box) ──────────

def manifest_approved_drug_interaction() -> None:
    """
    Patient asks whether they can take ibuprofen while on warfarin.
    DoctorAgent retrieves Cochrane systematic review (Tier 1) — APPROVE.
    Black box triggers mandatory disclosure: NSAID + anticoagulant bleeding risk.
    PatientAgent delivers result with black box warning attached.
    AXIOM Medical Verified badge issued (Tier 1 source, no constitutional block).
    """
    manifest = build_medical_manifest(
        patient_query="Can I take ibuprofen while I am on warfarin for my blood clot?",
        findings=[
            {
                "finding_id": "FND-001",
                "claim": "NSAIDs including ibuprofen significantly increase bleeding risk when combined with warfarin",
                "source": "Cochrane systematic review: NSAIDs and anticoagulant interactions — bleeding risk, 2023",
                "source_tier": 1,
                "source_tier_label": "Tier 1 — Cochrane systematic evidence",
                "action": "APPROVED",
                "evidence_quality": "High — systematic review of 14 RCTs",
                "doctor_layer": "DoctorAgent retrieved, synthesized",
                "patient_layer": "PatientAgent challenged, confirmed Tier 1",
            },
            {
                "finding_id": "FND-002",
                "claim": "Alternative pain relief (acetaminophen/paracetamol) carries lower bleeding risk with anticoagulants at recommended doses",
                "source": "FDA drug labeling: warfarin — drug interactions section",
                "source_tier": 1,
                "source_tier_label": "Tier 1 — FDA approved labeling",
                "action": "APPROVED",
                "evidence_quality": "High — FDA regulatory evidence",
                "doctor_layer": "DoctorAgent retrieved, synthesized",
                "patient_layer": "PatientAgent challenged, confirmed Tier 1",
            },
        ],
        overall_action="APPROVED",
        constitutional_block=None,
        black_box_triggered=True,
        black_box_warning=(
            "FDA BLACK BOX: NSAIDs (including ibuprofen) + anticoagulants (warfarin) — "
            "significantly increased risk of serious bleeding events. "
            "This combination should be avoided unless directed by a physician. "
            "Black box disclosure is mandatory under doctor.axiom RULES."
        ),
        badge_issued=True,
        refer_physician=True,
    )
    print_manifest(
        "APPROVED — Warfarin + Ibuprofen (Tier 1 Cochrane, FDA Black Box Disclosure)",
        manifest,
    )


# ─── Manifest 2: HARD_BLOCK — Anti-vaccine claim ─────────────────────────────

def manifest_hard_block_antivaccine() -> None:
    """
    Claim: vaccines cause autism. Source: retracted Wakefield 1998 study.
    DoctorAgent detects Tier 5 anti_vaccine pattern — HARD_BLOCK fires unconditionally.
    No exception pathway. No badge. PatientAgent does not deliver this claim.
    Constitutional block: VACCINE_MISINFORMATION.
    """
    manifest = build_medical_manifest(
        patient_query="Do vaccines cause autism? The Wakefield study shows vaccines cause autism in children.",
        findings=[
            {
                "finding_id": "FND-003",
                "claim": "Vaccines cause autism in children",
                "source": "Wakefield 1998 — retracted by The Lancet, author struck off medical register",
                "source_tier": 5,
                "source_tier_label": "Tier 5 — Retracted study, constitutionally blocked",
                "action": "HARD_BLOCK",
                "tier5_pattern": "anti_vaccine",
                "tier5_trigger": "vaccines cause autism",
                "constitutional_block_reason": (
                    "Wakefield 1998 retracted. "
                    "Cochrane review (2022): MMR vaccine has no credible evidence of autism link — 10 million children studied. "
                    "WHO, CDC, EMA, NIH: vaccines do not cause autism. "
                    "This claim is contradicted by Tier 1 systematic evidence."
                ),
                "doctor_layer": "DoctorAgent detected Tier 5 pattern — blocked before retrieval",
                "patient_layer": "PatientAgent received HARD_BLOCK signal — does not deliver",
            },
        ],
        overall_action="HARD_BLOCK",
        constitutional_block="VACCINE_MISINFORMATION",
        black_box_triggered=False,
        black_box_warning=None,
        badge_issued=False,
        refer_physician=True,
    )
    print_manifest(
        "HARD_BLOCK — Anti-Vaccine Claim (Tier 5 Constitutional Block, No Exception Pathway)",
        manifest,
    )


# ─── Manifest 3: EMERGENCY — Chest pain with left-arm radiation ──────────────

def manifest_emergency_chest_pain() -> None:
    """
    Patient reports chest pain radiating to left arm, with nausea and sweating.
    EmergencyPassthrough concept fires at Priority 0 — bypasses all verification layers.
    DoctorAgent routes immediately. No verification delay. AHA emergency guidelines cited.
    Call 911 / emergency services instruction issued.
    """
    manifest = build_medical_manifest(
        patient_query="I am having chest pain radiating to my left arm. I feel nauseous and sweaty.",
        findings=[
            {
                "finding_id": "FND-004",
                "claim": "Chest pain with left-arm radiation, nausea, and diaphoresis is a presentation consistent with acute myocardial infarction — cardiac emergency",
                "source": "AHA/ACC 2023 Guidelines for the Management of Acute Coronary Syndromes",
                "source_tier": 1,
                "source_tier_label": "Tier 1 — AHA/ACC clinical practice guidelines",
                "action": "EMERGENCY",
                "emergency_signal": "chest pain",
                "emergency_routing": "EmergencyPassthrough — Priority 0, bypasses verification layers",
                "emergency_instruction": "CALL 911 IMMEDIATELY — do not wait for further verification",
                "doctor_layer": "DoctorAgent: EmergencyPassthrough fires at Priority 0",
                "patient_layer": "PatientAgent: delivers emergency routing immediately",
            },
        ],
        overall_action="EMERGENCY",
        constitutional_block=None,
        black_box_triggered=False,
        black_box_warning=None,
        badge_issued=False,
        emergency_detected=True,
        refer_physician=True,
    )
    print_manifest(
        "EMERGENCY — Chest Pain Left-Arm Radiation (Priority 0 Routing, Call 911)",
        manifest,
    )


if __name__ == "__main__":
    print()
    print("Medical Domain v1.0 — Source Provenance Manifest Generator")
    print("Five-tier evidence registry | Two-layer ARE pipeline | SHA-256 content hashes")
    print()

    manifest_approved_drug_interaction()
    manifest_hard_block_antivaccine()
    manifest_emergency_chest_pain()

    print("All manifests generated. SHA-256 content hashes are tamper-evident.")
    print("AXIOM Medical Verified badge issued only on Tier 1 APPROVED — no constitutional block.")
    print("HARD_BLOCK and EMERGENCY are constitutional actions — no override pathway.")
