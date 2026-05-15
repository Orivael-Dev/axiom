#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compile the AXIOM Agent training corpora into a signed .AXM container.

This is the Option-A bridge between the two existing training sources:

    axiom_training_data.jsonl        ~500 flat {instruction,input,output,type}
    axiom_behavioral_training.jsonl  ~200 ChatML {messages, type, quality_score}

and the ORVL-023 AXM container format. The compilation is intentionally
lossy: each ``type`` cluster collapses into one ``TrajectoryBlock``
whose ``task_pattern`` is the canonical instruction template for that
type and whose ``action_sequence`` is the agent's runtime pipeline for
handling it. Raw records stay at the repo root as the source-of-truth
dataset; the .axm becomes the signed, verifiable, derived artifact —
the same relationship as source-vs-binary.

What lands in the container:

  - Header (core_logic = axiom_agent_v1_1, hardware_map = compile_on_load)
  - 5 SkillDelegates — one per agent mode (FEATURE / BUG_HUNT /
    EFFICIENCY / REASONING_LAB) plus always-on constitutional_enforcer
  - One TrajectoryBlock per training type cluster
    (~27 across both source files)
  - VectorVertex entries derived from agent capability surfaces
  - Proof ledger — auto-built by AXMContainer.pack(): one HMAC-signed
    entry per sub-module file on disk

The compilation is deterministic — re-running with the same sources
and the same AXIOM_MASTER_KEY yields a byte-identical container and
the same fingerprint. The tests rely on this.

Usage:
    python axiom_training_to_axm.py [output_path] [--archive]

    python axiom_training_to_axm.py                   # writes ./axiom_agent.axm/
    python axiom_training_to_axm.py /tmp/agent.axm    # custom path (directory)
    python axiom_training_to_axm.py /tmp/agent.axm --archive   # zip artifact

BUG-003: UTF-8 output encoding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── Source files ──────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent
TRAINING_JSONL    = REPO_ROOT / "axiom_training_data.jsonl"
BEHAVIORAL_JSONL  = REPO_ROOT / "axiom_behavioral_training.jsonl"


# ── Type → (task_pattern, action_sequence) abstraction ────────────────
#
# The training corpus self-classified into these clusters. Each cluster
# maps to one TrajectoryBlock. Patterns + actions stay grounded in the
# vocabulary used by the agent's runtime pipeline (axiom_agent.py docstring).
TYPE_PATTERNS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    # From axiom_training_data.jsonl
    "bug_fix":               ("fix the <bug_id> in <artifact>",
                              ("identify_bug_id", "apply_canonical_fix",
                               "verify_contract")),
    "bug_knowledge":         ("explain <bug_id> and how to fix it",
                              ("retrieve_bug_pattern", "cite_canonical_fix",
                               "ground_in_example")),
    "spec_writing":          ("write a constitutional spec for <agent>",
                              ("draft_axiom_header", "enumerate_constraints",
                               "specify_cannot_mutate", "sign_supply_chain")),
    "spec_explanation":      ("explain the spec for <agent>",
                              ("load_axiom_file", "summarise_constraints",
                               "cite_cannot_mutate")),
    "language_knowledge":    ("answer a question about the AXIOM language",
                              ("retrieve_grammar_rule", "ground_in_example")),
    "pattern_knowledge":     ("describe pattern <name>",
                              ("retrieve_pattern", "cite_canonical_use")),
    "benchmark_knowledge":   ("describe benchmark <name>",
                              ("retrieve_benchmark_spec",
                               "describe_acceptance_metric")),
    "guard_writing":         ("write a guard regex for <intent>",
                              ("identify_intent_class", "draft_regex",
                               "apply_bug_001_rule", "write_blocked_test")),
    "implementation_pattern": ("implement <pattern> in <module>",
                              ("retrieve_pattern", "implement",
                               "verify_with_test")),
    "orvl_knowledge":        ("explain <orvl_id>",
                              ("retrieve_orvl_spec", "cite_role_in_stack",
                               "ground_in_example")),
    "trajectory":            ("walk through trajectory <id>",
                              ("retrieve_trajectory", "narrate_steps",
                               "cite_terminal_state")),
    "contrastive":           ("contrast <approach_a> with <approach_b>",
                              ("retrieve_both_approaches",
                               "enumerate_tradeoffs", "recommend_with_caveat")),
    "hierarchical":          ("decompose <task> into sub-tasks",
                              ("identify_top_goal", "enumerate_sub_tasks",
                               "order_by_dependency")),
    "transition":            ("transition from <state_a> to <state_b>",
                              ("identify_source_state", "identify_target_state",
                               "emit_action_sequence")),
    "negative":              ("identify why <approach> would fail",
                              ("retrieve_failure_mode", "cite_canonical_fix",
                               "ground_in_example")),

    # From axiom_behavioral_training.jsonl
    "constitutional_reasoning":  ("respond to <cannot_mutate violation>",
                                  ("identify_cannot_mutate_field",
                                   "cite_constraint",
                                   "refuse_with_remediation")),
    "bug_pattern_detection":     ("detect <bug_id> in <code>",
                                  ("scan_for_pattern", "cite_bug_id",
                                   "propose_fix")),
    "test_first_implementation": ("implement <feature> test-first",
                                  ("write_blocked_test", "write_passed_test",
                                   "implement", "verify_both_pass")),
    "uncertainty_bounding":      ("respond when confidence below floor",
                                  ("check_uncertainty_floor",
                                   "ask_for_clarification",
                                   "treat_clarification_as_completion")),
    "rival_approach":            ("compare <approach> with rival",
                                  ("enumerate_alternatives",
                                   "score_against_constraints",
                                   "recommend_with_rationale")),
    "manifest_signing":          ("sign a manifest for <decision>",
                                  ("canonicalise_payload",
                                   "compute_hmac_sha256",
                                   "attach_signature_field")),
    "spec_authoring":            ("author a new .axiom spec",
                                  ("draft_header", "enumerate_constraints",
                                   "mark_cannot_mutate", "request_review")),
    "contrastive_pairs":         ("show good-vs-bad example for <topic>",
                                  ("retrieve_pair", "highlight_diff",
                                   "cite_rule")),
    "constraint_enforcement":    ("enforce <constraint> at runtime",
                                  ("load_constraint", "check_predicate",
                                   "block_or_pass")),
}

# Records with no type get bucketed into a single catch-all trajectory.
_UNTYPED_FALLBACK: Tuple[str, Tuple[str, ...]] = (
    "respond to an unclassified agent request",
    ("classify_intent", "route_to_best_matching_skill", "emit_answer"),
)


# ── Mode → SkillDelegate map ──────────────────────────────────────────
#
# Mirrors axiom_agent.py's four runtime modes plus a constitutional
# enforcer that's always on (matches the brief's "every skill activates
# only after proof verification" rule).
AGENT_DELEGATES: List[Dict[str, Any]] = [
    {
        "name":            "feature_writer",
        "when_condition":  "intent_class in {INFORM,CLARIFY,REQUEST}",
        "intent_classes":  ["INFORM", "CLARIFY", "REQUEST"],
        "weight_manifest": "delegates/feature_writer/weights.bin",
    },
    {
        "name":            "bug_hunter",
        "when_condition":  "intent_class in {REQUEST,UNCERTAIN}",
        "intent_classes":  ["REQUEST", "UNCERTAIN"],
        "weight_manifest": "delegates/bug_hunter/weights.bin",
    },
    {
        "name":            "efficiency_profiler",
        "when_condition":  "intent_class in {REQUEST,EXPLORE}",
        "intent_classes":  ["REQUEST", "EXPLORE"],
        "weight_manifest": "delegates/efficiency_profiler/weights.bin",
    },
    {
        "name":            "reasoning_lab",
        "when_condition":  "intent_class in {EXPLORE,UNCERTAIN}",
        "intent_classes":  ["EXPLORE", "UNCERTAIN"],
        "weight_manifest": "delegates/reasoning_lab/weights.bin",
    },
    {
        "name":            "constitutional_enforcer",
        "when_condition":  "always",
        "intent_classes":  ["INFORM", "CLARIFY", "REQUEST", "EXPLORE",
                            "UNCERTAIN", "REFUSE", "HARM", "DECEIVE"],
        "weight_manifest": "delegates/constitutional_enforcer/weights.bin",
    },
]


# ── Vector-vertex DB seed — agent capability surfaces ─────────────────
AGENT_VERTICES: List[Dict[str, str]] = [
    {"semantic_class": "ConstitutionalSpec",    "vertex_cluster": "Axiom_Header_v1"},
    {"semantic_class": "BugPattern",            "vertex_cluster": "BUG_000_Regex_Suite"},
    {"semantic_class": "GuardModule",           "vertex_cluster": "RegEx_Tier1"},
    {"semantic_class": "ManifestSignature",     "vertex_cluster": "HMAC_SHA256_v1"},
    {"semantic_class": "TrajectoryGeometry",    "vertex_cluster": "Constitutional_Distance"},
    {"semantic_class": "UncertaintyFloor",      "vertex_cluster": "Confidence_GE_0_15"},
    {"semantic_class": "CannotMutateField",     "vertex_cluster": "Frozen_At_Cert_Time"},
]


# ── Source ingestion ──────────────────────────────────────────────────
def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"training source missing: {path}")
    out: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path.name}: malformed JSONL — {e}") from e
    return out


def _count_by_type(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in records:
        t = r.get("type") or "?"
        counts[t] = counts.get(t, 0) + 1
    return counts


# ── Spec assembly ─────────────────────────────────────────────────────
def build_spec(training: List[Dict[str, Any]],
               behavioral: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the full AXMContainer.pack() spec from the two corpora."""
    type_counts: Dict[str, int] = {}
    for batch in (training, behavioral):
        for t, n in _count_by_type(batch).items():
            type_counts[t] = type_counts.get(t, 0) + n

    trajectories: List[Dict[str, Any]] = []
    # Iterate in sorted order so the output is deterministic across runs.
    for type_name in sorted(type_counts):
        n = type_counts[type_name]
        pattern, actions = TYPE_PATTERNS.get(type_name, _UNTYPED_FALLBACK)
        trajectories.append({
            "id":              f"traj-axiom-agent-{type_name}",
            "task_pattern":    pattern,
            "action_sequence": list(actions),
            # `record_count` is a doc-only field; AXMContainer ignores
            # unknown spec keys but TrajectoryBlock canonicalises only
            # the four declared fields so the signature is stable
            # regardless of this hint.
        })

    return {
        "format_version": "0.1-concept",
        "core_logic":     "axiom_agent_v1_1",
        "quant_map":      "elastic_per_layer",
        "hardware_map":   "compile_on_load",
        "safety_proofs":  True,
        "core": {
            "name":         "axiom_agent_v1_1",
            "params":       "3B (stub) · constitutional reasoning core",
            "quant_map":    "elastic_per_layer",
            "residency":    "always",
            "description":  "AXIOM Agent — constitutional AI development "
                             "core. Operates in 4 modes (FEATURE, BUG_HUNT, "
                             "EFFICIENCY, REASONING_LAB) with a permanent "
                             "constitutional enforcer pass.",
            "training_sources": [
                {"file": TRAINING_JSONL.name,   "records": len(training)},
                {"file": BEHAVIORAL_JSONL.name, "records": len(behavioral)},
            ],
        },
        "delegates":    AGENT_DELEGATES,
        "trajectories": trajectories,
        "vertices":     AGENT_VERTICES,
    }


def pack(output_path: str, *, archive: bool = False):
    """Read the sources, build the spec, write the container, return it."""
    if not os.environ.get("AXIOM_MASTER_KEY"):
        # Deliberately not silent — the signatures bind to this key, so
        # an unset key would yield a container that can't verify on
        # anyone else's box. The caller picks a value (or runs the CLI
        # with the env var actually set).
        raise RuntimeError(
            "AXIOM_MASTER_KEY not set — signatures would be unstable. "
            "Generate one: "
            'python3 -c "import secrets; print(secrets.token_hex(32))"'
        )

    training   = _read_jsonl(TRAINING_JSONL)
    behavioral = _read_jsonl(BEHAVIORAL_JSONL)
    spec       = build_spec(training, behavioral)

    from axiom_axm import AXMContainer
    return AXMContainer.pack(spec, output_path, archive=archive)


# ── CLI ───────────────────────────────────────────────────────────────
def _main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile AXIOM agent training data into a signed .AXM container.",
    )
    parser.add_argument("output", nargs="?", default="./axiom_agent.axm",
                        help="Output path (directory, or .axm zip with --archive)")
    parser.add_argument("--archive", action="store_true",
                        help="Pack as a single-file zip artifact")
    args = parser.parse_args(argv)

    container = pack(args.output, archive=args.archive)
    info = container.inspect()
    print(f"packed {args.output}")
    print(f"  fingerprint     : {info['fingerprint']}")
    print(f"  core_logic      : {info['header']['core_logic']}")
    print(f"  delegates       : {info['delegate_count']}  "
          f"({', '.join(info['header']['delegates'])})")
    print(f"  trajectories    : {info['trajectory_count']}")
    print(f"  vertices        : {info['vertex_count']}")
    print(f"  proofs          : {info['proof_count']}")
    if container.verify_proofs():
        print("  proof verify    : ✓")
    else:
        print("  proof verify    : ✗")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
