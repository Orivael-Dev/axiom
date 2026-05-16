"""
AXIOM Reverse QRF — demo
========================
Show reverse-QRF collapse on three (prompt, answer) pairs across domains.

For each case:
  1. Run forward QRFEngine.forecast to get the weighted branch superposition
  2. Run ReverseQRFEngine.collapse to recover trajectory hypotheses
     consistent with the observed answer
  3. Print the accepted superposition with scores

Run:
  export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  python3 examples/reverse_qrf_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from axiom_qrf_reverse import DEFAULT_TAU_THRESHOLD, ReverseQRFEngine
from axiom_signing import derive_key


CASES = [
    {
        "domain": "financial",
        "prompt": "Will the central bank cut rates next quarter?",
        "answer": "A small cut is plausible but conditional on inflation softening; markets are split.",
    },
    {
        "domain": "medical",
        "prompt": "Does vitamin D supplementation improve sleep?",
        "answer": "Evidence is mixed; effects appear modest and may depend on baseline deficiency.",
    },
    {
        "domain": "security",
        "prompt": "Is the production network exposed to lateral movement risk?",
        "answer": "Likely yes from the staging segment; rival hypothesis flags the VPN concentrator.",
    },
]


def _print_case(engine: ReverseQRFEngine, case: dict) -> None:
    result = engine.collapse(case["prompt"], case["answer"])
    domain = case["domain"]

    print(f"  [{domain.upper()}]")
    print(f"  Prompt:  {case['prompt']}")
    print(f"  Answer:  {case['answer']}")
    print(f"  Considered: {result.n_branches_considered} branches  "
          f"|  Accepted: {len(result.hypotheses)}  "
          f"|  Rejected: {len(result.rejected)}")
    print()

    if result.hypotheses:
        for h in result.hypotheses:
            pct = h["score"] * 100
            bar = "█" * max(1, int(pct / 2))
            print(f"    {h['branch_name']:18s}  "
                  f"score={h['score']:.3f}  "
                  f"fw={h['forward_weight']:.2f}  "
                  f"compat={h['compatibility']:.2f}  "
                  f"dist={h['constitutional_distance']:.2f}  {bar}")
    else:
        print("    (no hypotheses above tau)")

    print()
    print(f"    HMAC:      {result.hmac_signature[:16]}...")
    print(f"    Timestamp: {result.timestamp}")
    print()
    print("  " + "-" * 64)
    print()


def main() -> None:
    key = derive_key(b"axiom-qrf-reverse-v1")
    print()
    print("  AXIOM Reverse QRF — trajectory superposition recovery")
    print("  =====================================================")
    print(f"  Default tau:           {DEFAULT_TAU_THRESHOLD}")
    print(f"  HMAC label:            b'axiom-qrf-reverse-v1'")
    print()

    for case in CASES:
        engine = ReverseQRFEngine(domain=case["domain"], hmac_key=key)
        _print_case(engine, case)


if __name__ == "__main__":
    main()
