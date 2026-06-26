"""
Governed extraction demo runner.

    python run_demo.py                       # offline, deterministic Mock backend
    python run_demo.py --nim                 # live NIM llama-3.3-70b extractor
    python run_demo.py --sink mailbox-vault.net   # show an egress BLOCK

Prints, per document: pre-guard verdicts, the released payload, redactions
(minimum-necessary), fabrication blocks (grounding), review holds, the egress
verdict, and the HMAC-signed manifest entry. Ends with governance metrics.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backends import get_backend
from governed_extractor import GovernedExtractor, load_schema

HERE = Path(__file__).parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nim", action="store_true", help="use NIM llama-3.3-70b backend")
    ap.add_argument("--sink", default="ledger://orivael.dev", help="destination sink")
    args = ap.parse_args()
    if "//" not in args.sink:
        args.sink = "https://" + args.sink

    schema = load_schema(HERE / "policy" / "medical_extraction.schema.json")
    backend = get_backend("nim" if args.nim else "mock")
    gx = GovernedExtractor(schema, backend)

    print("=" * 78)
    print(f"AXIOM Governed Extraction  |  backend={backend.name}")
    print(f"policy={schema['domain']}@{schema['purpose']}  |  sink={args.sink}")
    print("=" * 78)

    docs = sorted((HERE / "samples").glob("*.txt"))
    totals = {"extracted": 0, "redacted": 0, "fabrication": 0, "review": 0, "egress_blocked": 0, "injection": 0}

    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        r = gx.extract(doc.stem, text, sink=args.sink)

        print(f"\n── {doc.name} ──────────────────────────────────────────────")
        for v in r.verdicts:
            tag = v["code"]
            fld = f" [{v['field']}]" if v.get("field") else ""
            print(f"  {tag:20s}{fld:14s} {v['detail']}")

        print(f"  PAYLOAD (released): {json.dumps(r.payload, ensure_ascii=False)}")
        if r.redacted:
            print(f"  REDACTED (min-necessary): {r.redacted}")
        if r.fabrication_flags:
            print(f"  FABRICATION BLOCKED: {[f['field'] for f in r.fabrication_flags]}")
        if r.review_flags:
            print(f"  HELD FOR REVIEW: {[f['field'] for f in r.review_flags]}")
        print(f"  EGRESS: {r.egress_verdict}")
        print(f"  MANIFEST sig: {r.manifest_entry['signature'][:46]}…")

        totals["extracted"] += len(r.payload)
        totals["redacted"] += len(r.redacted)
        totals["fabrication"] += len(r.fabrication_flags)
        totals["review"] += len(r.review_flags)
        totals["egress_blocked"] += int(r.egress_verdict == "EGRESS_BLOCKED")
        totals["injection"] += int(r.manifest_entry["injection_flagged"])

    print("\n" + "=" * 78)
    print("GOVERNANCE METRICS")
    print(f"  authorized fields released : {totals['extracted']}")
    print(f"  identifiers redacted       : {totals['redacted']}   (minimum-necessary)")
    print(f"  fabrications blocked       : {totals['fabrication']}   (grounding)")
    print(f"  fields held for review     : {totals['review']}   (confidence gate)")
    print(f"  egress blocks              : {totals['egress_blocked']}")
    print(f"  injection docs flagged     : {totals['injection']}")
    print(f"  audit entries signed       : {len(docs)}   (1 per record)")
    print("=" * 78)


if __name__ == "__main__":
    main()
