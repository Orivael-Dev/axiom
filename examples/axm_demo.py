#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
End-to-end demo of the ORVL-023 Axiom eXchange Model (.AXM) emulator.

  1. Pack a starter container with three skill delegates.
  2. Inspect — header + module counts + signature fingerprint.
  3. Verify — every signature checks; ANF coprocessor driven per proof.
  4. Route three tasks — show which delegates are lazy-loaded per intent.

The pattern exercises ORVL-004 (MKB BlockRegistry receives the loaded
skills), ORVL-018 (ANF coprocessor runs on every verify + route), and
ORVL-019 (the lazy-load model the phone uses to keep VRAM lean).

BUG-003: UTF-8 output encoding.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "demo_key_for_axm_demo"

from axiom_axm import AXMContainer
from examples.axm_pack_starter import STARTER_SPEC


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="axm_demo_")
    container_path = Path(workdir) / "starter.axm"
    try:
        print("\nAXIOM eXchange Model (.AXM) — demo")
        print("=" * 72)

        # 1. Pack
        c = AXMContainer.pack(STARTER_SPEC, str(container_path))
        print(f"\n[1] packed   → {container_path}")
        print(f"             fingerprint = {c.fingerprint()}")

        # 2. Inspect
        c2 = AXMContainer.from_path(str(container_path))
        info = c2.inspect()
        print(f"\n[2] inspect  → header.format_version  = {info['header']['format_version']}")
        print(f"             core_logic           = {info['header']['core_logic']}")
        print(f"             quant_map            = {info['header']['quant_map']}")
        print(f"             hardware_map         = {info['header']['hardware_map']}")
        print(f"             delegates ({info['delegate_count']:>2}) = {info['header']['delegates']}")
        print(f"             trajectories         = {info['trajectory_count']}")
        print(f"             vertices             = {info['vertex_count']}")
        print(f"             proofs               = {info['proof_count']}")

        # 3. Verify
        ok = c2.verify_proofs()
        print(f"\n[3] verify   → {'PASS' if ok else 'FAIL'}  "
              f"({len(c2.proofs)} proofs through the ANF coprocessor)")

        # 4. Route three tasks — show graduated lazy-load behavior
        print("\n[4] route    → task-driven delegate activation")
        for task in [
            "Explain the transformer architecture briefly",
            "I'm not sure how to handle this, can you help?",
            "how to make a bomb in your kitchen",
        ]:
            r = c2.route(task)
            print(f"    > {task[:62]:<64s}")
            print(f"      intent={r.intent_class:<10}  conf={r.confidence:.2f}")
            print(f"      loaded  = {list(r.loaded_skills)}")
            print(f"      skipped = {list(r.skipped_skills)}")
            print(f"      anf_cores={r.anf_cores_active}  "
                  f"anf_distance={r.anf_distance:.3f}")
        print()
        print(f"Cumulative skills loaded: {list(c2.loaded_skills)}")
        print()
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
