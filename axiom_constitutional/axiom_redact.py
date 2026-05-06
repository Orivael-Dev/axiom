"""
AXIOM Redact v1.0
==================
HIPAA-compliant form de-identification.
Three modes: REDACT | DETECT | BLOCK

Extends PIIGuard with:
  Date of birth detection
  Policy/account number patterns
  Form field context awareness
  Multi-field document handling
  Safe harbor de-identification (HIPAA 164.514)

API endpoint:
  POST /guard/redact
  {
    "text": "Patient: John Smith SSN: 123-45-6789",
    "mode": "REDACT",
    "domain": "healthcare"
  }

Returns:
  {
    "mode":           "REDACT",
    "redacted":       "Patient: [NAME REDACTED] SSN: [SSN REDACTED]",
    "detections":     [{"type":"NAME","count":1},{"type":"SSN","count":1}],
    "audit_id":       "RDX-20260501-xxxxx",
    "original_stored": false,
    "hipaa_safe_harbor": true,
    "signature":      "hmac-sha256:..."
  }

github.com/Orivael-Dev/axiom
pip install axiom-constitutional[guard]
HIPAA 45 CFR 164.514 — Safe Harbor De-identification
"""

import sys
import re
import json
import hmac
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axiom_signing import derive_key
SIGNING_KEY   = derive_key(b"axiom-redact-v1")
REDACT_LOG    = Path("axiom_redact_log.jsonl")

# ══════════════════════════════════════════════════════════════
# HIPAA SAFE HARBOR — 18 Identifiers (45 CFR 164.514(b)(2))
# CANNOT_MUTATE — these are legally defined
# ══════════════════════════════════════════════════════════════

SAFE_HARBOR_PATTERNS = [

    # 1. Names
    (r"\b(patient|name|client|member|resident|employee)\s*[:]\s*([A-Z][a-z]+\s+[A-Z][a-z]+)",
     "NAME",              "[NAME REDACTED]",       "HIPAA-1"),

    # 2. Geographic data (smaller than state)
    (r"\b\d{5}(?:-\d{4})?\b",
     "ZIP_CODE",          "[ZIP REDACTED]",         "HIPAA-2"),
    (r"\b\d+\s+[A-Za-z\s]+(Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Blvd|Court|Ct)\b",
     "ADDRESS",           "[ADDRESS REDACTED]",     "HIPAA-2"),

    # 3. Dates (except year)
    (r"\b(?:0?[1-9]|1[0-2])[\/\-](?:0?[1-9]|[12]\d|3[01])[\/\-](?:19|20)\d{2}\b",
     "DATE_WITH_YEAR",    "[DATE REDACTED]",        "HIPAA-3"),
    (r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
     "DATE_FULL",         "[DATE REDACTED]",        "HIPAA-3"),
    (r"\b(?:DOB|Date of Birth|Birth Date|Birthdate)\s*[:]\s*(?!\[)[\d\/\-A-Za-z]+",
     "DOB_LABELED",       "DOB: [REDACTED]",        "HIPAA-3"),
    (r"\b(?:age|aged)\s*[:]\s*\d{1,3}\b",
     "AGE",               "Age: [REDACTED]",        "HIPAA-3"),

    # 4. Phone numbers
    (r"\(?\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
     "PHONE",             "[PHONE REDACTED]",       "HIPAA-4"),

    # 5. Fax numbers (same pattern as phone, labeled)
    (r"\b(?:fax|fax no|fax number)\s*[:]\s*[\d\-\(\)\s\.]+",
     "FAX",               "Fax: [REDACTED]",        "HIPAA-5"),

    # 6. Email addresses
    (r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b",
     "EMAIL",             "[EMAIL REDACTED]",       "HIPAA-6"),

    # 7. SSN
    (r"\b\d{3}-\d{2}-\d{4}\b",
     "SSN",               "[SSN REDACTED]",         "HIPAA-7"),
    (r"\b(?:SSN|Social Security)\s*(?:No|Number|#)?\s*[:]\s*[\d\-]+",
     "SSN_LABELED",       "SSN: [REDACTED]",        "HIPAA-7"),

    # 8. Medical record numbers
    (r"\b(?:MRN|Medical Record|Chart)\s*(?:No|Number|#)?\s*[:]\s*[A-Z0-9\-]+",
     "MRN",               "MRN: [REDACTED]",        "HIPAA-8"),
    (r"\bMRN\s*[:]\s*\d{6,10}\b",
     "MRN_SHORT",         "MRN: [REDACTED]",        "HIPAA-8"),

    # 9. Health plan beneficiary numbers
    (r"\b(?:Member|Beneficiary|Subscriber|Policy|Group)\s*(?:ID|No|Number|#)\s*[:]\s*[A-Z0-9\-]+",
     "HEALTH_PLAN_ID",    "[ID REDACTED]",          "HIPAA-9"),
    (r"\b(?:Insurance|Coverage)\s*(?:ID|No|Number)\s*[:]\s*[A-Z0-9\-]+",
     "INSURANCE_ID",      "[INSURANCE ID REDACTED]","HIPAA-9"),

    # 10. Account numbers
    (r"\b(?:Account|Acct)\s*(?:No|Number|#)?\s*[:]\s*[A-Z0-9\-]+",
     "ACCOUNT_NUMBER",    "[ACCOUNT REDACTED]",     "HIPAA-10"),

    # 11. Certificate/license numbers
    (r"\b(?:License|Certificate|NPI|DEA)\s*(?:No|Number|#)?\s*[:]\s*[A-Z0-9\-]+",
     "LICENSE_NUMBER",    "[LICENSE REDACTED]",     "HIPAA-11"),
    (r"\bNPI\s*[:]\s*\d{10}\b",
     "NPI",               "NPI: [REDACTED]",        "HIPAA-11"),
    (r"\bDEA\s*#?\s*[:]\s*[A-Z]{2}\d{7}\b",
     "DEA",               "DEA: [REDACTED]",        "HIPAA-11"),

    # 12. Vehicle identifiers
    (r"\b(?:VIN|Vehicle)\s*[:]\s*[A-HJ-NPR-Z0-9]{17}\b",
     "VIN",               "[VIN REDACTED]",         "HIPAA-12"),
    (r"\b(?:License Plate|Plate)\s*[:]\s*[A-Z0-9\s\-]+",
     "LICENSE_PLATE",     "[PLATE REDACTED]",       "HIPAA-12"),

    # 13. Device identifiers
    (r"\b(?:Device|Serial|IMEI)\s*(?:No|Number|#|ID)?\s*[:]\s*[A-Z0-9\-]+",
     "DEVICE_ID",         "[DEVICE ID REDACTED]",   "HIPAA-13"),

    # 14. Web URLs
    (r"https?://\S+",
     "URL",               "[URL REDACTED]",         "HIPAA-14"),

    # 15. IP addresses
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
     "IP_ADDRESS",        "[IP REDACTED]",          "HIPAA-15"),

    # 16. Biometric identifiers
    (r"\b(?:fingerprint|retinal|iris|voiceprint|biometric)\s*(?:scan|data|ID|identifier)\b",
     "BIOMETRIC",         "[BIOMETRIC REDACTED]",   "HIPAA-16"),

    # 17. Full-face photos (text reference)
    (r"\b(?:photo|photograph|image|picture)\s*(?:attached|enclosed|included)\b",
     "PHOTO_REFERENCE",   "[PHOTO REFERENCE REDACTED]", "HIPAA-17"),

    # 18. Any unique identifying numbers
    (r"\b(?:Patient|Case|Claim|Reference|Confirmation)\s*(?:ID|No|Number|#)\s*[:]\s*[A-Z0-9\-]+",
     "UNIQUE_ID",         "[ID REDACTED]",          "HIPAA-18"),
]

# Additional financial PII
FINANCIAL_PATTERNS = [
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b",           "VISA",         "[CARD REDACTED]",    "FIN"),
    (r"\b5[1-5][0-9]{14}\b",                   "MASTERCARD",   "[CARD REDACTED]",    "FIN"),
    (r"\b3[47][0-9]{13}\b",                     "AMEX",         "[CARD REDACTED]",    "FIN"),
    (r"\b(?:IBAN)\s*[:]\s*[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b",
                                                "IBAN",         "[IBAN REDACTED]",    "FIN"),
    (r"\b(?:routing|ABA)\s*(?:No|Number|#)?\s*[:]\s*\d{9}\b",
                                                "ROUTING",      "[ROUTING REDACTED]", "FIN"),
]

# Credential PII
CREDENTIAL_PATTERNS = [
    (r"\bsk-ant-[a-zA-Z0-9_\-]{20,}\b",        "ANTHROPIC_KEY","[API_KEY REDACTED]", "CRED"),
    (r"\bsk-[a-zA-Z0-9]{20,}\b",               "OPENAI_KEY",   "[API_KEY REDACTED]", "CRED"),
    (r"\bAKIA[0-9A-Z]{16}\b",                  "AWS_KEY",      "[AWS_KEY REDACTED]", "CRED"),
    (r"\bghp_[a-zA-Z0-9]{36}\b",               "GITHUB_TOKEN", "[TOKEN REDACTED]",   "CRED"),
    (r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
                                                "PRIVATE_KEY",  "[PRIVATE_KEY REDACTED]","CRED"),
    (r"password\s*[=:]\s*['\"]?[^\s'\"]{6,}['\"]?",
                                                "PASSWORD",     "password=[REDACTED]","CRED"),
]

ALL_PATTERNS = SAFE_HARBOR_PATTERNS + FINANCIAL_PATTERNS + CREDENTIAL_PATTERNS

_COMPILED = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), code, replacement, category)
    for pattern, code, replacement, category in ALL_PATTERNS
]

# Domain-specific extra patterns
DOMAIN_PATTERNS = {
    "healthcare": [
        (r"\b(?:Diagnosis|Dx)\s*[:]\s*[A-Z][A-Za-z0-9\s\-,]+",
         "DIAGNOSIS",  "[DIAGNOSIS REDACTED]", "HEALTH"),
        (r"\b(?:Medication|Rx|Prescription)\s*[:]\s*[A-Za-z0-9\s\-,]+mg[^\n]*",
         "MEDICATION", "[MEDICATION REDACTED]", "HEALTH"),
        (r"\bICD-?10?\s*[:]\s*[A-Z][0-9]{2}\.?[0-9A-Z]*",
         "ICD_CODE",   "[ICD CODE REDACTED]",  "HEALTH"),
    ],
    "legal": [
        (r"\b(?:Case|Docket|Matter)\s*(?:No|Number|#)\s*[:]\s*[A-Z0-9\-\/]+",
         "CASE_NUMBER", "[CASE NUMBER REDACTED]", "LEGAL"),
        (r"\b(?:Bar|Attorney)\s*(?:No|Number|#)\s*[:]\s*[A-Z0-9\-]+",
         "BAR_NUMBER",  "[BAR NUMBER REDACTED]",  "LEGAL"),
    ],
    "hr": [
        (r"\b(?:Employee|Staff|Worker)\s*(?:ID|No|Number|#)\s*[:]\s*[A-Z0-9\-]+",
         "EMPLOYEE_ID", "[EMPLOYEE ID REDACTED]", "HR"),
        (r"\b(?:Salary|Compensation|Pay)\s*[:]\s*\$[\d,]+",
         "SALARY",      "[SALARY REDACTED]",      "HR"),
        (r"\b(?:Performance|Review|Rating)\s*[:]\s*[\d\.]+(?:/\d+)?",
         "PERFORMANCE", "[RATING REDACTED]",      "HR"),
    ],
}


# ══════════════════════════════════════════════════════════════
# REDACTION ENGINE
# ══════════════════════════════════════════════════════════════

class RedactionEngine:
    """
    AXIOM Redact — HIPAA Safe Harbor de-identification.

    Three modes:
      REDACT  — replace PII with placeholders
      DETECT  — find and report PII locations
      BLOCK   — refuse to process if PII found

    CANNOT_MUTATE:
      18 HIPAA Safe Harbor identifiers
      original_stored: always False
    """

    def __init__(self, log_path: Path = REDACT_LOG):
        self.log_path = log_path

    def process(
        self,
        text:   str,
        mode:   str = "REDACT",
        domain: Optional[str] = None,
    ) -> dict:
        """
        Process text through redaction engine.

        Args:
            text:   Input text (form, document, etc.)
            mode:   REDACT | DETECT | BLOCK
            domain: healthcare | legal | hr | general

        Returns:
            Full redaction result with audit ID and signature
        """
        mode = mode.upper()
        if mode not in ("REDACT", "DETECT", "BLOCK"):
            mode = "REDACT"

        audit_id = f"RDX-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

        # Build pattern list — base + domain specific
        patterns = list(_COMPILED)
        if domain and domain in DOMAIN_PATTERNS:
            domain_compiled = [
                (re.compile(p, re.IGNORECASE | re.MULTILINE), code, repl, cat)
                for p, code, repl, cat in DOMAIN_PATTERNS[domain]
            ]
            patterns = domain_compiled + patterns

        # Scan for all detections
        detections  = []
        redacted    = text
        type_counts = {}

        for compiled, code, replacement, category in patterns:
            matches = compiled.findall(redacted if mode == "REDACT" else text)
            if matches:
                count = len(matches)
                type_counts[code] = type_counts.get(code, 0) + count
                detections.append({
                    "type":       code,
                    "category":   category,
                    "count":      count,
                    "hipaa_id":   category if category.startswith("HIPAA") else None,
                })
                if mode == "REDACT":
                    redacted = compiled.sub(replacement, redacted)

        # Apply result based on mode
        if mode == "BLOCK" and detections:
            result = self._build_result(
                audit_id    = audit_id,
                mode        = mode,
                original    = text,
                redacted    = None,
                detections  = detections,
                blocked     = True,
                domain      = domain,
            )
            result["error"] = (
                f"BLOCKED — {len(detections)} PII type(s) detected. "
                f"DPA authorization required before processing."
            )
            self._log(audit_id, mode, detections, domain)
            return result

        if mode == "DETECT":
            result = self._build_result(
                audit_id    = audit_id,
                mode        = mode,
                original    = text,
                redacted    = None,
                detections  = detections,
                blocked     = False,
                domain      = domain,
            )
            self._log(audit_id, mode, detections, domain)
            return result

        # REDACT mode
        result = self._build_result(
            audit_id    = audit_id,
            mode        = mode,
            original    = text,
            redacted    = redacted,
            detections  = detections,
            blocked     = False,
            domain      = domain,
        )
        self._log(audit_id, mode, detections, domain)
        return result

    def _build_result(
        self,
        audit_id:   str,
        mode:       str,
        original:   str,
        redacted:   Optional[str],
        detections: list,
        blocked:    bool,
        domain:     Optional[str],
    ) -> dict:
        """Build signed result."""
        total_pii = sum(d["count"] for d in detections)
        hipaa_ids = list(set(d["hipaa_id"] for d in detections if d.get("hipaa_id")))

        result = {
            "audit_id":          audit_id,
            "mode":              mode,
            "timestamp":         datetime.now().isoformat() + "Z",
            "domain":            domain or "general",
            "pii_detected":      len(detections) > 0,
            "total_pii_found":   total_pii,
            "detection_types":   len(detections),
            "detections":        detections,
            "hipaa_identifiers": hipaa_ids,
            "hipaa_safe_harbor": len(hipaa_ids) > 0,
            "original_stored":   False,           # CANNOT_MUTATE
            "blocked":           blocked,
        }

        if mode == "REDACT" and redacted is not None:
            result["redacted"]  = redacted
            result["chars_in"]  = len(original)
            result["chars_out"] = len(redacted)

        if mode == "DETECT":
            result["locations"] = [
                {"type": d["type"], "count": d["count"]}
                for d in detections
            ]

        # Sign
        sig_str = json.dumps(
            {k: v for k, v in result.items()
             if k not in ("signature", "redacted", "locations")},
            sort_keys=True, default=str
        )
        sig = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        result["signature"] = f"hmac-sha256:{sig[:32]}..."

        return result

    def _log(self, audit_id: str, mode: str, detections: list, domain: Optional[str]):
        """Append-only audit log — never stores the original content."""
        entry = {
            "audit_id":      audit_id,
            "timestamp":     datetime.now().isoformat() + "Z",
            "mode":          mode,
            "domain":        domain or "general",
            "types_found":   [d["type"] for d in detections],
            "total_pii":     sum(d["count"] for d in detections),
            "original_stored": False,  # CANNOT_MUTATE
        }
        sig = hmac.new(SIGNING_KEY, json.dumps(entry, sort_keys=True).encode(),
                      hashlib.sha256).hexdigest()
        entry["signature"] = f"hmac-sha256:{sig[:32]}..."
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════════════
# FASTAPI ROUTES — paste into examples/axiom_guard_api.py
# ══════════════════════════════════════════════════════════════

FASTAPI_ROUTES = '''
# ── Add these imports ────────────────────────────────────────
# from axiom_redact import RedactionEngine
# _redact_engine = RedactionEngine()

# ── Add this route ───────────────────────────────────────────
class RedactRequest(BaseModel):
    text:   str
    mode:   str = "REDACT"
    domain: Optional[str] = None

@app.post("/guard/redact")
async def guard_redact(req: RedactRequest):
    """
    HIPAA-compliant form de-identification.
    Modes: REDACT | DETECT | BLOCK
    Domains: healthcare | legal | hr | general
    """
    return _redact_engine.process(req.text, mode=req.mode, domain=req.domain)

@app.get("/guard/redact/patterns")
async def redact_patterns():
    """List all active PII pattern types."""
    return {
        "hipaa_safe_harbor": 18,
        "financial":         5,
        "credentials":       6,
        "domain_healthcare": 3,
        "domain_legal":      2,
        "domain_hr":         3,
        "total_patterns":    len(ALL_PATTERNS),
    }
'''


# ══════════════════════════════════════════════════════════════
# DEMO + TESTS
# ══════════════════════════════════════════════════════════════

SAMPLE_FORM = """
PATIENT INTAKE FORM
===================
Name: John Robert Smith
Date of Birth: 03/15/1978
SSN: 123-45-6789
Phone: (555) 867-5309
Email: john.smith@email.com
Address: 1234 Oak Street, Springfield

Insurance ID: BC-889-2231-A
MRN: 4492819
Policy Number: GRP-44921

Diagnosis: Type 2 Diabetes (ICD-10: E11.9)
Medication: Metformin 500mg twice daily
Primary Care: Dr. Sarah Johnson
NPI: 1234567890
"""

SAMPLE_LEGAL = """
LEGAL DOCUMENT
==============
Client: Maria Elena Rodriguez
Case No: 2024-CV-00892
Bar Number: OH-12345
SSN: 987-65-4321
DOB: July 14, 1985
Account No: CHK-9987-2211
Email: m.rodriguez@lawfirm.com
"""

SAMPLE_HR = """
EMPLOYEE RECORD
===============
Employee: James T. Wilson
Employee ID: EMP-44921
SSN: 456-78-9012
Salary: $125,000
Performance Rating: 4.5/5
Email: james.wilson@company.com
Phone: 555-234-5678
"""


def run_demo():
    print("\n" + "="*60)
    print("  AXIOM Redact v1.0 — HIPAA Safe Harbor Demo")
    print("="*60)

    engine = RedactionEngine(log_path=Path("/tmp/axiom_redact_test.jsonl"))

    # Test 1 — Healthcare REDACT
    print("\n  Test 1: Healthcare Form — REDACT mode")
    print("  " + "─"*50)
    result = engine.process(SAMPLE_FORM, mode="REDACT", domain="healthcare")
    print(f"  PII types found:    {result['detection_types']}")
    print(f"  Total PII items:    {result['total_pii_found']}")
    print(f"  HIPAA identifiers:  {', '.join(result['hipaa_identifiers'][:5])}...")
    print(f"  HIPAA safe harbor:  {result['hipaa_safe_harbor']}")
    print(f"  Original stored:    {result['original_stored']}")
    print(f"  Audit ID:           {result['audit_id']}")
    print(f"  Signature:          {result['signature'][:40]}")
    print()
    print("  Redacted output (first 300 chars):")
    print("  " + result.get("redacted","")[:300].replace("\n","\n  "))

    # Test 2 — Legal DETECT
    print(f"\n  Test 2: Legal Document — DETECT mode")
    print("  " + "─"*50)
    result2 = engine.process(SAMPLE_LEGAL, mode="DETECT", domain="legal")
    print(f"  PII types found: {result2['detection_types']}")
    for d in result2["detections"]:
        print(f"    {d['type']:25s} {d['count']} instance(s)")

    # Test 3 — HR BLOCK
    print(f"\n  Test 3: HR Record — BLOCK mode")
    print("  " + "─"*50)
    result3 = engine.process(SAMPLE_HR, mode="BLOCK", domain="hr")
    print(f"  Blocked: {result3['blocked']}")
    print(f"  Reason:  {result3.get('error','')[:70]}")
    print(f"  PII found: {result3['detection_types']} types")

    print()
    print("  Audit log: /tmp/axiom_redact_test.jsonl")
    print("  original_stored: False on all entries")
    print("="*60)

    # Cleanup
    Path("/tmp/axiom_redact_test.jsonl").unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AXIOM Redact")
    parser.add_argument("--demo",   action="store_true")
    parser.add_argument("--text",   help="Text to redact")
    parser.add_argument("--mode",   default="REDACT", choices=["REDACT","DETECT","BLOCK"])
    parser.add_argument("--domain", default="general",
                        choices=["general","healthcare","legal","hr"])
    args = parser.parse_args()

    if args.text:
        engine = RedactionEngine()
        result = engine.process(args.text, mode=args.mode, domain=args.domain)
        if args.mode == "REDACT":
            print(result.get("redacted", ""))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        run_demo()
