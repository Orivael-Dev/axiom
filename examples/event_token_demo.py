#!/usr/bin/env python3
"""Reproduce the AXIOM_EVENT_TOKEN example from the concept note.

The PDF's canonical worked example: "The glass cup fell and shattered."

Run:
    export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
    python3 examples/event_token_demo.py

Output: a fully-signed AXIOM_EVENT_TOKEN as JSON. Validates that the
container abstraction holds end-to-end with the Text Agent firing on
the real IntentClassifier + stubs for Audio / Video / Physics + a
real Governance Agent stitching evidence trace.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from axiom_event_token import Coordinator


def main() -> int:
    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit(
            "AXIOM_MASTER_KEY must be set. Generate one:\n"
            "  export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
        )

    coord = Coordinator()

    token = coord.compose(
        text="The glass cup fell and shattered.",
        audio={
            "impact_profile":     "sharp_transient",
            "material_signature": "glass",
            "decay_pattern":      "scattered_fragments",
            "depth":              0.31,
            "width":              0.62,
            "rhythm":             "single_impact",
            "confidence":         0.91,
        },
        video={
            "objects":          [
                {"id": "glass_cup_01", "type": "cup", "motion": "downward"},
                {"id": "floor_01",     "type": "floor", "motion": "static"},
            ],
            "object_motion":    "downward",
            "impact_point":     "floor",
            "fracture_pattern": "radial_scatter",
            "temporal_chain":   [
                "glass_cup_01 falls",
                "glass_cup_01 hits floor_01",
                "glass_cup_01 fractures",
                "fragments_01 scatter",
            ],
            "confidence":       0.88,
        },
        physics={
            "material": "brittle_glass",
            "surface":  "hard_surface",
            "motion":   "downward",
        },
        activate=("text", "audio", "video", "physics", "governance"),
        token_id="event_cup_shatter_001",
    )

    print(token.to_json(indent=2))
    print(file=sys.stderr)
    print(f"verified: {token.verify()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
