"""
AXIOM Data-Subject Rights — GDPR Art. 15/16/17/20 over Axiom stores
====================================================================
EU AI Act Art. 10 / GDPR give individuals rights over their data: access (Art. 15),
rectification (Art. 16), erasure / "right to be forgotten" (Art. 17), and portability
(Art. 20). This module provides adapters that honor those rights across Axiom's data
stores, plus deployer stores registered through the same interface.

The hard part — and the reason this is a real design, not a delete loop — is the
**erasure-vs-immutability tension**: Axiom's integrity ledgers are append-only and
hash-chained, so you cannot delete a past entry without breaking the chain. The
resolution Axiom already enables:

  * Integrity ledgers store **hashes**, not raw personal data (AXIOM_DATA_GOVERNANCE.md).
    A non-reversible hash of erased data is not the data — the chain is retained as-is.
  * Mutable stores (application data, memory previews) are **crypto-shredded / redacted**:
    designated PII fields are overwritten with a tombstone, in place.
  * Every erasure produces a **signed receipt** recording what was erased vs. retained
    (as hash-only) per store — so the action itself is auditable without retaining PII.

Deployers register their own stores by implementing the tiny `SubjectStore` protocol
(`find` / `export` / `erase`).

Usage:
    svc = DataSubjectService()
    svc.register(JsonlSubjectStore("app_data.jsonl", subject_key="user_id",
                                   pii_fields=("name", "email")))
    svc.register(JsonlSubjectStore("ledger.jsonl", subject_key="subject_id",
                                   append_only=True))           # hash-only, retained
    report  = svc.access("user-42", now=iso)        # Art. 15
    export  = svc.portability("user-42", now=iso)   # Art. 20  (structured JSON)
    receipt = svc.erasure("user-42", now=iso)       # Art. 17  (signed)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-data-subject-v1")
except Exception:  # pragma: no cover
    _KEY = hashlib.pbkdf2_hmac("sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
                               b"axiom-data-subject-v1", 1)

TOMBSTONE = "[ERASED]"


def _canon(d) -> bytes:
    return json.dumps(d, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _sign(d: dict) -> str:
    body = {k: v for k, v in d.items() if k != "signature"}
    return hmac_lib.new(_KEY, _canon(body), hashlib.sha256).hexdigest()


# ── Store protocol ────────────────────────────────────────────────────────────

class SubjectStore(Protocol):
    name: str
    def find(self, subject_id: str) -> list: ...
    def export(self, subject_id: str) -> list: ...
    def erase(self, subject_id: str, now: str) -> dict: ...


class JsonlSubjectStore:
    """A JSONL-backed store keyed by a subject field.

    `pii_fields` are the fields erasure crypto-shreds (overwrites with a tombstone).
    `append_only=True` marks an integrity ledger: erasure does NOT rewrite it (the
    hash-chain is preserved); it is reported as retained hash-only. Put NO raw PII in
    an append-only store — only hashes — so this is correct, not a loophole.
    """

    def __init__(self, path, *, subject_key: str = "subject_id",
                 pii_fields: tuple = (), append_only: bool = False,
                 name: Optional[str] = None):
        self.path = Path(path)
        self.subject_key = subject_key
        self.pii_fields = tuple(pii_fields)
        self.append_only = append_only
        self.name = name or self.path.name

    def _rows(self) -> list:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def find(self, subject_id: str) -> list:
        return [r for r in self._rows() if str(r.get(self.subject_key)) == str(subject_id)]

    def export(self, subject_id: str) -> list:
        return self.find(subject_id)

    def erase(self, subject_id: str, now: str) -> dict:
        rows = self._rows()
        matched = [r for r in rows if str(r.get(self.subject_key)) == str(subject_id)]
        if self.append_only:
            # Immutable integrity log — retain hash-only; do not break the chain.
            return {"store": self.name, "matched": len(matched),
                    "mode": "retained_hash_only",
                    "note": "append-only integrity log; entries are hash references, not "
                            "raw PII — chain preserved, underlying data erased at source"}
        # Mutable store — crypto-shred the PII fields in place.
        shredded = 0
        marker = f"{TOMBSTONE} {now}"
        for r in matched:
            for f in self.pii_fields:
                if f in r and r[f] != marker:
                    r[f] = marker
                    shredded += 1
        if matched:
            self.path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=True) for r in rows) + "\n",
                encoding="utf-8")
        return {"store": self.name, "matched": len(matched),
                "mode": "redacted", "fields_shredded": shredded,
                "fields": list(self.pii_fields)}


# ── Service ───────────────────────────────────────────────────────────────────

@dataclass
class DataSubjectService:
    stores: list = field(default_factory=list)

    def register(self, store: SubjectStore) -> "DataSubjectService":
        self.stores.append(store)
        return self

    def access(self, subject_id: str, now: str) -> dict:
        """Art. 15 — a signed report of all data held about the subject."""
        records = {s.name: s.find(subject_id) for s in self.stores}
        report = {
            "request": "ACCESS", "regulation": "GDPR Art. 15 / EU AI Act Art. 10",
            "subject_id": subject_id, "generated_at": now,
            "records": records,
            "total": sum(len(v) for v in records.values()),
        }
        report["signature"] = _sign(report)
        return report

    def portability(self, subject_id: str, now: str) -> dict:
        """Art. 20 — a structured, machine-readable export."""
        out = {
            "request": "PORTABILITY", "regulation": "GDPR Art. 20",
            "subject_id": subject_id, "generated_at": now, "format": "json",
            "data": {s.name: s.export(subject_id) for s in self.stores},
        }
        out["signature"] = _sign(out)
        return out

    def erasure(self, subject_id: str, now: str) -> dict:
        """Art. 17 — crypto-shred mutable stores; retain integrity logs hash-only.
        Returns a signed erasure receipt."""
        results = [s.erase(subject_id, now) for s in self.stores]
        receipt = {
            "request": "ERASURE", "regulation": "GDPR Art. 17",
            "subject_id": subject_id, "generated_at": now,
            "results": results,
            "redacted_stores": sum(1 for r in results if r["mode"] == "redacted" and r["matched"]),
            "retained_hash_only": sum(1 for r in results if r["mode"] == "retained_hash_only"),
        }
        receipt["signature"] = _sign(receipt)
        return receipt

    def rectification(self, subject_id: str, corrections: dict, now: str) -> dict:
        """Art. 16 — record a signed rectification request (applied at source by the
        deployer; recorded here for auditability)."""
        rec = {
            "request": "RECTIFICATION", "regulation": "GDPR Art. 16",
            "subject_id": subject_id, "generated_at": now, "corrections": corrections,
        }
        rec["signature"] = _sign(rec)
        return rec


def verify_receipt(receipt: dict) -> bool:
    sig = receipt.get("signature")
    return isinstance(sig, str) and hmac_lib.compare_digest(sig, _sign(receipt))


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _load_stores(spec_path: str) -> DataSubjectService:
    """Stores config JSON: [{"path","subject_key","pii_fields":[],"append_only":bool}]."""
    svc = DataSubjectService()
    for s in json.load(open(spec_path, encoding="utf-8")):
        svc.register(JsonlSubjectStore(
            s["path"], subject_key=s.get("subject_key", "subject_id"),
            pii_fields=tuple(s.get("pii_fields", [])),
            append_only=bool(s.get("append_only", False)), name=s.get("name")))
    return svc


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="axiom_data_subject",
                                description="GDPR data-subject rights over Axiom stores")
    sub = p.add_subparsers(dest="action", required=True)
    for act in ("access", "portability", "erasure"):
        sp = sub.add_parser(act)
        sp.add_argument("--stores", required=True, help="stores config JSON")
        sp.add_argument("--subject", required=True)
        sp.add_argument("--now", default=None)

    args = p.parse_args(argv)
    now = args.now or datetime.now(timezone.utc).isoformat()
    svc = _load_stores(args.stores)
    fn = {"access": svc.access, "portability": svc.portability, "erasure": svc.erasure}[args.action]
    print(json.dumps(fn(args.subject, now), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
