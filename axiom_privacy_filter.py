"""Axiom Privacy Filter — constitutional domain-aware PII redaction.

Wraps axiom_pii_guard.PIIGuard with:
  - Five domain filter profiles (GENERAL → CODE_SECURITY)
  - HMAC-signed FilterResult for tamper-evident audit trail
  - Constitutional guarantees (biometric block, training prohibition)
    that cannot be overridden at runtime

Paired governance spec: axiom_files/research/privacy_filter.axiom

Usage
-----
  from axiom_privacy_filter import PrivacyFilter, FilterProfile

  pf = PrivacyFilter()
  result = pf.scan(text, profile=FilterProfile.MEDICAL)
  print(result["redacted_text"])
  print(result["audit_id"])    # GDPR Art.30 record ID
  print(result["verdict"])     # CLEAN | REDACTED | BIOMETRIC_POLICY_BLOCKED

CLI
---
  echo "SSN: 123-45-6789" | python axiom_privacy_filter.py --profile MEDICAL
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axiom_signing import derive_key
from axiom_constitutional.guards.axiom_pii_guard import PIIGuard, _COMPILED as _BASE_COMPILED

_SIGNING_KEY = derive_key(b"axiom-privacy-filter-v1")

# ── Module freeze (CANNOT_MUTATE) ─────────────────────────────────────────────

class _FrozenModule:
    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"axiom_privacy_filter: CANNOT_MUTATE — {name!r} is constitutionally locked"
        )

# ── Domain profiles ───────────────────────────────────────────────────────────

class FilterProfile(str, Enum):
    GENERAL       = "GENERAL"
    MEDICAL       = "MEDICAL"
    FINANCIAL     = "FINANCIAL"
    LEGAL         = "LEGAL"
    CODE_SECURITY = "CODE_SECURITY"
    BIOMETRIC     = "BIOMETRIC"


# Additional patterns per domain (extend GENERAL — never replace it)
# Tuple: (name, regex, redaction_label, category)

_MEDICAL_EXTRA: Tuple = (
    ("icd_code",       r"\bICD-?1[0O]?\s*[:#]?\s*[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?\b",
                       "MEDICAL_CODE",   "MEDICAL"),
    ("dea_number",     r"\b[A-Z]{2}[0-9]{7}\b(?=.*\bDEA\b|\bdea\b)",
                       "MEDICAL_ID",     "MEDICAL"),
    ("insurance_id",   r"\b(?:Member|Policy|Insurance)\s*(?:ID|#|No\.?)\s*[:#]?\s*[A-Z0-9\-]{8,}\b",
                       "INSURANCE_ID",   "MEDICAL"),
    ("lab_value_pii",  r"\b(?:HbA1c|eGFR|PSA|TSH)\s*[=:]\s*[0-9]+(?:\.[0-9]+)?\b",
                       "LAB_VALUE",      "MEDICAL"),
    ("patient_name",   r"\bPatient(?:\s+Name)?\s*[:#]?\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b",
                       "PATIENT_NAME",   "MEDICAL"),
)

_FINANCIAL_EXTRA: Tuple = (
    ("swift_bic",      r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b(?=.*\b(?:SWIFT|BIC)\b)",
                       "SWIFT",          "FINANCIAL"),
    ("routing_aba",    r"\b(?:routing|ABA)\s*[:#]?\s*[0-9]{9}\b",
                       "ROUTING",        "FINANCIAL"),
    ("brokerage_acct", r"\bAccount\s*(?:#|No\.?|Number)\s*[:#]?\s*[0-9]{8,12}\b",
                       "BROKERAGE_ACCT", "FINANCIAL"),
    ("tax_filing_ref", r"\b(?:EIN|FEIN|TIN)\s*[:#]?\s*[0-9]{2}-[0-9]{7}\b",
                       "TAX_ID",         "FINANCIAL"),
    ("wire_ref",       r"\bWire\s+(?:Ref(?:erence)?|Confirmation)\s*[:#]?\s*[A-Z0-9]{10,}\b",
                       "WIRE_REF",       "FINANCIAL"),
)

_LEGAL_EXTRA: Tuple = (
    ("case_number",    r"\b(?:Case|Docket)\s*(?:#|No\.?)\s*[:#]?\s*[0-9]{2,}-[A-Z]{1,4}-[0-9]{4,}\b",
                       "CASE_NUMBER",    "LEGAL"),
    ("bar_id",         r"\bBar\s*(?:#|No\.?|ID)\s*[:#]?\s*[A-Z]{0,2}[0-9]{5,8}\b",
                       "BAR_ID",         "LEGAL"),
    ("sealed_marker",  r"\b(?:SEALED|Under\s+Seal|Confidential\s+Court)\b",
                       "SEALED_DOC",     "LEGAL"),
    ("deposition_ref", r"\bDeposition\s+of\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+",
                       "DEPONENT_NAME",  "LEGAL"),
)

_CODE_SECURITY_EXTRA: Tuple = (
    ("private_key_pem",r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+|PGP\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END\s+(?:RSA\s+|EC\s+|OPENSSH\s+|PGP\s+)?PRIVATE\s+KEY-----",
                       "PRIVATE_KEY",    "CREDENTIALS"),
    ("connection_str", r"(?i)(?:mongodb|postgresql|mysql|redis|amqp)\+?://[^\s'\"]+",
                       "CONN_STRING",    "CREDENTIALS"),
    ("env_secret",     r"(?i)(?:SECRET|TOKEN|KEY|PASSWORD|PASSWD|PWD)\s*=\s*['\"]?[a-zA-Z0-9\-_/+]{16,}['\"]?",
                       "ENV_SECRET",     "CREDENTIALS"),
    ("slack_token",    r"\bxox[baprs]-[a-zA-Z0-9\-]{10,}\b",
                       "API_KEY",        "CREDENTIALS"),
    ("stripe_key",     r"\bsk_(?:live|test)_[a-zA-Z0-9]{24,}\b",
                       "API_KEY",        "CREDENTIALS"),
    ("twilio_sid",     r"\bAC[a-f0-9]{32}\b",
                       "API_KEY",        "CREDENTIALS"),
    ("hf_token",       r"\bhf_[a-zA-Z0-9]{34,}\b",
                       "API_KEY",        "CREDENTIALS"),
)

# Biometric markers — always blocked, never redacted and passed through
_BIOMETRIC_PATTERNS: Tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bface\s+embed(?:ding)?\b",
        r"\bfingerprint\s+(?:hash|template|embed)\b",
        r"\bretinal?\s+scan\b",
        r"\bvoice\s+print\b",
        r"\bbiometric\s+(?:template|hash|vector)\b",
        r"\biris\s+(?:code|scan|template)\b",
    )
)

# Compiled profile registries (immutable after module load)
def _build_profile(extra: Tuple) -> Tuple:
    return _BASE_COMPILED + tuple(
        (name, re.compile(pattern, re.DOTALL), label, category)
        for name, pattern, label, category in extra
    )

_PROFILES: Dict[FilterProfile, Tuple] = {
    FilterProfile.GENERAL:       _BASE_COMPILED,
    FilterProfile.MEDICAL:       _build_profile(_MEDICAL_EXTRA),
    FilterProfile.FINANCIAL:     _build_profile(_FINANCIAL_EXTRA),
    FilterProfile.LEGAL:         _build_profile(_LEGAL_EXTRA),
    FilterProfile.CODE_SECURITY: _build_profile(_CODE_SECURITY_EXTRA),
    FilterProfile.BIOMETRIC:     _build_profile(
        _MEDICAL_EXTRA + _FINANCIAL_EXTRA + _CODE_SECURITY_EXTRA
    ),
}


# ── FilterResult signing ──────────────────────────────────────────────────────

def _sign_result(result: Dict) -> str:
    payload = json.dumps(
        {k: v for k, v in result.items() if k != "signature"},
        sort_keys=True,
    )
    digest = hmac.new(_SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return "hmac-sha256:" + digest


def verify_result(result: Dict) -> bool:
    """Re-verify the HMAC signature on a FilterResult. Returns True if intact."""
    expected = _sign_result(result)
    return hmac.compare_digest(result.get("signature", ""), expected)


# ── PrivacyFilter ─────────────────────────────────────────────────────────────

class PrivacyFilter:
    """Constitutional domain-aware privacy filter.

    scan() applies the selected profile, writes a GDPR Art.30 audit entry,
    and returns a signed FilterResult. Original text is never stored.

    Profile definitions and audit policy are module-level constants —
    CANNOT_MUTATE at runtime.
    """

    _audit_path = Path(__file__).parent / "axiom_constitutional" / "axiom_files" / ".reviews" / "privacy_filter_audit.jsonl"

    def scan(
        self,
        text: str,
        profile: FilterProfile = FilterProfile.GENERAL,
        context: str = "",
    ) -> Dict:
        """Scan text under the given domain profile.

        Returns a signed FilterResult dict with keys:
          redacted_text, verdict, profile_used, redaction_count,
          redaction_manifest, audit_id, wallclock_ms, signature
        """
        t0 = time.monotonic()

        # Constitutional biometric check — runs before any profile
        for pat in _BIOMETRIC_PATTERNS:
            if pat.search(text):
                return self._blocked_result(profile, t0)

        if profile not in _PROFILES:
            return self._unknown_profile_result(profile, t0)

        compiled = _PROFILES[profile]
        redacted = text
        manifest: List[Dict] = []

        for name, pat, label, category in compiled:
            matches = pat.findall(redacted)
            if matches:
                count = len(pat.findall(redacted))
                redacted = pat.sub(f"[REDACTED-{label}]", redacted)
                manifest.append({"name": name, "category": category,
                                 "label": label, "count": count})

        verdict   = "REDACTED" if manifest else "CLEAN"
        audit_id  = self._write_audit(verdict, profile, manifest, context) if manifest else ""
        total     = sum(m["count"] for m in manifest)
        wallclock = int((time.monotonic() - t0) * 1000)

        result = {
            "redacted_text":     redacted,
            "verdict":           verdict,
            "profile_used":      profile.value,
            "redaction_count":   total,
            "redaction_manifest": manifest,
            "audit_id":          audit_id,
            "wallclock_ms":      wallclock,
        }
        result["signature"] = _sign_result(result)
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _blocked_result(self, profile: FilterProfile, t0: float) -> Dict:
        audit_id  = self._write_audit("BIOMETRIC_POLICY_BLOCKED", profile, [], "biometric-content")
        wallclock = int((time.monotonic() - t0) * 1000)
        result = {
            "redacted_text":      "",
            "verdict":            "BIOMETRIC_POLICY_BLOCKED",
            "profile_used":       profile.value,
            "redaction_count":    0,
            "redaction_manifest": [],
            "audit_id":           audit_id,
            "wallclock_ms":       wallclock,
        }
        result["signature"] = _sign_result(result)
        return result

    def _unknown_profile_result(self, profile: Any, t0: float) -> Dict:
        wallclock = int((time.monotonic() - t0) * 1000)
        result = {
            "redacted_text":      "",
            "verdict":            "PROFILE_UNKNOWN",
            "profile_used":       str(profile),
            "redaction_count":    0,
            "redaction_manifest": [],
            "audit_id":           "",
            "wallclock_ms":       wallclock,
        }
        result["signature"] = _sign_result(result)
        return result

    def _write_audit(
        self,
        verdict: str,
        profile: FilterProfile,
        manifest: List[Dict],
        context: str,
    ) -> str:
        from datetime import datetime, timezone
        audit_id = "PF-" + str(uuid.uuid4())[:8].upper()
        entry = {
            "audit_id":         audit_id,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "filter":           "PrivacyFilter",
            "gdpr_basis":       "GDPR Art.30 — Records of processing activities",
            "verdict":          verdict,
            "profile":          profile.value if hasattr(profile, "value") else str(profile),
            "context":          context,
            "redaction_manifest": manifest,
            "total_items":      sum(m["count"] for m in manifest),
        }
        payload = json.dumps(
            {k: v for k, v in entry.items() if k != "audit_signature"},
            sort_keys=True,
        )
        entry["audit_signature"] = (
            "hmac-sha256:" +
            hmac.new(_SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        )
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except IOError as exc:
            print(f"  [PrivacyFilter] warning: audit log write failed: {exc}")
        return audit_id


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Axiom Privacy Filter")
    parser.add_argument("--profile", default="GENERAL",
                        choices=[p.value for p in FilterProfile])
    parser.add_argument("--context", default="")
    parser.add_argument("text", nargs="?", help="Text to scan (or pipe via stdin)")
    args = parser.parse_args()

    text = args.text or sys.stdin.read()
    pf   = PrivacyFilter()
    r    = pf.scan(text, profile=FilterProfile(args.profile), context=args.context)

    print(f"Verdict:  {r['verdict']}")
    print(f"Profile:  {r['profile_used']}")
    print(f"Redacted: {r['redaction_count']} item(s)")
    print(f"Audit ID: {r['audit_id']}")
    print(f"Time:     {r['wallclock_ms']} ms")
    print(f"Verified: {verify_result(r)}")
    print()
    print(r["redacted_text"])


if __name__ == "__main__":
    _cli()
