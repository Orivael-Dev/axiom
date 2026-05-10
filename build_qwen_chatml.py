"""
Build Qwen2.5-Coder ChatML training file from axiom_training_data.jsonl.

Qwen2.5 uses standard ChatML format:
  <|im_start|>system\n{system}<|im_end|>\n
  <|im_start|>user\n{user}<|im_end|>\n
  <|im_start|>assistant\n{assistant}<|im_end|>\n

Output: autotrain_data/train_qwen_chatml.jsonl
Each line is {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}

This format works with:
  - Hugging Face TRL SFTTrainer
  - Unsloth
  - Axolotl
  - Google Colab QLoRA notebooks
"""

import json
import hashlib
import sys

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

INPUT_PATH = "axiom_training_data.jsonl"
OUTPUT_PATH = "autotrain_data/train_qwen_chatml.jsonl"


def build():
    examples = []
    seen_hashes = set()

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            instruction = obj.get("instruction", "").strip()
            inp = obj.get("input", "").strip()
            output = obj.get("output", "").strip()

            if not instruction or not output:
                continue

            # Build user message
            if inp:
                user_msg = f"{instruction}\n\n{inp}"
            else:
                user_msg = instruction

            # Dedup by content hash
            h = hashlib.md5(f"{user_msg}|{output}".encode()).hexdigest()
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            # Qwen2.5 ChatML format
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": output},
            ]
            examples.append({"messages": messages})

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=True) + "\n")

    # Stats
    total_chars = sum(
        sum(len(m["content"]) for m in ex["messages"])
        for ex in examples
    )
    est_tokens = total_chars // 4

    print(f"  Qwen2.5-Coder ChatML Build")
    print(f"  {'=' * 44}")
    print(f"  Source:     {INPUT_PATH}")
    print(f"  Output:     {OUTPUT_PATH}")
    print(f"  Examples:   {len(examples)}")
    print(f"  Total chars: {total_chars:,}")
    print(f"  Est tokens:  {est_tokens:,}")
    print()
    print(f"  Format: Qwen2.5 ChatML (messages array)")
    print(f"  Compatible with: TRL SFTTrainer, Unsloth, Axolotl")
    print()


if __name__ == "__main__":
    build()
