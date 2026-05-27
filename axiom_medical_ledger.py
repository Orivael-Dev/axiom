"""Persistent audit ledger for the medical research instrument.

Every `MedicalResearchAgent.research(...)` appends one signed
record line to a JSONL file so research sessions are queryable
later. Each record is HMAC-signed under
`axiom-medical-ledger-v1` — independent of the per-EventToken
signatures and the MedicalCoordinatorToken's fusion_signature.

Default path: `~/.axiom/medical-ledger.jsonl`
Override via env: `AXIOM_MEDICAL_LEDGER`

Record shape:
  {
    "timestamp_utc":          "2026-05-20T03:30:00.000Z",
    "research_question":      "What mechanisms link GLP-1 to ...?",
    "profile":                "mechanism",
    "container_id":           "axm-med-2026-001",
    "coordinator_token_id":   "medcoord_4f9b1c2d",
    "event_token_ids":        ["evt_...", ...],
    "active_layers":          ["source", "text", ...],
    "primary_layer":          "text",
    "cross_layer_consistency": 0.86,
    "tier_distribution":      {"1": 2, "2": 0, ...},
    "requires_human_review":  false,
    "manifest_root":          "sha256:...",
    "verified":               true,
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


LEDGER_KEY_NS = b"axiom-medical-ledger-v1"


def _ledger_key() -> bytes:
    return derive_key(LEDGER_KEY_NS)


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: dict) -> str:
    return hmac.new(_ledger_key(), _canonical(payload),
                    hashlib.sha256).hexdigest()


def default_ledger_path() -> Path:
    p = os.environ.get("AXIOM_MEDICAL_LEDGER")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".axiom" / "medical-ledger.jsonl"


@dataclass(frozen=True)
class MedicalLedgerEntry:
    timestamp_utc:           str
    research_question:       str
    profile:                 str
    container_id:            str
    coordinator_token_id:    str
    event_token_ids:         tuple[str, ...]
    active_layers:           tuple[str, ...]
    primary_layer:           str
    cross_layer_consistency: float
    tier_distribution:       dict[str, int]
    requires_human_review:   bool
    manifest_root:           str
    verified:                bool
    signature:               str = ""

    def _payload(self) -> dict:
        return {
            "timestamp_utc":          self.timestamp_utc,
            "research_question":      self.research_question,
            "profile":                self.profile,
            "container_id":           self.container_id,
            "coordinator_token_id":   self.coordinator_token_id,
            "event_token_ids":        list(self.event_token_ids),
            "active_layers":          list(self.active_layers),
            "primary_layer":          self.primary_layer,
            "cross_layer_consistency": round(
                float(self.cross_layer_consistency), 6),
            "tier_distribution":      dict(self.tier_distribution),
            "requires_human_review":  bool(self.requires_human_review),
            "manifest_root":          self.manifest_root,
            "verified":               bool(self.verified),
        }

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
    """Append-only JSONL writer with per-entry HMAC signing."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        coord_token,              # MedicalCoordinatorToken
        event_tokens: list,       # list[EventToken]
        research_question: str,
        profile: str,
        container_id: str,
        manifest_root: str = "",
        tier_distribution: Optional[dict[str, int]] = None,
    ) -> MedicalLedgerEntry:
        verified = bool(coord_token.verify()) and all(
            t.verify() for t in event_tokens
        )
        tier_dist = tier_distribution or {}
        unsigned = MedicalLedgerEntry(
            timestamp_utc=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            research_question=research_question,
            profile=profile,
            container_id=container_id,
            coordinator_token_id=coord_token.event_id,
            event_token_ids=tuple(t.id for t in event_tokens),
            active_layers=coord_token.active_layers,
            primary_layer=coord_token.primary_layer,
            cross_layer_consistency=coord_token.cross_layer_consistency,
            tier_distribution=dict(tier_dist),
            requires_human_review=coord_token.requires_human_review,
            manifest_root=manifest_root,
            verified=verified,
        )
        sig = _sign(unsigned._payload())
        signed = MedicalLedgerEntry(
            **{**unsigned.to_dict(), "signature": sig}
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(signed.to_dict(),
                                ensure_ascii=True) + "\n")
        return signed


def read_ledger(path: Optional[Path] = None) -> List[MedicalLedgerEntry]:
    p = Path(path) if path else default_ledger_path()
    if not p.exists():
        return []
    out: List[MedicalLedgerEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(MedicalLedgerEntry(
            timestamp_utc=d["timestamp_utc"],
            research_question=d["research_question"],
            profile=d.get("profile", "summarize"),
            container_id=d.get("container_id", ""),
            coordinator_token_id=d["coordinator_token_id"],
            event_token_ids=tuple(d.get("event_token_ids", ())),
            active_layers=tuple(d.get("active_layers", ())),
            primary_layer=d.get("primary_layer", "text"),
            cross_layer_consistency=float(
                d.get("cross_layer_consistency", 0.0)),
            tier_distribution=dict(d.get("tier_distribution", {})),
            requires_human_review=bool(
                d.get("requires_human_review", False)),
            manifest_root=d.get("manifest_root", ""),
            verified=bool(d.get("verified", False)),
            signature=d.get("signature", ""),
        ))
    return out


def query_ledger(
    *,
    path:    Optional[Path] = None,
    profile: Optional[str] = None,
    since:   Optional[str] = None,
    limit:   Optional[int] = None,
) -> List[MedicalLedgerEntry]:
    out = read_ledger(path)
    if profile:
        out = [e for e in out if e.profile == profile]
    if since:
        out = [e for e in out if e.timestamp_utc >= since]
    if limit:
        out = out[-int(limit):]
    return out
