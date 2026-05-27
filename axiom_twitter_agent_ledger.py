"""Signed audit ledger for the human-in-the-loop Twitter reply agent.

Every approve / reject / mark-sent decision appends one HMAC-signed
record under the `axiom-twitter-ledger-v1` namespace so the audit
trail can be verified tamper-free, independent of the EventToken's
own signature chain.

Default path: `~/.axiom/twitter-agent-ledger.jsonl`
Override via env: `AXIOM_TWITTER_AGENT_LEDGER`

Decisions:
  - "approve"  — founder signed off on a candidate reply
  - "reject"   — founder rejected a candidate; reason recorded
  - "sent"     — founder self-attests they pasted the approved
                 reply into Twitter. The system has no way to
                 verify the post actually happened — `sent` is a
                 signed assertion by the reviewer, not a proof.

Record shape:
  {
    "timestamp_utc":          "2026-05-22T15:30:00.000Z",
    "draft_id":               "twdr_4f9b1c2d…",
    "input_id":               "twin_a0b1c2d3…",
    "parent_tweet_id":        "1234567890",
    "parent_author_handle":   "somebody",
    "framing":                "acknowledge",
    "reply_hash":             "sha256:…",
    "char_count":             142,
    "over_limit":             false,
    "honesty_block_count":    0,
    "honesty_flag_count":     1,
    "decision":               "approve" | "reject" | "sent",
    "reviewer_principal":     "alice@example.com",
    "event_token_id":         "twdr_…",
    "rejection_reason":       "wrong tone",    (omit unless reject)
    "referenced_token_id":    "twdr_…",        (only on sent)
    "sent_at":                "2026-05-22T15:30:00Z",  (only on sent)
    "signature":              "<hex>"
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


LEDGER_KEY_NS = b"axiom-twitter-ledger-v1"


def _ledger_key() -> bytes:
    return derive_key(LEDGER_KEY_NS)


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: dict) -> str:
    return hmac.new(_ledger_key(), _canonical(payload),
                    hashlib.sha256).hexdigest()


def default_ledger_path() -> Path:
    p = os.environ.get("AXIOM_TWITTER_AGENT_LEDGER")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".axiom" / "twitter-agent-ledger.jsonl"


@dataclass(frozen=True)
class TwitterLedgerEntry:
    timestamp_utc:        str
    draft_id:             str
    input_id:             str
    parent_tweet_id:      str
    parent_author_handle: str
    framing:              str
    reply_hash:           str
    char_count:           int
    over_limit:           bool
    honesty_block_count:  int
    honesty_flag_count:   int
    decision:             str   # "approve" | "reject" | "sent"
    reviewer_principal:   str
    event_token_id:       str
    rejection_reason:     Optional[str] = None
    referenced_token_id:  Optional[str] = None
    sent_at:              Optional[str] = None
    signature:            str = ""

    def _payload(self) -> dict:
        d = {
            "timestamp_utc":        self.timestamp_utc,
            "draft_id":             self.draft_id,
            "input_id":             self.input_id,
            "parent_tweet_id":      self.parent_tweet_id,
            "parent_author_handle": self.parent_author_handle,
            "framing":              self.framing,
            "reply_hash":           self.reply_hash,
            "char_count":           int(self.char_count),
            "over_limit":           bool(self.over_limit),
            "honesty_block_count":  int(self.honesty_block_count),
            "honesty_flag_count":   int(self.honesty_flag_count),
            "decision":             self.decision,
            "reviewer_principal":   self.reviewer_principal,
            "event_token_id":       self.event_token_id,
        }
        if self.rejection_reason is not None:
            d["rejection_reason"] = self.rejection_reason
        if self.referenced_token_id is not None:
            d["referenced_token_id"] = self.referenced_token_id
        if self.sent_at is not None:
            d["sent_at"] = self.sent_at
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


def _now_ms() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class LedgerWriter:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, entry: TwitterLedgerEntry) -> TwitterLedgerEntry:
        sig = _sign(entry._payload())
        signed = TwitterLedgerEntry(
            **{**entry.to_dict(), "signature": sig}
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(signed.to_dict(),
                                ensure_ascii=True) + "\n")
        return signed

    def append(
        self,
        *,
        draft,                      # TweetReplyDraft
        token,                      # EventToken
        decision: str,
        reviewer_principal: str,
        rejection_reason: Optional[str] = None,
    ) -> TwitterLedgerEntry:
        if decision not in ("approve", "reject"):
            raise ValueError(
                f"append(): decision must be 'approve' or 'reject', "
                f"got {decision!r} — use append_sent() for sent records"
            )
        unsigned = TwitterLedgerEntry(
            timestamp_utc=_now_ms(),
            draft_id=draft.draft_id,
            input_id=draft.input_id,
            parent_tweet_id=draft.parent_tweet_id,
            parent_author_handle=draft.parent_author_handle,
            framing=draft.framing,
            reply_hash=draft.reply_hash,
            char_count=int(draft.char_count),
            over_limit=bool(draft.over_limit),
            honesty_block_count=int(draft.honesty_block_count),
            honesty_flag_count=int(draft.honesty_flag_count),
            decision=decision,
            reviewer_principal=reviewer_principal,
            event_token_id=token.id,
            rejection_reason=rejection_reason,
        )
        return self._write(unsigned)

    def append_sent(
        self,
        *,
        draft,                      # TweetReplyDraft
        referenced_token_id: str,
        sent_at: str,
    ) -> TwitterLedgerEntry:
        """Sign a self-attested mark-sent record.

        The referenced_token_id points back to the EventToken signed
        at approval time — verifying both ledgers shows the founder
        attested they pasted the same reply that was approved.
        """
        unsigned = TwitterLedgerEntry(
            timestamp_utc=_now_ms(),
            draft_id=draft.draft_id,
            input_id=draft.input_id,
            parent_tweet_id=draft.parent_tweet_id,
            parent_author_handle=draft.parent_author_handle,
            framing=draft.framing,
            reply_hash=draft.reply_hash,
            char_count=int(draft.char_count),
            over_limit=bool(draft.over_limit),
            honesty_block_count=int(draft.honesty_block_count),
            honesty_flag_count=int(draft.honesty_flag_count),
            decision="sent",
            reviewer_principal="(self-attested)",
            event_token_id=referenced_token_id or "",
            referenced_token_id=referenced_token_id or "",
            sent_at=sent_at,
        )
        return self._write(unsigned)


def read_ledger(path: Optional[Path] = None) -> List[TwitterLedgerEntry]:
    p = Path(path) if path else default_ledger_path()
    if not p.exists():
        return []
    out: List[TwitterLedgerEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(TwitterLedgerEntry(
            timestamp_utc=d["timestamp_utc"],
            draft_id=d.get("draft_id", ""),
            input_id=d.get("input_id", ""),
            parent_tweet_id=d.get("parent_tweet_id", ""),
            parent_author_handle=d.get("parent_author_handle", ""),
            framing=d.get("framing", ""),
            reply_hash=d.get("reply_hash", ""),
            char_count=int(d.get("char_count", 0)),
            over_limit=bool(d.get("over_limit", False)),
            honesty_block_count=int(d.get("honesty_block_count", 0)),
            honesty_flag_count=int(d.get("honesty_flag_count", 0)),
            decision=d.get("decision", ""),
            reviewer_principal=d.get("reviewer_principal", ""),
            event_token_id=d.get("event_token_id", ""),
            rejection_reason=d.get("rejection_reason"),
            referenced_token_id=d.get("referenced_token_id"),
            sent_at=d.get("sent_at"),
            signature=d.get("signature", ""),
        ))
    return out


def query_ledger(
    *,
    path:     Optional[Path] = None,
    decision: Optional[str]  = None,
    reviewer: Optional[str]  = None,
    draft_id: Optional[str]  = None,
    since:    Optional[str]  = None,
    limit:    Optional[int]  = None,
) -> List[TwitterLedgerEntry]:
    out = read_ledger(path)
    if decision:
        out = [e for e in out if e.decision == decision]
    if reviewer:
        out = [e for e in out if e.reviewer_principal == reviewer]
    if draft_id:
        out = [e for e in out if e.draft_id == draft_id]
    if since:
        out = [e for e in out if e.timestamp_utc >= since]
    if limit:
        out = out[-int(limit):]
    return out
