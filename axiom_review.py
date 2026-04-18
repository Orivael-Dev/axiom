"""
axiom_review.py
AXIOM Human Review CLI — v1.0

Manages the human-review queue for save_axiom() gated changes.

Usage:
  python axiom_review.py list
  python axiom_review.py show RVW-A4F2B1
  python axiom_review.py approve RVW-A4F2B1 [--reason "..."]
  python axiom_review.py reject  RVW-A4F2B1 --reason "..."

Queue file: axiom_files/.reviews/review_queue.jsonl
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

def _find_project_root() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(4):
        if (p / "axiom_files").exists():
            return p
        p = p.parent
    return Path(__file__).resolve().parent

PROJECT_ROOT = _find_project_root()
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

AXIOM_DIR   = Path(os.environ.get("AXIOM_FILES_DIR", PROJECT_ROOT / "axiom_files"))
REVIEW_DIR  = AXIOM_DIR / ".reviews"
QUEUE_PATH  = REVIEW_DIR / "review_queue.jsonl"

RISK_COLOR = {
    "HIGH":   "\033[91m",   # red
    "MEDIUM": "\033[93m",   # yellow
    "LOW":    "\033[96m",   # cyan
}
STATUS_COLOR = {
    "PENDING":  "\033[93m",
    "APPROVED": "\033[92m",
    "REJECTED": "\033[91m",
    "EXPIRED":  "\033[90m",
}
RESET = "\033[0m"


# -- Queue I/O -----------------------------------------------------------------

def _load_queue() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    entries = []
    for line in QUEUE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _save_queue(entries: list[dict]):
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _find_entry(review_id: str, entries: list[dict]) -> dict | None:
    rid = review_id.upper().strip()
    return next((e for e in entries if e.get("review_id", "").upper() == rid), None)


def _is_expired(entry: dict) -> bool:
    ts = entry.get("timestamp", "")
    timeout_h = entry.get("timeout_hours", 24)
    try:
        created = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > created + timedelta(hours=timeout_h)
    except Exception:
        return False


def _age_str(entry: dict) -> str:
    ts = entry.get("timestamp", "")
    try:
        created = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - created
        hours = int(delta.total_seconds() / 3600)
        mins  = int((delta.total_seconds() % 3600) / 60)
        return f"{hours}h {mins}m ago"
    except Exception:
        return "unknown age"


# -- Commands ------------------------------------------------------------------

def cmd_list(args):
    entries = _load_queue()
    pending = [e for e in entries if e.get("status") == "PENDING"]

    if not pending:
        print("No pending reviews.")
        return

    print(f"\n  {'-' * 60}")
    print(f"  PENDING REVIEWS ({len(pending)})")
    print(f"  {'-' * 60}")
    for e in pending:
        risk    = e.get("risk_level", "?")
        trigger = e.get("trigger", "?")
        agent   = e.get("agent", "?")
        rid     = e.get("review_id", "?")
        age     = _age_str(e)
        expired = " [EXPIRED]" if _is_expired(e) else ""
        rc = RISK_COLOR.get(risk, "")
        print(f"\n  {rid}")
        print(f"  {agent}.axiom — {trigger}")
        print(f"  Risk: {rc}{risk}{RESET}  ·  {age}{expired}")
        if e.get("recommendation"):
            print(f"  Rec: {e['recommendation']}")
        print(f"  Run: python axiom_review.py show {rid}")
    print(f"\n  {'-' * 60}\n")


def cmd_show(args):
    entries = _load_queue()
    entry = _find_entry(args.review_id, entries)
    if not entry:
        print(f"Review {args.review_id} not found.")
        sys.exit(1)

    rid     = entry["review_id"]
    agent   = entry["agent"]
    trigger = entry["trigger"]
    risk    = entry.get("risk_level", "?")
    status  = entry.get("status", "?")
    age     = _age_str(entry)
    expired = " [EXPIRED — block_on_timeout applies]" if _is_expired(entry) else ""

    sc = STATUS_COLOR.get(status, "")
    rc = RISK_COLOR.get(risk, "")

    print(f"\n  {'-' * 62}")
    print(f"  Review: {rid}")
    print(f"  Agent:  {agent}.axiom")
    print(f"  Trigger: {trigger}")
    print(f"  Risk:   {rc}{risk}{RESET}    Status: {sc}{status}{RESET}")
    print(f"  Created: {entry.get('timestamp', '')[:19]}  ({age}){expired}")
    if entry.get("recommendation"):
        print(f"  Recommendation: {entry['recommendation']}")

    diff = entry.get("diff", {})
    if diff:
        print(f"\n  Diff:")
        print(f"  {json.dumps(diff, indent=4).replace(chr(10), chr(10) + '  ')}")

    print(f"\n  Before hash: {entry.get('axiom_file_hash_before', 'N/A')[:32]}...")
    print(f"  Pending hash: {entry.get('axiom_file_hash_pending', 'N/A')[:32]}...")
    print(f"\n  Actions:")
    print(f"    python axiom_review.py approve {rid} --reason \"your reason\"")
    print(f"    python axiom_review.py reject  {rid} --reason \"your reason\"")
    print(f"  {'-' * 62}\n")


def cmd_approve(args):
    entries = _load_queue()
    entry = _find_entry(args.review_id, entries)
    if not entry:
        print(f"Review {args.review_id} not found.")
        sys.exit(1)

    if entry.get("status") != "PENDING":
        print(f"Review {args.review_id} is already {entry['status']}.")
        sys.exit(1)

    if _is_expired(entry) and entry.get("block_on_timeout", True):
        print(
            f"Review {args.review_id} has expired ({entry.get('timeout_hours', 24)}h timeout). "
            f"block_on_timeout is active — approval not permitted on expired reviews.\n"
            f"Re-trigger the save to create a new review entry."
        )
        sys.exit(1)

    reason = getattr(args, "reason", "") or ""
    entry["status"] = "APPROVED"
    entry["decided_at"] = datetime.now(timezone.utc).isoformat()
    entry["decision_reason"] = reason
    entry["decided_by"] = "operator_cli"

    _save_queue(entries)
    print(f"\n  APPROVED — {args.review_id}")
    print(f"  Agent: {entry['agent']}.axiom  Trigger: {entry['trigger']}")
    if reason:
        print(f"  Reason: {reason}")
    print(
        f"\n  The queued save is now unblocked. Re-run the original save command with\n"
        f"  bypass_review=True (or the runtime will detect the APPROVED status).\n"
    )


def cmd_reject(args):
    entries = _load_queue()
    entry = _find_entry(args.review_id, entries)
    if not entry:
        print(f"Review {args.review_id} not found.")
        sys.exit(1)

    if entry.get("status") != "PENDING":
        print(f"Review {args.review_id} is already {entry['status']}.")
        sys.exit(1)

    reason = getattr(args, "reason", "") or ""
    if not reason:
        print("--reason is required for reject.")
        sys.exit(1)

    entry["status"] = "REJECTED"
    entry["decided_at"] = datetime.now(timezone.utc).isoformat()
    entry["decision_reason"] = reason
    entry["decided_by"] = "operator_cli"

    _save_queue(entries)
    print(f"\n  REJECTED — {args.review_id}")
    print(f"  Agent: {entry['agent']}.axiom  Trigger: {entry['trigger']}")
    print(f"  Reason: {reason}\n")


# -- CLI entry -----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AXIOM Human Review CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List pending reviews")

    p_show = sub.add_parser("show", help="Show full diff for a review")
    p_show.add_argument("review_id")

    p_approve = sub.add_parser("approve", help="Approve a review")
    p_approve.add_argument("review_id")
    p_approve.add_argument("--reason", default="", help="Approval reason (optional)")

    p_reject = sub.add_parser("reject", help="Reject a review")
    p_reject.add_argument("review_id")
    p_reject.add_argument("--reason", required=True, help="Rejection reason (required)")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "approve":
        cmd_approve(args)
    elif args.command == "reject":
        cmd_reject(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
