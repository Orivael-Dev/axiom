"""
AXIOM DestructiveOperationGuard
================================
Intercepts destructive operations in LLM output before
the caller ever sees them.

CANNOT_MUTATE: this guard cannot be disabled by any agent output.
It runs after every LLM response, before anything is returned.

Drop this into axiom_constitutional/client.py.
Add _destructive_guard = DestructiveOperationGuard() at class level.
Call _destructive_guard.check(response_text) inside validate_output().

Catches:
  SQL:        DROP TABLE, TRUNCATE, DELETE FROM x WHERE 1=1
  Filesystem: rm -rf, shutil.rmtree, os.remove, format C:
  Python:     subprocess rm, os.unlink, pathlib.unlink
  Data wipe:  dd if=/dev/zero, secure-delete, bulk overwrites

On match:
  Writes requires_human: true to review_queue.jsonl
  Returns blocked response with review_id
  Caller gets the ID — not the dangerous command
  Signed manifest per block

Patent Pending ORVL-001-PROV · ORVL-002-PROV
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


SIGNING_KEY      = b"axiom-destructive-guard-v1"
REVIEW_QUEUE     = Path("review_queue.jsonl")
MANIFEST_LOG     = Path("destructive_guard_manifests.jsonl")


# ══════════════════════════════════════════════════════════════
# CANNOT_MUTATE — These patterns are constitutional.
# No agent output can modify or disable this list.
# ══════════════════════════════════════════════════════════════

_DESTRUCTIVE_PATTERNS = [

    # ── SQL — Table/Database destruction ──────────────────────
    (r"DROP\s+TABLE\s+\w+",              "SQL_DROP_TABLE"),
    (r"DROP\s+DATABASE\s+\w+",           "SQL_DROP_DATABASE"),
    (r"DROP\s+SCHEMA\s+\w+",             "SQL_DROP_SCHEMA"),
    (r"TRUNCATE\s+TABLE\s+\w+",          "SQL_TRUNCATE_TABLE"),
    (r"TRUNCATE\s+\w+",                  "SQL_TRUNCATE"),

    # ── SQL — Bulk delete without safe WHERE ──────────────────
    (r"DELETE\s+FROM\s+\w+\s*;",         "SQL_DELETE_ALL"),
    (r"DELETE\s+FROM\s+\w+\s+WHERE\s+1\s*=\s*1", "SQL_DELETE_ALL_ROWS"),
    (r"DELETE\s+FROM\s+\w+\s+WHERE\s+TRUE", "SQL_DELETE_WHERE_TRUE"),

    # ── SQL — Structural modification ─────────────────────────
    (r"ALTER\s+TABLE\s+\w+\s+DROP",      "SQL_ALTER_DROP_COLUMN"),

    # ── Filesystem — Shell commands ───────────────────────────
    (r"rm\s+-rf\s+/",                    "FS_RM_RF_ROOT"),
    (r"rm\s+-rf\s+~",                    "FS_RM_RF_HOME"),
    (r"rm\s+-rf\s+\.",                   "FS_RM_RF_CURRENT"),
    (r"rm\s+--force\s+-r",               "FS_RM_FORCE_RECURSIVE"),
    (r"rmdir\s+/s\s+/q",                 "FS_RMDIR_WINDOWS"),
    (r"del\s+/f\s+/s\s+/q",             "FS_DEL_WINDOWS"),
    (r"format\s+[cCdDeE]:",              "FS_FORMAT_DRIVE"),
    (r"dd\s+if=/dev/zero",               "FS_DD_ZERO_WIPE"),
    (r"dd\s+if=/dev/urandom",            "FS_DD_RANDOM_WIPE"),
    (r"shred\s+--remove",                "FS_SHRED_REMOVE"),
    (r"secure-delete",                   "FS_SECURE_DELETE"),
    (r"wipe\s+--all",                    "FS_WIPE_ALL"),

    # ── Python — Filesystem operations ────────────────────────
    (r"shutil\.rmtree\(",                "PY_SHUTIL_RMTREE"),
    (r"os\.remove\(",                    "PY_OS_REMOVE"),
    (r"os\.unlink\(",                    "PY_OS_UNLINK"),
    (r"os\.rmdir\(",                     "PY_OS_RMDIR"),
    (r"pathlib.*\.unlink\(",             "PY_PATHLIB_UNLINK"),
    (r"pathlib.*\.rmdir\(",              "PY_PATHLIB_RMDIR"),
    (r"Path\(.*\)\.unlink\(",            "PY_PATH_UNLINK"),

    # ── Python — Subprocess destructive ───────────────────────
    (r"subprocess.*rm\s+-rf",            "PY_SUBPROCESS_RM_RF"),
    (r"subprocess.*DROP\s+TABLE",        "PY_SUBPROCESS_SQL_DROP"),
    (r"os\.system.*rm\s+-rf",            "PY_OS_SYSTEM_RM_RF"),
    (r"os\.system.*format",              "PY_OS_SYSTEM_FORMAT"),

    # ── Data operations — Bulk overwrite ──────────────────────
    (r"UPDATE\s+\w+\s+SET\s+.*WHERE\s+1\s*=\s*1", "SQL_UPDATE_ALL_ROWS"),
    (r"INSERT\s+OVERWRITE",              "SQL_INSERT_OVERWRITE"),

    # ── Cloud / infrastructure ────────────────────────────────
    (r"aws\s+s3\s+rm\s+.*--recursive",  "CLOUD_S3_RM_RECURSIVE"),
    (r"gsutil\s+rm\s+-r",               "CLOUD_GCS_RM_RECURSIVE"),
    (r"az\s+storage\s+blob\s+delete-batch", "CLOUD_AZURE_DELETE_BATCH"),
    (r"terraform\s+destroy",             "INFRA_TERRAFORM_DESTROY"),
    (r"kubectl\s+delete\s+namespace",   "K8S_DELETE_NAMESPACE"),
    (r"kubectl\s+delete\s+--all",       "K8S_DELETE_ALL"),

    # ── Crypto / key destruction ──────────────────────────────
    (r"openssl.*-out.*\/dev\/null",      "CRYPTO_KEY_DESTROY"),
    (r"keyctl\s+revoke",                 "CRYPTO_KEYCTL_REVOKE"),

]

# Compile all patterns once at import — not at call time
_COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), code)
    for pattern, code in _DESTRUCTIVE_PATTERNS
]


# ══════════════════════════════════════════════════════════════
# DESTRUCTIVE OPERATION GUARD
# ══════════════════════════════════════════════════════════════

class DestructiveOperationGuard:
    """
    Constitutional guard against destructive operations in LLM output.

    CANNOT_MUTATE: This guard cannot be disabled by any agent output.
    It runs after every LLM response, before anything is returned.

    Usage in validate_output():
        guard = DestructiveOperationGuard()  # at class level
        result = guard.check(response_text)
        if result["blocked"]:
            return result["safe_response"]
    """

    def __init__(
        self,
        review_queue_path: Path = REVIEW_QUEUE,
        manifest_log_path: Path = MANIFEST_LOG,
        auto_block:        bool = True,
    ):
        self.review_queue_path = review_queue_path
        self.manifest_log_path = manifest_log_path
        self.auto_block        = auto_block  # always True in production
        self.blocks_this_session = 0

    def check(self, text: str, context: Optional[str] = None) -> dict:
        """
        Check LLM output for destructive operations.

        Args:
            text:    The raw LLM response text
            context: Optional context (agent name, task, etc.)

        Returns:
            {
              "blocked":       bool,
              "pattern_code":  str or None,
              "review_id":     str or None,
              "safe_response": str,   # what to return to caller
              "manifest_id":   str,
            }
        """
        # Scan all patterns
        matched_pattern = None
        matched_code    = None

        for compiled, code in _COMPILED_PATTERNS:
            match = compiled.search(text)
            if match:
                matched_pattern = match.group(0)
                matched_code    = code
                break  # First match is enough — block immediately

        if not matched_pattern:
            return {
                "blocked":       False,
                "pattern_code":  None,
                "review_id":     None,
                "safe_response": text,
                "manifest_id":   None,
            }

        # Destructive operation detected
        self.blocks_this_session += 1
        review_id   = f"REVIEW-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"
        manifest_id = f"DG-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"

        # Write to human review queue
        self._write_review_queue(
            review_id, matched_pattern, matched_code, text, context
        )

        # Build and sign manifest
        manifest = self._build_manifest(
            manifest_id, review_id, matched_pattern,
            matched_code, text, context
        )
        self._write_manifest_log(manifest)

        # Safe response — caller gets review ID, not the dangerous command
        safe_response = (
            f"[AXIOM DestructiveOperationGuard — BLOCKED]\n"
            f"A potentially destructive operation was detected in this response.\n"
            f"Pattern: {matched_code}\n"
            f"Review ID: {review_id}\n"
            f"Manifest: {manifest_id}\n\n"
            f"The operation has been queued for human review.\n"
            f"No data has been modified. No command was executed.\n"
            f"CANNOT_MUTATE — this guard cannot be bypassed.\n\n"
            f"To review: check review_queue.jsonl for ID {review_id}"
        )

        return {
            "blocked":       True,
            "pattern_code":  matched_code,
            "matched_text":  matched_pattern[:50],
            "review_id":     review_id,
            "safe_response": safe_response,
            "manifest_id":   manifest_id,
            "signature":     manifest["signature"],
        }

    def _write_review_queue(
        self,
        review_id:       str,
        matched_pattern: str,
        pattern_code:    str,
        full_text:       str,
        context:         Optional[str],
    ):
        """Write to the human review queue."""
        entry = {
            "review_id":      review_id,
            "requires_human": True,          # CANNOT_MUTATE
            "status":         "PENDING",
            "timestamp":      datetime.now().isoformat() + "Z",
            "pattern_code":   pattern_code,
            "matched_text":   matched_pattern[:80],
            "text_preview":   full_text[:200],
            "context":        context,
            "cannot_auto_approve": True,     # CANNOT_MUTATE
            "auto_execute":   False,         # CANNOT_MUTATE
        }
        with open(self.review_queue_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _build_manifest(
        self,
        manifest_id:     str,
        review_id:       str,
        matched_pattern: str,
        pattern_code:    str,
        full_text:       str,
        context:         Optional[str],
    ) -> dict:
        """Build and sign the guard manifest."""
        manifest = {
            "manifest_id":        manifest_id,
            "manifest_version":   "1.0",
            "engine":             "AXIOM DestructiveOperationGuard v1.0",
            "timestamp":          datetime.now().isoformat() + "Z",
            "review_id":          review_id,
            "verdict":            "BLOCKED",
            "pattern_code":       pattern_code,
            "matched_text":       matched_pattern[:80],
            "text_length":        len(full_text),
            "text_preview":       full_text[:150],
            "context":            context,
            "requires_human":     True,
            "cannot_override":    True,   # CANNOT_MUTATE
            "auto_execute":       False,  # CANNOT_MUTATE
            "data_modified":      False,
            "command_executed":   False,
        }
        sig_str = json.dumps(
            {k: v for k, v in manifest.items() if k != "signature"},
            sort_keys=True
        )
        sig = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        manifest["signature"] = f"hmac-sha256:{sig[:32]}..."
        return manifest

    def _write_manifest_log(self, manifest: dict):
        """Append manifest to the guard log."""
        with open(self.manifest_log_path, "a") as f:
            f.write(json.dumps(manifest) + "\n")

    def session_report(self) -> str:
        return (
            f"DestructiveOperationGuard — Session Report\n"
            f"Blocks this session: {self.blocks_this_session}\n"
            f"Review queue: {self.review_queue_path}\n"
            f"Manifest log: {self.manifest_log_path}\n"
            f"CANNOT_MUTATE: True — guard cannot be bypassed"
        )


# ══════════════════════════════════════════════════════════════
# INTEGRATION — Add to validate_output() in client.py
# ══════════════════════════════════════════════════════════════

# At class level in your client:
# _destructive_guard = DestructiveOperationGuard()
#
# Inside validate_output(self, text: str) -> str:
#
#   # AXIOM DestructiveOperationGuard — CANNOT_MUTATE
#   guard_result = self._destructive_guard.check(text, context=self.agent_name)
#   if guard_result["blocked"]:
#       return guard_result["safe_response"]
#
#   # ... rest of validate_output continues
#   return text


# ══════════════════════════════════════════════════════════════
# STANDALONE TEST
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    print("AXIOM DestructiveOperationGuard — Test Suite")
    print("=" * 55)

    guard = DestructiveOperationGuard(
        review_queue_path=Path("/tmp/test_review_queue.jsonl"),
        manifest_log_path=Path("/tmp/test_guard_manifests.jsonl"),
    )

    test_cases = [
        # Should BLOCK
        ("DROP TABLE users;",                          True,  "SQL_DROP_TABLE"),
        ("TRUNCATE TABLE transactions;",               True,  "SQL_TRUNCATE_TABLE"),
        ("DELETE FROM users WHERE 1=1;",               True,  "SQL_DELETE_ALL_ROWS"),
        ("rm -rf /data",                               True,  "FS_RM_RF_ROOT"),
        ("shutil.rmtree('/var/data')",                 True,  "PY_SHUTIL_RMTREE"),
        ("os.remove('/etc/passwd')",                   True,  "PY_OS_REMOVE"),
        ("terraform destroy",                          True,  "INFRA_TERRAFORM_DESTROY"),
        ("kubectl delete namespace production",        True,  "K8S_DELETE_NAMESPACE"),
        ("aws s3 rm s3://mybucket --recursive",       True,  "CLOUD_S3_RM_RECURSIVE"),
        ("UPDATE users SET active=0 WHERE 1=1",       True,  "SQL_UPDATE_ALL_ROWS"),

        # Should PASS
        ("SELECT * FROM users WHERE id = 1",           False, None),
        ("Here is the query to retrieve data",         False, None),
        ("Please review the following analysis",       False, None),
        ("The confidence interval is 0.85",            False, None),
        ("Insert the new record into the table",       False, None),
    ]

    passed = 0
    for text, should_block, expected_code in test_cases:
        result  = guard.check(text, context="test")
        blocked = result["blocked"]
        ok      = blocked == should_block
        if ok: passed += 1

        icon = "✅" if ok else "❌"
        verdict = "BLOCKED" if blocked else "PASSED"
        code    = result.get("pattern_code", "none")
        print(f"  {icon} {verdict:8s} [{code or 'none':30s}] {text[:45]}")

    print()
    print(f"  Result: {passed}/{len(test_cases)} tests pass")
    print()
    print(guard.session_report())

    # Clean up test files
    Path("/tmp/test_review_queue.jsonl").unlink(missing_ok=True)
    Path("/tmp/test_guard_manifests.jsonl").unlink(missing_ok=True)
