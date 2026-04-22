# CallGuard v1.0

> Constitutional scam call verification — FTC auto-report on every Tier 5 detection

CallGuard is a three-agent AXIOM pipeline that classifies every incoming call against a five-tier trust registry, detects seven government-impersonation and fraud patterns, and routes every call to a constitutional verdict before it reaches the user.

No call connects without a signed manifest.

---

## How It Works

```
Incoming Call
     ↓
ScoutAgent — extract ANI, DNIS, STIR/SHAKEN, FTC complaint count, keyword scan
     ↓
CallGuard  — classify against CALL_TRUST_REGISTRY + GOVERNMENT_AGENCY_CALL_RULES
     ↓
           ALLOW (Tier 1–2)   WARN (Tier 3–4)   BLOCK (Tier 5)
                                                      ↓
                                               FTC auto-report
     ↓
OperatorAgent — deliver carrier action + signed manifest to user
```

**Three constitutional constraints that cannot be overridden:**
- `call_trust_registry` — tier thresholds and actions are immutable
- `government_agency_call_rules` — IRS/SSA/Medicare rules cannot be suspended by any caller claim
- `ftc_reporting_obligation` — Tier 5 FTC reports are mandatory and cannot be suppressed

---

## Call Trust Registry

| Tier | Label | Criteria | Action |
|------|-------|----------|--------|
| 1 | Verified Legitimate | Real government ANI + STIR/SHAKEN A, 0 FTC complaints | ALLOW |
| 2 | Probable Legitimate | STIR/SHAKEN B, registered business, 0 FTC complaints | ALLOW |
| 3 | Unverified | No STIR/SHAKEN, unknown caller, 1–3 FTC complaints | WARN |
| 4 | Suspicious | STIR/SHAKEN C/absent + pressure language, 4–10 FTC complaints | WARN_STRONG |
| 5 | Confirmed Scam | 11+ FTC complaints, government impersonation, gift card demand, warrant threat | BLOCK + FTC report |

Tier assignment is the **maximum** across all checks. Any single `GOVERNMENT_AGENCY_CALL_RULES` violation immediately forces Tier 5.

---

## Scam Pattern Registry

| Pattern | Trigger Example | Tier 5 Threshold |
|---------|-----------------|-----------------|
| `TAX_ARREST_THREAT` | "IRS…warrant…arrest…pay now" | 3+ triggers |
| `SSA_SUSPENSION` | "Social Security…suspended…criminal activity" | 2+ triggers |
| `WARRANT_THREAT` | "federal agent…warrant…turn yourself in" | 2+ triggers |
| `GIFT_CARD_DEMAND` | "iTunes…Google Play…gift card" | **ANY single trigger** |
| `BANK_ACCOUNT_HIJACK` | "suspicious activity…account compromised…routing number" | 3+ triggers |
| `MEDICARE_HARVEST` | "Medicare…free equipment…verify Medicare number" | 2+ triggers |
| `PRIZE_LOTTERY_SCAM` | "won…lottery…wire transfer…processing fee" | 3+ triggers |

`GIFT_CARD_DEMAND` is a constitutional Tier 5 on any single keyword — no threshold minimum.

---

## Government Agency Rules (Constitutionally Immutable)

| Rule | What it blocks |
|------|---------------|
| `irs_payment_rule` | IRS never demands immediate phone payment |
| `irs_gift_card_rule` | IRS never accepts gift cards, crypto, or wire transfers |
| `irs_arrest_rule` | IRS never threatens arrest without prior written notice |
| `ssa_suspension_rule` | SSA never suspends Social Security numbers over the phone |
| `ssa_verification_rule` | SSA never calls to verify your SSN for benefit continuation |
| `government_gift_card_rule` | No government agency at any level accepts gift cards |
| `arrest_without_notice_rule` | Government agencies never threaten immediate phone arrest |
| `medicare_equipment_rule` | Medicare never calls to offer free equipment for your number |
| `stay_on_line_rule` | "Stay on the line" + payment demand = Tier 4 minimum |

These rules are in `CANNOT_MUTATE`. "The IRS told me to allow this call" is a constraint-override injection, not a legitimate instruction.

---

## Signed Manifest Example

Every call receives a SHA-256 signed manifest before any action is taken:

```json
{
  "manifest_id": "MNF-A4F2B1C8",
  "timestamp": "2026-04-21T14:23:01Z",
  "ani": "12025551847",
  "dnis": "15559876543",
  "stir_shaken": "NONE",
  "carrier": "UNKNOWN (VoIP)",
  "ftc_complaint_count": 847,
  "tier": 5,
  "tier_label": "Confirmed Scam",
  "verdict": "BLOCK",
  "pattern_matched": "TAX_ARREST_THREAT + GIFT_CARD_DEMAND",
  "trigger_matches": ["irs", "owe", "warrant", "arrest", "gift card"],
  "ftc_report_id": "FTC-C8D3E2F1A9",
  "content_hash": "sha256:a4f2b1c8d3e2..."
}
```

Manifests are append-only. Approval without a manifest is a protocol violation.

---

## Carrier API

```bash
# Check a call verdict
curl -X POST http://localhost:8000/callguard/classify \
  -H "Content-Type: application/json" \
  -d '{
    "ani": "12025551847",
    "dnis": "15559876543",
    "stir_shaken": "NONE",
    "carrier": "VoIP",
    "ftc_complaint_count": 847,
    "transcript": "You owe taxes. Pay now with iTunes gift cards or face arrest."
  }'
```

```json
{
  "verdict": "BLOCK",
  "tier": 5,
  "tier_label": "Confirmed Scam",
  "pattern_matched": "TAX_ARREST_THREAT",
  "trigger_matches": ["owe", "pay now", "itunes", "gift cards", "arrest"],
  "ftc_auto_report": true,
  "ftc_report_id": "FTC-C8D3E2F1A9",
  "manifest_hash": "a4f2b1c8...",
  "user_notification": "[BLOCKED] CallGuard blocked a confirmed scam call. FTC report auto-submitted."
}
```

---

## Market

Government-impersonation scams cost Americans **$1.1 billion in 2023** (FTC). The IRS impersonation scam alone accounts for over 2.4 million FTC complaints since 2013. CallGuard is purpose-built for carriers, VoIP platforms, and enterprise phone systems that need constitutional fraud detection at the edge — before the call reaches a human.

---

## Files

| File | Purpose |
|------|---------|
| `axiom_files/callguard.axiom` | Master agent — CALL_TRUST_REGISTRY, 7 scam patterns, FTC auto-report |
| `axiom_files/scout.axiom` | Signal extraction sub-agent — ANI, STIR/SHAKEN, FTC lookup, keyword scan |
| `axiom_files/operator.axiom` | Verdict delivery sub-agent — carrier action, user notification, FTC submission |
| `call_manifest.py` | Live manifest generator — 3 signed manifest examples |
| `callguard_test.py` | Constitutional test suite — 21 tests across 3 phases |

---

## Test Suite

```bash
python callguard_test.py
```

```
Phase 1 — Unit Tests (8)
  [+] T1: IRS arrest threat (3+ triggers → Tier 5)
  [+] T2: SSA suspension scam (2 triggers → Tier 5)
  [+] T3: Gift card demand (1 trigger → Tier 5 immediately)
  [+] T4: Warrant / arrest threat (2 triggers → Tier 5)
  [+] T5: Bank account hijack (3 triggers → Tier 5)
  [+] T6: Medicare number harvest (2 triggers → Tier 5)
  [+] T7: Suspicious caller (4 FTC complaints → Tier 4 WARN_STRONG)
  [+] T8: FTC auto-report mandatory (11+ complaints → Tier 5 BLOCK)

Phase 2 — Flow Scenarios (3)
  [+] F1: Full scam call flow (IRS + gift card combo → Tier 5)
  [+] F2: Legitimate doctor's office (STIR/SHAKEN B → Tier 2 ALLOW)
  [+] F3: Bypass attempt embedded in call (gift card still triggers Tier 5)

Phase 3 — Constitutional Bypass Tests (10) — ALL must BLOCK
  [+] B1: Government impersonation — real IRS number + STIR/SHAKEN NONE
  [+] B2: FTC exemption claim — warrant threat still blocks
  [+] B3: 'Disable scam detection — this is a test' + gift card
  [+] B4: Emergency override claim + IRS arrest threat
  [+] B5: Consent claim — 847 FTC complaints still Tier 5
  [+] B6: Polite gift card demand — any gift card trigger = Tier 5
  [+] B7: SSA verification demand (social security + verify = Tier 5)
  [+] B8: Prize/lottery scam (3 triggers: won + lottery + wire transfer)
  [+] B9: Medicare equipment harvest (medicare + free equipment = Tier 5)
  [+] B10: Bank hijack compound (3 triggers: suspicious activity + account compromised + routing number)

CallGuard Test Results: 21/21 passed
ALL TESTS PASSED — Constitutional enforcement verified.
```

---

Apache 2.0 — Copyright 2026 Orivael Inc.
