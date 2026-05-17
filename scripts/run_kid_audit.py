#!/usr/bin/env python3
"""Generate a kid-safety audit PDF for an AI toy company.

Usage:
    AXIOM_MASTER_KEY=<hex> python scripts/run_kid_audit.py \\
        --toy "Buddy the Bear" \\
        --vendor "Acme Toys Inc." \\
        --system-prompt path/to/their_system_prompt.txt \\
        --out audit-buddy-2026-05-16.pdf

The PDF is emitted alongside a `.sig` file containing the HMAC
signature so the toy company (or a regulator) can verify the
document hasn't been tampered with.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# Allow running without installation
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from axiom_report.audits import run_audit  # noqa: E402
from axiom_report.generator import render_pdf  # noqa: E402


def main(argv: list[str]) -> int:
    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit(
            "AXIOM_MASTER_KEY must be set — the classifier and the PDF "
            "signature both derive from it."
        )

    p = argparse.ArgumentParser(description="Generate a kid-safety audit PDF.")
    p.add_argument("--toy", required=True, help="Toy product name.")
    p.add_argument("--vendor", required=True, help="Vendor / company name.")
    p.add_argument(
        "--system-prompt",
        required=True,
        type=Path,
        help="Path to a text file containing the toy's system prompt.",
    )
    p.add_argument(
        "--out", "-o",
        required=True,
        type=Path,
        help="Output path for the PDF (e.g. audit.pdf).",
    )
    p.add_argument(
        "--corpus",
        default="kid_safety_v1",
        help="Red-team corpus name. Default: kid_safety_v1",
    )
    p.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Audit date (YYYY-MM-DD). Default: today.",
    )
    p.add_argument(
        "--packs",
        default="",
        help="Comma-separated list of Skill Packs to apply during the "
             "audit (e.g. 'coppa,kid-voice-output,kid-bedtime-mode'). "
             "Demonstrates the score lift when packs are installed. "
             "Default: none (baseline audit).",
    )

    args = p.parse_args(argv[1:])

    if not args.system_prompt.is_file():
        sys.exit(f"System prompt file not found: {args.system_prompt}")

    system_prompt = args.system_prompt.read_text(encoding="utf-8").strip()

    installed_packs = tuple(
        p.strip() for p in args.packs.split(",") if p.strip()
    )

    print(f"Running kid-safety audit on {args.toy}...")
    if installed_packs:
        print(f"  Packs active: {', '.join(installed_packs)}")
    else:
        print("  Packs active: (baseline — no packs)")

    result = run_audit(
        toy_name=args.toy,
        vendor=args.vendor,
        audit_date=args.date,
        system_prompt=system_prompt,
        corpus_name=args.corpus,
        installed_packs=installed_packs,
    )

    print()
    print(f"  Safety:       {'★' * result.safety_stars}{'☆' * (5 - result.safety_stars)}")
    print(f"  Privacy:      {'★' * result.privacy_stars}{'☆' * (5 - result.privacy_stars)}")
    print(f"  Age-fit:      {'★' * result.age_fit_stars}{'☆' * (5 - result.age_fit_stars)}")
    print(f"  Parent trust: {'★' * result.parent_trust_stars}{'☆' * (5 - result.parent_trust_stars)}")
    print()
    if result.recommended_packs:
        print(f"  Recommended packs: {', '.join(result.recommended_packs)}")

    print()
    print("Rendering PDF...")
    pdf_bytes, signature = render_pdf("audit_kid_toy.html", {"result": result})

    args.out.write_bytes(pdf_bytes)
    sig_path = args.out.with_suffix(args.out.suffix + ".sig")
    sig_path.write_text(signature + "\n", encoding="utf-8")

    print(f"  PDF:       {args.out} ({len(pdf_bytes):,} bytes)")
    print(f"  Signature: {sig_path}")
    print()
    print(f"  Verify with:")
    print(f"    python -c \"from axiom_report.generator import verify_pdf; "
          f"import sys; print(verify_pdf(open('{args.out}','rb').read(), "
          f"open('{sig_path}').read().strip()))\"")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
