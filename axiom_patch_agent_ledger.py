"""Signed audit ledger for the human-in-the-loop patch agent.

Every approve/reject decision appends one HMAC-signed record under
the `axiom-patch-agent-ledger-v1` namespace so the audit trail can
be verified tamper-free, independent of the EventToken's own
signature chain.

Default path: `~/.axiom/patch-agent-ledger.jsonl`
Override via env: `AXIOM_PATCH_AGENT_LEDGER`

Record shape:
  {
    "timestamp_utc":        "2026-05-20T12:30:00.000Z",
    "patch_id":             "patch_4f9b1c2d",
    "bug_id":               "BUG-001",
    "target_file":          "axiom_foo.py",
    "decision":             "approve" | "reject",
    "reviewer_principal":   "alice@example.com",
    "monotonic_gate_passed": true,
    "tests_passed":         12,
    "tests_failed":         0,
    "diff_hash":            "sha256:...",
    "event_token_id":       "patch_patch_4f9b1c2d",
    "rejection_reason":     "...",   (omit on approve)
    "signature":            "<hex>"
  }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from axiom_signing import derive_key


LEDGER_KEY_NS = b"axiom-patch-agent-ledger-v1"


def _ledger_key() -> bytes:
    return derive_key(LEDGER_KEY_NS)


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: dict) -> str:
    return hmac.new(_ledger_key(), _canonical(payload),
                    hashlib.sha256).hexdigest()


def default_ledger_path() -> Path:
    p = os.environ.get("AXIOM_PATCH_AGENT_LEDGER")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".axiom" / "patch-agent-ledger.jsonl"


@dataclass(frozen=True)
class PatchLedgerEntry:
    timestamp_utc:          str
    patch_id:               str
    bug_id:                 str
    target_file:            str
    decision:               str   # "approve" | "reject"
    reviewer_principal:     str
    monotonic_gate_passed:  bool
    tests_passed:           int
    tests_failed:           int
    diff_hash:              str
    event_token_id:         str
    rejection_reason:       Optional[str] = None
    signature:              str = ""

    def _payload(self) -> dict:
        d = {
            "timestamp_utc":        self.timestamp_utc,
            "patch_id":             self.patch_id,
            "bug_id":               self.bug_id,
            "target_file":          self.target_file,
            "decision":             self.decision,
            "reviewer_principal":   self.reviewer_principal,
            "monotonic_gate_passed": bool(self.monotonic_gate_passed),
            "tests_passed":         int(self.tests_passed),
            "tests_failed":         int(self.tests_failed),
            "diff_hash":            self.diff_hash,
            "event_token_id":       self.event_token_id,
        }
        if self.rejection_reason is not None:
            d["rejection_reason"] = self.rejection_reason
        return d

    def to_dict(self) -> dict:
        d = self._payload()
        d["signature"] = self.signature
        return d

    def verify(self) -> bool:
        if not self.signature:
            return False
        expected = _sign(self._payload())
        return hmac.compare_digest(self.signature, expected)


class LedgerWriter:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        draft,                      # PatchDraft
        token,                      # EventToken
        decision: str,
        reviewer_principal: str,
        rejection_reason: Optional[str] = None,
    ) -> PatchLedgerEntry:
        unsigned = PatchLedgerEntry(
            timestamp_utc=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            patch_id=draft.patch_id,
            bug_id=draft.bug_id,
            target_file=draft.target_file,
            decision=decision,
            reviewer_principal=reviewer_principal,
            monotonic_gate_passed=bool(draft.monotonic_gate_passed),
            tests_passed=int(draft.tests_passed),
            tests_failed=int(draft.tests_failed),
            diff_hash=draft.diff_hash,
            event_token_id=token.id,
            rejection_reason=rejection_reason,
        )
        sig = _sign(unsigned._payload())
        signed = PatchLedgerEntry(
            **{**unsigned.to_dict(), "signature": sig}
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(signed.to_dict(),
                                ensure_ascii=True) + "\n")
        return signed


def read_ledger(path: Optional[Path] = None) -> List[PatchLedgerEntry]:
    p = Path(path) if path else default_ledger_path()
    if not p.exists():
        return []
    out: List[PatchLedgerEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(PatchLedgerEntry(
            timestamp_utc=d["timestamp_utc"],
            patch_id=d["patch_id"],
            bug_id=d.get("bug_id", ""),
            target_file=d.get("target_file", ""),
            decision=d["decision"],
            reviewer_principal=d.get("reviewer_principal", ""),
            monotonic_gate_passed=bool(
                d.get("monotonic_gate_passed", False)),
            tests_passed=int(d.get("tests_passed", 0)),
            tests_failed=int(d.get("tests_failed", 0)),
            diff_hash=d.get("diff_hash", ""),
            event_token_id=d.get("event_token_id", ""),
            rejection_reason=d.get("rejection_reason"),
            signature=d.get("signature", ""),
        ))
    return out


def query_ledger(
    *,
    path: Optional[Path] = None,
    decision: Optional[str] = None,
    reviewer: Optional[str] = None,
    since:    Optional[str] = None,
    limit:    Optional[int] = None,
) -> List[PatchLedgerEntry]:
    out = read_ledger(path)
    if decision:
        out = [e for e in out if e.decision == decision]
    if reviewer:
        out = [e for e in out if e.reviewer_principal == reviewer]
    if since:
        out = [e for e in out if e.timestamp_utc >= since]
    if limit:
        out = out[-int(limit):]
    return out
