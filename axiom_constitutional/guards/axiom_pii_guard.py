"""
AXIOM PII Guard
================
Constitutional runtime guard: detects and redacts Personally Identifiable
Information (PII) from LLM output before it reaches the caller.

OWASP LLM Top 10 — LLM06: Sensitive Information Disclosure
GDPR Article 30 — Records of processing activities (audit log)

Behaviour: REDACT, not block.
  - PII is replaced with [REDACTED-<TYPE>] in-place
  - Caller receives the redacted text, not a block message
  - Every redaction is written to pii_audit.jsonl with HMAC-SHA256 signature
  - The pattern registry is a module-level constant — CANNOT_MUTATE

30 patterns across 6 categories:
  CREDENTIALS  — API keys, passwords, private keys, tokens, JWT
  IDENTITY     — SSN, passport, driver licence, tax ID
  FINANCIAL    — credit cards, IBAN, routing number, crypto addresses
  CONTACT      — email, phone
  NETWORK      — private IP addresses
  MEDICAL      — NPI, MRN, DOB

Run standalone for tests:
  python axiom_constitutional/axiom_pii_guard.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Constants — module-level, CANNOT_MUTATE ───────────────────────────────────

_SIGNING_KEY = b"axiom-pii-guard-v1"
_PII_AUDIT   = (
    Path(__file__).resolve().parents[1] / "axiom_files" / ".reviews" / "pii_audit.jsonl"
)

# ── PII pattern registry ──────────────────────────────────────────────────────
# Tuple of (name, regex, redaction_label, category) — immutable at module level.
# Ordered from most-specific to least-specific to avoid partial overlaps.

_PII_PATTERNS: Tuple[Tuple[str, str, str, str], ...] = (
    # ── CREDENTIALS ───────────────────────────────────────────────────────────
    ("private_key_block",  r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+|PGP\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END\s+(?:RSA\s+|EC\s+|OPENSSH\s+|PGP\s+)?PRIVATE\s+KEY-----",
                           "PRIVATE_KEY",  "CREDENTIALS"),
    ("anthropic_key",      r"\bsk-ant-(?:api\d+-)?[a-zA-Z0-9\-_]{20,}\b",
                           "API_KEY",      "CREDENTIALS"),
    ("openai_key",         r"\bsk-[a-zA-Z0-9]{32,}\b",
                           "API_KEY",      "CREDENTIALS"),
    ("aws_access_key",     r"\bAKIA[0-9A-Z]{16}\b",
                           "API_KEY",      "CREDENTIALS"),
    ("aws_secret_key",     r"(?i)aws[_\-\s]*secret[_\-\s]*(?:access[_\-\s]*)?key\s*[=:]\s*['\"]?([a-zA-Z0-9/+]{40})['\"]?",
                           "API_KEY",      "CREDENTIALS"),
    ("github_pat",         r"\bghp_[a-zA-Z0-9]{36}\b",
                           "API_KEY",      "CREDENTIALS"),
    ("github_fine_token",  r"\bgithub_pat_[a-zA-Z0-9_]{82}\b",
                           "API_KEY",      "CREDENTIALS"),
    ("gcp_api_key",        r"\bAIza[0-9A-Za-z\-_]{35}\b",
                           "API_KEY",      "CREDENTIALS"),
    ("jwt_token",          r"\beyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\b",
                           "JWT",          "CREDENTIALS"),
    ("bearer_token",       r"\bBearer\s+[a-zA-Z0-9\-._~+/=]{20,}\b",
                           "TOKEN",        "CREDENTIALS"),
    ("password_assign",    r"(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"]?([^\s'\"]{6,})['\"]?",
                           "PASSWORD",     "CREDENTIALS"),
    ("api_key_assign",     r"(?i)(?:api[_\-]?key|api_secret|secret[_\-]?key)\s*[=:]\s*['\"]?([a-zA-Z0-9\-_]{16,})['\"]?",
                           "API_KEY",      "CREDENTIALS"),

    # ── IDENTITY ──────────────────────────────────────────────────────────────
    ("ssn_dashed",         r"\b\d{3}-\d{2}-\d{4}\b",
                           "SSN",          "IDENTITY"),
    ("passport_us",        r"\b(?:passport\s*[:#]?\s*)?[A-Z]{1,2}[0-9]{6,9}\b(?=.*\bpassport\b|\bPASSPORT\b)",
                           "PASSPORT",     "IDENTITY"),
    ("drivers_license",    r"\bDL\s*[:#]?\s*[A-Z][0-9]{7}\b",
                           "DL",           "IDENTITY"),
    ("tax_id_ein",         r"\b\d{2}-\d{7}\b",
                           "TAX_ID",       "IDENTITY"),

    # ── FINANCIAL ─────────────────────────────────────────────────────────────
    ("card_visa",          r"\b4[0-9]{12}(?:[0-9]{3})?\b",
                           "CARD",         "FINANCIAL"),
    ("card_mastercard",    r"\b5[1-5][0-9]{14}\b",
                           "CARD",         "FINANCIAL"),
    ("card_amex",          r"\b3[47][0-9]{13}\b",
                           "CARD",         "FINANCIAL"),
    ("card_discover",      r"\b6(?:011|5[0-9]{2})[0-9]{12}\b",
                           "CARD",         "FINANCIAL"),
    ("iban",               r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{4}[0-9]{7}(?:[A-Z0-9]{0,16})\b",
                           "IBAN",         "FINANCIAL"),
    ("crypto_btc",         r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b",
                           "CRYPTO_ADDR",  "FINANCIAL"),
    ("crypto_eth",         r"\b0x[a-fA-F0-9]{40}\b",
                           "CRYPTO_ADDR",  "FINANCIAL"),

    # ── MEDICAL — before CONTACT so NPI digits don't match phone ─────────────
    ("npi_number",         r"\bNPI\s*[:#]?\s*[0-9]{10}\b",
                           "MEDICAL_ID",   "MEDICAL"),
    ("mrn_number",         r"\bMRN\s*[:#]?\s*[A-Z0-9\-]{6,}\b",
                           "MEDICAL_ID",   "MEDICAL"),
    ("date_of_birth",      r"\bDOB\s*[:#]?\s*[0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{2,4}\b",
                           "DOB",          "MEDICAL"),

    # ── CONTACT ───────────────────────────────────────────────────────────────
    ("email_address",      r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b",
                           "EMAIL",        "CONTACT"),
    ("phone_us",           r"\b(?:\+1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b",
                           "PHONE",        "CONTACT"),

    # ── NETWORK ───────────────────────────────────────────────────────────────
    ("ip_private",         r"\b(?:10\.[0-9]{1,3}|172\.(?:1[6-9]|2[0-9]|3[01])|192\.168)\.[0-9]{1,3}\.[0-9]{1,3}\b",
                           "IP_PRIVATE",   "NETWORK"),
)

# Pre-compiled once at import time
_COMPILED: Tuple[Tuple[str, re.Pattern, str, str], ...] = tuple(  # type: ignore[type-arg]
    (name, re.compile(pattern, re.DOTALL), label, category)
    for name, pattern, label, category in _PII_PATTERNS
)


# ── Guard ─────────────────────────────────────────────────────────────────────

class PIIGuard:
    """
    Constitutional PII guard wired into validate_output().

    scan() is the primary public method. It:
      1. Scans text for all registered PII patterns
      2. Replaces each match with [REDACTED-<TYPE>]
      3. Writes a GDPR Art.30 audit entry to pii_audit.jsonl
      4. Returns the redacted text and a redactions manifest

    Redacts rather than blocks — the caller still receives a response,
    just with sensitive data removed.
    """

    def scan(self, text: str, context: str = "") -> Dict:
        """
        Scan and redact PII from text.

        Returns:
            {
                "redacted_text": str,
                "pii_found":     bool,
                "redaction_count": int,
                "redactions":    list of {name, category, label, count},
                "audit_id":      str,   # non-empty if PII was found
            }
        """
        redacted = text
        found: List[Dict] = []

        for name, compiled, label, category in _COMPILED:
            matches = compiled.findall(redacted)
            if matches:
                count = len(compiled.findall(redacted))
                redacted = compiled.sub("[REDACTED-%s]" % label, redacted)
                found.append({"name": name, "category": category,
                              "label": label, "count": count})

        if not found:
            return {
                "redacted_text":   text,
                "pii_found":       False,
                "redaction_count": 0,
                "redactions":      [],
                "audit_id":        "",
            }

        audit_id = self._write_audit(found, context)
        total = sum(r["count"] for r in found)
        categories = sorted({r["category"] for r in found})
        print(
            "  [PIIGuard] REDACTED %d item(s) across %s  audit=%s"
            % (total, categories, audit_id)
        )
        return {
            "redacted_text":   redacted,
            "pii_found":       True,
            "redaction_count": total,
            "redactions":      found,
            "audit_id":        audit_id,
        }

    # ── Audit log (GDPR Art.30) ───────────────────────────────────────────────

    def _write_audit(self, redactions: List[Dict], context: str) -> str:
        audit_id = "PII-" + str(uuid.uuid4())[:8].upper()
        entry = {
            "audit_id":     audit_id,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "guard":        "PIIGuard",
            "gdpr_basis":   "GDPR Art.30 — Records of processing activities",
            "context":      context,
            "redactions":   redactions,
            "total_items":  sum(r["count"] for r in redactions),
        }
        entry["signature"] = self._sign(entry)
        try:
            _PII_AUDIT.parent.mkdir(parents=True, exist_ok=True)
            with open(_PII_AUDIT, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except IOError as exc:
            print("  [PIIGuard] warning: could not write audit log: %s" % exc)
        return audit_id

    @staticmethod
    def _sign(entry: Dict) -> str:
        payload = json.dumps(
            {k: v for k, v in entry.items() if k != "signature"},
            sort_keys=True,
        )
        digest = hmac.new(_SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return "hmac-sha256:" + digest[:32] + "..."


# ── Module-level singleton ────────────────────────────────────────────────────

_guard = PIIGuard()


def scan(text: str, context: str = "") -> Dict:
    """Module-level shortcut: axiom_pii_guard.scan(text)."""
    return _guard.scan(text, context=context)


# ── Standalone test runner (11 cases) ────────────────────────────────────────

if __name__ == "__main__":
    TESTS = [
        # (description,             text,                                                          expect_redact, expected_label)
        ("SSN",                     "Patient SSN: 123-45-6789 admitted today.",                    True,  "SSN"),
        ("Credit card Visa",        "Card ending 4111111111111111 charged.",                       True,  "CARD"),
        ("API key Anthropic",       "Key: sk-ant-api03-abc123def456ghi789jkl012mno",               True,  "API_KEY"),
        ("AWS access key",          "AKIAIOSFODNN7EXAMPLE was found in logs.",                     True,  "API_KEY"),
        ("Password assignment",     "password=SuperSecret99!",                                     True,  "PASSWORD"),
        ("Private key block",       "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----", True, "PRIVATE_KEY"),
        ("Email address",           "Contact billing@example.com for invoices.",                   True,  "EMAIL"),
        ("US phone number",         "Call us at 555-867-5309.",                                    True,  "PHONE"),
        ("NPI medical",             "Provider NPI: 1234567890 signed the order.",                  True,  "MEDICAL_ID"),
        ("Ethereum address",        "Send to 0xAbCdEf1234567890AbCdEf1234567890AbCdEf12",          True,  "CRYPTO_ADDR"),
        # Clean pass-through
        ("safe text",               "The 2024 Q3 earnings exceeded projections by 8%.",            False, ""),
        ("safe technical",          "Set timeout=30 and retry_count=3 in config.",                 False, ""),
    ]

    passed = failed = 0
    guard = PIIGuard()

    print("\nAXIOM PIIGuard — test suite")
    print("=" * 62)

    for desc, text, expect_redact, expect_label in TESTS:
        result = guard.scan(text, context="test")
        got_redact = result["pii_found"]
        ok = got_redact == expect_redact

        if ok and expect_redact:
            labels = [r["label"] for r in result["redactions"]]
            ok = expect_label in labels

        status = "PASS" if ok else "FAIL"
        flag = ("REDACTED " if got_redact else "ALLOWED  ")
        found_labels = [r["label"] for r in result["redactions"]] if got_redact else []
        print(
            "  [%s] %-28s %s  %s"
            % (status, desc[:28], flag, ",".join(found_labels))
        )
        if ok:
            passed += 1
        else:
            failed += 1

    print("=" * 62)
    print("  %d/%d tests passed" % (passed, len(TESTS)))
    if failed == 0:
        print("  ALL PASS")
    else:
        print("  %d FAILED" % failed)
        raise SystemExit(1)
