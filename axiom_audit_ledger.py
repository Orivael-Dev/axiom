"""
AXIOM Audit Ledger — general append-only signed event log.
==========================================================
A small, general-purpose audit trail: every record is HMAC-signed and the
file is append-only JSONL. Where ``axiom_exoskeleton_ledger`` records
LLM-transport facts for one EventToken call, this records *arbitrary*
governance events — "record every action, denial, state change, and
permission decision" — which is what AX OS's signed audit manifests need.

It mirrors the exoskeleton ledger's shape (per-entry HMAC, verifiable
rows, lexical-ISO ``since`` filtering) and shares the signing primitives.

  from axiom_audit_ledger import AuditLedger
  led = AuditLedger("audit.jsonl")
  led.append("workspace_opened", actor="aui", subject="goal: ship demo",
             outcome="allowed", attributes={"recall_hit": True})

github.com/Orivael-Dev/axiom | Patent Pending ORVL-001-PROV
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from axiom_signing import derive_key

LEDGER_KEY_NS = b"axiom-audit-ledger-v1"


def _ledger_key() -> bytes:
    return derive_key(LEDGER_KEY_NS)


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: dict) -> str:
    return hmac.new(_ledger_key(), _canonical(payload), hashlib.sha256).hexdigest()


def default_ledger_path() -> Path:
    """Where the audit ledger goes if nothing else is configured."""
    p = os.environ.get("AXIOM_AUDIT_LEDGER")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".axiom" / "audit-ledger.jsonl"


@dataclass(frozen=True)
class AuditEvent:
    timestamp_utc: str
    event_type:    str
    actor:         str
    subject:       str
    outcome:       str
    attributes:    dict = field(default_factory=dict)
    signature:     str = ""

    def _payload(self) -> dict:
        return {
            "timestamp_utc": self.timestamp_utc,
            "event_type":    self.event_type,
            "actor":         self.actor,
            "subject":       self.subject,
            "outcome":       self.outcome,
            "attributes":    self.attributes,
        }

    def to_dict(self) -> dict:
        d = self._payload()
        d["signature"] = self.signature
        return d

    def verify(self) -> bool:
        """True iff this entry's signature was produced under LEDGER_KEY_NS."""
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(self._payload()))


class AuditLedger:
    """Append-only JSONL audit log with per-entry HMAC signing."""

    def __init__(self, path: Optional[Any] = None) -> None:
        self.path = Path(path) if path else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, *, actor: str = "", subject: str = "",
               outcome: str = "", attributes: Optional[dict] = None) -> AuditEvent:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        unsigned = AuditEvent(
            timestamp_utc=datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            event_type=event_type,
            actor=actor,
            subject=subject[:200],
            outcome=outcome,
            attributes=dict(attributes or {}),
        )
        signed = AuditEvent(**{**unsigned.to_dict(),
                               "signature": _sign(unsigned._payload())})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(signed.to_dict(), ensure_ascii=True) + "\n")
        return signed

    def read(self) -> List[AuditEvent]:
        if not self.path.exists():
            return []
        out: List[AuditEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(AuditEvent(
                    timestamp_utc=d["timestamp_utc"], event_type=d["event_type"],
                    actor=d.get("actor", ""), subject=d.get("subject", ""),
                    outcome=d.get("outcome", ""), attributes=d.get("attributes", {}),
                    signature=d.get("signature", ""),
                ))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return out

    def query(self, *, event_type: Optional[str] = None,
              since: Optional[str] = None,
              limit: Optional[int] = None) -> List[AuditEvent]:
        out = self.read()
        if event_type:
            out = [e for e in out if e.event_type == event_type]
        if since:
            out = [e for e in out if e.timestamp_utc >= since]
        if limit:
            out = out[-int(limit):]
        return out


if __name__ == "__main__":
    print("AXIOM Audit Ledger — append-only signed event log")
    print("  from axiom_audit_ledger import AuditLedger")
