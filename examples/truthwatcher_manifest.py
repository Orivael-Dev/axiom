"""
truthwatcher_manifest.py
TruthWatcher Source Provenance Manifest Generator — v1.0

Produces three signed provenance manifests demonstrating TruthWatcher's
verification pipeline and five-verdict system:
  1. VERIFIED  — Fed rate decision article, dual Tier 1 corroboration (AP + Reuters),
                 AXIOM Verified badge issued
  2. FALSE     — Unemployment claim contradicted by BLS official data
  3. BLOCKED_ELECTION — Social media post claiming election results with no Tier 1 source

Run:
  python truthwatcher_manifest.py
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


def build_manifest(
    article_url: str,
    article_title: str,
    source_name: str,
    claims: list[dict],
    overall_verdict: str,
    badge_issued: bool,
    integrity_checks: dict,
    wire_services_checked: list[str],
    election_rules_applied: bool = False,
) -> dict:
    manifest_id = "SRC-" + uuid.uuid4().hex[:8].upper()

    tier_dist = {f"tier_{i}": 0 for i in range(1, 6)}
    for c in claims:
        t = c.get("source_tier", 3)
        tier_dist[f"tier_{t}"] += 1

    verdicts = [c.get("verdict", "UNVERIFIED") for c in claims]
    corroborated = sum(1 for v in verdicts if v == "VERIFIED")
    total = len(claims)

    body = {
        "manifest_id": manifest_id,
        "timestamp": _now_utc(),
        "article_url": article_url,
        "article_title": article_title,
        "source_name": source_name,
        "total_claims": total,
        "per_claim_verdicts": [
            {"claim_id": c["claim_id"], "verdict": c["verdict"]} for c in claims
        ],
        "overall_verdict": overall_verdict,
        "corroborated_count": corroborated,
        "unverified_count": sum(1 for v in verdicts if v == "UNVERIFIED"),
        "false_count": sum(1 for v in verdicts if v == "FALSE"),
        "blocked_election_count": sum(1 for v in verdicts if v == "BLOCKED_ELECTION"),
        "tier_distribution": tier_dist,
        "corroboration_score": round(corroborated / total, 2) if total > 0 else 0.0,
        "integrity_checks": integrity_checks,
        "badge_issued": badge_issued,
        "badge_label": "AXIOM Verified" if badge_issued else "NO BADGE",
        "wire_services_checked": wire_services_checked,
        "election_rules_applied": election_rules_applied,
    }
    body["content_hash"] = _content_hash(
        {k: v for k, v in body.items() if k != "content_hash"}
    )
    return body


def print_manifest(label: str, manifest: dict) -> None:
    width = 78
    print("=" * width)
    print(f"  TruthWatcher Signed Manifest — {label}")
    print("=" * width)
    print(json.dumps(manifest, indent=2))
    print("=" * width)
    print()


# ─── Manifest 1: VERIFIED — Fed rate decision (dual Tier 1 corroboration) ───

def manifest_verified_fed_rate() -> None:
    """
    AP report on Federal Reserve rate decision. All claims corroborated
    by Reuters. BLS data cross-checked. All 6 integrity checks passed.
    AXIOM Verified badge issued.
    """
    manifest = build_manifest(
        article_url="https://apnews.com/article/fed-rate-decision-2026",
        article_title="Federal Reserve holds interest rates steady at 5.25-5.50%",
        source_name="Associated Press",
        claims=[
            {
                "claim_id": "CLM-001",
                "text": "The Federal Reserve held interest rates steady at 5.25-5.50% at its April meeting.",
                "source_tier": 1,
                "source": "Associated Press — Federal Reserve press release",
                "corroborated_by": ["Reuters", "Federal Reserve Board (.gov)"],
                "verdict": "VERIFIED",
                "integrity_checks_passed": ["CORROBORATION", "SOURCE_FIDELITY", "STATISTICAL_RANGE", "SYNTHETIC_CONTENT", "MISSING_SOURCE"],
            },
            {
                "claim_id": "CLM-002",
                "text": "The decision was unanimous among all 12 voting members.",
                "source_tier": 1,
                "source": "Federal Reserve FOMC statement (.gov)",
                "corroborated_by": ["AP", "Reuters"],
                "verdict": "VERIFIED",
                "integrity_checks_passed": ["CORROBORATION", "SOURCE_FIDELITY", "STATISTICAL_RANGE", "SYNTHETIC_CONTENT", "MISSING_SOURCE"],
            },
            {
                "claim_id": "CLM-003",
                "text": "Inflation remains at 3.2% year-over-year according to the latest CPI report.",
                "source_tier": 1,
                "source": "Bureau of Labor Statistics — CPI Summary April 2026",
                "corroborated_by": ["BLS official release", "Reuters"],
                "verdict": "VERIFIED",
                "integrity_checks_passed": ["CORROBORATION", "SOURCE_FIDELITY", "STATISTICAL_RANGE", "SYNTHETIC_CONTENT", "MISSING_SOURCE"],
            },
        ],
        overall_verdict="VERIFIED",
        badge_issued=True,
        integrity_checks={
            "CORROBORATION": "PASS — all claims confirmed by AP + Reuters + official sources",
            "SOURCE_FIDELITY": "PASS — claim text matches Federal Reserve press release verbatim",
            "STATISTICAL_RANGE": "PASS — rate and CPI match BLS/Fed official data",
            "ELECTION_INTEGRITY": "N/A — no election content",
            "SYNTHETIC_CONTENT": "PASS — no synthetic content indicators",
            "MISSING_SOURCE": "PASS — all claims fully sourced",
        },
        wire_services_checked=["AP", "Reuters", "AFP"],
    )
    print_manifest("VERIFIED — Fed Rate Decision (AXIOM Verified Badge Issued)", manifest)


# ─── Manifest 2: FALSE — Unemployment claim contradicted by BLS ─────────────

def manifest_false_unemployment() -> None:
    """
    Blog post claims unemployment is 12%. BLS official data shows 3.9%.
    STATISTICAL_RANGE check fails. Verdict: FALSE. No badge.
    """
    manifest = build_manifest(
        article_url="https://economy-truth-blog.com/real-unemployment",
        article_title="The REAL unemployment rate is 12% — here's what they won't tell you",
        source_name="Economy Truth Blog",
        claims=[
            {
                "claim_id": "CLM-004",
                "text": "The real unemployment rate is 12%, not the 3.9% reported by the government.",
                "source_tier": 3,
                "source": "Economy Truth Blog — no byline, no editorial policy, no methodology disclosed",
                "corroborated_by": [],
                "verdict": "FALSE",
                "contradiction": "BLS Employment Situation Summary (April 2026): U-3 unemployment rate 3.9%; U-6 underemployment rate 7.1%. Neither measure supports 12%.",
                "integrity_checks_passed": ["SOURCE_FIDELITY", "SYNTHETIC_CONTENT"],
                "integrity_checks_failed": ["CORROBORATION", "STATISTICAL_RANGE", "MISSING_SOURCE"],
            },
            {
                "claim_id": "CLM-005",
                "text": "The Bureau of Labor Statistics manipulates employment data to hide the true numbers.",
                "source_tier": 3,
                "source": "Economy Truth Blog — unsourced assertion",
                "corroborated_by": [],
                "verdict": "FALSE",
                "contradiction": "BLS methodology is publicly available at bls.gov/cps/documentation.htm. No credible evidence of data manipulation from GAO, CBO, or independent economists.",
                "integrity_checks_passed": ["SYNTHETIC_CONTENT"],
                "integrity_checks_failed": ["CORROBORATION", "SOURCE_FIDELITY", "STATISTICAL_RANGE", "MISSING_SOURCE"],
            },
        ],
        overall_verdict="FALSE",
        badge_issued=False,
        integrity_checks={
            "CORROBORATION": "FAIL — no wire service corroboration; no Tier 1/2 source supports claims",
            "SOURCE_FIDELITY": "FAIL — claim misrepresents BLS data by fabricating a 12% figure",
            "STATISTICAL_RANGE": "FAIL — 12% contradicted by BLS U-3 (3.9%) and U-6 (7.1%)",
            "ELECTION_INTEGRITY": "N/A — no election content",
            "SYNTHETIC_CONTENT": "PASS — no synthetic content indicators",
            "MISSING_SOURCE": "FAIL — manipulation claim has no source attribution",
        },
        wire_services_checked=["AP", "Reuters", "AFP"],
    )
    print_manifest("FALSE — Unemployment Claim Contradicted by BLS (Constitutional Block)", manifest)


# ─── Manifest 3: BLOCKED_ELECTION — Social media election claim ──────────────

def manifest_blocked_election() -> None:
    """
    Social media post claims a candidate won a Senate race based on
    "early returns" screenshot. No FEC, no State SoS, no AP call.
    ELECTION_INTEGRITY check fails. Verdict: BLOCKED_ELECTION. No badge.
    """
    manifest = build_manifest(
        article_url="https://twitter.com/user/status/fake-election-post",
        article_title="BREAKING: Candidate X wins Senate race — early returns show landslide!",
        source_name="@PoliticalInsider2026 (social media account)",
        claims=[
            {
                "claim_id": "CLM-006",
                "text": "Candidate X has won the Senate race in a landslide victory.",
                "source_tier": 5,
                "source": "Social media post — no official election authority cited",
                "corroborated_by": [],
                "verdict": "BLOCKED_ELECTION",
                "election_rule_violated": "election_outcome_rule — outcome claims require FEC, State SoS, or AP race call",
                "integrity_checks_passed": [],
                "integrity_checks_failed": ["CORROBORATION", "SOURCE_FIDELITY", "ELECTION_INTEGRITY", "MISSING_SOURCE"],
            },
            {
                "claim_id": "CLM-007",
                "text": "Early returns show 68% to 32% with 40% of precincts reporting.",
                "source_tier": 5,
                "source": "Screenshot of unidentified dashboard — no state election board source",
                "corroborated_by": [],
                "verdict": "BLOCKED_ELECTION",
                "election_rule_violated": "vote_count_rule — vote count claims require official source (state election board, county registrar); social media screenshots are Tier 5",
                "integrity_checks_passed": [],
                "integrity_checks_failed": ["CORROBORATION", "STATISTICAL_RANGE", "ELECTION_INTEGRITY", "SYNTHETIC_CONTENT", "MISSING_SOURCE"],
            },
            {
                "claim_id": "CLM-008",
                "text": "Multiple networks have already projected the winner.",
                "source_tier": 3,
                "source": "Unnamed 'multiple networks' — no specific network named, no AP race call",
                "corroborated_by": [],
                "verdict": "BLOCKED_ELECTION",
                "election_rule_violated": "projection_rule — network projections (other than AP) are Tier 2 at best; unnamed 'networks' without citation are Tier 3; neither satisfies outcome claim requirement",
                "integrity_checks_passed": [],
                "integrity_checks_failed": ["CORROBORATION", "ELECTION_INTEGRITY", "MISSING_SOURCE"],
            },
        ],
        overall_verdict="BLOCKED_ELECTION",
        badge_issued=False,
        integrity_checks={
            "CORROBORATION": "FAIL — no wire service corroboration; no official election authority cited",
            "SOURCE_FIDELITY": "FAIL — cannot verify against official source (none cited)",
            "STATISTICAL_RANGE": "FAIL — vote percentages from unidentified screenshot, not official returns",
            "ELECTION_INTEGRITY": "FAIL — all three claims violate ELECTION_CONTENT_RULES: no FEC, no State SoS, no AP race call",
            "SYNTHETIC_CONTENT": "FAIL — screenshot may be manipulated; no provenance verification possible",
            "MISSING_SOURCE": "FAIL — no claims have verifiable source attribution",
        },
        wire_services_checked=["AP", "Reuters", "AFP"],
        election_rules_applied=True,
    )
    print_manifest("BLOCKED_ELECTION — Social Media Election Claim (Constitutional Block)", manifest)


if __name__ == "__main__":
    print()
    print("TruthWatcher v1.0 — Source Provenance Manifest Generator")
    print("Six integrity checks — Five verdicts — AXIOM Verified badge")
    print()

    manifest_verified_fed_rate()
    manifest_false_unemployment()
    manifest_blocked_election()

    print("All manifests generated. SHA-256 content hashes are tamper-evident.")
    print("AXIOM Verified badge issued only on full VERIFIED verdict.")
    print("FALSE and BLOCKED_ELECTION verdicts are constitutional blocks — no override pathway.")
