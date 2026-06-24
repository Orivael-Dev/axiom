"""Persistent audit ledger for the company exoskeleton agent.

Every `ExoskeletonAgent.invoke(...)` appends one signed-record line to
a JSONL file so founder workflow runs are queryable later. Each record
is itself HMAC-signed under `axiom-exoskeleton-ledger-v1` so a tampered
ledger entry can be detected — independent of the EventToken's own
signatures.

Default path: `~/.axiom/exoskeleton-ledger.jsonl`
Override via env: `AXIOM_EXOSKELETON_LEDGER`

Record shape:
  {
    "timestamp_utc":  "2026-05-19T03:30:00.000Z",
    "use_case":       "outreach_personalization",
    "token_id":       "exo_4f9b1c2d",
    "input_excerpt":  "Buyer: CISO at a 1500-…" (first 200 chars),
    "input_chars":    312,
    "backend":        "local" | "nim" | …,
    "model":          "llama3.2:3b",
    "input_tokens":   88,
    "output_tokens":  61,
    "latency_ms":     1842,
    "verified":       true,
    "signature":      "<hex>"
  }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

from axiom_signing import derive_key


LEDGER_KEY_NS = b"axiom-exoskeleton-ledger-v1"


def _ledger_key() -> bytes:
    return derive_key(LEDGER_KEY_NS)


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: dict) -> str:
    return hmac.new(_ledger_key(), _canonical(payload),
                    hashlib.sha256).hexdigest()


def default_ledger_path() -> Path:
    """Where the ledger goes if nothing else is configured."""
    p = os.environ.get("AXIOM_EXOSKELETON_LEDGER")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".axiom" / "exoskeleton-ledger.jsonl"


@dataclass(frozen=True)
class LedgerEntry:
    timestamp_utc:  str
    use_case:       str
    token_id:       str
    input_excerpt:  str
    input_chars:    int
    backend:        str
    model:          str
    input_tokens:   int
    output_tokens:  int
    latency_ms:     int
    verified:       bool
    signature:      str = ""
    domain:         str = ""   # routing metadata — not included in HMAC payload

    def _payload(self) -> dict:
        # domain is intentionally excluded so old signatures remain valid
        return {
            "timestamp_utc":  self.timestamp_utc,
            "use_case":       self.use_case,
            "token_id":       self.token_id,
            "input_excerpt":  self.input_excerpt,
            "input_chars":    self.input_chars,
            "backend":        self.backend,
            "model":          self.model,
            "input_tokens":   self.input_tokens,
            "output_tokens":  self.output_tokens,
            "latency_ms":     self.latency_ms,
            "verified":       self.verified,
        }

    def to_dict(self) -> dict:
        d = self._payload()
        d["signature"] = self.signature
        d["domain"]    = self.domain
        return d

    def verify(self) -> bool:
        """True iff this entry's signature was produced under LEDGER_KEY_NS."""
        if not self.signature:
            return False
        expected = _sign(self._payload())
        return hmac.compare_digest(self.signature, expected)


class LedgerWriter:
    """Append-only JSONL writer with per-entry HMAC signing."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        token,                                  # EventToken
        use_case: str,
        input_text: str,
    ) -> LedgerEntry:
        """Extract transport facts from a signed EventToken; sign + append."""
        if token.text is None:
            payload_d = {"backend": "unknown", "model": "unknown",
                         "input_tokens": 0, "output_tokens": 0,
                         "latency_ms": 0}
        else:
            payload_d = token.text.payload

        excerpt = input_text[:200]
        unsigned = LedgerEntry(
            timestamp_utc=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            use_case=use_case,
            token_id=token.id,
            input_excerpt=excerpt,
            input_chars=len(input_text),
            backend=str(payload_d.get("backend", "unknown")),
            model=str(payload_d.get("model", "unknown")),
            input_tokens=int(payload_d.get("input_tokens", 0)),
            output_tokens=int(payload_d.get("output_tokens", 0)),
            latency_ms=int(payload_d.get("latency_ms", 0)),
            verified=bool(token.verify()),
        )
        sig = _sign(unsigned._payload())
        signed = LedgerEntry(**{**unsigned.to_dict(), "signature": sig})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(signed.to_dict(), ensure_ascii=True) + "\n")
        return signed


def read_ledger(path: Optional[Path] = None) -> List[LedgerEntry]:
    """Read all entries from a ledger file. Missing file → []."""
    p = Path(path) if path else default_ledger_path()
    if not p.exists():
        return []
    out: List[LedgerEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(LedgerEntry(
            timestamp_utc=d["timestamp_utc"],
            use_case=d["use_case"],
            token_id=d["token_id"],
            input_excerpt=d["input_excerpt"],
            input_chars=int(d["input_chars"]),
            backend=d["backend"],
            model=d["model"],
            input_tokens=int(d["input_tokens"]),
            output_tokens=int(d["output_tokens"]),
            latency_ms=int(d["latency_ms"]),
            verified=bool(d["verified"]),
            signature=d.get("signature", ""),
            domain=d.get("domain", ""),
        ))
    return out


def query_ledger(
    *,
    path: Optional[Path] = None,
    use_case: Optional[str] = None,
    since:    Optional[str] = None,    # ISO-8601 prefix match
    limit:    Optional[int] = None,
) -> List[LedgerEntry]:
    """Filtered read. Each filter is optional. `since` matches by string
    comparison on the ISO timestamp (works because UTC ISO sorts
    lexically)."""
    out = read_ledger(path)
    if use_case:
        out = [e for e in out if e.use_case == use_case]
    if since:
        out = [e for e in out if e.timestamp_utc >= since]
    if limit:
        out = out[-int(limit):]
    return out
