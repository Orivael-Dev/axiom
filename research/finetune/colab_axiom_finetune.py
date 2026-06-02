"""Orivael Axiom Model — Colab fine-tune pipeline.

Fine-tunes Qwen/Qwen2.5-Coder-1.5B (base) on the Axiom metric-targeted dataset
using QLoRA, evaluates against 8 metric targets, then merges and pushes to HF.

Paste each CELL block into a separate Colab code cell and run top to bottom.

Hardware requirements:
  Recommended: T4 (15 GB) — fits comfortably, ~4–5 hours
  Better:      A100 (40 GB) — ~1.5 hours
  Minimum:     T4 with 12.7 GB RAM — works if no other notebooks open

Time estimates on T4:
  Cell 2 (generate data):  ~3 min
  Cell 3 (merge data):     ~30 s
  Cell 4 (load model):     ~2 min
  Cell 5 (train 6 epochs): ~4–5 hours
  Cell 6 (eval):           ~30 min
  Cell 7 (merge + GGUF):   ~20 min
  Cell 8 (push to hub):    ~10 min (upload ~3 GB)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO        = Path("/content/axiom")
REPO_URL    = "https://github.com/orivael-dev/axiom.git"
REPO_BRANCH = "claude/srd-prototype-benchmark-JRtv1"   # files live on this branch, not main
OUT_DIR     = Path("/content")
DATA_DIR   = REPO / "autotrain_data"
RESULTS    = REPO / "results"
MODEL_ID   = "Qwen/Qwen2.5-Coder-1.5B"
HF_REPO    = "orivael/axiom-qwen2.5-coder-1.5b"
LLAMA_DIR  = Path("/content/llama.cpp")
LLAMA_CLI  = LLAMA_DIR / "build/bin/llama-cli"
MERGED_DIR = OUT_DIR / "axiom_model_merged"
GGUF_PATH  = OUT_DIR / "axiom_qwen_q4km.gguf"
LORA_DIR   = OUT_DIR / "axiom_model_lora"


# ════════════════════════════════════════════════════════════════════════════
# CELL 1 — GPU check + clone + install deps  (~3 min)
# ════════════════════════════════════════════════════════════════════════════
def cell1_setup():
    import torch

    p = torch.cuda.get_device_properties(0)
    vram_gb = p.total_memory / 1024**3
    print(f"GPU:  {p.name}  {vram_gb:.1f} GB VRAM  SM {p.major}.{p.minor}")

    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / 1024**3
        print(f"RAM:  {ram_gb:.1f} GB system")
    except ImportError:
        ram_gb = 0

    if vram_gb < 14:
        print("\n  ⚠  Less than 14 GB VRAM — training will be very slow.")
        print("  Upgrade to T4 or A100 if available.")
    else:
        print("  ✓ Memory looks sufficient for fine-tune")

    if not REPO.is_dir():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", REPO_BRANCH,
             REPO_URL, str(REPO)],
            check=True,
        )
    else:
        subprocess.run(["git", "-C", str(REPO), "pull", "origin", REPO_BRANCH], check=True)

    subprocess.run([
        sys.executable, "-m", "pip", "install", "-q",
        "transformers==4.46.0",
        "peft==0.13.0",
        "trl==0.12.0",
        "bitsandbytes",
        "accelerate==1.0.0",
        "datasets",
        "scipy",
        "huggingface_hub",
    ], check=True)

    if not os.environ.get("AXIOM_MASTER_KEY"):
        import secrets
        os.environ["AXIOM_MASTER_KEY"] = secrets.token_hex(32)
        print("AXIOM_MASTER_KEY set (random, session-only)")

    sys.path.insert(0, str(REPO))
    RESULTS.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n✓ Ready.  Repo: {REPO}")


# ════════════════════════════════════════════════════════════════════════════
# CELL 2 — Generate metric-targeted dataset  (~3 min)
# ════════════════════════════════════════════════════════════════════════════
def cell2_generate_data():
    sys.path.insert(0, str(REPO))

    print("=" * 60)
    print("CELL 2: Generate metric-targeted training data")
    print("=" * 60)

    targeted_path = DATA_DIR / "axiom_metric_targeted.jsonl"
    t0 = time.time()
    subprocess.run(
        [sys.executable, "research/finetune/gen_axiom_dataset.py",
         "--output", str(targeted_path),
         "--count",  "5000",
         "--seed",   "42",
         "--no-dedup"],
        cwd=REPO, check=True,
    )
    elapsed = time.time() - t0
    count = sum(1 for _ in open(targeted_path))
    print(f"\n✓ Generated {count:,} metric-targeted examples in {elapsed:.1f}s")
    print(f"  Output: {targeted_path}")
    print("  Categories: verdict, json_struct, tamper, revocation, tool_refusal,")
    print("              no_fake_sig, cli, kv_dag, format, adapter_block (10 total)")
    return targeted_path


# ════════════════════════════════════════════════════════════════════════════
# CELL 3 — Merge with existing 707 examples  (~30 s)
# ════════════════════════════════════════════════════════════════════════════
def cell3_merge_data():
    import random as _random

    print("=" * 60)
    print("CELL 3: Merge datasets → final_train.jsonl")
    print("=" * 60)

    sources = [
        DATA_DIR / "axiom_metric_targeted.jsonl",
        DATA_DIR / "train_qwen_chatml.jsonl",
    ]

    examples = []
    for src in sources:
        if not src.is_file():
            print(f"  ⚠ Missing: {src}")
            continue
        n = 0
        with open(src) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # Normalise to {"messages": [...]} format
                    if "messages" in obj:
                        examples.append(obj)
                    elif "instruction" in obj:
                        sys_content = obj.get("system", "")
                        examples.append({"messages": [
                            {"role": "system",    "content": sys_content},
                            {"role": "user",      "content": obj["instruction"]},
                            {"role": "assistant", "content": obj.get("output", "")},
                        ]})
                    n += 1
                except json.JSONDecodeError:
                    pass
        print(f"  Loaded {n:,} from {src.name}")

    # Deduplicate by first 80 chars of user message
    seen: set = set()
    deduped = []
    for ex in examples:
        key = ex["messages"][1]["content"][:80] if len(ex["messages"]) > 1 else str(ex)
        if key not in seen:
            seen.add(key)
            deduped.append(ex)

    _random.Random(42).shuffle(deduped)

    out_path = DATA_DIR / "final_train.jsonl"
    with open(out_path, "w") as f:
        for ex in deduped:
            f.write(json.dumps(ex, ensure_ascii=True) + "\n")

    print(f"\n✓ Merged: {len(deduped):,} total examples (after dedup)")
    print(f"  Output: {out_path}")
    return out_path


# ════════════════════════════════════════════════════════════════════════════
# CELL 4 — Load model in 4-bit + configure QLoRA  (~2 min)
# ════════════════════════════════════════════════════════════════════════════
def cell4_load_model():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    print("=" * 60)
    print("CELL 4: Load Qwen2.5-Coder-1.5B in 4-bit + LoRA")
    print("=" * 60)
    print(f"  Base model: {MODEL_ID}")
    print("  Quantization: nf4 double_quant")
    print("  LoRA: r=32, alpha=64, 7 target modules")

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=32,
        lora_alpha=64,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    print("\n✓ Model ready")
    return model, tokenizer


# ════════════════════════════════════════════════════════════════════════════
# CELL 5 — Train  (~4–5 hours on T4)
# ════════════════════════════════════════════════════════════════════════════
def cell5_train(model, tokenizer, data_path=None):
    from datasets import load_dataset
    from trl import SFTTrainer, DataCollatorForCompletionOnlyLM
    from transformers import TrainingArguments

    print("=" * 60)
    print("CELL 5: SFTTrainer — 6 epochs on final_train.jsonl")
    print("=" * 60)

    if data_path is None:
        data_path = str(DATA_DIR / "final_train.jsonl")

    dataset = load_dataset("json", data_files={"train": data_path}, split="train")
    print(f"  Training examples: {len(dataset):,}")

    def format_example(ex):
        msgs = ex["messages"]
        text = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
        return {"text": text}

    dataset = dataset.map(format_example, remove_columns=dataset.column_names)

    training_args = TrainingArguments(
        output_dir=str(LORA_DIR),
        num_train_epochs=6,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=1e-4,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        max_grad_norm=0.3,
        optim="paged_adamw_8bit",
        eval_strategy="no",
        save_strategy="steps",
        save_steps=100,
        save_total_limit=2,
        logging_steps=10,
        fp16=True,
        dataloader_num_workers=2,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=training_args,
        dataset_text_field="text",
        max_seq_length=2048,
        packing=False,
    )

    t0 = time.time()
    trainer.train()
    elapsed = time.time() - t0
    trainer.save_model(str(LORA_DIR))
    tokenizer.save_pretrained(str(LORA_DIR))

    print(f"\n✓ Training complete in {elapsed/3600:.2f} hours")
    print(f"  LoRA adapter saved to: {LORA_DIR}")
    return trainer


# ════════════════════════════════════════════════════════════════════════════
# CELL 6 — Evaluate 8 metrics  (~30 min on T4)
# ════════════════════════════════════════════════════════════════════════════
def cell6_evaluate(lora_path=None):
    sys.path.insert(0, str(REPO))

    print("=" * 60)
    print("CELL 6: Evaluate 8 Axiom metrics")
    print("=" * 60)

    if lora_path is None:
        lora_path = str(LORA_DIR)

    result = subprocess.run(
        [sys.executable, "research/finetune/eval_axiom_metrics.py",
         "--model",  lora_path,
         "--output", str(RESULTS / "axiom_eval_lora.json")],
        cwd=REPO, capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)

    eval_path = RESULTS / "axiom_eval_lora.json"
    if eval_path.is_file():
        results = json.loads(eval_path.read_text())
        all_pass = results.get("all_pass", False)
        if not all_pass:
            failed = [k for k, v in results.items()
                      if isinstance(v, dict) and not v.get("passed", True)]
            print(f"\n⚠  Failed metrics: {failed}")
            print("  Consider: more training epochs, larger dataset, or targeted data augmentation.")
        else:
            print("\n✓ All 8 metrics passed — ready to merge and push")
        return results
    return {}


# ════════════════════════════════════════════════════════════════════════════
# CELL 7 — Merge LoRA + build GGUF Q4_K_M  (~20 min)
# ════════════════════════════════════════════════════════════════════════════
def cell7_merge_and_gguf():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    print("=" * 60)
    print("CELL 7: Merge LoRA → base + build GGUF Q4_K_M")
    print("=" * 60)

    print("  Loading base model in FP16 ...")
    tokenizer = AutoTokenizer.from_pretrained(str(LORA_DIR), trust_remote_code=True)
    base = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16,
        device_map="cpu",
        trust_remote_code=True,
    )

    print("  Merging LoRA adapter ...")
    model = PeftModel.from_pretrained(base, str(LORA_DIR))
    model = model.merge_and_unload()
    model.save_pretrained(str(MERGED_DIR))
    tokenizer.save_pretrained(str(MERGED_DIR))
    print(f"  ✓ Merged model saved to {MERGED_DIR}")

    # Build llama.cpp if needed
    if not LLAMA_CLI.is_file():
        import torch as _t
        p    = _t.cuda.get_device_properties(0)
        arch = f"{p.major}{p.minor}"
        print(f"  Building llama.cpp for SM {p.major}.{p.minor} ...")
        if not LLAMA_DIR.is_dir():
            subprocess.run(["git", "clone", "--depth", "1",
                            "https://github.com/ggerganov/llama.cpp.git", str(LLAMA_DIR)], check=True)
        subprocess.run(
            ["cmake", "-B", str(LLAMA_DIR / "build"), "-S", str(LLAMA_DIR),
             "-DGGML_CUDA=ON", f"-DCMAKE_CUDA_ARCHITECTURES={arch}",
             "-DCMAKE_BUILD_TYPE=Release"], check=True,
        )
        nproc = subprocess.check_output(["nproc"]).decode().strip()
        subprocess.run(
            ["cmake", "--build", str(LLAMA_DIR / "build"),
             "-j", nproc, "-t", "llama-cli", "llama-quantize"], check=True,
        )

    # Convert to GGUF
    print("  Converting to GGUF F16 ...")
    f16_gguf = OUT_DIR / "axiom_qwen_f16.gguf"
    subprocess.run(
        [sys.executable, str(LLAMA_DIR / "convert_hf_to_gguf.py"),
         str(MERGED_DIR), "--outfile", str(f16_gguf), "--outtype", "f16"],
        check=True,
    )
    print("  Quantizing to Q4_K_M ...")
    subprocess.run(
        [str(LLAMA_DIR / "build/bin/llama-quantize"),
         str(f16_gguf), str(GGUF_PATH), "Q4_K_M"],
        check=True,
    )
    size_gb = GGUF_PATH.stat().st_size / 1024**3
    print(f"\n✓ GGUF Q4_K_M: {GGUF_PATH}  ({size_gb:.2f} GB)")
    return MERGED_DIR, GGUF_PATH


# ════════════════════════════════════════════════════════════════════════════
# CELL 8 — Push to HuggingFace Hub
# ════════════════════════════════════════════════════════════════════════════
def cell8_push():
    print("=" * 60)
    print("CELL 8: Push to HuggingFace Hub")
    print("=" * 60)

    eval_path = RESULTS / "axiom_eval_lora.json"
    eval_results = json.loads(eval_path.read_text()) if eval_path.is_file() else {}

    result = subprocess.run(
        [sys.executable, "research/finetune/push_to_hub.py",
         "--model-path",   str(MERGED_DIR),
         "--repo-id",      HF_REPO,
         "--gguf-path",    str(GGUF_PATH),
         "--lora-path",    str(LORA_DIR),
         "--eval-json",    str(eval_path),
         "--public"],
        cwd=REPO, check=True,
    )
    print(f"\n✓ Model published at https://huggingface.co/{HF_REPO}")


# ════════════════════════════════════════════════════════════════════════════
# Colab cell snippets
# ════════════════════════════════════════════════════════════════════════════
CELL_SNIPPETS = """\
# ── CELL 1: GPU check + clone + install deps (~3 min) ──────────────────────────
# The pipeline files live on the feature branch, not main — clone that branch.
import subprocess, sys
subprocess.run(["git", "clone", "--depth", "1",
    "--branch", "claude/srd-prototype-benchmark-JRtv1",
    "https://github.com/orivael-dev/axiom.git", "/content/axiom"], check=True)
sys.path.insert(0, "/content/axiom")
from research.finetune.colab_axiom_finetune import *
cell1_setup()

# ── CELL 2: Generate metric-targeted training data (~3 min) ────────────────────
cell2_generate_data()

# ── CELL 3: Merge datasets → final_train.jsonl (~30 s) ─────────────────────────
cell3_merge_data()

# ── CELL 4: Load Qwen2.5-Coder-1.5B in 4-bit + QLoRA config (~2 min) ──────────
model, tokenizer = cell4_load_model()

# ── CELL 5: Train 6 epochs (~4–5 hours on T4) ──────────────────────────────────
cell5_train(model, tokenizer)

# ── CELL 6: Evaluate 8 Axiom metrics (~30 min) ─────────────────────────────────
eval_results = cell6_evaluate()

# ── CELL 7: Merge LoRA → base + build GGUF Q4_K_M (~20 min) ───────────────────
merged_dir, gguf_path = cell7_merge_and_gguf()

# ── CELL 8: Push to HuggingFace Hub ────────────────────────────────────────────
# Requires: export HF_TOKEN="hf_..."  (write access to orivael org)
import os; os.environ["HF_TOKEN"] = "hf_YOUR_TOKEN_HERE"
cell8_push()
"""

if __name__ == "__main__":
    print(CELL_SNIPPETS)
