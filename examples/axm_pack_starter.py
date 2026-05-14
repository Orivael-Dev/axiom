#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pack a starter .AXM container for the ORVL-023 emulator.

The container is NOT committed to git — signatures are deployer-key-specific
(derived from AXIOM_MASTER_KEY) and a committed container would fail to
verify under another user's key. Run this script once after cloning to
generate a local container you can poke at.

Usage:
    python examples/axm_pack_starter.py [output_path]
            (default: ./starter.axm/ in cwd)

BUG-003: UTF-8 output encoding.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── Starter spec — exercised by axiom_axm pack ────────────────────────
STARTER_SPEC = {
    "format_version": "0.1-concept",
    "core_logic":     "axiom_core_3b",
    "quant_map":      "elastic_per_layer",
    "hardware_map":   "compile_on_load",
    "safety_proofs":  True,
    "core": {
        "name":         "axiom_core_3b",
        "params":       "3B (stub)",
        "quant_map":    "elastic_per_layer",
        "residency":    "always",
        "description":  "Always-resident reasoning core — handles routing, "
                         "safety, verification, and task intent.",
    },
    "delegates": [
        {
            "name":            "pii_redactor",
            "when_condition":  "intent_class in {INFORM,CLARIFY}",
            "intent_classes":  ["INFORM", "CLARIFY"],
            "weight_manifest": "delegates/pii_redactor/weights.bin",
        },
        {
            "name":            "anf_governance",
            "when_condition":  "always",
            "intent_classes":  ["INFORM", "CLARIFY", "REFUSE", "UNCERTAIN",
                                 "HARM", "DECEIVE"],
            "weight_manifest": "delegates/anf_governance/weights.bin",
        },
        {
            "name":            "vector_recall",
            "when_condition":  "intent_class in {UNCERTAIN}",
            "intent_classes":  ["UNCERTAIN"],
            "weight_manifest": "delegates/vector_recall/weights.bin",
        },
    ],
    "trajectories": [
        {
            "id":              "traj-benign-explain",
            "task_pattern":    "explain <concept>",
            "action_sequence": ["retrieve_definition", "ground_in_examples",
                                 "emit_answer"],
        },
        {
            "id":              "traj-block-harm",
            "task_pattern":    "how to <harm verb> <object>",
            "action_sequence": ["classify_harm", "block_at_coprocessor",
                                 "emit_sovereign_alert"],
        },
    ],
    "vertices": [
        {"semantic_class": "Glass",     "vertex_cluster": "Cylindrical_Thin"},
        {"semantic_class": "Box",       "vertex_cluster": "Rectangular_Solid"},
        {"semantic_class": "Sphere",    "vertex_cluster": "Spherical_Smooth"},
        {"semantic_class": "Door",      "vertex_cluster": "Planar_Hinged"},
        {"semantic_class": "Handle",    "vertex_cluster": "Cylindrical_Graspable"},
    ],
}


def main(argv=None) -> int:
    if not os.environ.get("AXIOM_MASTER_KEY"):
        os.environ["AXIOM_MASTER_KEY"] = "demo_key_for_axm_pack_starter"
    output = (argv[1] if argv and len(argv) > 1 else
              (sys.argv[1] if len(sys.argv) > 1 else "./starter.axm"))
    from axiom_axm import AXMContainer
    c = AXMContainer.pack(STARTER_SPEC, output)
    print(f"packed {output}")
    print(f"  fingerprint     : {c.fingerprint()}")
    print(f"  delegates       : {len(c.delegates)}  ({', '.join(d.name for d in c.delegates)})")
    print(f"  trajectories    : {len(c.trajectories)}")
    print(f"  vertices        : {len(c.vertices)}")
    print(f"  proofs          : {len(c.proofs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
