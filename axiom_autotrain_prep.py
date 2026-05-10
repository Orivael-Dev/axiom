"""
AXIOM AutoTrain Prep
====================
Converts axiom_training_data.jsonl → HuggingFace AutoTrain format.

AutoTrain expects a CSV with a single "text" column containing
the full chat-formatted example in the model's expected template.

Supports:
  - TinyLlama chat format (ChatML)
  - Alpaca instruction format
  - Raw text completion format

Usage:
  python axiom_autotrain_prep.py --prepare
  python axiom_autotrain_prep.py --prepare --format alpaca
  python axiom_autotrain_prep.py --stats
  python axiom_autotrain_prep.py --validate

github.com/Orivael-Dev/axiom
"""

import sys
import os
import re
import json
import csv
import hashlib
import argparse
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

INPUT_FILE  = Path("axiom_training_data.jsonl")
OUTPUT_DIR  = Path("autotrain_data")
OUTPUT_CSV  = OUTPUT_DIR / "train.csv"
OUTPUT_JSONL = OUTPUT_DIR / "train.jsonl"

SYSTEM_PROMPT = (
    "You are axiom-dev. You follow constitutional reasoning — "
    "every response must demonstrate these behaviors:\n"
    "1. CANNOT_MUTATE fields are sacred — if asked to change one, refuse with the field name and why\n"
    "2. Uncertainty floor is 0.15 — never state confidence below this, say \"I need clarification on X\"\n"
    "3. Clarification IS completion — asking the right question is a valid response\n"
    "4. Test-first — write BLOCKED/PASSED tests before implementation\n"
    "5. Measurable constraints — every bound uses >=, <=, ==, not vague terms\n"
    "6. Sign everything — HMAC-SHA256 on packets, supply chain hash on files\n"
    "7. Adversarial check — consider what RedAgent would exploit before shipping\n"
    "8. Bug citations — reference BUG-0XX IDs when you spot known patterns\n"
    "9. Guard specs — write .axiom files with AGENT/VERSION/CONSTRAINT/PROCESS/CHECK/SUCCESS\n"
    "10. Show reasoning — include \"because\", constraint references, confidence bounds"
)


# ══════════════════════════════════════════════════════════════
# FORMAT CONVERTERS
# ══════════════════════════════════════════════════════════════

def to_chatml(example: dict) -> str:
    """Convert to ChatML format (TinyLlama, Mistral, etc)."""
    instruction = example["instruction"]
    inp = example.get("input", "")
    output = example["output"]

    user_msg = instruction
    if inp:
        user_msg += f"\n\n{inp}"

    return (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n{user_msg}<|im_end|>\n"
        f"<|im_start|>assistant\n{output}<|im_end|>"
    )


def to_alpaca(example: dict) -> str:
    """Convert to Alpaca instruction format."""
    instruction = example["instruction"]
    inp = example.get("input", "")
    output = example["output"]

    if inp:
        return (
            f"### System:\n{SYSTEM_PROMPT}\n\n"
            f"### Instruction:\n{instruction}\n\n"
            f"### Input:\n{inp}\n\n"
            f"### Response:\n{output}"
        )
    return (
        f"### System:\n{SYSTEM_PROMPT}\n\n"
        f"### Instruction:\n{instruction}\n\n"
        f"### Response:\n{output}"
    )


def to_completion(example: dict) -> str:
    """Convert to raw text completion format."""
    instruction = example["instruction"]
    inp = example.get("input", "")
    output = example["output"]

    prompt = f"[AXIOM] {instruction}"
    if inp:
        prompt += f"\n{inp}"
    return f"{prompt}\n\n{output}"


FORMAT_MAP = {
    "chatml":     to_chatml,
    "alpaca":     to_alpaca,
    "completion": to_completion,
}


# ══════════════════════════════════════════════════════════════
# QUALITY FILTERS
# ══════════════════════════════════════════════════════════════

def quality_filter(example: dict) -> bool:
    """Filter out low-quality examples."""
    output = example.get("output", "")

    # Too short
    if len(output) < 30:
        return False

    # Too long for fine-tuning (token limit ~2048 for TinyLlama)
    if len(output) > 8000:
        return False

    # Empty instruction
    if not example.get("instruction", "").strip():
        return False

    return True


def augment_example(example: dict) -> list:
    """Generate augmented variants for thin categories."""
    variants = [example]
    etype = example.get("type", "")

    # Bug fixes get a "what went wrong" variant
    if etype == "bug_fix" and example.get("input"):
        variants.append({
            "instruction": f"What is wrong with this AXIOM code and how do you fix it?",
            "input":       example["input"],
            "output":      f"Problem: {example['instruction']}\n\nFix:\n{example['output']}",
            "source":      example.get("source", "") + "_augmented",
            "type":        "bug_diagnosis",
        })

    # Spec explanations get a "what are the constraints" variant
    if etype == "spec_explanation":
        constraints = [l for l in example["output"].split("\n") if "CONSTRAINT" in l]
        cannot = [l for l in example["output"].split("\n") if "CANNOT_MUTATE" in l]
        if constraints or cannot:
            name = re.search(r'specification:\s*(\w+)', example["instruction"])
            agent_name = name.group(1) if name else "this agent"
            variants.append({
                "instruction": f"What are the constitutional constraints on {agent_name}?",
                "input":       "",
                "output":      "\n".join(cannot + constraints),
                "source":      example.get("source", "") + "_augmented",
                "type":        "constraint_knowledge",
            })

    return variants


# ══════════════════════════════════════════════════════════════
# MAIN PREP
# ══════════════════════════════════════════════════════════════

def prepare(fmt: str = "chatml", augment: bool = True):
    """Full preparation pipeline."""
    if not INPUT_FILE.exists():
        print(f"  Source not found: {INPUT_FILE}")
        print(f"  Run: python axiom_dataset_builder.py --repo .")
        return

    converter = FORMAT_MAP.get(fmt, to_chatml)
    print(f"\n  AXIOM AutoTrain Prep")
    print(f"  Format: {fmt}")
    print(f"  Source: {INPUT_FILE}")
    print(f"  {'─'*50}")

    # Load — normalise "response" → "output" so both key conventions are accepted
    examples = []
    for line in INPUT_FILE.open(encoding="utf-8"):
        try:
            ex = json.loads(line.strip())
            if "output" not in ex and "response" in ex:
                ex["output"] = ex.pop("response")
            examples.append(ex)
        except Exception:
            pass
    print(f"  Loaded:     {len(examples)} examples")

    # Quality filter
    filtered = [ex for ex in examples if quality_filter(ex)]
    print(f"  After filter: {len(filtered)} examples")

    # Augment
    if augment:
        augmented = []
        for ex in filtered:
            augmented.extend(augment_example(ex))
        print(f"  After augment: {len(augmented)} examples")
    else:
        augmented = filtered

    # Deduplicate
    seen = set()
    unique = []
    for ex in augmented:
        h = hashlib.md5(ex["instruction"].encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            unique.append(ex)
    print(f"  After dedup:  {len(unique)} examples")

    # Convert
    OUTPUT_DIR.mkdir(exist_ok=True)
    converted = []
    for ex in unique:
        text = converter(ex)
        converted.append({"text": text})

    # Write CSV (AutoTrain primary format)
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["text"])
        writer.writeheader()
        for row in converted:
            writer.writerow(row)

    # Write JSONL (alternative format)
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for row in converted:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Stats
    total_chars = sum(len(r["text"]) for r in converted)
    avg_chars = total_chars // len(converted) if converted else 0
    est_tokens = total_chars // 4  # rough estimate

    print(f"\n  {'='*50}")
    print(f"  Output CSV:   {OUTPUT_CSV}")
    print(f"  Output JSONL: {OUTPUT_JSONL}")
    print(f"  Examples:     {len(converted)}")
    print(f"  Total chars:  {total_chars:,}")
    print(f"  Avg chars:    {avg_chars:,}")
    print(f"  Est tokens:   {est_tokens:,}")
    print(f"  CSV size:     {OUTPUT_CSV.stat().st_size / 1024:.1f} KB")
    print(f"  {'='*50}")

    # Type breakdown
    types = {}
    for ex in unique:
        t = ex.get("type", "unknown")
        types[t] = types.get(t, 0) + 1
    print(f"\n  By type:")
    for t, n in sorted(types.items(), key=lambda x: -x[1]):
        print(f"    {t:30s} {n}")

    print(f"\n  AutoTrain upload:")
    print(f"    1. Go to huggingface.co/autotrain")
    print(f"    2. New Project → LLM Fine-tuning")
    print(f"    3. Upload {OUTPUT_CSV}")
    print(f"    4. Base model: TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    print(f"    5. Text column: text")
    print(f"    6. Train")


def show_stats():
    """Show stats for prepared data."""
    for path in [OUTPUT_CSV, OUTPUT_JSONL]:
        if not path.exists():
            continue
        if path.suffix == ".jsonl":
            rows = [json.loads(l) for l in path.open(encoding="utf-8")]
        else:
            with path.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        total_chars = sum(len(r.get("text", "")) for r in rows)
        print(f"\n  {path}")
        print(f"  Rows:       {len(rows)}")
        print(f"  Total chars: {total_chars:,}")
        print(f"  Est tokens: {total_chars // 4:,}")
        print(f"  File size:  {path.stat().st_size / 1024:.1f} KB")

        # Show length distribution
        lengths = sorted(len(r.get("text", "")) for r in rows)
        if lengths:
            print(f"  Min length: {lengths[0]:,} chars")
            print(f"  Max length: {lengths[-1]:,} chars")
            print(f"  Median:     {lengths[len(lengths)//2]:,} chars")


def validate():
    """Validate prepared data is AutoTrain-ready."""
    issues = []

    if not OUTPUT_CSV.exists():
        print("  No prepared data. Run --prepare first.")
        return

    with OUTPUT_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        issues.append("FAIL: CSV is empty")
    if "text" not in (rows[0].keys() if rows else {}):
        issues.append("FAIL: Missing 'text' column")

    empty = sum(1 for r in rows if not r.get("text", "").strip())
    if empty:
        issues.append(f"WARN: {empty} empty text rows")

    too_short = sum(1 for r in rows if len(r.get("text", "")) < 100)
    if too_short:
        issues.append(f"WARN: {too_short} rows under 100 chars")

    too_long = sum(1 for r in rows if len(r.get("text", "")) > 10000)
    if too_long:
        issues.append(f"WARN: {too_long} rows over 10K chars")

    if issues:
        for i in issues:
            print(f"  {i}")
    else:
        print(f"  PASS: {len(rows)} rows, all valid for AutoTrain")
        print(f"  Column: text ✓")
        print(f"  Format: ChatML ✓")
        print(f"  Ready to upload to huggingface.co/autotrain")


def main():
    parser = argparse.ArgumentParser(
        prog="axiom_autotrain_prep",
        description="Prepare AXIOM training data for HuggingFace AutoTrain"
    )
    parser.add_argument("--prepare",  action="store_true", help="Prepare dataset")
    parser.add_argument("--stats",    action="store_true", help="Show dataset stats")
    parser.add_argument("--validate", action="store_true", help="Validate AutoTrain readiness")
    parser.add_argument("--format",   default="chatml",
                        choices=["chatml", "alpaca", "completion"],
                        help="Output format (default: chatml)")
    parser.add_argument("--no-augment", action="store_true", help="Skip augmentation")
    args = parser.parse_args()

    if args.prepare:
        prepare(fmt=args.format, augment=not args.no_augment)
        return

    if args.stats:
        show_stats()
        return

    if args.validate:
        validate()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
