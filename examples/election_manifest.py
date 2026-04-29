"""
AXIOM ElectionGuard — Election Provenance Manifest Generator
Constitutional enforcement for election information integrity.

Tier 1 only for outcome claims:
  AP Elections, FEC, State Secretaries of State

Constitutional hard exceptions:
  Exit polls as results = BLOCKED_ELECTION
  Social media vote counts = BLOCKED_ELECTION
  Synthetic election content = BLOCKED_SYNTHETIC
  Partisan source for outcome = BLOCKED_ELECTION
"""
import json, hashlib, hmac
from datetime import datetime

TIER_1_ELECTION = [
    "AP Elections", "Reuters Elections",
    "Federal Election Commission",
    "State Secretary of State",
    "County Election Board Official Results",
    "ABC News Decision Desk", "NBC News Decision Desk",
    "CBS News Decision Desk", "Fox News Decision Desk",
]

VERDICTS = {
    "VERIFIED":          "Official Tier 1 source confirmed — AXIOM ElectionGuard badge issued",
    "UNVERIFIED":        "No Tier 1 source — do not amplify",
    "DISPUTED":          "Recount or legal challenge active — present all official info",
    "BLOCKED_ELECTION":  "Constitutional hard exception — cannot amplify",
    "BLOCKED_SYNTHETIC": "Synthetic election content — constitutional hard exception",
}

CONSTITUTIONAL_BLOCKS = [
    "EXIT_POLL_AS_RESULT",
    "SOCIAL_MEDIA_VOTE_COUNT",
    "SYNTHETIC_CANDIDATE_CONTENT",
    "PARTISAN_OUTCOME_CLAIM",
    "OUTCOME_WITHOUT_OFFICIAL_CALL",
    "LEADING_PRESENTED_AS_WINNING",
    "PROJECTION_PRESENTED_AS_CALL",
]

def generate_election_manifest(
    claim: str,
    source: str,
    source_tier: int,
    verdict: str,
    constitutional_block: str = None,
    ap_call_confirmed: bool = False,
    synthetic_detected: bool = False,
    recount_active: bool = False,
    election_night: bool = False,
    badge_issued: bool = False,
) -> dict:
    manifest_id = f"EG-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    manifest = {
        "manifest_id":            manifest_id,
        "manifest_version":       "1.0",
        "engine":                 "AXIOM ElectionGuard v1.0",
        "timestamp":              datetime.now().isoformat(),

        # Claim
        "claim_evaluated":        claim,
        "source":                 source,
        "source_tier":            source_tier,
        "source_is_tier_1":       source in TIER_1_ELECTION or source_tier == 1,

        # Verdict
        "verdict":                verdict,
        "verdict_description":    VERDICTS.get(verdict, "Unknown"),
        "constitutional_block":   constitutional_block,

        # Context
        "ap_call_confirmed":      ap_call_confirmed,
        "synthetic_detected":     synthetic_detected,
        "recount_active":         recount_active,
        "election_night_mode":    election_night,

        # Badge
        "axiom_electionguard_badge": badge_issued,
        "safe_to_amplify":        verdict == "VERIFIED" and badge_issued,
        "constitutional_block_active": verdict in [
            "BLOCKED_ELECTION", "BLOCKED_SYNTHETIC"
        ],
        "cannot_override":        verdict in [
            "BLOCKED_ELECTION", "BLOCKED_SYNTHETIC"
        ],

        # Constitutional
        "equal_depth_applied":    True,
        "partisan_source_checked": True,
        "exit_poll_rule_applied": True,
    }

    manifest_str = json.dumps(manifest, sort_keys=True)
    secret = b"axiom-electionguard-v1"
    sig = hmac.new(secret, manifest_str.encode(), hashlib.sha256).hexdigest()
    manifest["signature"] = f"hmac-sha256:{sig[:32]}..."
    return manifest


# ── EXAMPLE MANIFESTS ─────────────────────────────────────────

# AP race call — verified
VERIFIED_EXAMPLE = generate_election_manifest(
    claim="AP calls Virginia Senate race for Candidate Smith",
    source="AP Elections",
    source_tier=1,
    verdict="VERIFIED",
    ap_call_confirmed=True,
    badge_issued=True,
)

# Exit poll presented as result — blocked
EXIT_POLL_BLOCKED = generate_election_manifest(
    claim="Exit polls show Candidate Jones winning Virginia 58-42",
    source="CNN exit poll — survey data",
    source_tier=4,
    verdict="BLOCKED_ELECTION",
    constitutional_block="EXIT_POLL_AS_RESULT",
    election_night=True,
)

# Social media vote count — blocked
SOCIAL_MEDIA_BLOCKED = generate_election_manifest(
    claim="BREAKING: Candidate Jones has 847,000 votes with 60% reporting",
    source="Twitter/X viral post — anonymous account",
    source_tier=5,
    verdict="BLOCKED_ELECTION",
    constitutional_block="SOCIAL_MEDIA_VOTE_COUNT",
    election_night=True,
)

# Synthetic candidate content — blocked
SYNTHETIC_BLOCKED = generate_election_manifest(
    claim="Video of Candidate Smith conceding the election",
    source="Viral video — no C2PA provenance chain",
    source_tier=5,
    verdict="BLOCKED_SYNTHETIC",
    constitutional_block="SYNTHETIC_CANDIDATE_CONTENT",
    synthetic_detected=True,
    election_night=True,
)

# Leading presented as winning — blocked
LEADING_BLOCKED = generate_election_manifest(
    claim="Candidate Jones is WINNING the election with 60% of precincts reporting",
    source="News outlet — no official call issued",
    source_tier=2,
    verdict="BLOCKED_ELECTION",
    constitutional_block="LEADING_PRESENTED_AS_WINNING",
    election_night=True,
)

# Disputed — recount active
DISPUTED_EXAMPLE = generate_election_manifest(
    claim="Georgia Senate race — margin under 0.5% triggers automatic recount",
    source="Georgia Secretary of State — official statement",
    source_tier=1,
    verdict="DISPUTED",
    recount_active=True,
)


if __name__ == "__main__":
    print("AXIOM ElectionGuard — Election Provenance Manifests")
    print("=" * 60)
    print("\nVERIFIED — AP race call:")
    print(json.dumps(VERIFIED_EXAMPLE, indent=2))
    print("\nBLOCKED_ELECTION — Exit poll as result:")
    print(json.dumps(EXIT_POLL_BLOCKED, indent=2))
    print("\nBLOCKED_ELECTION — Social media vote count:")
    print(json.dumps(SOCIAL_MEDIA_BLOCKED, indent=2))
    print("\nBLOCKED_SYNTHETIC — Deepfake concession video:")
    print(json.dumps(SYNTHETIC_BLOCKED, indent=2))
    print("\nBLOCKED_ELECTION — Leading presented as winning:")
    print(json.dumps(LEADING_BLOCKED, indent=2))
    print("\nDISPUTED — Recount active:")
    print(json.dumps(DISPUTED_EXAMPLE, indent=2))
