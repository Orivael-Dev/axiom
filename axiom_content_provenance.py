"""
AXIOM Content Provenance — EU AI Act Article 50(2) synthetic-content marking
=============================================================================
Art. 50(2) requires providers of AI systems that generate synthetic text/audio/
image/video to mark the output as artificially generated, in a **machine-readable**
format, detectable as AI-generated. Art. 50(1)/(4) require a human-facing disclosure.

This module marks AI-generated **text** with both, in one pass:

  1. A human-readable AI-disclosure footer (Art. 50(1)/(4)).
  2. An embedded, signed, machine-readable provenance tag (Art. 50(2)) — an HTML
     comment carrying base64url(JSON) + an HMAC signature, so a downstream verifier
     can confirm the content is AI-marked AND that neither the content nor the tag
     was altered after marking.

Reuses the repo signing primitive (`axiom_signing.derive_key`) and the same
`AXIOM_DEPLOYER_*` identity the Art. 50 `/disclosure` endpoint uses, so the marker
and the disclosure speak with one voice.

Non-invasive: a standalone final step a server applies AFTER OutputShaper. It does
not modify the frozen `axiom_output_shaper` module.

Usage:
    from axiom_content_provenance import mark, verify
    marked = mark(text, system="Hello Operator", deployer="Acme", model="claude-…")
    result = verify(marked)        # result.status == "VALID"

CLI:
    echo "the answer is 42" | python axiom_content_provenance.py mark --deployer Acme
    python axiom_content_provenance.py verify --file out.txt
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac as hmac_lib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROVENANCE_VERSION = 1

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-content-provenance-v1")
except Exception:  # pragma: no cover
    _KEY = hashlib.pbkdf2_hmac(
        "sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
        b"axiom-content-provenance-v1", 1,
    )

# Exact boundary between the AI content and the appended marking. An HTML comment, so
# it is invisible in rendered markdown/HTML but gives a byte-exact split on verify.
_SEP = "\n\n<!--AXM-MARK-->\n"
_TAG_RE = re.compile(r"<!--\s*AI-PROVENANCE v1 ([A-Za-z0-9_\-]+)\s*-->\s*$")


def _canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _sign(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "sig"}
    return hmac_lib.new(_KEY, _canon(body), hashlib.sha256).hexdigest()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def provenance_identity() -> dict:
    """Deployer/system identity, from the same env the /disclosure endpoint reads."""
    return {
        "deployer": os.environ.get("AXIOM_DEPLOYER_NAME", "AXIOM Operator"),
        "contact":  os.environ.get("AXIOM_DEPLOYER_CONTACT", "operator@example.com"),
        "jurisdiction": os.environ.get("AXIOM_DEPLOYER_JURISDICTION", "EU"),
    }


@dataclass
class ProvenanceResult:
    status:  str            # VALID | CONTENT_ALTERED | SIG_INVALID | UNMARKED
    record:  Optional[dict] # the decoded provenance record (None if UNMARKED/undecodable)
    content: Optional[str]  # the recovered original AI content (None if UNMARKED)

    @property
    def ai_marked(self) -> bool:
        return self.status in ("VALID", "CONTENT_ALTERED", "SIG_INVALID")

    def to_dict(self) -> dict:
        return {"status": self.status, "ai_marked": self.ai_marked,
                "record": self.record}


def mark(text: str, *, system: str = "AXIOM",
         deployer: Optional[str] = None, model: str = "unknown",
         now: Optional[str] = None, footer: bool = True) -> str:
    """Return `text` marked per Art. 50: human-readable footer + signed machine tag.

    `now` is a caller-supplied ISO-8601 UTC timestamp (defaults to current time);
    `deployer` defaults to AXIOM_DEPLOYER_NAME.
    """
    ident = provenance_identity()
    deployer = deployer or ident["deployer"]
    generated_at = now or datetime.now(timezone.utc).isoformat()

    record = {
        "v":              PROVENANCE_VERSION,
        "ai_generated":   True,
        "system":         system,
        "deployer":       deployer,
        "model":          model,
        "generated_at":   generated_at,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    record["sig"] = _sign(record)
    tag = f"<!-- AI-PROVENANCE v1 {_b64e(_canon(record))} -->"

    parts = [text, _SEP]
    if footer:
        parts.append(
            f"— \U0001F916 **AI-generated content.** Produced by an AI system "
            f"({system}, model {model}) operated by {deployer}. "
            f"Marked under EU AI Act Art. 50. Generated {generated_at}.\n"
        )
    parts.append(tag)
    return "".join(parts)


def verify(marked: str) -> ProvenanceResult:
    """Check an Art. 50-marked string. Reports tampering of content OR tag."""
    if _SEP not in marked or not _TAG_RE.search(marked):
        return ProvenanceResult("UNMARKED", None, None)

    content, tail = marked.split(_SEP, 1)
    m = _TAG_RE.search(tail)
    if not m:
        return ProvenanceResult("UNMARKED", None, None)
    try:
        record = json.loads(_b64d(m.group(1)).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return ProvenanceResult("SIG_INVALID", None, content)

    # Tag authenticity: the signature must match the record body.
    sig = record.get("sig", "")
    if not isinstance(sig, str) or not hmac_lib.compare_digest(sig, _sign(record)):
        return ProvenanceResult("SIG_INVALID", record, content)

    # Content integrity: the recovered content must still hash to the recorded digest.
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != record.get("content_sha256"):
        return ProvenanceResult("CONTENT_ALTERED", record, content)

    return ProvenanceResult("VALID", record, content)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="axiom_content_provenance",
        description="EU AI Act Art. 50 synthetic-content marking for text",
    )
    sub = p.add_subparsers(dest="action", required=True)

    pm = sub.add_parser("mark", help="mark AI-generated text (footer + signed tag)")
    pm.add_argument("--text", help="text to mark (default: read stdin)")
    pm.add_argument("--system", default="AXIOM")
    pm.add_argument("--deployer", default=None)
    pm.add_argument("--model", default="unknown")
    pm.add_argument("--now", default=None, help="ISO-8601 UTC timestamp")
    pm.add_argument("--no-footer", action="store_true")

    pv = sub.add_parser("verify", help="verify a marked string")
    pv.add_argument("--file", help="file to verify (default: read stdin)")

    args = p.parse_args(argv)

    if args.action == "mark":
        text = args.text if args.text is not None else sys.stdin.read()
        out = mark(text, system=args.system, deployer=args.deployer,
                   model=args.model, now=args.now, footer=not args.no_footer)
        sys.stdout.write(out)
        return 0

    if args.action == "verify":
        data = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
        r = verify(data)
        print(json.dumps(r.to_dict(), indent=2, ensure_ascii=True))
        return 0 if r.status == "VALID" else 1

    return 2


if __name__ == "__main__":
    sys.exit(_main())
