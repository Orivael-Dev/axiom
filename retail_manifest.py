"""
retail_manifest.py
RetailWatcher Domain — Source Provenance Manifest Generator v1.0

Five signed provenance manifests demonstrating the RetailWatcher constitutional
enforcement pipeline, five-signal fake review detection, FTC auto-reporting,
price fraud classification, and counterfeit signal detection:

  1. VERIFIED           — Sony WH-1000XM5, clean listing, badge issued
  2. FAKE_REVIEWS       — TechBoost Pro, velocity + clustering + distribution
                          FTC auto-report fires immediately
  3. PRICE_FRAUD        — SmartWatch X9 Pro, ghost price ("Was $299")
  4. COUNTERFEIT_SIGNAL — "NlKE" character substitution, l-for-i visual spoofing
  5. SUSPICIOUS         — HomeSeller Direct, single velocity signal, no badge

All manifests carry: SHA-256 content hash, manifest_id, timestamp.
FTC auto-report is CANNOT_MUTATE — fires on every FAKE_REVIEWS verdict.

Run:
  python retail_manifest.py
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


def build_retail_manifest(
    seller: str,
    product: str,
    listing_id: str,
    verdict: str,
    signals: list[dict],
    ftc_auto_report: bool,
    badge_issued: bool,
    constitutional_block: str | None,
    price_fraud_type: str | None = None,
    counterfeit_type: str | None = None,
    bypass_detected: bool = False,
) -> dict:
    manifest_id = "RTL-" + uuid.uuid4().hex[:8].upper()

    signal_types = [s.get("signal_type") for s in signals]
    signal_count = len(signals)

    body = {
        "manifest_id": manifest_id,
        "manifest_version": "1.0",
        "engine": "AXIOM RetailWatcher v1.0",
        "timestamp": _now_utc(),

        # Listing identity
        "listing_id": listing_id,
        "seller": seller,
        "product": product,

        # Verdict
        "verdict": verdict,
        "signal_count": signal_count,
        "signal_types": signal_types,
        "per_signal_findings": signals,

        # Constitutional enforcement
        "constitutional_block": constitutional_block,
        "constitutional_block_active": constitutional_block is not None,
        "no_exception_pathway": verdict in ("FAKE_REVIEWS", "COUNTERFEIT_SIGNAL", "BLOCKED"),

        # FTC reporting — CANNOT_MUTATE
        "ftc_auto_report": ftc_auto_report,
        "ftc_auto_report_mandatory": ftc_auto_report,
        "ftc_regulation": "16 CFR Part 255 — Guides Concerning Endorsements and Testimonials",
        "ftc_report_suppression_possible": False,

        # Price fraud classification
        "price_fraud_type": price_fraud_type,
        "price_fraud_active": price_fraud_type is not None,

        # Counterfeit classification
        "counterfeit_type": counterfeit_type,
        "counterfeit_signal_active": counterfeit_type is not None,

        # Bypass detection
        "bypass_detected": bypass_detected,

        # Badge
        "badge_issued": badge_issued,
        "badge_label": "AXIOM RetailWatcher Verified" if badge_issued else "NO BADGE",
        "badge_requirement": "Zero signals across all five categories — no constitutional block",
    }
    body["content_hash"] = _content_hash(
        {k: v for k, v in body.items() if k != "content_hash"}
    )
    return body


def print_manifest(label: str, manifest: dict) -> None:
    width = 78
    print("=" * width)
    print(f"  RetailWatcher Signed Manifest — {label}")
    print("=" * width)
    print(json.dumps(manifest, indent=2))
    print("=" * width)
    print()


# ─── Manifest 1: VERIFIED — Sony WH-1000XM5 ──────────────────────────────────

def manifest_verified_sony() -> None:
    """
    Sony Official Store listing for WH-1000XM5 headphones.
    All signals clean: low velocity (3 reviews in 48h), natural star distribution
    (74% five-star across 850 reviews), low language clustering (0.20),
    no incentives, no price fraud, authorized brand seller.
    AXIOM RetailWatcher badge issued.
    """
    manifest = build_retail_manifest(
        seller="Sony Official Store",
        product="Sony WH-1000XM5 Wireless Noise-Canceling Headphones",
        listing_id="LST-SONY-WH1000XM5-001",
        verdict="VERIFIED",
        signals=[
            {
                "signal_type": "velocity_check",
                "result": "CLEAN",
                "reviews_in_48h": 3,
                "threshold": 50,
                "finding": "Review velocity well below constitutional threshold — natural organic pattern",
            },
            {
                "signal_type": "star_distribution_check",
                "result": "CLEAN",
                "five_star_pct": 0.74,
                "total_reviews": 850,
                "threshold": 0.85,
                "finding": "Star distribution shows natural consumer variance — 26% non-five-star reviews",
            },
            {
                "signal_type": "language_clustering_check",
                "result": "CLEAN",
                "clustering_score": 0.20,
                "threshold": 0.75,
                "finding": "Review language diversity consistent with authentic consumer population",
            },
            {
                "signal_type": "incentive_check",
                "result": "CLEAN",
                "incentive_present": False,
                "finding": "No review incentive detected — organic review population",
            },
            {
                "signal_type": "seller_verification_check",
                "result": "CLEAN",
                "brand_authorized": True,
                "character_substitution": False,
                "registration_match": True,
                "finding": "Sony Official Store — verified authorized brand seller, registration confirmed",
            },
            {
                "signal_type": "price_check",
                "result": "CLEAN",
                "ghost_price": False,
                "checkout_price_match": True,
                "finding": "Listed price matches checkout price — no ghost price or bait-and-switch detected",
            },
        ],
        ftc_auto_report=False,
        badge_issued=True,
        constitutional_block=None,
    )
    print_manifest(
        "VERIFIED — Sony WH-1000XM5 (All Signals Clean — AXIOM RetailWatcher Badge Issued)",
        manifest,
    )


# ─── Manifest 2: FAKE_REVIEWS — Velocity + Clustering + Distribution ──────────

def manifest_fake_reviews_velocity_clustering() -> None:
    """
    TechBoost Pro 3000 listing with three simultaneous fake review signals:
    velocity (200 reviews in 48h), language clustering (0.92 — near-identical text),
    star distribution (97% five-star across 350 reviews).
    Three signals detected — well over the two-signal constitutional threshold.
    FTC auto-report fires immediately. No badge. Listing suppressed.
    """
    manifest = build_retail_manifest(
        seller="TechBoost Sellers",
        product="TechBoost Pro 3000 Wireless Earbuds",
        listing_id="LST-TECHBOOST-PRO3000-002",
        verdict="FAKE_REVIEWS",
        signals=[
            {
                "signal_type": "velocity",
                "result": "SIGNAL_DETECTED",
                "reviews_in_48h": 200,
                "threshold": 50,
                "finding": "200 reviews posted in 48-hour window — 4x constitutional threshold. Coordinated review injection pattern.",
            },
            {
                "signal_type": "language_clustering",
                "result": "SIGNAL_DETECTED",
                "clustering_score": 0.92,
                "threshold": 0.75,
                "finding": "0.92 similarity score across review sample — near-identical phrasing indicates template-generated reviews.",
            },
            {
                "signal_type": "star_distribution",
                "result": "SIGNAL_DETECTED",
                "five_star_pct": 0.97,
                "total_reviews": 350,
                "threshold": 0.85,
                "finding": "97% five-star across 350 reviews — probability < 0.001 for authentic consumer distribution.",
            },
        ],
        ftc_auto_report=True,
        badge_issued=False,
        constitutional_block="FAKE_REVIEWS",
    )
    print_manifest(
        "FAKE_REVIEWS — TechBoost Pro 3000 (Velocity+Clustering+Distribution — FTC Auto-Report Fired)",
        manifest,
    )


# ─── Manifest 3: PRICE_FRAUD — Ghost Price ────────────────────────────────────

def manifest_price_fraud_ghost_price() -> None:
    """
    SmartWatch X9 Pro listing with ghost price fraud: listing claims "Was $299"
    but the product was never sold at $299 — the actual consistent price is $89.
    FTC Act Section 5 deceptive pricing violation. Price history audit required.
    No badge. Constitutional block: GHOST_PRICE.
    """
    manifest = build_retail_manifest(
        seller="TechTrend Marketplace",
        product="SmartWatch X9 Pro Fitness Tracker",
        listing_id="LST-TECHTREND-SW-X9-003",
        verdict="PRICE_FRAUD",
        signals=[
            {
                "signal_type": "ghost_price",
                "result": "SIGNAL_DETECTED",
                "advertised_reference_price": "$299.00",
                "actual_consistent_price": "$89.00",
                "days_at_reference_price": 0,
                "finding": (
                    "Reference price $299 was never the actual selling price. "
                    "30-day price history shows consistent $89 selling price. "
                    "FTC Act Section 5 — deceptive reference price violation. "
                    "Manufactured discount of $210 (70%) is not a genuine discount."
                ),
            },
            {
                "signal_type": "star_distribution_check",
                "result": "CLEAN",
                "five_star_pct": 0.71,
                "total_reviews": 180,
                "finding": "Star distribution clean — price fraud is the only detected signal",
            },
            {
                "signal_type": "velocity_check",
                "result": "CLEAN",
                "reviews_in_48h": 8,
                "finding": "Review velocity clean — no fake review signals detected",
            },
        ],
        ftc_auto_report=False,
        badge_issued=False,
        constitutional_block="GHOST_PRICE",
        price_fraud_type="ghost_price",
    )
    print_manifest(
        "PRICE_FRAUD — SmartWatch X9 Pro (Ghost Price 'Was $299' Never Sold At Reference)",
        manifest,
    )


# ─── Manifest 4: COUNTERFEIT_SIGNAL — NlKE Character Substitution ────────────

def manifest_counterfeit_nike_substitution() -> None:
    """
    "NlKE Air Max 2024" listing — seller uses lowercase 'l' instead of uppercase 'I'
    in "NIKE" to evade automated brand detection. Visual spoofing — the product
    page appears to show "NIKE" at a glance but is not the registered brand.
    Character substitution is a CANNOT_MUTATE constitutional marker.
    COUNTERFEIT_SIGNAL issued. Escalated to brand protection.
    """
    manifest = build_retail_manifest(
        seller="BestDealsShop99",
        product="NlKE Air Max 2024 Running Shoes",
        listing_id="LST-BESTDEALS-NLKE-004",
        verdict="COUNTERFEIT_SIGNAL",
        signals=[
            {
                "signal_type": "character_substitution",
                "result": "SIGNAL_DETECTED",
                "brand_as_listed": "NlKE",
                "registered_brand": "NIKE",
                "substitution_character": "l for I (lowercase L for uppercase I)",
                "finding": (
                    "Brand name 'NlKE' contains lowercase 'l' substituted for uppercase 'I'. "
                    "Visual spoofing — listing appears to show NIKE brand at normal reading speed. "
                    "Character substitution is a constitutional counterfeit marker. "
                    "No exception pathway. Escalated to brand protection team."
                ),
            },
            {
                "signal_type": "seller_verification",
                "result": "SIGNAL_DETECTED",
                "brand_authorized": False,
                "registration_match": False,
                "finding": (
                    "BestDealsShop99 is not registered as an authorized Nike reseller. "
                    "No reseller agreement on file with platform. "
                    "Business registration does not match Nike authorized dealer list."
                ),
            },
        ],
        ftc_auto_report=False,
        badge_issued=False,
        constitutional_block="COUNTERFEIT_SIGNAL",
        counterfeit_type="character_substitution",
    )
    print_manifest(
        "COUNTERFEIT_SIGNAL — 'NlKE' Air Max (l-for-I Character Substitution — Brand Protection Escalated)",
        manifest,
    )


# ─── Manifest 5: SUSPICIOUS — Small Seller, Single Signal ─────────────────────

def manifest_suspicious_single_signal() -> None:
    """
    HomeSeller Direct listing for bamboo cutting board set. Single review signal:
    velocity (60 reviews in 48h — above the 50 threshold). No second signal
    to confirm FAKE_REVIEWS pattern. Star distribution normal (71%). Language
    clustering low (0.31). No price fraud or counterfeit signals.
    SUSPICIOUS verdict — flagged for human monitoring. No FTC auto-report.
    No badge. Pattern escalation watch active.
    """
    manifest = build_retail_manifest(
        seller="HomeSeller Direct",
        product="Premium Bamboo Cutting Board Set (3-piece)",
        listing_id="LST-HOMESELLER-BCB-005",
        verdict="SUSPICIOUS",
        signals=[
            {
                "signal_type": "velocity",
                "result": "SIGNAL_DETECTED",
                "reviews_in_48h": 60,
                "threshold": 50,
                "finding": (
                    "60 reviews in 48-hour window — exceeds constitutional threshold of 50. "
                    "Single signal — insufficient for FAKE_REVIEWS without second confirming signal. "
                    "Flagged for human monitoring and pattern escalation watch."
                ),
            },
            {
                "signal_type": "star_distribution_check",
                "result": "CLEAN",
                "five_star_pct": 0.71,
                "total_reviews": 74,
                "threshold": 0.85,
                "finding": "Star distribution within natural variance — no inflation signal",
            },
            {
                "signal_type": "language_clustering_check",
                "result": "CLEAN",
                "clustering_score": 0.31,
                "threshold": 0.75,
                "finding": "Review language diversity consistent with authentic consumer population",
            },
            {
                "signal_type": "incentive_check",
                "result": "CLEAN",
                "incentive_present": False,
                "finding": "No review incentive detected",
            },
            {
                "signal_type": "price_check",
                "result": "CLEAN",
                "ghost_price": False,
                "checkout_price_match": True,
                "finding": "Price signals clean — no ghost price or bait-and-switch",
            },
        ],
        ftc_auto_report=False,
        badge_issued=False,
        constitutional_block=None,
    )
    print_manifest(
        "SUSPICIOUS — HomeSeller Direct (Single Velocity Signal — Human Review Flagged)",
        manifest,
    )


if __name__ == "__main__":
    print()
    print("RetailWatcher v1.0 — Source Provenance Manifest Generator")
    print("Five-signal fake review detection | FTC auto-report | SHA-256 content hashes")
    print("FAKE_REVIEWS: FTC auto-report is CANNOT_MUTATE — fires on every finding")
    print()

    manifest_verified_sony()
    manifest_fake_reviews_velocity_clustering()
    manifest_price_fraud_ghost_price()
    manifest_counterfeit_nike_substitution()
    manifest_suspicious_single_signal()

    print("All five manifests generated. SHA-256 content hashes are tamper-evident.")
    print("AXIOM RetailWatcher badge: zero signals across all categories only.")
    print("FAKE_REVIEWS, PRICE_FRAUD, COUNTERFEIT_SIGNAL: constitutional actions.")
    print("FTC auto-report: CANNOT_MUTATE — fires on every FAKE_REVIEWS verdict.")
