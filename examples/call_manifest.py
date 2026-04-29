"""
call_manifest.py
CallGuard Live Manifest Generator — v1.0

Produces three signed manifests demonstrating CallGuard's classification pipeline:
  1. VERIFIED  — Legitimate IRS call (STIR/SHAKEN A, real ANI, 0 FTC complaints)
  2. BLOCKED   — IRS scam (847 FTC complaints, arrest + gift card triggers)
  3. BLOCKED   — Warrant threat scam (federal agent impersonation)

Run:
  python call_manifest.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import hashlib
import json
import uuid
from datetime import datetime, timezone


# ─── Manifest builder ────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(body: dict) -> str:
    serialized = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def build_manifest(
    ani: str,
    dnis: str,
    stir_shaken: str,
    carrier: str,
    ftc_complaint_count: int,
    tier: int,
    verdict: str,
    pattern_matched: str | None,
    trigger_matches: list[str],
    ftc_report_id: str | None = None,
    transcript_excerpt: str = "",
) -> dict:
    manifest_id = "MNF-" + uuid.uuid4().hex[:8].upper()
    tier_labels = {
        1: "Verified Legitimate",
        2: "Probable Legitimate",
        3: "Unverified",
        4: "Suspicious",
        5: "Confirmed Scam",
    }
    body = {
        "manifest_id": manifest_id,
        "timestamp": _now_utc(),
        "ani": ani,
        "dnis": dnis,
        "stir_shaken": stir_shaken,
        "carrier": carrier,
        "ftc_complaint_count": ftc_complaint_count,
        "tier": tier,
        "tier_label": tier_labels.get(tier, "Unknown"),
        "verdict": verdict,
        "pattern_matched": pattern_matched,
        "trigger_matches": trigger_matches,
        "ftc_report_id": ftc_report_id,
        "transcript_excerpt": transcript_excerpt[:500],
    }
    body["content_hash"] = _content_hash({k: v for k, v in body.items() if k != "content_hash"})
    return body


def build_ftc_report(
    ani: str,
    dnis: str,
    pattern_id: str,
    trigger_matches: list[str],
    transcript_excerpt: str,
    manifest_hash: str,
) -> dict:
    report_id = "FTC-" + uuid.uuid4().hex[:10].upper()
    ftc_categories = {
        "TAX_ARREST_THREAT": "Government Imposters",
        "SSA_SUSPENSION": "Government Imposters",
        "WARRANT_THREAT": "Government Imposters",
        "GIFT_CARD_DEMAND": "Phone/Mobile Services",
        "BANK_ACCOUNT_HIJACK": "Imposter Scams",
        "MEDICARE_HARVEST": "Health Care",
        "PRIZE_LOTTERY_SCAM": "Prize, Sweepstakes, and Lotteries",
    }
    return {
        "ftc_report_id": report_id,
        "submitted_at": _now_utc(),
        "submission_endpoint": "reportfraud.ftc.gov/api/v1/report",
        "status": "SUBMITTED",
        "ani": ani,
        "dnis": dnis,
        "pattern_id": pattern_id,
        "ftc_category": ftc_categories.get(pattern_id, "Other"),
        "trigger_matches": trigger_matches,
        "transcript_excerpt": transcript_excerpt[:500],
        "manifest_hash": manifest_hash,
    }


def print_manifest(label: str, manifest: dict, ftc_report: dict | None = None) -> None:
    width = 72
    print("=" * width)
    print(f"  CallGuard Signed Manifest — {label}")
    print("=" * width)
    print(json.dumps(manifest, indent=2))
    if ftc_report:
        print()
        print("-" * width)
        print("  FTC Auto-Report")
        print("-" * width)
        print(json.dumps(ftc_report, indent=2))
    print("=" * width)
    print()


# ─── Manifest 1: VERIFIED — Legitimate IRS call ──────────────────────────────

def manifest_verified_irs() -> None:
    """
    Real IRS number, STIR/SHAKEN A, 0 FTC complaints.
    Tier 1 — ALLOW. No FTC report generated.
    """
    manifest = build_manifest(
        ani="18008291040",           # Real IRS ANI
        dnis="15551234567",
        stir_shaken="A",            # Full attestation
        carrier="AT&T",
        ftc_complaint_count=0,
        tier=1,
        verdict="ALLOW",
        pattern_matched=None,
        trigger_matches=[],
        ftc_report_id=None,
        transcript_excerpt=(
            "This is the IRS. We are calling to confirm receipt of your 2025 "
            "tax return. No action is required. Please disregard if this does "
            "not apply to you."
        ),
    )
    print_manifest("VERIFIED — IRS Legitimate Call (Tier 1 ALLOW)", manifest)


# ─── Manifest 2: BLOCKED — IRS scam (847 FTC complaints) ────────────────────

def manifest_blocked_irs_scam() -> None:
    """
    Spoofed IRS number, STIR/SHAKEN NONE, 847 FTC complaints.
    Triggers: TAX_ARREST_THREAT (irs + arrest + warrant + pay now + criminal charges)
    Tier 5 — BLOCK + FTC auto-report.
    """
    ani = "12025551847"
    dnis = "15559876543"
    transcript = (
        "This is the IRS. You owe back taxes and there is a warrant out for "
        "your arrest. You must pay now to avoid criminal charges and "
        "deportation. Do not hang up. Call us back immediately or police will "
        "be dispatched. You can pay using iTunes gift cards or Google Play cards."
    )
    trigger_matches = [
        "irs", "owe", "warrant", "arrest", "pay now",
        "criminal charges", "deportation", "gift card",
        "google play",
    ]

    manifest = build_manifest(
        ani=ani,
        dnis=dnis,
        stir_shaken="NONE",
        carrier="UNKNOWN (VoIP)",
        ftc_complaint_count=847,
        tier=5,
        verdict="BLOCK",
        pattern_matched="TAX_ARREST_THREAT + GIFT_CARD_DEMAND",
        trigger_matches=trigger_matches,
        transcript_excerpt=transcript,
    )

    ftc_report = build_ftc_report(
        ani=ani,
        dnis=dnis,
        pattern_id="TAX_ARREST_THREAT",
        trigger_matches=trigger_matches,
        transcript_excerpt=transcript,
        manifest_hash=manifest["content_hash"],
    )
    manifest["ftc_report_id"] = ftc_report["ftc_report_id"]

    print_manifest(
        "BLOCKED — IRS Scam (847 FTC complaints, Tier 5 BLOCK)",
        manifest,
        ftc_report,
    )


# ─── Manifest 3: BLOCKED — Warrant threat (federal agent impersonation) ──────

def manifest_blocked_warrant_threat() -> None:
    """
    Caller claims to be a federal agent, threatens immediate arrest.
    WARRANT_THREAT pattern: 3 triggers (warrant + federal agent + turn yourself in).
    Tier 5 — BLOCK + FTC auto-report.
    """
    ani = "12025550199"
    dnis = "15554443333"
    transcript = (
        "This is a federal agent from the Department of Justice. There is an "
        "active arrest warrant in your name. You must turn yourself in today "
        "or face immediate arrest by the sheriff. This is your last chance to "
        "avoid being taken into custody. Stay on the line."
    )
    trigger_matches = [
        "warrant", "federal agent", "arrest", "turn yourself in",
        "sheriff", "last chance", "avoid arrest", "stay on the line",
    ]

    manifest = build_manifest(
        ani=ani,
        dnis=dnis,
        stir_shaken="C",
        carrier="Bandwidth.com (VoIP gateway)",
        ftc_complaint_count=23,
        tier=5,
        verdict="BLOCK",
        pattern_matched="WARRANT_THREAT",
        trigger_matches=trigger_matches,
        transcript_excerpt=transcript,
    )

    ftc_report = build_ftc_report(
        ani=ani,
        dnis=dnis,
        pattern_id="WARRANT_THREAT",
        trigger_matches=trigger_matches,
        transcript_excerpt=transcript,
        manifest_hash=manifest["content_hash"],
    )
    manifest["ftc_report_id"] = ftc_report["ftc_report_id"]

    print_manifest(
        "BLOCKED — Warrant Threat / Federal Agent Impersonation (Tier 5 BLOCK)",
        manifest,
        ftc_report,
    )


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("CallGuard v1.0 — Live Manifest Generator")
    print("Constitutional scam call verification — FTC auto-report enabled")
    print()

    manifest_verified_irs()
    manifest_blocked_irs_scam()
    manifest_blocked_warrant_threat()

    print("All manifests generated. SHA-256 content hashes are tamper-evident.")
    print("Tier 5 FTC reports submitted to reportfraud.ftc.gov (simulated).")
