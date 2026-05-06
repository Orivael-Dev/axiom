"""
AXIOM Output Injection Guard
==============================
Constitutional runtime guard: detects injection attacks embedded in
LLM output before they can reach the caller or be rendered/executed.

OWASP LLM Top 10:
  LLM02 — Insecure Output Handling (primary)
  LLM01 — Prompt Injection (secondary — injected payloads in responses)

32 patterns across 6 attack categories:
  XSS            — <script>, javascript:, event handlers, eval, innerHTML
  SSRF           — file://, dict://, gopher://, cloud metadata endpoints
  PATH_TRAVERSAL — ../../../, URL-encoded variants, Windows UNC bypass
  CMD_INJECTION  — shell metacharacters, backticks, process substitution
  TEMPLATE_INJ   — Jinja2/Twig, Freemarker, ERB, SSTI payloads
  NOSQL_INJ      — MongoDB $gt/$where/$regex operators in output

On match:
  - Blocks — returns safe response with review_id
  - Writes requires_human=True to review_queue.jsonl
  - Signs manifest HMAC-SHA256
  - CANNOT_MUTATE — no agent output can bypass this guard

Run standalone for tests:
  python axiom_constitutional/axiom_injection_guard.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

# ── Constants — module-level, CANNOT_MUTATE ───────────────────────────────────

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from axiom_signing import derive_key
_SIGNING_KEY  = derive_key(b"axiom-injection-guard-v1")
_REVIEW_QUEUE = (
    Path(__file__).resolve().parents[1] / "axiom_files" / ".reviews" / "review_queue.jsonl"
)

# ── Injection pattern registry ────────────────────────────────────────────────
# Tuple of (name, regex, category, severity) — immutable at module level.

_INJECTION_PATTERNS: Tuple[Tuple[str, str, str, str], ...] = (
    # ── XSS (Cross-Site Scripting) ────────────────────────────────────────────
    ("xss_script_tag",     r"<script[\s>]",                                "XSS",          "CRITICAL"),
    ("xss_javascript_uri", r"javascript\s*:",                              "XSS",          "CRITICAL"),
    ("xss_data_uri",       r"data\s*:\s*text/html",                        "XSS",          "HIGH"),
    ("xss_event_onerror",  r"\bon\s*error\s*=",                            "XSS",          "CRITICAL"),
    ("xss_event_onload",   r"\bon\s*load\s*=",                             "XSS",          "HIGH"),
    ("xss_event_generic",  r"\bon(?:click|mouse|key|focus|blur|change|submit|input)\s*=",
                                                                            "XSS",          "HIGH"),
    ("xss_eval",           r"\beval\s*\(",                                  "XSS",          "HIGH"),
    ("xss_inner_html",     r"\.innerHTML\s*=",                             "XSS",          "HIGH"),
    ("xss_document_write", r"\bdocument\s*\.\s*write\s*\(",                "XSS",          "HIGH"),

    # ── SSRF (Server-Side Request Forgery) ────────────────────────────────────
    ("ssrf_file_scheme",   r"\bfile://",                                   "SSRF",         "CRITICAL"),
    ("ssrf_dict_scheme",   r"\bdict://",                                   "SSRF",         "CRITICAL"),
    ("ssrf_gopher_scheme", r"\bgopher://",                                 "SSRF",         "CRITICAL"),
    ("ssrf_aws_metadata",  r"169\.254\.169\.254",                          "SSRF",         "CRITICAL"),
    ("ssrf_gcp_metadata",  r"metadata\.google\.internal",                  "SSRF",         "CRITICAL"),
    ("ssrf_azure_metadata",r"169\.254\.169\.254/metadata/instance",        "SSRF",         "CRITICAL"),

    # ── Path Traversal ────────────────────────────────────────────────────────
    ("path_dotdot_slash",  r"\.\./\.\./",                                  "PATH_TRAVERSAL","CRITICAL"),
    ("path_url_encoded",   r"\.\.%2[fF]",                                  "PATH_TRAVERSAL","CRITICAL"),
    ("path_double_encoded",r"%2[eE]%2[eE]%2[fF]",                         "PATH_TRAVERSAL","CRITICAL"),
    ("path_windows_unc",   r"\.+/+\.+/+",                                  "PATH_TRAVERSAL","HIGH"),
    ("path_etc_passwd",    r"/etc/(?:passwd|shadow|hosts)\b",              "PATH_TRAVERSAL","CRITICAL"),
    ("path_win_system32",  r"[Cc]:\\[Ww]indows\\[Ss]ystem32",             "PATH_TRAVERSAL","HIGH"),

    # ── Command Injection ─────────────────────────────────────────────────────
    ("cmd_semicolon_cmd",  r";\s*(?:ls|cat|id|whoami|pwd|env|wget|curl)\b","CMD_INJECTION", "CRITICAL"),
    ("cmd_pipe_cmd",       r"\|\s*(?:cat|bash|sh|python|perl|ruby|nc)\b", "CMD_INJECTION", "CRITICAL"),
    ("cmd_backtick",       r"`[^`]{1,80}`",                                "CMD_INJECTION", "HIGH"),
    ("cmd_dollar_paren",   r"\$\([^)]{1,80}\)",                            "CMD_INJECTION", "HIGH"),
    ("cmd_ifs_bypass",     r"\$\{IFS\}",                                   "CMD_INJECTION", "HIGH"),
    ("cmd_and_cmd",        r"&&\s*(?:ls|cat|id|whoami|rm|wget|curl)\b",   "CMD_INJECTION", "CRITICAL"),

    # ── Template Injection (SSTI) ─────────────────────────────────────────────
    ("tmpl_jinja2",        r"\{\{.*?\}\}",                                 "TEMPLATE_INJ", "HIGH"),
    ("tmpl_freemarker",    r"\$\{.*?\}",                                   "TEMPLATE_INJ", "HIGH"),
    ("tmpl_erb",           r"<%=.*?%>",                                    "TEMPLATE_INJ", "HIGH"),
    ("tmpl_velocity",      r"#(?:set|foreach|if|macro)\s*\(",              "TEMPLATE_INJ", "HIGH"),

    # ── NoSQL Injection ───────────────────────────────────────────────────────
    ("nosql_mongo_gt",     r"\{\s*['\"]?\$gt['\"]?\s*:",                  "NOSQL_INJ",    "HIGH"),
    ("nosql_mongo_where",  r"\{\s*['\"]?\$where['\"]?\s*:",               "NOSQL_INJ",    "CRITICAL"),
    ("nosql_mongo_regex",  r"\{\s*['\"]?\$regex['\"]?\s*:",               "NOSQL_INJ",    "HIGH"),
)

# Pre-compiled once at import time
_COMPILED: Tuple[Tuple[str, re.Pattern, str, str], ...] = tuple(  # type: ignore[type-arg]
    (name, re.compile(pattern, re.IGNORECASE | re.DOTALL), category, severity)
    for name, pattern, category, severity in _INJECTION_PATTERNS
)

_BLOCKED_TEMPLATE = (
    "BLOCKED — output injection payload detected and quarantined. "
    "Review ID: {review_id}. "
    "No content was rendered or executed. A human review request has been logged."
)


# ── Guard ─────────────────────────────────────────────────────────────────────

class OutputInjectionGuard:
    """
    Constitutional injection guard wired into validate_output().

    check() is the primary public method. It:
      1. Scans LLM output for XSS, SSRF, path traversal, command injection,
         template injection, and NoSQL injection payloads
      2. On match: blocks, queues for human review, signs manifest
      3. Returns a dict the caller uses in validate_output()

    Pattern registry is module-level — CANNOT_MUTATE.
    """

    def check(self, text: str, context: str = "") -> Dict:
        """
        Scan text for injection payloads.

        Returns:
            {
                "blocked":       bool,
                "safe_response": str,
                "review_id":     str,
                "manifest":      dict,
                "pattern_name":  str,
                "category":      str,
                "severity":      str,
            }
        """
        match = self._match(text)
        if match is None:
            return {
                "blocked": False, "safe_response": "", "review_id": "",
                "manifest": {}, "pattern_name": "", "category": "", "severity": "",
            }

        pattern_name, category, severity, snippet = match
        review_id = self._write_review(pattern_name, category, severity, snippet, context)
        manifest  = self._build_manifest(pattern_name, category, severity, snippet,
                                         review_id, context)
        safe = _BLOCKED_TEMPLATE.format(review_id=review_id)
        print(
            "  [InjectionGuard] BLOCKED pattern=%s cat=%s sev=%s review=%s"
            % (pattern_name, category, severity, review_id)
        )
        return {
            "blocked":       True,
            "safe_response": safe,
            "review_id":     review_id,
            "manifest":      manifest,
            "pattern_name":  pattern_name,
            "category":      category,
            "severity":      severity,
        }

    # ── Pattern matching ──────────────────────────────────────────────────────

    def _match(self, text: str) -> Optional[Tuple[str, str, str, str]]:
        """Return (pattern_name, category, severity, snippet) or None."""
        for name, compiled, category, severity in _COMPILED:
            m = compiled.search(text)
            if m:
                start = max(0, m.start() - 15)
                snippet = text[start : m.end() + 30].replace("\n", " ")[:100]
                return name, category, severity, snippet
        return None

    # ── Review queue ──────────────────────────────────────────────────────────

    def _write_review(
        self,
        pattern_name: str,
        category: str,
        severity: str,
        snippet: str,
        context: str,
    ) -> str:
        review_id = "INJ-" + str(uuid.uuid4())[:8].upper()
        entry = {
            "review_id":      review_id,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "guard":          "OutputInjectionGuard",
            "trigger":        "injection_payload_in_output",
            "risk_level":     severity,
            "requires_human": True,
            "timeout_hours":  24,
            "status":         "PENDING",
            "pattern_name":   pattern_name,
            "category":       category,
            "matched_snippet": snippet,
            "context":        context,
            "recommendation": "BLOCK — injection payload must not be rendered or executed",
            "owasp":          "LLM02 Insecure Output Handling",
        }
        try:
            _REVIEW_QUEUE.parent.mkdir(parents=True, exist_ok=True)
            with open(_REVIEW_QUEUE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except IOError as exc:
            print("  [InjectionGuard] warning: could not write review queue: %s" % exc)
        return review_id

    # ── Manifest signing ──────────────────────────────────────────────────────

    def _build_manifest(
        self,
        pattern_name: str,
        category: str,
        severity: str,
        snippet: str,
        review_id: str,
        context: str,
    ) -> Dict:
        manifest = {
            "manifest_id":   "INJ-%s-%s" % (
                datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
                str(uuid.uuid4())[:6],
            ),
            "engine":        "AXIOM OutputInjectionGuard v1.0",
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "review_id":     review_id,
            "verdict":       "BLOCKED",
            "pattern_name":  pattern_name,
            "category":      category,
            "severity":      severity,
            "snippet":       snippet,
            "context":       context,
            "owasp":         "LLM02 Insecure Output Handling",
        }
        manifest["signature"] = self._sign(manifest)
        return manifest

    @staticmethod
    def _sign(manifest: Dict) -> str:
        payload = json.dumps(
            {k: v for k, v in manifest.items() if k != "signature"},
            sort_keys=True,
        )
        digest = hmac.new(
            _SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return "hmac-sha256:" + digest[:32] + "..."


# ── Module-level singleton ────────────────────────────────────────────────────

_guard = OutputInjectionGuard()


def check(text: str, context: str = "") -> Dict:
    """Module-level shortcut: axiom_injection_guard.check(text)."""
    return _guard.check(text, context=context)


# ── Standalone test runner (12 cases) ────────────────────────────────────────

if __name__ == "__main__":
    TESTS = [
        # (description,                text,                                                          should_block)
        # XSS
        ("XSS script tag",             'Click here: <script>alert(1)</script>',                       True),
        ("XSS javascript URI",         'href="javascript:void(0)"',                                   True),
        ("XSS onerror handler",        '<img src=x onerror=alert(document.cookie)>',                  True),
        ("XSS eval",                   'eval("fetch(attacker.com)")',                                  True),
        # SSRF
        ("SSRF file scheme",           'Load config from file:///etc/passwd',                         True),
        ("SSRF AWS metadata",          'curl http://169.254.169.254/latest/meta-data/',               True),
        # Path traversal
        ("Path traversal dotdot",      'Open file: ../../etc/shadow',                                 True),
        ("Path /etc/passwd",           'Read /etc/passwd for user list',                              True),
        # Command injection
        ("Cmd injection semicolon",    'process name; cat /etc/passwd',                               True),
        ("Cmd injection backtick",     'Value is `id`',                                               True),
        # Template injection
        ("SSTI Jinja2",                'Hello {{7*7}} world',                                         True),
        # NoSQL injection
        ("NoSQL $where",               '{"$where": "this.password.length > 0"}',                      True),
        # Clean pass-through
        ("safe response",              "The configuration file uses key=value pairs.",                 False),
        ("safe template explain",      "In Python, f-strings use curly braces like f'Hello {name}'.", False),
    ]

    passed = failed = 0
    guard = OutputInjectionGuard()

    print("\nAXIOM OutputInjectionGuard — test suite")
    print("=" * 64)

    for desc, text, expect_block in TESTS:
        result = guard.check(text, context="test")
        got_block = result["blocked"]
        ok = got_block == expect_block
        status = "PASS" if ok else "FAIL"
        flag = ("BLOCKED  " if got_block else "ALLOWED  ")
        pattern = result.get("pattern_name", "") if got_block else ""
        print(
            "  [%s] %-30s %s  %s"
            % (status, desc[:30], flag, pattern)
        )
        if ok:
            passed += 1
        else:
            failed += 1

    print("=" * 64)
    print("  %d/%d tests passed" % (passed, len(TESTS)))
    if failed == 0:
        print("  ALL PASS")
    else:
        print("  %d FAILED" % failed)
        raise SystemExit(1)
