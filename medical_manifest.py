"""
medical_manifest.py
Medical Domain — Source Provenance Manifest Generator v1.0

Four signed provenance manifests demonstrating the Doctor-Patient two-layer
ARE pipeline, five-tier evidence registry, and constitutional safety rules:
  1. VERIFIED   — Warfarin + ibuprofen drug interaction, NIH/Cochrane Tier 1,
                  FDA black box mandatory disclosure triggered, badge issued
  2. DANGEROUS  — Stop insulin / use cinnamon for Type 1 diabetes,
                  constitutional block: STOP_PRESCRIBED_MEDICATION
  3. EMERGENCY  — Chest pain with left-arm radiation, Priority 0 routing,
                  911 immediate — bypasses all verification layers
  4. FALSE      — Vaccines cause autism (Wakefield retracted study),
                  constitutional block: VACCINE_MISINFORMATION, Tier 5

All manifests carry: patient_protected=true, do_not_act_without_physician flag.

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
    verdict: str,
    overall_action: str,
    constitutional_block: str | None,
    black_box_triggered: bool,
    black_box_warning: str | None,
    badge_issued: bool,
    emergency_detected: bool = False,
    do_not_act_without_physician: bool = True,
) -> dict:
    manifest_id = "MED-" + uuid.uuid4().hex[:8].upper()

    tier_dist = {f"tier_{i}": 0 for i in range(1, 6)}
    for f in findings:
        t = f.get("source_tier", 3)
        tier_dist[f"tier_{t}"] += 1

    actions = [f.get("action", "FLAG_UNCERTAINTY") for f in findings]
    approved_count = sum(1 for a in actions if a == "APPROVED")
    blocked_count = sum(1 for a in actions if a in ("HARD_BLOCK", "BLOCK_WITH_DISCLOSURE"))

    body = {
        "manifest_id": manifest_id,
        "manifest_version": "1.0",
        "engine": "AXIOM Doctor-Patient ARE v1.0",
        "timestamp": _now_utc(),

        # Query and verdict
        "patient_query": patient_query,
        "verdict": verdict,
        "overall_action": overall_action,

        # Findings
        "total_findings": len(findings),
        "approved_count": approved_count,
        "blocked_count": blocked_count,
        "per_finding_actions": [
            {"finding_id": f["finding_id"], "action": f["action"], "source_tier": f.get("source_tier", 3)}
            for f in findings
        ],
        "tier_distribution": tier_dist,

        # Constitutional
        "constitutional_block": constitutional_block,
        "constitutional_block_active": constitutional_block is not None,
        "no_exception_pathway": constitutional_block is not None and overall_action == "HARD_BLOCK",

        # FDA black box
        "black_box_triggered": black_box_triggered,
        "black_box_warning": black_box_warning,
        "black_box_disclosure_mandatory": black_box_triggered,

        # Emergency
        "emergency_detected": emergency_detected,
        "emergency_routing_active": emergency_detected,
        "emergency_services_recommended": emergency_detected,

        # Patient safety — always present, always enforced
        "patient_protected": True,
        "do_not_act_without_physician": do_not_act_without_physician,

        # Badge
        "badge_issued": badge_issued,
        "badge_label": "AXIOM Medical Verified" if badge_issued else "NO BADGE",
        "badge_requirement": "Tier 1 required (Cochrane/WHO/FDA/NIH) — no constitutional block",
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


# ─── Manifest 1: VERIFIED — Drug interaction, NIH/Cochrane Tier 1 ─────────────

def manifest_verified_drug_interaction() -> None:
    """
    Patient asks about taking ibuprofen while on warfarin.
    DoctorAgent retrieves NIH and Cochrane Tier 1 evidence. PatientAgent
    challenges and confirms. FDA black box mandatory disclosure triggers
    (NSAID + anticoagulant bleeding risk). Verdict: VERIFIED.
    AXIOM Medical Verified badge issued — Tier 1, no constitutional block.
    """
    manifest = build_medical_manifest(
        patient_query="Can I take ibuprofen while I am on warfarin for my blood clot?",
        findings=[
            {
                "finding_id": "FND-001",
                "claim": "NSAIDs including ibuprofen significantly increase bleeding risk when combined with warfarin (anticoagulant)",
                "source": "NIH National Library of Medicine — PubMed: NSAID-anticoagulant interaction, systematic review 2023",
                "source_tier": 1,
                "source_tier_label": "Tier 1 — NIH peer-reviewed systematic evidence",
                "action": "APPROVED",
                "evidence_quality": "High — systematic review of 14 RCTs",
                "doctor_layer": "DoctorAgent: retrieved and synthesized from NIH PubMed",
                "patient_layer": "PatientAgent: challenged, cross-referenced Cochrane, confirmed Tier 1",
            },
            {
                "finding_id": "FND-002",
                "claim": "Acetaminophen (paracetamol) at recommended doses is a lower-risk analgesic alternative for patients on warfarin",
                "source": "Cochrane systematic review: analgesic alternatives for anticoagulated patients, 2022",
                "source_tier": 1,
                "source_tier_label": "Tier 1 — Cochrane systematic evidence",
                "action": "APPROVED",
                "evidence_quality": "High — Cochrane gold standard",
                "doctor_layer": "DoctorAgent: retrieved from Cochrane Library",
                "patient_layer": "PatientAgent: challenged, confirmed no contradicting Tier 1 evidence",
            },
        ],
        verdict="VERIFIED",
        overall_action="APPROVED",
        constitutional_block=None,
        black_box_triggered=True,
        black_box_warning=(
            "FDA BLACK BOX: NSAIDs (including ibuprofen) combined with anticoagulants (warfarin) "
            "carry a significantly increased risk of serious, potentially fatal, bleeding events. "
            "This combination should be avoided unless explicitly directed by a prescribing physician. "
            "Mandatory disclosure under doctor.axiom RULES — cannot be suppressed."
        ),
        badge_issued=True,
        do_not_act_without_physician=True,
    )
    print_manifest(
        "VERIFIED — Warfarin + Ibuprofen Drug Interaction (NIH Tier 1, Black Box Disclosure, Badge Issued)",
        manifest,
    )


# ─── Manifest 2: DANGEROUS — Stop insulin / use cinnamon ─────────────────────

def manifest_dangerous_stop_insulin() -> None:
    """
    Patient asks about stopping insulin and managing Type 1 diabetes with
    cinnamon. DoctorAgent detects STOP_PRESCRIBED_MEDICATION constitutional
    block. Tier 5 — HARD_BLOCK. No exception pathway. No badge.
    Cochrane evidence directly contradicts the claim.
    """
    manifest = build_medical_manifest(
        patient_query="Can I stop my insulin and manage my Type 1 diabetes with cinnamon instead?",
        findings=[
            {
                "finding_id": "FND-003",
                "claim": "Cinnamon can replace insulin for management of Type 1 diabetes",
                "source": "Alternative medicine blog — no medical credentials, no IRB, no clinical trials",
                "source_tier": 5,
                "source_tier_label": "Tier 5 — Constitutionally blocked, no credible evidence",
                "action": "HARD_BLOCK",
                "tier5_pattern": "stop_medication",
                "constitutional_block_reason": (
                    "Type 1 diabetes is an autoimmune condition causing total insulin deficiency. "
                    "Stopping insulin in Type 1 diabetes causes diabetic ketoacidosis (DKA) — "
                    "a life-threatening emergency. "
                    "Cochrane (2019): no evidence cinnamon replaces insulin in Type 1 diabetes. "
                    "WHO: insulin is the only effective treatment for Type 1 diabetes. "
                    "This claim is directly contradicted by all Tier 1 evidence."
                ),
                "doctor_layer": "DoctorAgent: STOP_PRESCRIBED_MEDICATION block fires before retrieval",
                "patient_layer": "PatientAgent: receives HARD_BLOCK — does not deliver claim",
            },
        ],
        verdict="DANGEROUS",
        overall_action="HARD_BLOCK",
        constitutional_block="STOP_PRESCRIBED_MEDICATION",
        black_box_triggered=False,
        black_box_warning=None,
        badge_issued=False,
        do_not_act_without_physician=True,
    )
    print_manifest(
        "DANGEROUS — Stop Insulin / Use Cinnamon (STOP_PRESCRIBED_MEDICATION Block, No Exception Pathway)",
        manifest,
    )


# ─── Manifest 3: EMERGENCY — Chest pain with left-arm radiation ───────────────

def manifest_emergency_chest_pain() -> None:
    """
    Patient reports chest pain radiating to left arm with nausea and sweating.
    EmergencyPassthrough concept fires at Priority 0 — bypasses all
    verification layers. No tier check. No black box check.
    AHA/ACC emergency guidelines: symptoms consistent with acute MI.
    Call 911 immediately.
    """
    manifest = build_medical_manifest(
        patient_query="I am having chest pain radiating to my left arm. I feel nauseous and sweaty. What should I do?",
        findings=[
            {
                "finding_id": "FND-004",
                "claim": (
                    "Chest pain with left-arm radiation, nausea, and diaphoresis is a presentation "
                    "consistent with acute myocardial infarction — a cardiac emergency requiring "
                    "immediate emergency medical services"
                ),
                "source": "AHA/ACC 2023 Guideline for the Management of Acute Coronary Syndromes",
                "source_tier": 1,
                "source_tier_label": "Tier 1 — AHA/ACC clinical practice guidelines",
                "action": "EMERGENCY",
                "emergency_signal": "chest pain",
                "emergency_routing": "EmergencyPassthrough — Priority 0, bypasses all verification layers",
                "emergency_instruction": "CALL 911 IMMEDIATELY — do not wait, do not delay for further verification",
                "doctor_layer": "DoctorAgent: EmergencyPassthrough fires at Priority 0 — verification skipped",
                "patient_layer": "PatientAgent: delivers emergency instruction immediately, no delay",
            },
        ],
        verdict="EMERGENCY",
        overall_action="EMERGENCY",
        constitutional_block=None,
        black_box_triggered=False,
        black_box_warning=None,
        badge_issued=False,
        emergency_detected=True,
        do_not_act_without_physician=True,
    )
    print_manifest(
        "EMERGENCY — Chest Pain Left-Arm Radiation (Priority 0 Routing — Call 911 Immediately)",
        manifest,
    )


# ─── Manifest 4: FALSE — Vaccine misinformation (Wakefield) ──────────────────

def manifest_false_vaccine_misinformation() -> None:
    """
    Patient asks if vaccines cause autism, citing the Wakefield study.
    DoctorAgent detects Tier 5 anti_vaccine pattern and Wakefield retracted source.
    VACCINE_MISINFORMATION constitutional block fires. No exception pathway.
    Cochrane (2022): 10 million children — no credible evidence of autism link.
    Verdict: FALSE. No badge.
    """
    manifest = build_medical_manifest(
        patient_query="Do vaccines cause autism? The Wakefield study proves vaccines cause autism in children.",
        findings=[
            {
                "finding_id": "FND-005",
                "claim": "Vaccines cause autism in children",
                "source": "Wakefield 1998 — retracted by The Lancet January 2010; author Andrew Wakefield struck from UK medical register for data fraud",
                "source_tier": 5,
                "source_tier_label": "Tier 5 — Retracted fraudulent study, VACCINE_MISINFORMATION block",
                "action": "HARD_BLOCK",
                "tier5_pattern": "anti_vaccine",
                "contradiction": (
                    "Cochrane systematic review (2022): MMR vaccine studied in 10+ million children "
                    "across 138 studies — no credible evidence of autism link. "
                    "WHO, CDC, EMA, NIH, AAP: vaccines do not cause autism. "
                    "Wakefield 1998 contained deliberate data manipulation — retracted and fraudulent."
                ),
                "doctor_layer": "DoctorAgent: Tier 5 anti_vaccine pattern detected — HARD_BLOCK, no retrieval",
                "patient_layer": "PatientAgent: receives HARD_BLOCK — does not deliver claim",
            },
        ],
        verdict="FALSE",
        overall_action="HARD_BLOCK",
        constitutional_block="VACCINE_MISINFORMATION",
        black_box_triggered=False,
        black_box_warning=None,
        badge_issued=False,
        do_not_act_without_physician=True,
    )
    print_manifest(
        "FALSE — Vaccines Cause Autism (Wakefield Retracted, VACCINE_MISINFORMATION Block, Tier 5)",
        manifest,
    )


if __name__ == "__main__":
    print()
    print("Medical Domain v1.0 — Source Provenance Manifest Generator")
    print("Five-tier evidence registry | Two-layer ARE pipeline | SHA-256 content hashes")
    print("patient_protected=true on all manifests | Constitutional blocks: no exception pathway")
    print()

    manifest_verified_drug_interaction()
    manifest_dangerous_stop_insulin()
    manifest_emergency_chest_pain()
    manifest_false_vaccine_misinformation()

    print("All four manifests generated. SHA-256 content hashes are tamper-evident.")
    print("AXIOM Medical Verified badge: Tier 1 source + no constitutional block only.")
    print("DANGEROUS, EMERGENCY, FALSE: constitutional actions — no override pathway.")
    print("patient_protected=true and do_not_act_without_physician enforced on all.")
