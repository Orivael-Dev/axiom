#!/usr/bin/env python3
"""Generate the bonded-paired-token revocation demo PDF.

Sales / auditor artifact. Walks one canonical scenario end-to-end:

  1. Mint a bonded pair (primary + mirror).
  2. Init the ledger to ACTIVE_VALIDATED.
  3. Submit a benign packet through the gate — passes.
  4. Revoke the pair.
  5. Submit the SAME benign packet — denied with HARM verdict and
     ``bonded_pair_revoked`` signal.
  6. Render every artifact (tokens, transitions, gate verdicts) into a
     signed PDF a third party can verify with verify_kid_audit.py.

Usage:
    AXIOM_MASTER_KEY=<hex> python3 scripts/run_bonded_pair_demo.py \
        --out fixtures/bonded_pair_demo/audit.pdf

The PDF is HMAC-signed under ``axiom-report-v1`` so an auditor can
verify byte integrity without re-running the scenario.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def build_scenario(ledger_path: Path) -> dict:
    """Run the bonded-pair revocation scenario and capture every artifact
    into a context dict ready for the Jinja template."""
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, mint_pair,
    )
    from axiom_intent_classifier import IntentClassifier
    from axiom_intent_gate import IntentGate
    from axiom_signing import derive_key

    led = BondedPairLedger(ledger_path)

    # Realistic scenario: a kid-toy executes a local model
    # optimisation under a grant; the security monitor holds the
    # mirror and can revoke mid-run.
    primary, mirror = mint_pair(
        {"execution_command": "run_local_model_optimization",
         "scope": "kid_toy_buddy_bear_v3"},
        {"monitor_target": "primary",
         "issued_to": "security_monitor"},
    )
    led.init_pair(primary.pair_id, actor="provisioner")

    # The same packet is sent twice — first while ACTIVE, then after
    # REVOKED. Only the ledger entry changes between checks.
    packet = {
        "packet_id":   "pkt-demo-001",
        "source":      "buddy_bear_runtime",
        "destination": "model_runtime",
        "payload":     {"text": "Tell me about how solar panels work.",
                        "pair_id": primary.pair_id},
    }

    classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))
    gate_log = ledger_path.parent / "_demo_gate.log"
    gate = IntentGate(
        classifier,
        log_path=str(gate_log),
        bonded_pair_ledger=led,
    )

    # Check 1 — ACTIVE_VALIDATED
    verdict_before = gate.check(packet)

    # Operator revokes via the mirror's authority
    led.revoke(primary.pair_id, actor="security_monitor")

    # Check 2 — same packet, REVOKED state
    verdict_after = gate.check(packet)

    transitions = [t.to_dict() for t in led.history(primary.pair_id)]

    return {
        "pair_id":          primary.pair_id,
        "primary":          primary.to_dict(),
        "mirror":           mirror.to_dict(),
        "primary_json":     json.dumps(primary.to_dict(), indent=2, sort_keys=True),
        "mirror_json":      json.dumps(mirror.to_dict(),  indent=2, sort_keys=True),
        "ledger_path":      str(ledger_path),
        "transitions":      transitions,
        "chain_ok":         led.verify_chain(),
        "primary_verifies": primary.verify(),
        "pair_verifies":    (primary.verify() and mirror.verify()
                             and primary.partner_token_id == mirror.token_id
                             and mirror.partner_token_id  == primary.token_id),
        "packet_json":      json.dumps(packet, indent=2, sort_keys=True),
        "verdict_before":   {
            "intent_class":  verdict_before.intent_class,
            "confidence":    verdict_before.confidence,
            "signals":       list(verdict_before.signals),
            "signature":     verdict_before.signature,
        },
        "verdict_after":    {
            "intent_class":  verdict_after.intent_class,
            "confidence":    verdict_after.confidence,
            "signals":       list(verdict_after.signals),
            "signature":     verdict_after.signature,
        },
    }


def main(argv: list) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--out", required=True, type=Path,
                   help="Output PDF path (signature written to <out>.sig).")
    args = p.parse_args(argv[1:])

    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit("AXIOM_MASTER_KEY must be set.")

    # Fresh ledger per invocation so the scenario is self-contained.
    with tempfile.TemporaryDirectory() as td:
        ledger_path = Path(td) / "demo_ledger.jsonl"
        ctx = build_scenario(ledger_path)

    print(f"  pair_id:           {ctx['pair_id']}")
    print(f"  primary verifies:  {ctx['primary_verifies']}")
    print(f"  pair verifies:     {ctx['pair_verifies']}")
    print(f"  chain ok:          {ctx['chain_ok']}")
    print(f"  verdict BEFORE:    {ctx['verdict_before']['intent_class']} "
          f"(signals: {ctx['verdict_before']['signals'] or '[]'})")
    print(f"  verdict AFTER:     {ctx['verdict_after']['intent_class']} "
          f"(signals: {ctx['verdict_after']['signals']})")
    print()

    from axiom_report.generator import render_pdf
    pdf_bytes, signature = render_pdf("bonded_pair_demo.html", ctx)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(pdf_bytes)
    sig_path = args.out.with_suffix(args.out.suffix + ".sig")
    sig_path.write_text(signature + "\n", encoding="utf-8")

    print(f"  PDF:       {args.out} ({len(pdf_bytes):,} bytes)")
    print(f"  Signature: {sig_path}")
    print()
    print(f"  Verify with:")
    print(f"    python3 scripts/verify_kid_audit.py --pdf {args.out} --sig {sig_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
