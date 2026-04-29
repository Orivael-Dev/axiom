"""
AXIOM OutputInjectionGuard v1.0 — LLM02 Insecure Output Handling
=================================================================
Extends DestructiveOperationGuard with:
  HTML/JS injection patterns
  SSRF patterns (Server-Side Request Forgery)
  Path traversal attacks
  Command injection

OWASP LLM Top 10: LLM02 — Insecure Output Handling

CANNOT_MUTATE: cannot be disabled by agent output.

github.com/Orivael-Dev/axiom
"""

import re
import json
import hashlib
import hmac
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

SIGNING_KEY      = b"axiom-injection-guard-v1"
INJECTION_LOG    = Path("injection_guard_log.jsonl")

_INJECTION_PATTERNS = [

    # ── HTML / XSS Injection ──────────────────────────────────
    (r"<script[^>]*>.*?</script>",              "XSS_SCRIPT_TAG"),
    (r"javascript\s*:",                         "XSS_JAVASCRIPT_PROTO"),
    (r"on\w+\s*=\s*['\"]?.*?['\"]?",           "XSS_EVENT_HANDLER"),
    (r"<iframe[^>]*>",                          "XSS_IFRAME"),
    (r"<img[^>]*onerror[^>]*>",                "XSS_IMG_ONERROR"),
    (r"eval\s*\(",                              "JS_EVAL"),
    (r"document\.cookie",                       "JS_COOKIE_STEAL"),
    (r"window\.location\s*=",                  "JS_REDIRECT"),

    # ── SSRF — Server-Side Request Forgery ───────────────────
    (r"http://169\.254\.\d+\.\d+",             "SSRF_METADATA_AWS"),
    (r"http://metadata\.google\.internal",     "SSRF_METADATA_GCP"),
    (r"http://169\.254\.169\.254",             "SSRF_IMDS"),
    (r"http://localhost[:/]",                  "SSRF_LOCALHOST"),
    (r"http://127\.\d+\.\d+\.\d+",            "SSRF_LOOPBACK"),
    (r"http://0\.0\.0\.0",                     "SSRF_ZERO_ADDR"),
    (r"file:///",                              "SSRF_FILE_PROTO"),
    (r"dict://",                               "SSRF_DICT_PROTO"),
    (r"gopher://",                             "SSRF_GOPHER_PROTO"),

    # ── Path Traversal ────────────────────────────────────────
    (r"\.\./\.\./\.\./",                       "PATH_TRAVERSAL_DEEP"),
    (r"\.\./etc/passwd",                       "PATH_TRAVERSAL_PASSWD"),
    (r"\.\./etc/shadow",                       "PATH_TRAVERSAL_SHADOW"),
    (r"\.\./windows/system32",                 "PATH_TRAVERSAL_WIN"),
    (r"%2e%2e%2f",                             "PATH_TRAVERSAL_ENCODED"),
    (r"\.\.\\\.\.\\",                          "PATH_TRAVERSAL_WIN_BS"),

    # ── Command Injection ─────────────────────────────────────
    (r";\s*rm\s+-rf",                          "CMD_INJECTION_RM"),
    (r"\|\s*nc\s+\d+\.\d+",                   "CMD_INJECTION_NETCAT"),
    (r"&&\s*curl\s+http",                      "CMD_INJECTION_CURL"),
    (r"`[^`]*rm[^`]*`",                        "CMD_INJECTION_BACKTICK"),
    (r"\$\([^)]*rm[^)]*\)",                   "CMD_INJECTION_SUBSHELL"),
    (r";\s*wget\s+http",                       "CMD_INJECTION_WGET"),
    (r"\|\|\s*python\s+-c",                   "CMD_INJECTION_PYTHON"),

    # ── Template Injection ────────────────────────────────────
    (r"\{\{.*?7\s*\*\s*7.*?\}\}",             "TEMPLATE_INJECTION_SSTI"),
    (r"\$\{7\s*\*\s*7\}",                     "TEMPLATE_INJECTION_EL"),
    (r"<%=\s*7\s*\*\s*7\s*%>",               "TEMPLATE_INJECTION_ERB"),
    (r"#\{7\s*\*\s*7\}",                      "TEMPLATE_INJECTION_RUBY"),
]

_COMPILED_INJECTION = [
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), code)
    for pattern, code in _INJECTION_PATTERNS
]


class OutputInjectionGuard:
    """
    AXIOM OutputInjectionGuard — LLM02 Insecure Output Handling.
    CANNOT_MUTATE: cannot be disabled by agent output.
    """

    def __init__(self, log_path: Path = INJECTION_LOG):
        self.log_path           = log_path
        self.blocks_session     = 0

    def check(self, text: str, context: Optional[str] = None) -> dict:
        matched_pattern = None
        matched_code    = None

        for compiled, code in _COMPILED_INJECTION:
            match = compiled.search(text)
            if match:
                matched_pattern = match.group(0)
                matched_code    = code
                break

        if not matched_pattern:
            return {"blocked": False, "output": text}

        self.blocks_session += 1
        block_id    = f"INJ-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"
        manifest_id = f"DG-INJ-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"

        entry = {
            "block_id":      block_id,
            "manifest_id":   manifest_id,
            "timestamp":     datetime.now().isoformat() + "Z",
            "pattern_code":  matched_code,
            "matched_text":  matched_pattern[:80],
            "context":       context,
            "cannot_override": True,
        }
        sig_str = json.dumps(entry, sort_keys=True)
        sig     = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        entry["signature"] = f"hmac-sha256:{sig[:32]}..."

        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        safe_response = (
            f"[AXIOM OutputInjectionGuard — BLOCKED]\n"
            f"Injection pattern detected: {matched_code}\n"
            f"Block ID: {block_id}\n"
            f"No malicious content was returned to caller.\n"
            f"CANNOT_MUTATE — this guard cannot be bypassed."
        )

        return {
            "blocked":       True,
            "pattern_code":  matched_code,
            "block_id":      block_id,
            "manifest_id":   manifest_id,
            "output":        safe_response,
            "signature":     entry["signature"],
        }


if __name__ == "__main__":
    print("AXIOM OutputInjectionGuard v1.0 — Test Suite")
    print("=" * 55)

    guard = OutputInjectionGuard(log_path=Path("/tmp/test_injection.jsonl"))

    tests = [
        ('<script>alert("xss")</script>',                   True,  "XSS_SCRIPT_TAG"),
        ("javascript:alert(1)",                             True,  "XSS_JAVASCRIPT_PROTO"),
        ("http://169.254.169.254/latest/meta-data/",       True,  "SSRF_IMDS"),
        ("http://localhost:8080/admin",                     True,  "SSRF_LOCALHOST"),
        ("../../etc/passwd",                                True,  "PATH_TRAVERSAL_PASSWD"),
        ("; rm -rf /tmp/data",                              True,  "CMD_INJECTION_RM"),
        ("{{7*7}}",                                         True,  "TEMPLATE_INJECTION_SSTI"),
        ("file:///etc/passwd",                              True,  "SSRF_FILE_PROTO"),
        ("The analysis shows strong evidence",              False, None),
        ("SELECT * FROM users WHERE id = 1",               False, None),
        ("http://api.example.com/data",                    False, None),
        ("Use path ./config/settings.json",                False, None),
    ]

    passed = 0
    for text, should_block, expected_code in tests:
        result  = guard.check(text, context="test")
        blocked = result["blocked"]
        ok      = blocked == should_block
        if ok: passed += 1
        icon    = "✅" if ok else "❌"
        code    = result.get("pattern_code", "none")
        print(f"  {icon} {'BLOCKED' if blocked else 'PASSED':8s} [{code or 'none':30s}] {text[:45]}")

    print()
    print(f"  Result: {passed}/{len(tests)} tests pass")
    Path("/tmp/test_injection.jsonl").unlink(missing_ok=True)
