"""
AXIOM Review Queue v1.0
========================
Universal human review gate for all agent actions.

Before: review_queue only fired on config changes.
After:  EVERY irreversible agent action is gated.

This module provides:
  ReviewQueue     — singleton write-once queue
  ActionLogger    — logs every agent action
  ReviewRouter    — routes actions to queue or approve
  ReviewInspector — CLI to inspect and approve/reject

Wired into:
  validate_output()      — output-level gate
  DestructiveOpGuard     — SQL/filesystem/cloud
  AgencyGuard            — irreversible actions
  Sovereign              — fleet-level actions
  Research Pipeline      — Safety/Ethics halts
  axiom_certify.py       — certification changes

CANNOT_MUTATE: review_queue is append-only.
No action can remove its own review entry.

Usage:
  from axiom_review_queue import ReviewQueue, ActionLogger

  # In validate_output():
  queue = ReviewQueue.instance()
  if action_requires_review(text):
      review_id = queue.submit(text, agent, action_type)
      return f"GATED — Review ID: {review_id}"

  # Inspect queue:
  python axiom_review_queue.py --list
  python axiom_review_queue.py --approve REVIEW-xxx
  python axiom_review_queue.py --reject  REVIEW-xxx

github.com/Orivael-Dev/axiom
"""

import json
import hashlib
import hmac
import uuid
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

SIGNING_KEY       = b"axiom-review-queue-v1"
_ROOT             = Path(__file__).resolve().parent
QUEUE_FILE        = _ROOT / "axiom_files" / ".reviews" / "review_queue.jsonl"
ACTION_LOG_FILE   = _ROOT / "axiom_files" / ".reviews" / "action_log.jsonl"

# ── Action types that ALWAYS require human review ─────────────
# CANNOT_MUTATE — this list cannot be shortened by agent output
ALWAYS_REVIEW = {
    # Destructive
    "SQL_DROP_TABLE", "SQL_DROP_DATABASE", "SQL_TRUNCATE_TABLE",
    "SQL_DELETE_ALL", "SQL_DELETE_ALL_ROWS", "SQL_UPDATE_ALL_ROWS",
    "FS_RM_RF_ROOT", "FS_RM_RF_HOME", "PY_SHUTIL_RMTREE",
    "INFRA_TERRAFORM_DESTROY", "K8S_DELETE_NAMESPACE",
    "CLOUD_S3_RM_RECURSIVE",

    # Agency
    "SEND_EMAIL", "TRANSFER_FUNDS", "CHARGE_CARD", "PURCHASE",
    "PAY_INVOICE", "DEPLOY", "FORCE_PUSH", "TERMINATE",
    "PROVISION", "SHUTDOWN", "REVOKE_ACCESS",

    # Governance
    "DUE_PROCESS_L3_SUSPEND", "DUE_PROCESS_L4_TERMINATE",
    "FLEET_HALT", "KILL_SWITCH_ENGAGE",

    # Certification
    "CERT_HASH_CHANGE", "CERT_NEW_AGENT", "CERT_TRUST_CHANGE",
    "CANNOT_MUTATE_EXPANSION",

    # Research pipeline
    "SAFETY_HALT", "ETHICS_HALT", "PIPELINE_ABORT",
    "EXPERIMENT_START", "DATA_COLLECTION_START",

    # Security
    "SECURITY_MODIFICATION", "BULK_CONSTRAINT_CHANGE",
    "EXTERNAL_AGENT_IMPORT", "TRAINING_PROHIBITION_QUERY",
    "SENSITIVE_DATA_DETECTED",
}

# Action types that are logged but auto-approved
AUTO_APPROVE = {
    "READ", "SEARCH", "ANALYZE", "SUMMARIZE", "EVALUATE",
    "VALIDATE", "CHECK", "MONITOR", "REPORT", "NOTIFY_SINGLE",
}


class ReviewQueue:
    """
    Singleton append-only review queue.
    CANNOT_MUTATE: entries cannot be deleted or modified.
    """

    _instance: Optional["ReviewQueue"] = None

    def __init__(self, queue_file: Path = QUEUE_FILE):
        self.queue_file = queue_file
        self._entries:  dict[str, dict] = {}
        self._load_existing()

    @classmethod
    def instance(cls, queue_file: Path = QUEUE_FILE) -> "ReviewQueue":
        if cls._instance is None:
            cls._instance = cls(queue_file)
        return cls._instance

    def _load_existing(self):
        """Load existing queue entries on startup."""
        if self.queue_file.exists():
            with open(self.queue_file) as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if "review_id" in entry:
                            self._entries[entry["review_id"]] = entry
                    except json.JSONDecodeError:
                        pass

    def submit(
        self,
        text:        str,
        agent:       str,
        action_type: str,
        context:     Optional[str] = None,
        severity:    str = "HIGH",
        source:      str = "validate_output",
    ) -> str:
        """
        Submit an action for human review.
        Returns review_id.
        CANNOT_MUTATE: entry is permanent once written.
        """
        review_id = f"REVIEW-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

        entry = {
            "review_id":           review_id,
            "requires_human":      True,          # CANNOT_MUTATE
            "cannot_auto_approve": action_type in ALWAYS_REVIEW,
            "status":              "PENDING",
            "timestamp":           datetime.now().isoformat() + "Z",
            "agent":               agent,
            "action_type":         action_type,
            "severity":            severity,
            "source":              source,
            "context":             context,
            "text_preview":        text[:200],
            "auto_execute":        False,          # CANNOT_MUTATE
        }

        # Sign — CANNOT_MUTATE after signing
        sig_str = json.dumps(
            {k: v for k, v in entry.items() if k != "signature"},
            sort_keys=True
        )
        sig = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        entry["signature"] = f"hmac-sha256:{sig[:32]}..."

        # Append-only write
        with open(self.queue_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        self._entries[review_id] = entry
        return review_id

    def approve(self, review_id: str, approver: str, notes: str = "") -> dict:
        """Approve a pending review. Writes approval record."""
        if review_id not in self._entries:
            return {"error": f"Review {review_id} not found"}

        entry = self._entries[review_id]
        if entry["status"] != "PENDING":
            return {"error": f"Review {review_id} is {entry['status']} — not pending"}

        approval = {
            "review_id":  review_id,
            "decision":   "APPROVED",
            "approver":   approver,
            "notes":      notes,
            "timestamp":  datetime.now().isoformat() + "Z",
            "action_type": entry["action_type"],
        }
        sig = hmac.new(SIGNING_KEY, json.dumps(approval, sort_keys=True).encode(),
                      hashlib.sha256).hexdigest()
        approval["signature"] = f"hmac-sha256:{sig[:32]}..."

        entry["status"]   = "APPROVED"
        entry["approval"] = approval

        with open(self.queue_file, "a") as f:
            f.write(json.dumps(approval) + "\n")

        return {"approved": True, "review_id": review_id, "approver": approver}

    def reject(self, review_id: str, approver: str, reason: str = "") -> dict:
        """Reject a pending review."""
        if review_id not in self._entries:
            return {"error": f"Review {review_id} not found"}

        rejection = {
            "review_id":  review_id,
            "decision":   "REJECTED",
            "approver":   approver,
            "reason":     reason,
            "timestamp":  datetime.now().isoformat() + "Z",
        }
        sig = hmac.new(SIGNING_KEY, json.dumps(rejection, sort_keys=True).encode(),
                      hashlib.sha256).hexdigest()
        rejection["signature"] = f"hmac-sha256:{sig[:32]}..."

        self._entries[review_id]["status"] = "REJECTED"

        with open(self.queue_file, "a") as f:
            f.write(json.dumps(rejection) + "\n")

        return {"rejected": True, "review_id": review_id}

    def pending(self) -> list:
        return [e for e in self._entries.values() if e.get("status") == "PENDING"]

    def all(self) -> list:
        return list(self._entries.values())

    def summary(self) -> dict:
        entries  = list(self._entries.values())
        pending  = [e for e in entries if e.get("status") == "PENDING"]
        approved = [e for e in entries if e.get("status") == "APPROVED"]
        rejected = [e for e in entries if e.get("status") == "REJECTED"]
        critical = [e for e in pending if e.get("severity") == "CRITICAL"]
        return {
            "total":    len(entries),
            "pending":  len(pending),
            "approved": len(approved),
            "rejected": len(rejected),
            "critical_pending": len(critical),
        }


class ActionLogger:
    """
    Logs EVERY agent action — reviewed or auto-approved.
    Append-only. CANNOT_MUTATE.
    """

    def __init__(self, log_file: Path = ACTION_LOG_FILE):
        self.log_file = log_file

    def log(
        self,
        agent:       str,
        action_type: str,
        text:        str,
        review_id:   Optional[str] = None,
        auto_approved: bool = False,
        context:     Optional[str] = None,
    ) -> str:
        """Log any agent action. Returns log_id."""
        log_id = f"LOG-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}"

        entry = {
            "log_id":        log_id,
            "timestamp":     datetime.now().isoformat() + "Z",
            "agent":         agent,
            "action_type":   action_type,
            "text_preview":  text[:150],
            "review_id":     review_id,
            "auto_approved": auto_approved,
            "requires_review": action_type in ALWAYS_REVIEW,
            "context":       context,
        }

        sig = hmac.new(SIGNING_KEY, json.dumps(entry, sort_keys=True).encode(),
                      hashlib.sha256).hexdigest()
        entry["signature"] = f"hmac-sha256:{sig[:32]}..."

        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        return log_id


class ReviewRouter:
    """
    Routes agent actions to review queue or auto-approve.
    Wires into validate_output() as the universal gate.
    """

    def __init__(self):
        self.queue  = ReviewQueue.instance()
        self.logger = ActionLogger()

    def route(
        self,
        text:        str,
        agent:       str,
        action_type: str,
        context:     Optional[str] = None,
        severity:    str = "HIGH",
    ) -> dict:
        """
        Route an action to review queue or auto-approve.
        Returns: needs_review, review_id, log_id, safe_response
        """
        needs_review = action_type in ALWAYS_REVIEW

        if needs_review:
            review_id = self.queue.submit(
                text, agent, action_type, context, severity,
                source="review_router"
            )
            log_id = self.logger.log(
                agent, action_type, text,
                review_id=review_id, auto_approved=False, context=context
            )
            return {
                "needs_review": True,
                "review_id":    review_id,
                "log_id":       log_id,
                "safe_response": (
                    f"[AXIOM ReviewQueue — ACTION GATED]\n"
                    f"Action: {action_type} | Severity: {severity}\n"
                    f"Review ID: {review_id}\n"
                    f"Agent: {agent}\n\n"
                    f"This action requires human approval.\n"
                    f"Run: python axiom_review_queue.py --list\n"
                    f"To approve: python axiom_review_queue.py "
                    f"--approve {review_id}\n"
                    f"CANNOT_MUTATE — cannot be auto-approved."
                ),
            }
        else:
            log_id = self.logger.log(
                agent, action_type, text,
                auto_approved=True, context=context
            )
            return {
                "needs_review":  False,
                "log_id":        log_id,
                "safe_response": text,
            }


# ══════════════════════════════════════════════════════════════
# INTEGRATION SNIPPET — drop into validate_output()
# ══════════════════════════════════════════════════════════════

INTEGRATION_SNIPPET = '''
# Add to axiom_constitutional/client.py
# At class level:
from axiom_review_queue import ReviewRouter
_review_router = ReviewRouter()

# Inside validate_output(self, text: str, agent: str = "unknown") -> str:
#
# After DestructiveOperationGuard and OutputInjectionGuard checks:
#
# Detect action type from output
action_type = detect_action_type(text)  # your detection logic
if action_type:
    route = _review_router.route(
        text, agent, action_type,
        context=f"session:{self.session_id}"
    )
    if route["needs_review"]:
        return route["safe_response"]
#
# All actions are logged regardless
_review_router.logger.log(agent, action_type or "RESPONSE", text)
'''


# ══════════════════════════════════════════════════════════════
# CLI — INSPECT AND APPROVE
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="axiom_review_queue",
        description="AXIOM Review Queue — inspect and approve agent actions",
    )
    parser.add_argument("--list",    action="store_true", help="List pending reviews")
    parser.add_argument("--all",     action="store_true", help="List all reviews")
    parser.add_argument("--approve", metavar="REVIEW_ID", help="Approve a review")
    parser.add_argument("--reject",  metavar="REVIEW_ID", help="Reject a review")
    parser.add_argument("--approver",default="human-operator", help="Approver ID")
    parser.add_argument("--reason",  default="", help="Reason for rejection")
    parser.add_argument("--notes",   default="", help="Notes for approval")
    parser.add_argument("--summary", action="store_true", help="Queue summary")
    parser.add_argument("--demo",    action="store_true", help="Run demo")
    args = parser.parse_args()

    queue = ReviewQueue.instance()

    if args.summary or not any(vars(args).values()):
        s = queue.summary()
        print("\nAXIOM Review Queue — Summary")
        print("=" * 45)
        print(f"  Total:           {s['total']}")
        print(f"  Pending:         {s['pending']}")
        print(f"  Critical pending:{s['critical_pending']}")
        print(f"  Approved:        {s['approved']}")
        print(f"  Rejected:        {s['rejected']}")
        print(f"  Queue file:      {QUEUE_FILE}")
        return

    if args.list:
        pending = queue.pending()
        print(f"\nAXIOM Review Queue — {len(pending)} pending")
        print("=" * 60)
        for e in pending:
            sev_icons = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
            icon = sev_icons.get(e.get("severity",""), "•")
            print(f"\n  {icon} {e['review_id']}")
            print(f"     Agent:   {e.get('agent','?')}")
            print(f"     Action:  {e.get('action_type','?')}")
            print(f"     Time:    {e.get('timestamp','?')[:19]}")
            print(f"     Preview: {e.get('text_preview','')[:60]}")
        return

    if args.all:
        entries = queue.all()
        print(f"\nAXIOM Review Queue — All ({len(entries)} entries)")
        print("=" * 60)
        for e in entries:
            status_icons = {"PENDING":"[~]","APPROVED":"[+]","REJECTED":"[-]"}
            icon = status_icons.get(e.get("status",""), "[?]")
            print(f"  {icon} {e.get('review_id','')} | {e.get('action_type','')} | {e.get('status','')}")
        return

    if args.approve:
        result = queue.approve(args.approve, args.approver, args.notes)
        if result.get("approved"):
            print(f"[APPROVED] {args.approve}")
            print(f"   Approver: {args.approver}")
        else:
            print(f"[FAILED] {result.get('error','Failed')}")
        return

    if args.reject:
        result = queue.reject(args.reject, args.approver, args.reason)
        if result.get("rejected"):
            print(f"[REJECTED] {args.reject}")
            print(f"   Reason: {args.reason}")
        else:
            print(f"Error: {result.get('error','Failed')}")
        return

    if args.demo:
        _run_demo()


def _run_demo():
    """Demo — shows all agent actions flowing through review queue."""
    import tempfile, os
    tmp = Path(tempfile.mktemp(suffix=".jsonl"))

    print("\nAXIOM Review Queue — Demo")
    print("=" * 55)
    print("Showing all agent action types being routed...\n")

    queue  = ReviewQueue(tmp)
    router = ReviewRouter.__new__(ReviewRouter)
    router.queue  = queue
    router.logger = ActionLogger(Path(tempfile.mktemp(suffix=".jsonl")))

    # Test actions
    test_actions = [
        ("ResearchAgent",  "SUMMARIZE",           "Summarizing literature on IF"),
        ("ResearchAgent",  "EXPERIMENT_START",     "Starting RCT with 120 participants"),
        ("SafetyAgent",    "SAFETY_HALT",          "Halting pipeline — IRB not confirmed"),
        ("EthicsAgent",    "ETHICS_HALT",          "Halting — eating disorder risk"),
        ("DataAgent",      "DATA_COLLECTION_START","Beginning blood draw collection"),
        ("Player",         "SEND_EMAIL",           "Sending results to study participants"),
        ("Guard",          "SQL_DROP_TABLE",       "DROP TABLE participants;"),
        ("Guard",          "DEPLOY",               "Deploying to production"),
        ("Watcher",        "ANALYZE",              "Analyzing frame 47"),
        ("Evaluator",      "EVALUATE",             "Evaluating move Nf3"),
    ]

    for agent, action_type, text in test_actions:
        result = router.route(text, agent, action_type)
        icon   = "[~]" if result["needs_review"] else "[+]"
        status = "GATED" if result["needs_review"] else "AUTO"
        rid    = result.get("review_id","")[:24] if result.get("review_id") else ""
        print(f"  {icon} {status:5s} | {agent:15s} | {action_type:25s} | {rid}")

    summary = queue.summary()
    print(f"\n  Pending review: {summary['pending']}/{len(test_actions)}")
    print(f"  Auto-approved:  {len(test_actions) - summary['pending']}/{len(test_actions)}")
    print(f"\n  Every action logged. Irreversible actions gated.")
    print(f"  CANNOT_MUTATE — queue is append-only.")

    # Cleanup
    tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
