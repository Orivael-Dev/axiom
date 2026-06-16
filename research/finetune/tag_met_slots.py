"""Tag training data examples with MET hydration slot annotations.

Each training example gets three new fields:

  axiom_met_slot     : str   — primary slot this example trains
  axiom_met_triggers : list  — all chunks needed when processing this intent
  axiom_hydration_intent : str — expected QRF intent class at inference time

Slot definitions match CHUNK_CATALOG in research/simulation/hydration_sim.py:

  embedding  — vocabulary-level knowledge, tokenization patterns (always pinned)
  early      — syntax, local context, format, JSON structure (L0-5)
  factual    — knowledge retrieval, HMAC/signing, CLI commands, entity facts (L6-11)
  reasoning  — multi-step inference, KV cache, planning, code flow (L12-22)
  governance — safety verdicts, tamper detection, refusal, intent classification (L23-29)

Usage
-----
  python3 research/finetune/tag_met_slots.py           # tags all known files
  python3 research/finetune/tag_met_slots.py --dry-run # print slot distribution only
  python3 research/finetune/tag_met_slots.py --input path/to/data.jsonl --output tagged.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

# ─────────────────────────────────────────────────────────────────────────────
# Slot → hydration intent (what QRF should predict when this slot is primary)
# ─────────────────────────────────────────────────────────────────────────────
SLOT_INTENT: dict[str, str] = {
    "embedding":  "INFORM",
    "early":      "INFORM",
    "factual":    "INFORM",
    "reasoning":  "CLARIFY",
    "governance": "REFUSE",
}

# Slot → all transformer chunks needed during inference
# (embedding always pinned; listed chunks are loaded from UFS on QRF signal)
SLOT_TRIGGERS: dict[str, list[str]] = {
    "embedding":  ["early"],
    "early":      ["early"],
    "factual":    ["early", "factual"],
    "reasoning":  ["early", "factual", "reasoning"],
    "governance": ["early", "governance"],
}

# ─────────────────────────────────────────────────────────────────────────────
# Type-field → slot mapping (for axiom_training_data + axiom_behavioral)
# ─────────────────────────────────────────────────────────────────────────────
TYPE_TO_SLOT: dict[str, str] = {
    # axiom_training_data.jsonl types
    "spec_explanation":          "factual",
    "spec_writing":              "reasoning",
    "orvl_knowledge":            "factual",
    "trajectory":                "reasoning",
    "contrastive":               "governance",
    "hierarchical":              "reasoning",
    "transition":                "reasoning",
    "negative":                  "governance",
    "bug_knowledge":             "factual",
    "pattern_knowledge":         "factual",
    "bug_fix":                   "reasoning",
    "language_knowledge":        "early",
    "benchmark_knowledge":       "factual",
    "guard_writing":             "governance",
    "implementation_pattern":    "reasoning",
    # axiom_behavioral_training.jsonl types
    "constitutional_reasoning":  "governance",
    "bug_pattern_detection":     "factual",
    "test_first_implementation": "reasoning",
    "uncertainty_bounding":      "governance",
    "rival_approach":            "reasoning",
    "manifest_signing":          "factual",
    "spec_authoring":            "reasoning",
    "constraint_enforcement":    "governance",
    "contrastive_pairs":         "governance",
}

# ─────────────────────────────────────────────────────────────────────────────
# Content-based slot classifier (for examples without a 'type' field,
# e.g. axiom_metric_targeted.jsonl and train_qwen_chatml.jsonl)
# ─────────────────────────────────────────────────────────────────────────────
_GOV_PATTERNS = re.compile(
    r"\b(HARM|DECEIVE|BLOCK|tamper|tamper_detected|revoc|REVOK|EXPIR|refus|"
    r"REFUSE|verdict|intent_class|classify|constitutional|guard|safety|"
    r"tool_call_allowed|UNCERTAIN|BLOCK)\b",
    re.IGNORECASE,
)
_FACTUAL_PATTERNS = re.compile(
    r"\b(HMAC|signature|fingerprint|verify|axm\s*(pack|verify|extract|run|info)|"
    r"LayerReport|EventToken|coordinator_sig|axiom_signing|bpw|quant|srd|"
    r"no.fake|cli\s*command|llama|gguf|field|namespace)\b",
    re.IGNORECASE,
)
_REASONING_PATTERNS = re.compile(
    r"\b(kv.?cache|reusable_prefix|prefix|invalidat|adapter.?block|AXIOM_BLOCK|"
    r"multi.?step|plan|chain|recompute|retriev|route|compress|hydrat)\b",
    re.IGNORECASE,
)
_EARLY_PATTERNS = re.compile(
    r"\b(json.?valid|format|schema|syntax|template|chatml|im_start|im_end|"
    r"system\s*prompt|output\s*format)\b",
    re.IGNORECASE,
)


def classify_by_content(messages: list[dict]) -> str:
    """Infer slot from message content when no type field is available."""
    text = " ".join(
        m.get("content", "") for m in messages
        if m.get("role") in {"user", "assistant"}
    )
    gov  = len(_GOV_PATTERNS.findall(text))
    fact = len(_FACTUAL_PATTERNS.findall(text))
    reas = len(_REASONING_PATTERNS.findall(text))
    earl = len(_EARLY_PATTERNS.findall(text))
    scores = {"governance": gov, "factual": fact, "reasoning": reas, "early": earl}
    best = max(scores, key=scores.get)
    # If no signal at all → early (basic format/syntax default)
    return best if scores[best] > 0 else "early"


# ─────────────────────────────────────────────────────────────────────────────
# Core annotation function
# ─────────────────────────────────────────────────────────────────────────────
def annotate(example: dict) -> dict:
    """Add axiom_met_slot, axiom_met_triggers, axiom_hydration_intent to example."""
    ex = dict(example)

    # Prefer explicit type field
    type_val = ex.get("type", "")
    if type_val in TYPE_TO_SLOT:
        slot = TYPE_TO_SLOT[type_val]
    else:
        msgs = ex.get("messages", [])
        # Fall back to instruction/output fields for older format
        if not msgs:
            text = f"{ex.get('instruction','')} {ex.get('input','')} {ex.get('output','')}"
            msgs = [{"role": "user", "content": text}]
        slot = classify_by_content(msgs)

    ex["axiom_met_slot"]     = slot
    ex["axiom_met_triggers"] = SLOT_TRIGGERS[slot]
    ex["axiom_hydration_intent"] = SLOT_INTENT[slot]
    return ex


# ─────────────────────────────────────────────────────────────────────────────
# File processor
# ─────────────────────────────────────────────────────────────────────────────
def tag_file(src: Path, dst: Path, dry_run: bool = False) -> dict:
    """Tag all examples in src, write to dst. Returns slot distribution."""
    lines   = src.read_text(encoding="utf-8").splitlines()
    tagged  = []
    counts  = Counter()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        ex  = json.loads(line)
        out = annotate(ex)
        tagged.append(out)
        counts[out["axiom_met_slot"]] += 1

    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            for ex in tagged:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    return dict(counts)


# ─────────────────────────────────────────────────────────────────────────────
# Default file manifest
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_FILES = [
    (_REPO / "axiom_training_data.jsonl",
     _REPO / "axiom_training_data_tagged.jsonl"),
    (_REPO / "axiom_behavioral_training.jsonl",
     _REPO / "axiom_behavioral_training_tagged.jsonl"),
    (_REPO / "autotrain_data" / "axiom_metric_targeted.jsonl",
     _REPO / "autotrain_data" / "axiom_metric_targeted_tagged.jsonl"),
    (_REPO / "autotrain_data" / "train_qwen_chatml.jsonl",
     _REPO / "autotrain_data" / "train_qwen_chatml_tagged.jsonl"),
]


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Tag training data with MET slot annotations")
    p.add_argument("--input",   default=None, help="single input JSONL (omit for all defaults)")
    p.add_argument("--output",  default=None, help="single output path (required with --input)")
    p.add_argument("--dry-run", action="store_true", help="print distribution, don't write files")
    args = p.parse_args(argv)

    if args.input:
        pairs = [(Path(args.input), Path(args.output or args.input.replace(".jsonl", "_tagged.jsonl")))]
    else:
        pairs = [(s, d) for s, d in DEFAULT_FILES if s.exists()]

    total_counts: Counter = Counter()

    print(f"\n{'File':<52}  {'emb':>4}  {'earl':>5}  {'fact':>5}  {'reas':>5}  {'gov':>5}  {'total':>6}")
    print("─" * 82)

    for src, dst in pairs:
        counts = tag_file(src, dst, dry_run=args.dry_run)
        total  = sum(counts.values())
        total_counts.update(counts)
        status = "(dry)" if args.dry_run else f"→ {dst.name}"
        print(
            f"  {src.name:<50}  "
            f"{counts.get('embedding',0):>4}  "
            f"{counts.get('early',0):>5}  "
            f"{counts.get('factual',0):>5}  "
            f"{counts.get('reasoning',0):>5}  "
            f"{counts.get('governance',0):>5}  "
            f"{total:>6}  {status}"
        )

    tot = sum(total_counts.values())
    print("─" * 82)
    print(
        f"  {'TOTAL':<50}  "
        f"{total_counts.get('embedding',0):>4}  "
        f"{total_counts.get('early',0):>5}  "
        f"{total_counts.get('factual',0):>5}  "
        f"{total_counts.get('reasoning',0):>5}  "
        f"{total_counts.get('governance',0):>5}  "
        f"{tot:>6}"
    )
    print()

    if not args.dry_run:
        print("Tagged fields added to each example:")
        print('  "axiom_met_slot"         : primary slot (embedding/early/factual/reasoning/governance)')
        print('  "axiom_met_triggers"     : transformer chunks QRF pre-hydrates for this intent')
        print('  "axiom_hydration_intent" : expected QRF intent class (INFORM/CLARIFY/REFUSE)')
        print()
        print("Use for:")
        print("  • Curriculum: train early→factual→reasoning→governance in order")
        print("  • LoRA: target layer ranges matching primary slot")
        print("  • QRF training signal: axiom_hydration_intent → which chunks to pre-fetch")
        print("  • Eval: per-slot accuracy breakdown (gov slot should hit >95% verdict accuracy)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
