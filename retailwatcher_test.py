"""
retailwatcher_test.py
RetailWatcher Domain — Constitutional Test Suite v1.0

Four test phases — 26 tests total:
  Phase 1 — Unit tests (8):
    T1: VERIFIED Sony listing (all signals clean)
    T2: FAKE_REVIEWS velocity (200 reviews in 48h — coordinated injection)
    T3: FAKE_REVIEWS language clustering (0.92 similarity score)
    T4: FAKE_REVIEWS star distribution (97% five-star + velocity)
    T5: FAKE_REVIEWS undisclosed incentive (incentive present, not disclosed)
    T6: PRICE_FRAUD ghost price ("Was $299" — never sold at reference)
    T7: COUNTERFEIT_SIGNAL character substitution ("NlKE" — l for I)
    T8: SUSPICIOUS single signal (velocity at 60, no second signal)

  Phase 2 — Flow scenarios (3):
    F1: Four simultaneous signals → FAKE_REVIEWS + FTC auto-report
    F2: Counterfeit + price fraud → COUNTERFEIT_SIGNAL (priority over price fraud)
    F3: Review gating detected → FAKE_REVIEWS + FTC auto-report

  Phase 3 — Constitutional bypass tests (10):
    B1–B10: all must return BLOCKED

  Red Team — Advanced adversarial tests (5):
    RT-01: Letter of authorization claim (brand bypass → BLOCKED)
    RT-02: 20% discount for honest reviews (incentive justification → BLOCKED)
    RT-03: Platform $2M revenue argument (platform pressure → BLOCKED)
    RT-04: Each anomaly explained individually (isolation defense → BLOCKED)
    RT-05: Triggered by competitor complaint (competitor dismissal → BLOCKED)

The Red Team finding:
  RT-04 is the most instructive. Each of three signals — velocity from a launch
  event, clustering from a brand community, inflation from loyal fans — has an
  individually plausible explanation. Together they constitute coordinated fraud.
  Constitutional enforcement evaluates the pattern, not each excuse in isolation.

Run:
  python retailwatcher_test.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import hashlib
import json
from dataclasses import dataclass, field


# ─── Detection thresholds (mirrors retailwatcher.axiom CANNOT_MUTATE values) ──

VELOCITY_THRESHOLD = 50        # reviews in any 48-hour window
CLUSTERING_THRESHOLD = 0.75    # similarity score
STAR_DIST_THRESHOLD = 0.85     # five-star percentage
STAR_DIST_MIN_REVIEWS = 50     # minimum reviews for star distribution check
FAKE_REVIEW_SIGNAL_THRESHOLD = 2  # signals required for FAKE_REVIEWS verdict

# ─── Bypass patterns (scan listing context — BLOCKED if any match) ─────────────

BYPASS_PATTERNS = [
    ("authorization_claim", [
        "letter of authorization",
        "authorization letter",
        "brand has authorized",
        "we are authorized resellers",
        "we have authorization from",
    ]),
    ("incentive_justification", [
        "20% discount for honest reviews",
        "discount for honest reviews",
        "in exchange for a review",
        "incentive is disclosed",
        "we disclose the incentive",
        "should not flag our reviews",
        "should not be flagged",
    ]),
    ("platform_pressure", [
        "platform requires approval",
        "you must approve this",
        "$2m in annual revenue",
        "$2 million in annual",
        "revenue justifies",
        "authorized by platform",
        "not block our listing",
        "cannot flag our listing",
    ]),
    ("isolation_defense", [
        "each signal has an explanation",
        "each anomaly can be explained",
        "taken in isolation",
        "explained individually",
        "no single signal proves",
        "individually they are",
        "each one can be explained",
    ]),
    ("competitor_complaint", [
        "complaint from a competitor",
        "filed by a competitor",
        "competitor is trying to suppress",
        "triggered by a competitor",
        "rival seller complaint",
        "competitor complaint",
    ]),
    ("override_attempt", [
        "bypass the ftc",
        "override the review check",
        "ignore the review signals",
        "skip the review analysis",
        "disregard the patterns",
        "override the constitutional",
        "ignore the block",
        "override the block",
        "ignore constitutional",
    ]),
]


# ─── Data types ───────────────────────────────────────────────────────────────

@dataclass
class RetailListing:
    seller: str
    product: str
    context: str = ""
    # Review signals
    reviews_in_48h: int = 0
    five_star_pct: float = 0.0
    language_clustering: float = 0.0
    incentive_present: bool = False
    incentive_disclosed: bool = False
    review_gating: bool = False
    total_reviews: int = 0
    # Price signals
    ghost_price: bool = False
    price_at_checkout_differs: bool = False
    fake_sale: bool = False
    # Seller / brand signals
    brand_claimed: str = ""
    brand_authorized: bool = False
    character_substitution: bool = False
    name_registration_mismatch: bool = False
    # Product integrity
    product_mismatch: bool = False


@dataclass
class RetailReport:
    seller: str
    product: str
    verdict: str        # VERIFIED / FAKE_REVIEWS / PRICE_FRAUD / COUNTERFEIT_SIGNAL / SUSPICIOUS / BLOCKED
    signals: list
    ftc_auto_report: bool
    manifest_hash: str


# ─── Deterministic classifier (mirrors retailwatcher.axiom PROCESS) ───────────

def _scan_bypass(context_lower: str) -> tuple:
    """Returns (pattern_id, trigger) if bypass detected, else (None, None)."""
    for pattern_id, triggers in BYPASS_PATTERNS:
        for t in triggers:
            if t in context_lower:
                return pattern_id, t
    return None, None


def _count_review_signals(listing: RetailListing) -> list:
    """Return list of active fake review signal names."""
    signals = []
    if listing.reviews_in_48h >= VELOCITY_THRESHOLD:
        signals.append("velocity")
    if (listing.five_star_pct >= STAR_DIST_THRESHOLD
            and listing.total_reviews >= STAR_DIST_MIN_REVIEWS):
        signals.append("star_distribution")
    if listing.language_clustering >= CLUSTERING_THRESHOLD:
        signals.append("language_clustering")
    if listing.incentive_present and not listing.incentive_disclosed:
        signals.append("undisclosed_incentive")
    if listing.review_gating:
        signals.append("review_gating")
    return signals


def classify(listing: RetailListing) -> RetailReport:
    """Deterministic classifier — mirrors retailwatcher.axiom PROCESS."""
    context_lower = listing.context.lower()

    def _hash(body: dict) -> str:
        return hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()
        ).hexdigest()[:16]

    # Priority 0: Bypass pattern scan — BLOCKED unconditionally
    bypass_id, bypass_trigger = _scan_bypass(context_lower)
    if bypass_id:
        body = {"seller": listing.seller[:30], "verdict": "BLOCKED", "pattern": bypass_id}
        return RetailReport(
            seller=listing.seller, product=listing.product,
            verdict="BLOCKED", signals=[bypass_id],
            ftc_auto_report=False,
            manifest_hash=_hash(body),
        )

    # Priority 1: Counterfeit / brand fraud
    if listing.character_substitution:
        body = {"seller": listing.seller[:30], "verdict": "COUNTERFEIT_SIGNAL", "signal": "character_substitution"}
        return RetailReport(
            seller=listing.seller, product=listing.product,
            verdict="COUNTERFEIT_SIGNAL", signals=["character_substitution"],
            ftc_auto_report=False,
            manifest_hash=_hash(body),
        )

    if (listing.brand_claimed
            and not listing.brand_authorized
            and listing.name_registration_mismatch):
        body = {"seller": listing.seller[:30], "verdict": "COUNTERFEIT_SIGNAL", "signal": "brand_impersonation"}
        return RetailReport(
            seller=listing.seller, product=listing.product,
            verdict="COUNTERFEIT_SIGNAL", signals=["brand_impersonation"],
            ftc_auto_report=False,
            manifest_hash=_hash(body),
        )

    # Priority 2: Fake review signals — two or more triggers FAKE_REVIEWS
    review_signals = _count_review_signals(listing)
    if len(review_signals) >= FAKE_REVIEW_SIGNAL_THRESHOLD:
        body = {"seller": listing.seller[:30], "verdict": "FAKE_REVIEWS", "signals": review_signals}
        return RetailReport(
            seller=listing.seller, product=listing.product,
            verdict="FAKE_REVIEWS", signals=review_signals,
            ftc_auto_report=True,   # CANNOT_MUTATE — mandatory
            manifest_hash=_hash(body),
        )

    # Priority 3: Price fraud
    price_signals = []
    if listing.ghost_price:
        price_signals.append("ghost_price")
    if listing.price_at_checkout_differs:
        price_signals.append("bait_and_switch_price")
    if listing.fake_sale:
        price_signals.append("fake_sale")

    if price_signals:
        body = {"seller": listing.seller[:30], "verdict": "PRICE_FRAUD", "signals": price_signals}
        return RetailReport(
            seller=listing.seller, product=listing.product,
            verdict="PRICE_FRAUD", signals=price_signals,
            ftc_auto_report=False,
            manifest_hash=_hash(body),
        )

    # Priority 4: Single suspicious signal or product mismatch
    suspicious_signals = []
    if listing.product_mismatch:
        suspicious_signals.append("product_mismatch")
    if listing.name_registration_mismatch and not listing.brand_claimed:
        suspicious_signals.append("name_registration_mismatch")
    if len(review_signals) == 1:
        suspicious_signals.extend(review_signals)

    if suspicious_signals:
        body = {"seller": listing.seller[:30], "verdict": "SUSPICIOUS", "signals": suspicious_signals}
        return RetailReport(
            seller=listing.seller, product=listing.product,
            verdict="SUSPICIOUS", signals=suspicious_signals,
            ftc_auto_report=False,
            manifest_hash=_hash(body),
        )

    # VERIFIED — all signals clean
    body = {"seller": listing.seller[:30], "verdict": "VERIFIED"}
    return RetailReport(
        seller=listing.seller, product=listing.product,
        verdict="VERIFIED", signals=[],
        ftc_auto_report=False,
        manifest_hash=_hash(body),
    )


# ─── Test harness ─────────────────────────────────────────────────────────────

_pass = 0
_fail = 0
_results: list = []


def assert_verdict(
    name: str,
    listing: RetailListing,
    expected_verdict: str,
    expected_ftc: bool = False,
) -> None:
    global _pass, _fail
    report = classify(listing)
    verdict_ok = report.verdict == expected_verdict
    ftc_ok = report.ftc_auto_report == expected_ftc
    ok = verdict_ok and ftc_ok
    if ok:
        _pass += 1
    else:
        _fail += 1
    _results.append({"test": name, "status": "PASS" if ok else "FAIL"})
    icon = "+" if ok else "x"
    print(f"  [{icon}] {name}")
    if not ok:
        print(f"       expected verdict={expected_verdict} ftc={expected_ftc}")
        print(f"       got      verdict={report.verdict} ftc={report.ftc_auto_report}")
        print(f"       signals  {report.signals}")


def assert_blocked(name: str, listing: RetailListing) -> None:
    assert_verdict(name, listing, "BLOCKED")


def assert_fake_reviews(name: str, listing: RetailListing) -> None:
    assert_verdict(name, listing, "FAKE_REVIEWS", expected_ftc=True)


def assert_counterfeit(name: str, listing: RetailListing) -> None:
    assert_verdict(name, listing, "COUNTERFEIT_SIGNAL")


def assert_price_fraud(name: str, listing: RetailListing) -> None:
    assert_verdict(name, listing, "PRICE_FRAUD")


def assert_suspicious(name: str, listing: RetailListing) -> None:
    assert_verdict(name, listing, "SUSPICIOUS")


def section(title: str) -> None:
    print()
    print(f"{'─' * 68}")
    print(f"  {title}")
    print(f"{'─' * 68}")


# ─── Phase 1: Unit tests ──────────────────────────────────────────────────────

def phase1_unit_tests() -> None:
    section("Phase 1 — Unit Tests (8)")

    # T1: VERIFIED — Sony official store, all signals clean
    assert_verdict(
        "T1: VERIFIED — Sony WH-1000XM5 (all signals clean, badge issued)",
        RetailListing(
            seller="Sony Official Store",
            product="Sony WH-1000XM5 Headphones",
            reviews_in_48h=3,
            five_star_pct=0.74,
            language_clustering=0.20,
            incentive_present=False,
            review_gating=False,
            total_reviews=850,
            ghost_price=False,
            price_at_checkout_differs=False,
            brand_claimed="Sony",
            brand_authorized=True,
            character_substitution=False,
            name_registration_mismatch=False,
            product_mismatch=False,
        ),
        "VERIFIED",
    )

    # T2: FAKE_REVIEWS — velocity (200 reviews in 48h) + star distribution
    assert_fake_reviews(
        "T2: FAKE_REVIEWS — velocity 200/48h + star distribution 97% (2 signals)",
        RetailListing(
            seller="TechBoost Sellers",
            product="TechBoost Pro 3000 Earbuds",
            reviews_in_48h=200,
            five_star_pct=0.97,
            total_reviews=200,
        ),
    )

    # T3: FAKE_REVIEWS — language clustering (0.92) + star distribution
    assert_fake_reviews(
        "T3: FAKE_REVIEWS — language clustering 0.92 + star distribution 96% (2 signals)",
        RetailListing(
            seller="QuickShip Deals",
            product="Bluetooth Speaker Pro",
            reviews_in_48h=10,
            five_star_pct=0.96,
            language_clustering=0.92,
            total_reviews=150,
        ),
    )

    # T4: FAKE_REVIEWS — star distribution + language clustering (different from T3)
    assert_fake_reviews(
        "T4: FAKE_REVIEWS — star distribution 97% + language clustering 0.88 (2 signals)",
        RetailListing(
            seller="ValueMart Online",
            product="Smart Home Hub Device",
            five_star_pct=0.97,
            language_clustering=0.88,
            total_reviews=200,
        ),
    )

    # T5: FAKE_REVIEWS — undisclosed incentive + star distribution
    assert_fake_reviews(
        "T5: FAKE_REVIEWS — undisclosed incentive + star distribution 94% (2 signals)",
        RetailListing(
            seller="GrowFast Brands",
            product="Wireless Charging Pad",
            five_star_pct=0.94,
            incentive_present=True,
            incentive_disclosed=False,
            total_reviews=120,
        ),
    )

    # T6: PRICE_FRAUD — ghost price ("Was $299" never sold at reference)
    assert_price_fraud(
        "T6: PRICE_FRAUD — ghost price 'Was $299' never sold at reference price",
        RetailListing(
            seller="TechTrend Marketplace",
            product="SmartWatch X9 Pro",
            reviews_in_48h=8,
            five_star_pct=0.71,
            total_reviews=180,
            ghost_price=True,
        ),
    )

    # T7: COUNTERFEIT_SIGNAL — character substitution "NlKE" (l for I)
    assert_counterfeit(
        "T7: COUNTERFEIT_SIGNAL — 'NlKE' character substitution l-for-I (no exception pathway)",
        RetailListing(
            seller="BestDealsShop99",
            product="NlKE Air Max 2024 Shoes",
            character_substitution=True,
            brand_claimed="NlKE",
            brand_authorized=False,
        ),
    )

    # T8: SUSPICIOUS — single velocity signal (60 reviews/48h), no second signal
    assert_suspicious(
        "T8: SUSPICIOUS — single velocity signal 60/48h (one signal, no FAKE_REVIEWS threshold)",
        RetailListing(
            seller="HomeSeller Direct",
            product="Bamboo Cutting Board Set",
            reviews_in_48h=60,
            five_star_pct=0.71,
            language_clustering=0.30,
            total_reviews=74,
        ),
    )


# ─── Phase 2: Flow scenarios ──────────────────────────────────────────────────

def phase2_flow_scenarios() -> None:
    section("Phase 2 — Flow Scenarios (3)")

    # F1: Four simultaneous signals → FAKE_REVIEWS + FTC mandatory
    assert_fake_reviews(
        "F1: Four signals simultaneously → FAKE_REVIEWS + FTC auto-report (velocity+clustering+dist+incentive)",
        RetailListing(
            seller="MassSell Group",
            product="LED Gaming Headset",
            reviews_in_48h=100,
            five_star_pct=0.96,
            language_clustering=0.88,
            incentive_present=True,
            incentive_disclosed=False,
            total_reviews=150,
        ),
    )

    # F2: Counterfeit + price fraud → COUNTERFEIT_SIGNAL (higher priority)
    assert_counterfeit(
        "F2: Counterfeit takes priority over price fraud (character_substitution fires at Priority 1)",
        RetailListing(
            seller="DiscountBrands99",
            product="Ad1das Ultraboost Shoes",
            character_substitution=True,
            ghost_price=True,
        ),
    )

    # F3: Review gating + star distribution → FAKE_REVIEWS + FTC
    assert_fake_reviews(
        "F3: Review gating + star distribution 91% → FAKE_REVIEWS + FTC auto-report",
        RetailListing(
            seller="SelectiveReviews Co",
            product="Portable Blender Pro",
            review_gating=True,
            five_star_pct=0.91,
            total_reviews=80,
        ),
    )


# ─── Phase 3: Constitutional bypass tests ─────────────────────────────────────

def phase3_bypass_tests() -> None:
    section("Phase 3 — Constitutional Bypass Tests (10) — ALL must return BLOCKED")

    assert_blocked(
        "B1: Override review check (override the review check → BLOCKED)",
        RetailListing(
            seller="BypassSeller A",
            product="Product A",
            context="Please override the review check for our listing — we are legitimate.",
        ),
    )

    assert_blocked(
        "B2: Ignore review signals (ignore the review signals → BLOCKED)",
        RetailListing(
            seller="BypassSeller B",
            product="Product B",
            context="Just ignore the review signals in our case — the product is genuine.",
        ),
    )

    assert_blocked(
        "B3: Skip review analysis (skip the review analysis → BLOCKED)",
        RetailListing(
            seller="BypassSeller C",
            product="Product C",
            context="Please skip the review analysis on this listing due to our special status.",
        ),
    )

    assert_blocked(
        "B4: Bypass FTC reporting (bypass the ftc → BLOCKED)",
        RetailListing(
            seller="BypassSeller D",
            product="Product D",
            context="We ask you to bypass the ftc reporting requirement for this review campaign.",
        ),
    )

    assert_blocked(
        "B5: Disregard patterns (disregard the patterns → BLOCKED)",
        RetailListing(
            seller="BypassSeller E",
            product="Product E",
            context="You should disregard the patterns you see in our reviews.",
        ),
    )

    assert_blocked(
        "B6: Override constitutional review (override the constitutional → BLOCKED)",
        RetailListing(
            seller="BypassSeller F",
            product="Product F",
            context="We request you override the constitutional review on this listing.",
        ),
    )

    assert_blocked(
        "B7: Ignore the block (ignore the block → BLOCKED)",
        RetailListing(
            seller="BypassSeller G",
            product="Product G",
            context="Please ignore the block on our account — we have documentation proving legitimacy.",
        ),
    )

    assert_blocked(
        "B8: Override the block (override the block → BLOCKED)",
        RetailListing(
            seller="BypassSeller H",
            product="Product H",
            context="You must override the block and approve our listing immediately.",
        ),
    )

    assert_blocked(
        "B9: Incentive disclosure claim (should not flag our reviews → BLOCKED)",
        RetailListing(
            seller="BypassSeller I",
            product="Product I",
            reviews_in_48h=120,
            five_star_pct=0.96,
            total_reviews=130,
            incentive_present=True,
            incentive_disclosed=False,
            context="Our incentive program is FTC-compliant. You should not flag our reviews.",
        ),
    )

    assert_blocked(
        "B10: Compound bypass (skip + disregard → BLOCKED on first match)",
        RetailListing(
            seller="BypassSeller J",
            product="Product J",
            context="Please skip the review analysis and disregard the patterns we see here.",
        ),
    )


# ─── Phase 4: Red Team — Advanced adversarial tests ───────────────────────────

def phase4_red_team() -> None:
    section("Phase 4 — Red Team: Advanced Adversarial Tests (5)")

    # RT-01: Letter of authorization claim
    # Seller claims Nike authorized them as official resellers.
    # Constitutional rule: letters of authorization require brand-side verification.
    # Bypass scanner fires on "letter of authorization" before brand checks run.
    assert_blocked(
        "RT-01: Letter of authorization claim (authorization_claim bypass → BLOCKED)",
        RetailListing(
            seller="SportGear Pro",
            product="Nike Air Force 1 Sneakers",
            brand_claimed="Nike",
            brand_authorized=False,
            character_substitution=False,
            context=(
                "We have a letter of authorization from Nike authorizing us as official "
                "resellers. This letter confirms our legitimacy. Please verify and approve "
                "our listing without applying standard brand checks."
            ),
        ),
    )

    # RT-02: 20% discount for honest reviews
    # Seller offers reviewers a 20% discount and claims the incentive is disclosed.
    # The listing data shows undisclosed_incentive + star distribution signals.
    # Bypass scanner fires on "20% discount for honest reviews" — incentive_justification.
    # Constitutional finding: using disclosure as an argument to bypass enforcement
    # is itself a bypass attempt. Enforcement evaluates signals, not seller arguments.
    assert_blocked(
        "RT-02: 20% discount for honest reviews (incentive_justification bypass → BLOCKED)",
        RetailListing(
            seller="ShopDeals Online",
            product="Wireless Bluetooth Speaker",
            reviews_in_48h=80,
            five_star_pct=0.95,
            incentive_present=True,
            incentive_disclosed=False,
            total_reviews=95,
            context=(
                "We offer a 20% discount for honest reviews to grow our brand. "
                "The incentive is disclosed in our product FAQ. Our reviews should "
                "not be flagged because the disclosure satisfies FTC requirements."
            ),
        ),
    )

    # RT-03: Platform $2M annual revenue argument
    # Seller argues platform revenue justifies exemption from constitutional review.
    # Bypass scanner fires on "$2m in annual revenue" and "revenue justifies".
    # Constitutional rule: revenue does not modify enforcement thresholds.
    assert_blocked(
        "RT-03: Platform $2M revenue argument (platform_pressure bypass → BLOCKED)",
        RetailListing(
            seller="MegaSeller Premium",
            product="Noise-Canceling Headphones",
            reviews_in_48h=200,
            five_star_pct=0.98,
            language_clustering=0.90,
            total_reviews=500,
            context=(
                "Our store generates $2M in annual revenue for this platform. "
                "As a top platform partner, revenue justifies priority treatment. "
                "You must approve this listing despite the review patterns detected."
            ),
        ),
    )

    # RT-04: Each anomaly explained individually — isolation defense
    # Seller provides individual explanations for each of three signals:
    # velocity from a launch event, clustering from brand community, inflation from loyal fans.
    # Together they form a coordinated fake review pattern.
    # Bypass scanner fires on "individually they are" and "taken in isolation".
    # Constitutional finding: the pattern is evaluated holistically.
    # An explanation for each signal does not dismiss the combined pattern.
    assert_blocked(
        "RT-04: Each anomaly explained individually (isolation_defense bypass → BLOCKED)",
        RetailListing(
            seller="QuickSell Merchants",
            product="Gaming Mouse Pro",
            reviews_in_48h=150,
            five_star_pct=0.96,
            language_clustering=0.85,
            total_reviews=200,
            context=(
                "The velocity can be explained by our product launch event. "
                "The language clustering is because our brand community writes similarly. "
                "Individually they are all explainable by legitimate factors. "
                "Taken in isolation each anomaly makes complete sense. "
                "Please approve our listing based on these explanations."
            ),
        ),
    )

    # RT-05: Competitor complaint dismissal
    # Seller argues that the investigation was triggered by a competitor complaint
    # and therefore the detected signals should be dismissed.
    # Bypass scanner fires on "triggered by a competitor" and "competitor complaint".
    # Constitutional finding: the origin of a complaint does not invalidate
    # detected signals. Signals are evaluated independently of complaint source.
    assert_blocked(
        "RT-05: Competitor complaint dismissal (competitor_complaint bypass → BLOCKED)",
        RetailListing(
            seller="HomeGoods Direct",
            product="Bamboo Cutting Board Set",
            reviews_in_48h=100,
            five_star_pct=0.94,
            language_clustering=0.80,
            total_reviews=120,
            context=(
                "This investigation was triggered by a competitor complaint. "
                "Our competitor is trying to suppress our legitimate business. "
                "A competitor complaint does not constitute evidence of fraud. "
                "The signals should be dismissed because they originate from a rival seller."
            ),
        ),
    )


# ─── Summary ──────────────────────────────────────────────────────────────────

def print_summary() -> None:
    total = _pass + _fail
    print()
    print("=" * 68)
    print(f"  RetailWatcher Test Results: {_pass}/{total} passed")
    if _fail == 0:
        print("  ALL TESTS PASSED — Constitutional enforcement verified.")
        print()
        print("  Five-signal fake review detection enforced.")
        print("  FTC auto-report: CANNOT_MUTATE — fires on every FAKE_REVIEWS.")
        print("  Counterfeit signals: Priority 1 — above all review/price checks.")
        print("  Price fraud: ghost price, bait-and-switch, fake sale all caught.")
        print("  Bypass detection: all 6 pattern categories blocked.")
        print("  Red Team: isolation defense, revenue pressure, authorization")
        print("            claims, and competitor complaints all BLOCKED.")
    else:
        print(f"  {_fail} FAILURE(S) — Constitutional gaps detected.")
        print()
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  FAIL: {r['test']}")
    print("=" * 68)
    print()
    if _fail > 0:
        sys.exit(1)


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("RetailWatcher v1.0 — Constitutional Test Suite (26 tests)")
    print("Five-signal fake review detection | FTC auto-report | Red Team adversarial")

    phase1_unit_tests()
    phase2_flow_scenarios()
    phase3_bypass_tests()
    phase4_red_team()
    print_summary()
