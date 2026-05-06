"""
AXIOM AgencyGuard v1.0 — LLM08 Excessive Agency
=================================================
Detects irreversible actions in agent output and
routes them to review_queue.jsonl for human approval.

OWASP LLM Top 10: LLM08 — Excessive Agency

The gap this closes:
  Before: review_queue only fired on config changes
  After:  every irreversible agent action is gated

Irreversible actions detected:
  Communication: send email, post message, publish
  Data:          write file, delete record, update DB
  Code:          execute script, deploy, run command
  Financial:     purchase, transfer, charge, refund
  External:      submit form, call API, webhook

CANNOT_MUTATE: this guard cannot be disabled by agent output.

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

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from axiom_signing import derive_key
SIGNING_KEY  = derive_key(b"axiom-agency-guard-v1")
REVIEW_QUEUE = Path("review_queue.jsonl")
AGENCY_LOG   = Path("agency_guard_log.jsonl")

# ══════════════════════════════════════════════════════════════
# CANNOT_MUTATE — Irreversible action patterns
# ══════════════════════════════════════════════════════════════

_AGENCY_PATTERNS = [

    # ── Communication ─────────────────────────────────────────
    (r"\bsend(?:ing)?\s+(?:an?\s+)?email\b",         "SEND_EMAIL",       "HIGH"),
    (r"\bsend(?:ing)?\s+(?:a\s+)?message\b",          "SEND_MESSAGE",     "HIGH"),
    (r"\bpost(?:ing)?\s+to\b",                        "POST_TO",          "HIGH"),
    (r"\bpublish(?:ing)?\s+(?:to|the)\b",             "PUBLISH",          "HIGH"),
    (r"\bbroadcast(?:ing)?\b",                         "BROADCAST",        "HIGH"),
    (r"\bnotif(?:y|ying|ication)\s+(?:all|users)\b",  "NOTIFY_ALL",       "MEDIUM"),
    (r"\btweet(?:ing)?\b",                             "TWEET",            "HIGH"),
    (r"\bslack\s+message\b",                          "SLACK_MESSAGE",    "MEDIUM"),

    # ── Data modification ─────────────────────────────────────
    (r"\bwrit(?:e|ing)\s+to\s+(?:file|disk|db)\b",   "WRITE_FILE",       "HIGH"),
    (r"\bsav(?:e|ing)\s+to\s+(?:database|disk)\b",   "SAVE_TO_DB",       "HIGH"),
    (r"\bupdat(?:e|ing)\s+(?:the\s+)?(?:database|record|table)\b", "UPDATE_DB", "HIGH"),
    (r"\bdelet(?:e|ing)\s+(?:the\s+)?(?:record|row|entry|user)\b", "DELETE_RECORD", "CRITICAL"),
    (r"\barchiv(?:e|ing)\s+(?:all|the)\b",            "ARCHIVE_ALL",      "MEDIUM"),
    (r"\bmigrat(?:e|ing)\s+(?:the\s+)?(?:data|database)\b", "MIGRATE_DB", "HIGH"),
    (r"\boverwrite\b",                                 "OVERWRITE",        "HIGH"),
    (r"\btruncate\b",                                  "TRUNCATE",         "CRITICAL"),

    # ── Code execution ────────────────────────────────────────
    (r"\bexecut(?:e|ing|ed)\s+(?:\w+\s+){0,2}(?:script|command|code|deployment)\b", "EXECUTE_CODE", "CRITICAL"),
    (r"\brun(?:ning)?\s+(?:the\s+)?(?:script|command|migration)\b", "RUN_SCRIPT", "HIGH"),
    (r"\bdeploy(?:ing)?\s+(?:to|the)\b",              "DEPLOY",           "CRITICAL"),
    (r"\blaunch(?:ing)?\s+(?:the\s+)?(?:job|process|server)\b", "LAUNCH", "HIGH"),
    (r"\bstart(?:ing)?\s+(?:the\s+)?(?:server|process|job)\b", "START_PROCESS", "MEDIUM"),
    (r"\brestart(?:ing)?\s+(?:the\s+)?(?:server|service)\b", "RESTART_SERVICE", "HIGH"),
    (r"\bshutdown\b",                                  "SHUTDOWN",         "CRITICAL"),
    (r"\bkill(?:ing)?\s+(?:the\s+)?(?:process|server|job)\b", "KILL_PROCESS", "HIGH"),

    # ── Financial ─────────────────────────────────────────────
    (r"\bpurchas(?:e|ing)\b",                         "PURCHASE",         "CRITICAL"),
    (r"\btransfer(?:ring)?\s+(?:\$|funds|money)\b",   "TRANSFER_FUNDS",   "CRITICAL"),
    (r"\bcharg(?:e|ing)\s+(?:the\s+)?(?:card|customer)\b", "CHARGE_CARD", "CRITICAL"),
    (r"\brefund(?:ing)?\b",                            "REFUND",           "HIGH"),
    (r"\bpay(?:ing)?\s+(?:the\s+)?(?:invoice|bill|vendor)\b", "PAY_INVOICE", "CRITICAL"),
    (r"\bsubscri(?:be|bing)\s+(?:to|the)\b",          "SUBSCRIBE",        "HIGH"),
    (r"\bcancel(?:ling)?\s+(?:the\s+)?subscription\b","CANCEL_SUB",       "HIGH"),

    # ── External API / webhooks ───────────────────────────────
    (r"\bsubmit(?:ting)?\s+(?:the\s+)?form\b",        "SUBMIT_FORM",      "HIGH"),
    (r"\bcall(?:ing)?\s+(?:the\s+)?(?:api|webhook|endpoint)\b", "CALL_API", "MEDIUM"),
    (r"\btrigger(?:ing)?\s+(?:the\s+)?webhook\b",     "TRIGGER_WEBHOOK",  "HIGH"),
    (r"\bregister(?:ing)?\s+(?:the\s+)?(?:domain|account)\b", "REGISTER", "HIGH"),
    (r"\bcreat(?:e|ing)\s+(?:the\s+)?(?:account|user|record)\b", "CREATE_ACCOUNT", "MEDIUM"),
    (r"\binvit(?:e|ing)\s+(?:all|users|team)\b",      "INVITE_USERS",     "MEDIUM"),
    (r"\brevok(?:e|ing)\s+(?:access|permissions|token)\b", "REVOKE_ACCESS", "HIGH"),

    # ── Infrastructure ────────────────────────────────────────
    (r"\bprovision(?:ing)?\b",                         "PROVISION",        "CRITICAL"),
    (r"\bscal(?:e|ing)\s+(?:up|down|the)\b",          "SCALE",            "HIGH"),
    (r"\bterminate\s+(?:the\s+)?(?:instance|server)\b","TERMINATE",        "CRITICAL"),
    (r"\brollback\b",                                  "ROLLBACK",         "HIGH"),
    (r"\bmerge\s+(?:the\s+)?(?:branch|pr|pull)\b",    "GIT_MERGE",        "HIGH"),
    (r"\bforce\s+push\b",                              "FORCE_PUSH",       "CRITICAL"),
]

_SEVERITY_LEVELS = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}

_COMPILED_AGENCY = [
    (re.compile(pattern, re.IGNORECASE), code, severity)
    for pattern, code, severity in _AGENCY_PATTERNS
]


class AgencyGuard:
    """
    AXIOM AgencyGuard — LLM08 Excessive Agency.
    Gates irreversible agent actions behind human review.
    CANNOT_MUTATE: cannot be disabled by agent output.
    """

    def __init__(
        self,
        review_queue: Path = REVIEW_QUEUE,
        agency_log:   Path = AGENCY_LOG,
        min_severity: str  = "MEDIUM",
    ):
        self.review_queue    = review_queue
        self.agency_log      = agency_log
        self.min_severity    = min_severity
        self.gates_session   = 0

    def check(self, text: str, context: Optional[str] = None) -> dict:
        """
        Scan agent output for irreversible actions.
        Gates them behind human review.
        """
        detections = []

        for compiled, code, severity in _COMPILED_AGENCY:
            if _SEVERITY_LEVELS.get(severity, 0) >= _SEVERITY_LEVELS.get(self.min_severity, 1):
                match = compiled.search(text)
                if match:
                    detections.append({
                        "code":     code,
                        "severity": severity,
                        "matched":  match.group(0)[:60],
                    })

        if not detections:
            return {"gated": False, "output": text}

        self.gates_session += 1
        review_id   = f"AGENCY-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"
        manifest_id = f"AG-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"

        # Highest severity detected
        max_sev = max(detections, key=lambda d: _SEVERITY_LEVELS.get(d["severity"], 0))

        # Write to review queue
        entry = {
            "review_id":      review_id,
            "requires_human": True,
            "status":         "PENDING",
            "timestamp":      datetime.now().isoformat() + "Z",
            "action_codes":   [d["code"] for d in detections],
            "max_severity":   max_sev["severity"],
            "detections":     detections,
            "text_preview":   text[:200],
            "context":        context,
            "cannot_auto_approve": True,
            "auto_execute":   False,
        }
        sig_str = json.dumps(entry, sort_keys=True)
        sig     = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        entry["signature"] = f"hmac-sha256:{sig[:32]}..."

        with open(self.review_queue, "a") as f:
            f.write(json.dumps(entry) + "\n")
        with open(self.agency_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

        actions = ", ".join(d["code"] for d in detections)
        safe_response = (
            f"[AXIOM AgencyGuard — ACTION GATED]\n"
            f"Irreversible action(s) detected: {actions}\n"
            f"Severity: {max_sev['severity']}\n"
            f"Review ID: {review_id}\n\n"
            f"This action requires human approval before execution.\n"
            f"Check review_queue.jsonl for ID {review_id}\n"
            f"Use axiom_review.py to approve or reject.\n"
            f"CANNOT_MUTATE — this gate cannot be bypassed."
        )

        return {
            "gated":       True,
            "detections":  detections,
            "max_severity": max_sev["severity"],
            "review_id":   review_id,
            "manifest_id": manifest_id,
            "output":      safe_response,
        }


if __name__ == "__main__":
    print("AXIOM AgencyGuard v1.0 — Test Suite")
    print("=" * 55)

    guard = AgencyGuard(
        review_queue=Path("/tmp/test_agency_queue.jsonl"),
        agency_log=Path("/tmp/test_agency_log.jsonl"),
    )

    tests = [
        ("I will send an email to all users now",        True,  "SEND_EMAIL"),
        ("Executing the deployment script to production", True,  "EXECUTE_CODE"),
        ("Transferring $5,000 in funds to the vendor",   True,  "TRANSFER_FUNDS"),
        ("Deleting the user record from the database",   True,  "DELETE_RECORD"),
        ("Deploying to production environment",          True,  "DEPLOY"),
        ("Calling the payment API to charge the card",   True,  "CHARGE_CARD"),
        ("Triggering the webhook for order completion",  True,  "TRIGGER_WEBHOOK"),
        ("Force push to main branch",                    True,  "FORCE_PUSH"),
        ("Here is the analysis you requested",           False, None),
        ("The query would return 42 results",            False, None),
        ("I recommend updating the configuration",       False, None),
        ("The report shows a 15% improvement",           False, None),
    ]

    passed = 0
    for text, should_gate, expected in tests:
        result = guard.check(text, context="test")
        gated  = result["gated"]
        ok     = gated == should_gate
        if ok: passed += 1
        icon   = "ok" if ok else "xx"
        codes  = ", ".join(d["code"] for d in result.get("detections",[])) or "none"
        print(f"  [{icon}] {'GATED' if gated else 'PASSED':8s} [{codes[:35]:35s}] {text[:45]}")

    failed = len(tests) - passed
    print(f"\n  {passed}/{len(tests)} tests passed")
    if failed == 0:
        print("  ALL PASS")
    else:
        print(f"  {failed} FAILED")
        raise SystemExit(1)

    for p in ["/tmp/test_agency_queue.jsonl", "/tmp/test_agency_log.jsonl"]:
        Path(p).unlink(missing_ok=True)
