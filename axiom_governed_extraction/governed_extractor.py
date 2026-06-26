"""
AXIOM Governed Extraction — core pipeline.

A compact model extracts; this constitutional layer enforces what may be extracted,
proves each value is grounded, redacts identifiers under minimum-necessary, gates
egress, and signs an audit manifest entry per record.

The model is intentionally NOT trusted to judge policy. It only proposes fields;
every governance decision here is deterministic and auditable.

    from governed_extractor import GovernedExtractor, load_schema
    gx = GovernedExtractor(load_schema("policy/medical_extraction.schema.json"), backend)
    result = gx.extract("DOC-1", text, sink="ledger://orivael.dev")
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Vocabulary that signals an attempt to smuggle instructions through document content.
_INJECTION_VOCAB = (
    "ignore previous", "ignore all", "disregard", "override", "bypass",
    "system prompt", "new instructions", "you are now", "forward all", "exfiltrate",
)


def load_schema(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _derive_key(secret: bytes = b"axiom-governed-extraction-v1") -> bytes:
    return hashlib.pbkdf2_hmac("sha256", b"axiom-extract", secret, 1)


@dataclass
class Verdict:
    code: str
    detail: str
    field: Optional[str] = None


@dataclass
class ExtractionResult:
    doc_id: str
    payload: dict                       # authorized, grounded clinical fields only
    redacted: list                      # identifier fields removed (minimum-necessary)
    fabrication_flags: list             # fields nulled for lack of grounding
    review_flags: list                  # fields held below the confidence threshold
    egress_verdict: str
    verdicts: list = field(default_factory=list)
    manifest_entry: dict = field(default_factory=dict)


def _norm(s: str) -> str:
    """Lowercase, collapse non-alphanumerics — for grounding substring checks."""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


class GovernedExtractor:
    def __init__(self, schema: dict, backend, signing_key: Optional[bytes] = None):
        self.schema = schema
        self.backend = backend
        self.fields = schema.get("fields", {})
        self.threshold = float(schema.get("confidence_threshold", 0.70))
        self.deidentified = "deidentified" in schema.get("purpose", "").replace("_", "")
        self.allowed_domains = tuple(schema.get("egress", {}).get("allowed_domains", []))
        self._key = signing_key or _derive_key()

    # ── guard stages ──────────────────────────────────────────────────────────

    def _pre_guard(self, text: str) -> list:
        verdicts: list[Verdict] = []
        low = text.lower()
        hits = [v for v in _INJECTION_VOCAB if v in low]
        if hits:
            verdicts.append(Verdict("INJECTION_FLAGGED",
                                    f"injection vocabulary in document: {hits!r}; treated as data"))
        # crude scope check — a medical record should mention clinical anchors
        if not re.search(r"diagnos|patient|mg|dose|admit|visit|clinic|lab|symptom", low):
            verdicts.append(Verdict("OUT_OF_SCOPE", "no clinical anchors found in document"))
        return verdicts

    def _grounded(self, value: Any, text: str) -> bool:
        ntext = _norm(text)
        vals = value if isinstance(value, list) else [value]
        for v in vals:
            if not str(v).strip():
                continue
            if _norm(v) and _norm(v) in ntext:
                return True
        return False

    def _post_guard(self, raw: dict, text: str, sink: str):
        payload, redacted, fabrication, review = {}, [], [], []
        verdicts: list[Verdict] = []

        for name, item in raw.items():
            spec = self.fields.get(name)
            # schema enforcement — unknown field can never appear
            if spec is None:
                verdicts.append(Verdict("FIELD_REDACTED", f"field not in schema: {name}", name))
                redacted.append(name)
                continue

            value = item.get("value") if isinstance(item, dict) else item
            conf = float(item.get("confidence", 1.0)) if isinstance(item, dict) else 1.0
            if value in (None, "", [], {}):
                continue

            # minimum-necessary / de-identification
            if self.deidentified and not spec.get("authorized", False):
                verdicts.append(Verdict("FIELD_REDACTED",
                                        f"{spec.get('sensitivity','')} redacted under minimum-necessary", name))
                redacted.append(name)
                continue

            # no-fabrication / grounding
            if not self._grounded(value, text):
                verdicts.append(Verdict("FABRICATION_BLOCKED",
                                        f"value not grounded in source: {value!r}", name))
                fabrication.append({"field": name, "claimed": value})
                continue

            # confidence gating
            if conf < self.threshold:
                verdicts.append(Verdict("FIELD_REVIEW", f"confidence {conf:.2f} < {self.threshold:.2f}", name))
                review.append({"field": name, "confidence": conf, "value": value})
                continue

            verdicts.append(Verdict("EXTRACT_CLEAN", f"authorized + grounded (conf {conf:.2f})", name))
            payload[name] = value

        # egress gate
        domain = sink.split("//")[-1].split("/")[0].lower()
        egress_ok = any(domain == d or domain.endswith("." + d) for d in self.allowed_domains)
        if egress_ok:
            egress_verdict = "EXTRACT_CLEAN"
        else:
            egress_verdict = "EGRESS_BLOCKED"
            verdicts.append(Verdict("EGRESS_BLOCKED", f"destination not approved: {sink}"))

        return payload, redacted, fabrication, review, egress_verdict, verdicts

    # ── signing ─────────────────────────────────────────────────────────────────

    def _sign(self, entry: dict) -> str:
        body = json.dumps({k: v for k, v in entry.items() if k != "signature"}, sort_keys=True)
        return "hmac-sha256:" + hmac_lib.new(self._key, body.encode(), hashlib.sha256).hexdigest()

    # ── public ────────────────────────────────────────────────────────────────

    def extract(self, doc_id: str, text: str, sink: str = "ledger://orivael.dev") -> ExtractionResult:
        verdicts = self._pre_guard(text)

        raw = self.backend.extract(text, self.schema)  # {field: {"value":..,"confidence":..}}
        payload, redacted, fabrication, review, egress_verdict, post_v = self._post_guard(raw, text, sink)
        verdicts.extend(post_v)

        # egress hard-block: do not release payload to a disallowed sink
        released = payload if egress_verdict == "EXTRACT_CLEAN" else {}

        entry = {
            "doc_id":            doc_id,
            "domain":            self.schema.get("domain"),
            "purpose":           self.schema.get("purpose"),
            "fields_extracted":  sorted(payload.keys()),
            "fields_redacted":   sorted(redacted),
            "fabrication_flags": [f["field"] for f in fabrication],
            "review_flags":      [r["field"] for r in review],
            "egress_verdict":    egress_verdict,
            "sink":              sink,
            "injection_flagged": any(v.code == "INJECTION_FLAGGED" for v in verdicts),
            "policy":            f"{self.schema.get('domain')}@{self.schema.get('purpose')}",
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }
        entry["signature"] = self._sign(entry)

        return ExtractionResult(
            doc_id=doc_id, payload=released, redacted=redacted,
            fabrication_flags=fabrication, review_flags=review,
            egress_verdict=egress_verdict,
            verdicts=[v.__dict__ for v in verdicts], manifest_entry=entry,
        )
