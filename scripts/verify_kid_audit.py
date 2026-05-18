#!/usr/bin/env python3
"""Verify a kid-safety audit PDF against its HMAC signature.

Auditor-facing entrypoint. A third-party auditor receives an audit
package (PDF + .sig file) and runs:

    AXIOM_MASTER_KEY=<hex> python3 scripts/verify_kid_audit.py \\
        --pdf  audit-buddy-2026-05-16.pdf \\
        --sig  audit-buddy-2026-05-16.pdf.sig

PASS  ⇒ the PDF bytes have not been altered since AXIOM signed it.
FAIL  ⇒ the PDF was modified, the signature was modified, or the
        AXIOM_MASTER_KEY is wrong.

Exits 0 on PASS, 2 on FAIL, 1 on usage/IO error.

The HMAC namespace is `axiom-report-v1` (declared by
axiom_report.generator.REPORTS_SIGNING_NAMESPACE). The script is
deliberately small and depends only on the standard library +
axiom_signing + axiom_report.generator.verify_pdf so an auditor
can read the verification path end-to-end in a few hundred lines.
"""
from __future__ import annotations

import argparse
import os
import sys
from hashlib import sha256
from pathlib import Path

# Allow running from a clone without `pip install -e .`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from axiom_report.generator import verify_pdf  # noqa: E402


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--pdf", required=True, type=Path, help="Audit PDF file.")
    p.add_argument("--sig", required=True, type=Path, help="Signature file (text, one hex line).")
    p.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress the PASS/FAIL banner; only exit code is used.",
    )
    args = p.parse_args(argv[1:])

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print(
            "AXIOM_MASTER_KEY must be set in the environment — it is the "
            "root key the HMAC signature was derived from. Ask the issuer "
            "of this audit (or AXIOM) for the key used at signing time.",
            file=sys.stderr,
        )
        return 1

    if not args.pdf.is_file():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 1
    if not args.sig.is_file():
        print(f"Signature file not found: {args.sig}", file=sys.stderr)
        return 1

    pdf_bytes = args.pdf.read_bytes()
    signature = args.sig.read_text(encoding="utf-8").strip()

    ok = verify_pdf(pdf_bytes, signature)

    # Always show enough provenance for the auditor to log this run
    pdf_digest = sha256(pdf_bytes).hexdigest()
    if not args.quiet:
        print(f"  pdf:        {args.pdf}  ({len(pdf_bytes):,} bytes)")
        print(f"  pdf sha256: {pdf_digest}")
        print(f"  signature:  {signature[:16]}…{signature[-16:]}")
        print(f"  namespace:  axiom-report-v1")
        print()
        if ok:
            print("  RESULT: PASS — signature verifies; PDF is unmodified.")
        else:
            print("  RESULT: FAIL — signature does NOT verify.")
            print("    Causes (any one is enough):")
            print("      1. The PDF was modified after signing.")
            print("      2. The signature file does not match this PDF.")
            print("      3. AXIOM_MASTER_KEY does not match the signing key.")

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
