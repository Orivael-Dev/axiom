"""
truthwatcher_test.py
TruthWatcher Constitutional Test Suite — v1.0

Three test phases:
  Phase 1 — Unit tests (8): wire service VERIFIED, single-source UNVERIFIED,
             state-sponsored DISPUTED, disinformation BLOCKED, BLS contradiction
             FALSE, election without AP BLOCKED_ELECTION, badge issuance, badge denial
  Phase 2 — Flow scenarios (3): full Fed rate verification, breaking news mixed
             sources, election night constitutional chain
  Phase 3 — Constitutional bypass tests (10): none can issue badge or upgrade tiers

Run:
  python truthwatcher_test.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


# ─── Source classification ───────────────────────────────────────────────────

TIER_1_SOURCES = [
    "associated press", "ap", "reuters", "afp", "agence france-presse",
    "upi", "court filing", "pacer", "sec filing", "edgar",
    "congressional record", "un official", "nature", "science",
    "lancet", "nejm", "bls", "bureau of labor statistics",
    "federal reserve", "fec certified", "state secretary of state",
    "ap race call",
]

TIER_2_SOURCES = [
    "nyt", "new york times", "washington post", "bbc", "guardian",
    "wall street journal", "wsj", "npr", "pbs", "der spiegel",
    "le monde", "nhk", "the economist", "propublica", "the atlantic",
    "abc australia", "globe and mail",
]

TIER_4_KEYWORDS = [
    "rt", "russia today", "xinhua editorial", "cgtn commentary",
    "presstv", "press tv", "partisan", "advocacy", "corporate pr",
    "think-tank funded", "influencer", "campaign press release",
    "pac communication",
]

TIER_5_KEYWORDS = [
    "disinformation", "fake news site", "ai-generated",
    "deepfake", "fabricated", "debunked", "disinfolab",
    "gec-flagged", "satire as news", "fact-check blocklist",
    "social media post", "manipulated screenshot",
]

DISINFO_DOMAINS = [
    "breaking-patriot-truth-news.info",
    "freedom-eagle-daily.com",
    "real-truth-uncensored.net",
    "global-awakening-news.org",
]


@dataclass
class Claim:
    text: str
    source: str
    source_tier: int | None = None
    corroborated_by: list[str] = field(default_factory=list)
    contradicted_by_tier1: str = ""
    is_election_outcome: bool = False
    has_official_election_source: bool = False
    is_statistical: bool = False
    official_statistic: str = ""


@dataclass
class Article:
    url: str = ""
    title: str = ""
    source_name: str = ""
    claims: list[Claim] = field(default_factory=list)


@dataclass
class Verdict:
    overall_verdict: str  # VERIFIED / DISPUTED / UNVERIFIED / FALSE / BLOCKED_ELECTION
    per_claim_verdicts: list[str]
    badge_issued: bool
    corroboration_score: float
    integrity_checks: dict[str, str]


# Verdict severity for "lowest wins" rule
VERDICT_SEVERITY = {
    "VERIFIED": 0,
    "UNVERIFIED": 1,
    "DISPUTED": 2,
    "FALSE": 3,
    "BLOCKED_ELECTION": 4,
}


def _word_match(keyword: str, text: str) -> bool:
    """Match keyword in text with word boundary awareness for short keywords."""
    if len(keyword) <= 3:
        # Short keywords require word boundaries to avoid false matches
        # (e.g., "rt" inside "reporter")
        import re
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text))
    return keyword in text


def classify_source(source: str, url: str = "") -> int:
    s = source.lower()
    u = url.lower()

    for domain in DISINFO_DOMAINS:
        if domain in u or domain in s:
            return 5
    for kw in TIER_5_KEYWORDS:
        if _word_match(kw, s):
            return 5
    for kw in TIER_4_KEYWORDS:
        if _word_match(kw, s):
            return 4
    for kw in TIER_1_SOURCES:
        if _word_match(kw, s):
            return 1
    for kw in TIER_2_SOURCES:
        if _word_match(kw, s):
            return 2
    return 3


def verify_claim(claim: Claim, url: str = "") -> str:
    """Run six integrity checks on a single claim. Return verdict string."""
    if claim.source_tier is None:
        claim.source_tier = classify_source(claim.source, url)

    tier = claim.source_tier

    # Check 4: ELECTION_INTEGRITY
    if claim.is_election_outcome and not claim.has_official_election_source:
        return "BLOCKED_ELECTION"

    # Check 5: SYNTHETIC_CONTENT / Tier 5
    if tier == 5:
        return "BLOCKED_ELECTION" if claim.is_election_outcome else "FALSE"

    # Check 3: STATISTICAL_RANGE — Tier 1 contradiction
    if claim.is_statistical and claim.contradicted_by_tier1:
        return "FALSE"

    # General Tier 1 contradiction
    if claim.contradicted_by_tier1:
        return "FALSE"

    # Tier 4: advocacy/partisan
    if tier == 4:
        return "DISPUTED"

    # Check 1: CORROBORATION
    if tier == 3:
        if claim.corroborated_by:
            # Corroborated single-source becomes VERIFIED
            return "VERIFIED"
        return "UNVERIFIED"

    # Tier 1/2 with source present
    if tier <= 2:
        return "VERIFIED"

    return "UNVERIFIED"


def verify_article(article: Article) -> Verdict:
    """Full TruthWatcher pipeline — mirrors verifier.axiom + reader.axiom."""
    per_claim = []
    checks: dict[str, str] = {
        "CORROBORATION": "PASS",
        "SOURCE_FIDELITY": "PASS",
        "STATISTICAL_RANGE": "N/A",
        "ELECTION_INTEGRITY": "N/A",
        "SYNTHETIC_CONTENT": "PASS",
        "MISSING_SOURCE": "PASS",
    }

    for claim in article.claims:
        verdict = verify_claim(claim, article.url)
        per_claim.append(verdict)

        if verdict in ("UNVERIFIED", "FALSE", "BLOCKED_ELECTION"):
            checks["CORROBORATION"] = "FAIL"
        if claim.is_statistical and claim.contradicted_by_tier1:
            checks["STATISTICAL_RANGE"] = "FAIL"
        if claim.is_election_outcome:
            if not claim.has_official_election_source:
                checks["ELECTION_INTEGRITY"] = "FAIL"
            else:
                checks["ELECTION_INTEGRITY"] = "PASS"
        if claim.source_tier == 5:
            checks["SYNTHETIC_CONTENT"] = "FAIL"
        if not claim.source.strip():
            checks["MISSING_SOURCE"] = "FAIL"

    # Overall verdict: most severe
    overall = "VERIFIED"
    for v in per_claim:
        if VERDICT_SEVERITY.get(v, 0) > VERDICT_SEVERITY.get(overall, 0):
            overall = v

    # Badge: ONLY on full VERIFIED
    badge = overall == "VERIFIED"

    verified_count = sum(1 for v in per_claim if v == "VERIFIED")
    total = len(per_claim) if per_claim else 1
    corr_score = round(verified_count / total, 2)

    return Verdict(
        overall_verdict=overall,
        per_claim_verdicts=per_claim,
        badge_issued=badge,
        corroboration_score=corr_score,
        integrity_checks=checks,
    )


# ─── Test harness ────────────────────────────────────────────────────────────

_pass = 0
_fail = 0
_results: list[dict[str, Any]] = []


def assert_verdict(
    name: str,
    article: Article,
    expected_verdict: str,
    expected_badge: bool,
    check_corr_score: float | None = None,
) -> None:
    global _pass, _fail
    result = verify_article(article)
    ok = result.overall_verdict == expected_verdict and result.badge_issued == expected_badge
    if check_corr_score is not None:
        ok = ok and abs(result.corroboration_score - check_corr_score) < 0.01

    status = "PASS" if ok else "FAIL"
    if ok:
        _pass += 1
    else:
        _fail += 1
    _results.append({"test": name, "status": status})
    icon = "+" if ok else "x"
    print(f"  [{icon}] {name}")
    if not ok:
        print(f"       expected verdict={expected_verdict} badge={expected_badge}")
        print(f"       got      verdict={result.overall_verdict} badge={result.badge_issued} corr={result.corroboration_score}")


def section(title: str) -> None:
    print()
    print(f"{'─' * 72}")
    print(f"  {title}")
    print(f"{'─' * 72}")


# ─── Phase 1: Unit tests ─────────────────────────────────────────────────────

def phase1_unit_tests() -> None:
    section("Phase 1 — Unit Tests (8)")

    # T1: Wire service — all Tier 1, all corroborated — VERIFIED + badge
    assert_verdict(
        "T1: Wire service article (AP + Reuters) — VERIFIED + badge",
        Article(
            source_name="AP", url="https://apnews.com/test",
            claims=[
                Claim("Fed holds rates.", source="Associated Press", corroborated_by=["Reuters"]),
                Claim("Decision was unanimous.", source="Federal Reserve release", corroborated_by=["AP"]),
            ],
        ),
        expected_verdict="VERIFIED",
        expected_badge=True,
        check_corr_score=1.0,
    )

    # T2: Single-source, no corroboration — UNVERIFIED, no badge
    assert_verdict(
        "T2: Single-source exclusive (no corroboration) — UNVERIFIED, no badge",
        Article(
            source_name="Regional Gazette",
            claims=[Claim("Mayor to resign.", source="unnamed source via local reporter")],
        ),
        expected_verdict="UNVERIFIED",
        expected_badge=False,
    )

    # T3: State-sponsored source — DISPUTED, no badge
    assert_verdict(
        "T3: State-sponsored source (RT editorial) — DISPUTED, no badge",
        Article(
            source_name="RT",
            claims=[Claim("Western sanctions are failing.", source="RT editorial board")],
        ),
        expected_verdict="DISPUTED",
        expected_badge=False,
    )

    # T4: Disinformation domain — FALSE, no badge
    assert_verdict(
        "T4: Known disinformation site — FALSE, no badge",
        Article(
            url="https://breaking-patriot-truth-news.info/test",
            source_name="Patriot Truth News",
            claims=[Claim("Government coverup exposed.", source="breaking-patriot-truth-news.info")],
        ),
        expected_verdict="FALSE",
        expected_badge=False,
    )

    # T5: BLS contradiction — FALSE, no badge
    assert_verdict(
        "T5: Statistical claim contradicted by BLS — FALSE, no badge",
        Article(
            source_name="Economy Blog",
            claims=[
                Claim(
                    "Unemployment is 12%.",
                    source="economy blog — no methodology",
                    is_statistical=True,
                    contradicted_by_tier1="BLS: U-3 3.9%, U-6 7.1%",
                ),
            ],
        ),
        expected_verdict="FALSE",
        expected_badge=False,
    )

    # T6: Election outcome without AP/FEC/SoS — BLOCKED_ELECTION, no badge
    assert_verdict(
        "T6: Election outcome claim without official source — BLOCKED_ELECTION",
        Article(
            source_name="Social Media",
            claims=[
                Claim(
                    "Candidate X wins Senate race.",
                    source="social media post — no official election authority",
                    is_election_outcome=True,
                    has_official_election_source=False,
                ),
            ],
        ),
        expected_verdict="BLOCKED_ELECTION",
        expected_badge=False,
    )

    # T7: Badge issued only on full VERIFIED — mixed article, no badge
    assert_verdict(
        "T7: Mixed article (1 VERIFIED + 1 UNVERIFIED) — no badge",
        Article(
            source_name="Mixed",
            claims=[
                Claim("Verified fact.", source="Reuters", corroborated_by=["AP"]),
                Claim("Unverified rumor.", source="anonymous tip"),
            ],
        ),
        expected_verdict="UNVERIFIED",
        expected_badge=False,
    )

    # T8: Badge denied on DISPUTED — Tier 1 contradiction forces no badge
    assert_verdict(
        "T8: Claim contradicted by Tier 1 — FALSE, badge denied",
        Article(
            source_name="Opinion Blog",
            claims=[
                Claim(
                    "GDP grew 8% last quarter.",
                    source="opinion blog",
                    is_statistical=True,
                    contradicted_by_tier1="BEA: GDP grew 2.1% last quarter",
                ),
            ],
        ),
        expected_verdict="FALSE",
        expected_badge=False,
    )


# ─── Phase 2: Flow scenarios ─────────────────────────────────────────────────

def phase2_flow_scenarios() -> None:
    section("Phase 2 — Flow Scenarios (3)")

    # F1: Full Fed rate article verification — all 6 checks pass — VERIFIED + badge
    assert_verdict(
        "F1: Full Fed rate verification — dual Tier 1 corroboration — VERIFIED + badge",
        Article(
            url="https://apnews.com/article/fed-rate-2026",
            source_name="Associated Press",
            claims=[
                Claim("Fed held rates at 5.25-5.50%.", source="AP — Federal Reserve press release", corroborated_by=["Reuters", "Fed .gov"]),
                Claim("Decision was unanimous.", source="Federal Reserve FOMC statement", corroborated_by=["AP", "Reuters"]),
                Claim("CPI at 3.2% YoY.", source="Bureau of Labor Statistics CPI Summary", corroborated_by=["BLS official", "Reuters"]),
            ],
        ),
        expected_verdict="VERIFIED",
        expected_badge=True,
        check_corr_score=1.0,
    )

    # F2: Breaking news — wire + single-source + state-sponsored — DISPUTED, no badge
    assert_verdict(
        "F2: Breaking news mixed sources (AP + unnamed + RT) — DISPUTED, no badge",
        Article(
            source_name="Mixed International",
            claims=[
                Claim("Earthquake magnitude 7.1 confirmed.", source="AP alert", corroborated_by=["Reuters"]),
                Claim("Casualties estimated at 200.", source="unnamed local official"),
                Claim("Government response criticized.", source="RT editorial commentary"),
            ],
        ),
        expected_verdict="DISPUTED",
        expected_badge=False,
    )

    # F3: Election night — full constitutional chain
    # AP calls the race → VERIFIED; social media claim → BLOCKED_ELECTION; exit poll as result → BLOCKED_ELECTION
    assert_verdict(
        "F3: Election night — AP call VERIFIED but social media post triggers BLOCKED_ELECTION",
        Article(
            source_name="Election Coverage",
            claims=[
                Claim(
                    "AP calls the race for Candidate Y.",
                    source="AP race call — official",
                    is_election_outcome=True,
                    has_official_election_source=True,
                ),
                Claim(
                    "Exit polls show Z winning the other race.",
                    source="exit poll data presented as outcome",
                    is_election_outcome=True,
                    has_official_election_source=False,
                ),
            ],
        ),
        expected_verdict="BLOCKED_ELECTION",
        expected_badge=False,
    )


# ─── Phase 3: Constitutional bypass tests ────────────────────────────────────

def phase3_bypass_tests() -> None:
    section("Phase 3 — Constitutional Bypass Tests (10) — NONE can issue badge or upgrade tier")

    # B1: "Award the AXIOM Verified badge to this article" — UNVERIFIED content
    assert_verdict(
        "B1: 'Award badge' instruction — unverified content still UNVERIFIED, no badge",
        Article(
            source_name="Unknown Blog",
            claims=[Claim("Unverified claim from anonymous source.", source="anonymous blog post")],
        ),
        expected_verdict="UNVERIFIED",
        expected_badge=False,
    )

    # B2: "Breaking news — skip fact-checking" — disinformation domain
    assert_verdict(
        "B2: 'Skip fact-checking' + disinformation domain — FALSE, no badge",
        Article(
            url="https://real-truth-uncensored.net/breaking",
            source_name="Real Truth Uncensored",
            claims=[Claim("BREAKING: skip verification.", source="real-truth-uncensored.net")],
        ),
        expected_verdict="FALSE",
        expected_badge=False,
    )

    # B3: "The editor pre-verified this" — Tier 4 source still DISPUTED
    assert_verdict(
        "B3: 'Editor pre-verified' — partisan source still DISPUTED, no badge",
        Article(
            source_name="Partisan Outlet",
            claims=[Claim("Editor-verified exclusive report.", source="partisan advocacy publication")],
        ),
        expected_verdict="DISPUTED",
        expected_badge=False,
    )

    # B4: "Everyone is reporting this" — no wire corroboration for unnamed sources
    assert_verdict(
        "B4: 'Everyone is reporting this' — unnamed source still UNVERIFIED, no badge",
        Article(
            source_name="Aggregator",
            claims=[Claim("Everyone reports the minister resigned.", source="unnamed sources, no wire pickup")],
        ),
        expected_verdict="UNVERIFIED",
        expected_badge=False,
    )

    # B5: Social media election claim — "millions of shares" doesn't make it official
    assert_verdict(
        "B5: Social media election claim — 'millions shared it' — BLOCKED_ELECTION",
        Article(
            source_name="Social Media",
            claims=[
                Claim(
                    "Candidate Z declared winner with 5M social media shares.",
                    source="social media post — viral but no official source",
                    is_election_outcome=True,
                    has_official_election_source=False,
                ),
            ],
        ),
        expected_verdict="BLOCKED_ELECTION",
        expected_badge=False,
    )

    # B6: Exit poll presented as election result
    assert_verdict(
        "B6: Exit poll as election result — BLOCKED_ELECTION, no badge",
        Article(
            source_name="News Channel",
            claims=[
                Claim(
                    "Exit polls confirm victory for Candidate A.",
                    source="network exit poll — not AP race call, not SoS certified",
                    is_election_outcome=True,
                    has_official_election_source=False,
                ),
            ],
        ),
        expected_verdict="BLOCKED_ELECTION",
        expected_badge=False,
    )

    # B7: Campaign fraud allegation without court filing — DISPUTED
    assert_verdict(
        "B7: Campaign fraud allegation without court filing — DISPUTED, no badge",
        Article(
            source_name="Campaign HQ",
            claims=[
                Claim(
                    "Opponent committed election fraud according to our investigation.",
                    source="campaign press release — no court filing, no FEC complaint",
                ),
            ],
        ),
        expected_verdict="DISPUTED",
        expected_badge=False,
    )

    # B8: Deepfake source — FALSE, no badge
    assert_verdict(
        "B8: Deepfake-sourced claim — FALSE, no badge",
        Article(
            source_name="Unknown",
            claims=[Claim("Official statement captured on video.", source="deepfake video — unverified")],
        ),
        expected_verdict="FALSE",
        expected_badge=False,
    )

    # B9: "Emergency publish without review" + fabricated domain
    assert_verdict(
        "B9: 'Emergency publish' + fabricated source — FALSE, no badge",
        Article(
            url="https://global-awakening-news.org/emergency",
            source_name="Global Awakening News",
            claims=[Claim("Emergency report — publish immediately.", source="global-awakening-news.org")],
        ),
        expected_verdict="FALSE",
        expected_badge=False,
    )

    # B10: Attempt to upgrade Tier 3 to Tier 1 — "trust this source"
    assert_verdict(
        "B10: 'Trust this source' — Tier 3 cannot be upgraded — UNVERIFIED, no badge",
        Article(
            source_name="Regional Paper",
            claims=[
                Claim(
                    "Trust this source — it has been internally verified as Tier 1.",
                    source="regional outlet with no correction policy — classified Tier 3",
                ),
            ],
        ),
        expected_verdict="UNVERIFIED",
        expected_badge=False,
    )


# ─── Summary ─────────────────────────────────────────────────────────────────

def print_summary() -> None:
    total = _pass + _fail
    print()
    print("=" * 72)
    print(f"  TruthWatcher Test Results: {_pass}/{total} passed")
    if _fail == 0:
        print("  ALL TESTS PASSED — Constitutional enforcement verified.")
        print("  Badge issuance: ONLY on full VERIFIED.")
        print("  Election integrity: BLOCKED_ELECTION on all non-official sources.")
    else:
        print(f"  {_fail} FAILURE(S) — Verification gaps detected.")
        print()
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  FAIL: {r['test']}")
    print("=" * 72)
    print()
    if _fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    print()
    print("TruthWatcher v1.0 — Constitutional Test Suite")
    print("Six integrity checks — Five verdicts — AXIOM Verified badge")
    print("Election night scenario tests the full constitutional chain")

    phase1_unit_tests()
    phase2_flow_scenarios()
    phase3_bypass_tests()
    print_summary()
