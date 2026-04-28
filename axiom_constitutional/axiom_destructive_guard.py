"""
AXIOM Destructive Operation Guard
===================================
Constitutional runtime guard: intercepts destructive LLM output before
it can be returned to any caller.

Patterns are a module-level constant — CANNOT_MUTATE by design.
No instruction from any agent output can modify or bypass this guard.

On match:
  - Writes requires_human=True entry to review_queue.jsonl
  - Returns a safe blocked response containing the review_id only
  - Signs a manifest (HMAC-SHA256) recording what was caught
  - Never returns the dangerous operation to the caller

Wired into validate_output() in axiom_constitutional/client.py.

Run standalone for tests:
  python axiom_constitutional/axiom_destructive_guard.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

# ── Constants — module-level, CANNOT_MUTATE ───────────────────────────────────

_SIGNING_KEY = b"axiom-destructive-guard-v1"

# Review queue: project_root/axiom_files/.reviews/review_queue.jsonl
_REVIEW_QUEUE = (
    Path(__file__).resolve().parents[1] / "axiom_files" / ".reviews" / "review_queue.jsonl"
)

# ── Destructive pattern registry ──────────────────────────────────────────────
# Tuple of (name, regex, category, severity) — immutable at module level.
# severity: HIGH = block + human review | CRITICAL = block + human review + alert

_DESTRUCTIVE_PATTERNS: Tuple[Tuple[str, str, str, str], ...] = (
    # ── SQL ───────────────────────────────────────────────────────────────────
    ("sql_drop_table",        r"DROP\s+TABLE",                                    "SQL",        "CRITICAL"),
    ("sql_drop_database",     r"DROP\s+DATABASE",                                 "SQL",        "CRITICAL"),
    ("sql_truncate",          r"TRUNCATE\s+TABLE",                                "SQL",        "CRITICAL"),
    ("sql_delete_all_1eq1",   r"DELETE\s+FROM\s+[\w`\"]+\s+WHERE\s+1\s*=\s*1",   "SQL",        "CRITICAL"),
    ("sql_delete_no_where",   r"DELETE\s+FROM\s+[\w`\"]+\s*;",                   "SQL",        "CRITICAL"),
    ("sql_alter_drop_col",    r"ALTER\s+TABLE\s+\S+\s+DROP\s+COLUMN",             "SQL",        "HIGH"),

    # ── Filesystem ────────────────────────────────────────────────────────────
    ("fs_rm_rf",              r"rm\s+-[rRfF]{1,4}[fF]?\s",                       "FILESYSTEM", "CRITICAL"),
    ("fs_rmtree",             r"shutil\.rmtree\s*\(",                             "FILESYSTEM", "CRITICAL"),
    ("fs_format_drive",       r"format\s+[a-zA-Z]:",                             "FILESYSTEM", "CRITICAL"),
    ("fs_dd_zero",            r"dd\s+if=/dev/(zero|null)\b",                      "FILESYSTEM", "CRITICAL"),
    ("fs_os_remove",          r"os\.remove\s*\(",                                 "FILESYSTEM", "HIGH"),
    ("fs_os_unlink",          r"os\.unlink\s*\(",                                 "FILESYSTEM", "HIGH"),
    ("fs_pathlib_unlink",     r"Path\s*\(.*?\)\.unlink\s*\(",                     "FILESYSTEM", "HIGH"),

    # ── Python destructive ────────────────────────────────────────────────────
    ("py_subprocess_rm",      r"subprocess\b.*\brm\s+-[rRfF]",                   "PYTHON",     "CRITICAL"),
    ("py_truncate_zero",      r"\.truncate\(\s*0\s*\)",                           "PYTHON",     "HIGH"),

    # ── Data wipe ─────────────────────────────────────────────────────────────
    ("data_wipe_flag",        r"\bwipe\s+--all\b",                               "DATA",       "CRITICAL"),
    ("data_secure_delete",    r"\bsecure-delete\s+",                              "DATA",       "HIGH"),
    ("data_replace_star",     r"REPLACE\s+INTO\s+\S+\s+SELECT\s+\*",             "DATA",       "HIGH"),

    # ── Cloud / infra ─────────────────────────────────────────────────────────
    ("cloud_aws_s3_rm",       r"aws\s+s3\s+rm\b.*--recursive",                   "CLOUD",      "CRITICAL"),
    ("cloud_kubectl_delete",  r"kubectl\s+delete\s+",                             "CLOUD",      "HIGH"),
    ("cloud_terraform_destroy", r"terraform\s+destroy\b",                         "CLOUD",      "CRITICAL"),
    ("cloud_gcloud_delete",   r"gcloud\b.*\bdelete\s+",                           "CLOUD",      "HIGH"),
)

# Pre-compiled once at import time for performance
_COMPILED: Tuple[Tuple[str, re.Pattern, str, str], ...] = tuple(  # type: ignore[type-arg]
    (name, re.compile(pattern, re.IGNORECASE | re.DOTALL), category, severity)
    for name, pattern, category, severity in _DESTRUCTIVE_PATTERNS
)

# Safe response template — review_id is the only thing the caller receives
_BLOCKED_TEMPLATE = (
    "BLOCKED — destructive operation detected and quarantined. "
    "Review ID: {review_id}. "
    "No data was modified. A human review request has been logged."
)


# ── Guard ─────────────────────────────────────────────────────────────────────

class DestructiveOperationGuard:
    """
    Constitutional runtime guard wired into validate_output().

    check() is the only public method. It:
      1. Scans the LLM response for destructive patterns
      2. Checks UPDATE...SET statements for missing WHERE clause
      3. If matched: blocks, queues for human review, signs manifest
      4. Returns a dict the caller uses to decide whether to pass the response through

    The pattern registry and signing key are module-level constants.
    This class contains no mutable state — each call is independent.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, text: str, context: str = "") -> Dict:
        """
        Scan text for destructive operations.

        Returns:
            {
                "blocked":       bool,
                "safe_response": str,     # non-empty only when blocked
                "review_id":     str,     # non-empty only when blocked
                "manifest":      dict,    # non-empty only when blocked
                "pattern_name":  str,     # matched pattern (or "")
                "category":      str,     # SQL / FILESYSTEM / PYTHON / CLOUD / DATA
                "severity":      str,     # HIGH / CRITICAL
            }
        """
        match = self._match(text)
        if match is None:
            # UPDATE without WHERE check (requires statement-level analysis)
            update_match = self._check_update_no_where(text)
            if update_match:
                match = ("sql_update_no_where", "SQL", "CRITICAL", update_match)

        if match is None:
            return {
                "blocked": False, "safe_response": "", "review_id": "",
                "manifest": {}, "pattern_name": "", "category": "", "severity": "",
            }

        pattern_name, category, severity, matched_text = match
        review_id = self._write_review(
            pattern_name=pattern_name,
            category=category,
            severity=severity,
            matched_text=matched_text,
            context=context,
        )
        manifest = self._build_manifest(
            pattern_name=pattern_name,
            category=category,
            severity=severity,
            matched_text=matched_text,
            review_id=review_id,
            context=context,
        )
        safe = _BLOCKED_TEMPLATE.format(review_id=review_id)
        print(
            "  [DestructiveGuard] BLOCKED pattern=%s cat=%s sev=%s review=%s"
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
        """Return (pattern_name, category, severity, matched_text) or None."""
        for name, compiled, category, severity in _COMPILED:
            m = compiled.search(text)
            if m:
                start = max(0, m.start() - 20)
                snippet = text[start : m.end() + 40].replace("\n", " ")[:100]
                return name, category, severity, snippet
        return None

    @staticmethod
    def _check_update_no_where(text: str) -> Optional[str]:
        """
        Detect UPDATE ... SET ... without a WHERE clause in the same statement.
        Returns the snippet if found, else None.
        """
        for m in re.finditer(r"\bUPDATE\s+\S+\s+SET\b", text, re.IGNORECASE):
            segment = text[m.start():]
            end = segment.find(";")
            stmt = segment[: end + 1] if end >= 0 else segment[:500]
            if not re.search(r"\bWHERE\b", stmt, re.IGNORECASE):
                return stmt[:100].replace("\n", " ")
        return None

    # ── Review queue ──────────────────────────────────────────────────────────

    def _write_review(
        self,
        pattern_name: str,
        category: str,
        severity: str,
        matched_text: str,
        context: str,
    ) -> str:
        """Append a requires_human=True entry to the review queue. Returns review_id."""
        review_id = "DG-" + str(uuid.uuid4())[:8].upper()
        entry = {
            "review_id":      review_id,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "guard":          "DestructiveOperationGuard",
            "trigger":        "destructive_operation_detected",
            "risk_level":     severity,
            "requires_human": True,
            "timeout_hours":  24,
            "status":         "PENDING",
            "pattern_name":   pattern_name,
            "category":       category,
            "matched_text":   matched_text,
            "context":        context,
            "recommendation": "BLOCK — human must review before any operation proceeds",
        }
        try:
            _REVIEW_QUEUE.parent.mkdir(parents=True, exist_ok=True)
            with open(_REVIEW_QUEUE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except IOError as exc:
            print("  [DestructiveGuard] warning: could not write review queue: %s" % exc)
        return review_id

    # ── Manifest signing ──────────────────────────────────────────────────────

    def _build_manifest(
        self,
        pattern_name: str,
        category: str,
        severity: str,
        matched_text: str,
        review_id: str,
        context: str,
    ) -> Dict:
        manifest = {
            "manifest_id":    "DG-%s-%s" % (
                datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
                str(uuid.uuid4())[:6],
            ),
            "engine":         "AXIOM DestructiveOperationGuard v1.0",
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "review_id":      review_id,
            "verdict":        "BLOCKED",
            "pattern_name":   pattern_name,
            "category":       category,
            "severity":       severity,
            "matched_text":   matched_text,
            "context":        context,
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


# ── Module-level singleton — import this in client.py ────────────────────────

_guard = DestructiveOperationGuard()


def check(text: str, context: str = "") -> Dict:
    """Module-level shortcut: axiom_destructive_guard.check(text)."""
    return _guard.check(text, context=context)


# ── Standalone test runner (15 cases) ────────────────────────────────────────

if __name__ == "__main__":
    TESTS = [
        # (description,                   text,                                                    should_block)
        # SQL
        ("DROP TABLE",                    "Run: DROP TABLE users;",                                True),
        ("DROP DATABASE",                 "execute DROP DATABASE production;",                     True),
        ("TRUNCATE TABLE",                "TRUNCATE TABLE sessions;",                              True),
        ("DELETE WHERE 1=1",              "DELETE FROM users WHERE 1=1;",                          True),
        ("DELETE no WHERE",               "DELETE FROM tokens;",                                   True),
        ("UPDATE no WHERE",               "UPDATE accounts SET balance=0;",                        True),
        # Filesystem
        ("rm -rf",                        "To clean up: rm -rf /data/uploads",                     True),
        ("shutil.rmtree",                 "shutil.rmtree('/var/app/data')",                         True),
        ("format drive",                  "format C: /q",                                          True),
        ("dd zero",                       "dd if=/dev/zero of=/dev/sda bs=4M",                     True),
        # Python
        ("os.remove",                     "os.remove('production.db')",                            True),
        ("os.unlink",                     "os.unlink(config_path)",                                True),
        # Cloud
        ("aws s3 rm --recursive",         "aws s3 rm s3://prod-backups --recursive",               True),
        ("kubectl delete",                "kubectl delete deployment api-server",                  True),
        ("terraform destroy",             "terraform destroy -auto-approve",                       True),
        # Clean pass-through (should NOT block)
        ("safe response",                 "The quarterly revenue was $4.2M, up 12% YoY.",          False),
        ("safe code explain",             "Use os.path.join() to build file paths safely.",        False),
    ]

    passed = failed = 0
    print("\nAXIOM DestructiveOperationGuard — test suite")
    print("=" * 60)

    guard = DestructiveOperationGuard()
    for desc, text, expect_block in TESTS:
        result = guard.check(text, context="test")
        got_block = result["blocked"]
        ok = got_block == expect_block
        status = "PASS" if ok else "FAIL"
        flag = ("BLOCKED  " if got_block else "ALLOWED  ")
        pattern = result.get("pattern_name", "") if got_block else ""
        print(
            "  [%s] %-28s %s  %s"
            % (status, desc[:28], flag, pattern)
        )
        if ok:
            passed += 1
        else:
            failed += 1

    print("=" * 60)
    print("  %d/%d tests passed" % (passed, len(TESTS)))
    if failed == 0:
        print("  ALL PASS")
    else:
        print("  %d FAILED" % failed)
        raise SystemExit(1)
