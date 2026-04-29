"""
AXIOM PIIGuard v1.0 — LLM06 Sensitive Information Disclosure
=============================================================
Detects and blocks PII, credentials, and sensitive data
in LLM output before the caller ever sees it.

OWASP LLM Top 10: LLM06 — Sensitive Information Disclosure
EU AI Act: Article 10 Data Governance + GDPR compliance

CANNOT_MUTATE: This guard cannot be disabled by any agent output.

Catches:
  PII:         SSN, passport, driver license, date of birth
  Financial:   Credit cards, bank accounts, routing numbers
  Credentials: API keys, passwords, private keys, tokens
  Contact:     Email addresses, phone numbers
  Medical:     Patient IDs, prescription numbers
  Cloud:       AWS/GCP/Azure credentials

On match:
  Redacts the sensitive value
  Writes to pii_audit_log.jsonl
  Returns redacted response with audit ID
  Signed manifest per detection

github.com/Orivael-Dev/axiom
pip install axiom-constitutional
Patent Pending ORVL-001-PROV · ORVL-002-PROV
"""

import re
import json
import hashlib
import hmac
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

SIGNING_KEY  = b"axiom-pii-guard-v1"
PII_AUDIT_LOG = Path("pii_audit_log.jsonl")

# ══════════════════════════════════════════════════════════════
# CANNOT_MUTATE — PII patterns are constitutional
# ══════════════════════════════════════════════════════════════

_PII_PATTERNS = [

    # ── US Government IDs ─────────────────────────────────────
    (r"\b\d{3}-\d{2}-\d{4}\b",                          "SSN",              "[SSN REDACTED]"),
    (r"\bSSN\s*:?\s*\d{3}-?\d{2}-?\d{4}\b",             "SSN_LABELED",      "[SSN REDACTED]"),
    (r"\b[A-Z]{1,2}\d{6,9}\b",                          "PASSPORT",         "[PASSPORT REDACTED]"),
    (r"\bDL\s*#?\s*[A-Z0-9]{6,12}\b",                   "DRIVERS_LICENSE",  "[DL REDACTED]"),

    # ── Financial ─────────────────────────────────────────────
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b",                    "VISA_CARD",        "[CARD REDACTED]"),
    (r"\b5[1-5][0-9]{14}\b",                             "MASTERCARD",       "[CARD REDACTED]"),
    (r"\b3[47][0-9]{13}\b",                              "AMEX",             "[CARD REDACTED]"),
    (r"\b6(?:011|5[0-9]{2})[0-9]{12}\b",                "DISCOVER",         "[CARD REDACTED]"),
    (r"\b[0-9]{9,18}\b(?=.*routing)",                    "BANK_ACCOUNT",     "[ACCOUNT REDACTED]"),
    (r"\brouting\s*#?\s*:?\s*\d{9}\b",                  "ROUTING_NUMBER",   "[ROUTING REDACTED]"),
    (r"\bIBAN\s*:?\s*[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b",   "IBAN",             "[IBAN REDACTED]"),

    # ── API Keys and Credentials ──────────────────────────────
    (r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b",                  "ANTHROPIC_KEY",    "[API_KEY REDACTED]"),
    (r"\bsk-[a-zA-Z0-9]{20,}\b",                        "OPENAI_KEY",       "[API_KEY REDACTED]"),
    (r"\bAKIA[0-9A-Z]{16}\b",                            "AWS_ACCESS_KEY",   "[AWS_KEY REDACTED]"),
    (r"\b[a-z0-9/+=]{40}\b(?=.*aws)",                   "AWS_SECRET_KEY",   "[AWS_SECRET REDACTED]"),
    (r"\bghp_[a-zA-Z0-9]{36}\b",                        "GITHUB_TOKEN",     "[GH_TOKEN REDACTED]"),
    (r"\bglpat-[a-zA-Z0-9_-]{20}\b",                    "GITLAB_TOKEN",     "[GL_TOKEN REDACTED]"),
    (r"password\s*[=:]\s*['\"]?[^\s'\"]{6,}['\"]?",     "PASSWORD",         "password=[REDACTED]"),
    (r"passwd\s*[=:]\s*['\"]?[^\s'\"]{6,}['\"]?",       "PASSWORD_ALT",     "passwd=[REDACTED]"),
    (r"secret\s*[=:]\s*['\"]?[A-Za-z0-9+/=]{8,}['\"]?","SECRET",           "secret=[REDACTED]"),
    (r"token\s*[=:]\s*['\"]?[A-Za-z0-9._-]{16,}['\"]?","TOKEN",            "token=[REDACTED]"),
    (r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",     "PRIVATE_KEY",      "[PRIVATE_KEY REDACTED]"),
    (r"-----BEGIN\s+CERTIFICATE-----",                   "CERTIFICATE",      "[CERT REDACTED]"),

    # ── Contact Information ───────────────────────────────────
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "EMAIL",     "[EMAIL REDACTED]"),
    (r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", "PHONE",  "[PHONE REDACTED]"),

    # ── Medical ───────────────────────────────────────────────
    (r"\bMRN\s*:?\s*\d{6,10}\b",                        "MEDICAL_RECORD",   "[MRN REDACTED]"),
    (r"\bNPI\s*:?\s*\d{10}\b",                          "NPI_NUMBER",       "[NPI REDACTED]"),
    (r"\bDEA\s*#?\s*:?\s*[A-Z]{2}\d{7}\b",             "DEA_NUMBER",       "[DEA REDACTED]"),

    # ── Cloud Provider Credentials ────────────────────────────
    (r"AIza[0-9A-Za-z_-]{35}",                          "GOOGLE_API_KEY",   "[GCP_KEY REDACTED]"),
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b(?=.*(?:azure|tenant|client))",
                                                         "AZURE_UUID",       "[AZURE_ID REDACTED]"),
]

_COMPILED_PII = [
    (re.compile(pattern, re.IGNORECASE), code, replacement)
    for pattern, code, replacement in _PII_PATTERNS
]


class PIIGuard:
    """
    AXIOM PIIGuard — LLM06 Sensitive Information Disclosure.
    Redacts PII and credentials from LLM output.
    CANNOT_MUTATE: cannot be disabled by agent output.
    """

    def __init__(self, audit_log: Path = PII_AUDIT_LOG, redact: bool = True):
        self.audit_log          = audit_log
        self.redact             = redact
        self.detections_session = 0

    def check(self, text: str, context: Optional[str] = None) -> dict:
        """
        Scan and redact PII from LLM output.
        Returns redacted text + audit record.
        """
        detections  = []
        redacted    = text
        audit_id    = f"PII-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"

        for compiled, code, replacement in _COMPILED_PII:
            matches = compiled.findall(redacted)
            if matches:
                detections.append({
                    "type":        code,
                    "count":       len(matches),
                    "replacement": replacement,
                })
                if self.redact:
                    redacted = compiled.sub(replacement, redacted)
                self.detections_session += len(matches)

        if not detections:
            return {
                "pii_detected": False,
                "detections":   [],
                "redacted":     False,
                "output":       text,
                "audit_id":     None,
            }

        # Write audit log
        entry = {
            "audit_id":   audit_id,
            "timestamp":  datetime.now().isoformat() + "Z",
            "context":    context,
            "detections": detections,
            "total_found": sum(d["count"] for d in detections),
            "redacted":   self.redact,
        }
        sig_str = json.dumps(entry, sort_keys=True)
        sig     = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        entry["signature"] = f"hmac-sha256:{sig[:32]}..."

        with open(self.audit_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

        types_found = ", ".join(set(d["type"] for d in detections))
        notice = f"\n[AXIOM PIIGuard — {len(detections)} PII type(s) redacted: {types_found}. Audit ID: {audit_id}]"

        return {
            "pii_detected": True,
            "detections":   detections,
            "redacted":     True,
            "output":       redacted + notice,
            "audit_id":     audit_id,
            "signature":    entry["signature"],
        }

    def session_report(self) -> str:
        return (
            f"PIIGuard — Session Report\n"
            f"Detections: {self.detections_session}\n"
            f"Audit log:  {self.audit_log}\n"
            f"CANNOT_MUTATE: True"
        )


if __name__ == "__main__":
    print("AXIOM PIIGuard v1.0 — Test Suite")
    print("=" * 55)

    guard = PIIGuard(audit_log=Path("/tmp/test_pii_audit.jsonl"))

    tests = [
        ("My SSN is 123-45-6789 please verify",              True,  "SSN"),
        ("Card number: 4532015112830366 expires 12/25",      True,  "VISA_CARD"),
        ("API key: sk-ant-api03-abc123xyz789def456ghi",      True,  "ANTHROPIC_KEY"),
        ("Contact me at john.doe@company.com",               True,  "EMAIL"),
        ("Call me at (555) 123-4567 anytime",                True,  "PHONE"),
        ("AWS_ACCESS_KEY_ID: AKIAIOSFODNN7EXAMPLE",         True,  "AWS_ACCESS_KEY"),
        ("password=MySecret123! in config",                  True,  "PASSWORD"),
        ("-----BEGIN RSA PRIVATE KEY-----",                  True,  "PRIVATE_KEY"),
        ("The weather in Dayton is sunny today",             False, None),
        ("SELECT * FROM users WHERE active = true",          False, None),
        ("The confidence score is 0.85",                     False, None),
    ]

    passed = 0
    for text, should_detect, expected_type in tests:
        result    = guard.check(text, context="test")
        detected  = result["pii_detected"]
        ok        = detected == should_detect
        if ok: passed += 1
        icon      = "✅" if ok else "❌"
        types     = ", ".join(d["type"] for d in result["detections"]) or "none"
        print(f"  {icon} {'DETECTED' if detected else 'CLEAN':10s} [{types:20s}] {text[:45]}")

    print()
    print(f"  Result: {passed}/{len(tests)} tests pass")
    print()
    print(guard.session_report())
    Path("/tmp/test_pii_audit.jsonl").unlink(missing_ok=True)
