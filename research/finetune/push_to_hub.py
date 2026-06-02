"""Push the fine-tuned Axiom model to HuggingFace Hub.

Usage:
    export HF_TOKEN="hf_..."   # must have write access to orivael org
    python3 research/finetune/push_to_hub.py \\
        --model-path /content/axiom_model_merged \\
        --repo-id    orivael/axiom-qwen2.5-coder-1.5b \\
        --gguf-path  /content/axiom_qwen_q4km.gguf \\
        --lora-path  /content/axiom_model_lora \\
        --eval-json  results/axiom_eval_lora.json \\
        --public
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parent.parent.parent
_TEMPLATE = Path(__file__).parent / "MODEL_CARD_TEMPLATE.md"


def _render_card(eval_results: dict, repo_id: str) -> str:
    template = _TEMPLATE.read_text()

    # Fill in eval metric table
    metric_rows = []
    metric_map = {
        "json_validity":      "Valid JSON rate",
        "verdict_accuracy":   "Correct verdict",
        "reason_code":        "Correct reason code",
        "tamper_detection":   "Tamper detection",
        "revocation":         "Revocation understanding",
        "refusal_on_harm":    "Tool-call refusal on harmful action",
        "no_fake_signatures": "No fake signatures",
        "cli_accuracy":       "CLI command accuracy",
    }
    target_map = {
        "json_validity":      ">98%",
        "verdict_accuracy":   ">95%",
        "reason_code":        ">90%",
        "tamper_detection":   ">95%",
        "revocation":         ">95%",
        "refusal_on_harm":    ">95%",
        "no_fake_signatures": ">98%",
        "cli_accuracy":       ">85%",
    }
    for key, label in metric_map.items():
        v = eval_results.get(key, {})
        score = f"{v.get('score', 0):.1%}" if isinstance(v, dict) else "N/A"
        target = target_map[key]
        passed = "✓" if isinstance(v, dict) and v.get("passed") else "✗"
        metric_rows.append(f"| {label} | {target} | {score} | {passed} |")

    metric_table = "\n".join(metric_rows)
    card = template.replace("{{METRIC_TABLE}}", metric_table)
    card = card.replace("{{REPO_ID}}", repo_id)
    card = card.replace("{{TIMESTAMP}}", eval_results.get("timestamp", "N/A"))
    return card


def push_model(
    model_path: str,
    repo_id: str = "orivael/axiom-qwen2.5-coder-1.5b",
    gguf_path: Optional[str] = None,
    lora_path: Optional[str] = None,
    eval_json: Optional[str] = None,
    token: Optional[str] = None,
    private: bool = False,
):
    from huggingface_hub import HfApi, create_repo

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("HF_TOKEN env var not set — needed for push to orivael org")

    api = HfApi(token=token)

    # 1. Create repo if it doesn't exist
    print(f"Creating/verifying repo: {repo_id}")
    create_repo(repo_id, token=token, private=private, exist_ok=True, repo_type="model")

    # 2. Push merged model weights + tokenizer
    print(f"Pushing merged model from {model_path} ...")
    api.upload_folder(
        folder_path=model_path,
        repo_id=repo_id,
        repo_type="model",
        commit_message="Add fine-tuned model weights",
        ignore_patterns=["*.pt", "*.pth"],  # exclude raw pytorch checkpoints
    )
    print("  ✓ Model weights uploaded")

    # 3. Push LoRA adapter to subfolder
    if lora_path and Path(lora_path).is_dir():
        print(f"Pushing LoRA adapter from {lora_path} ...")
        api.upload_folder(
            folder_path=lora_path,
            repo_id=repo_id,
            repo_type="model",
            path_in_repo="adapter",
            commit_message="Add LoRA adapter for community fine-tuning",
            ignore_patterns=["optimizer.pt", "rng_state.pth"],
        )
        print("  ✓ LoRA adapter uploaded to adapter/")

    # 4. Push GGUF
    if gguf_path and Path(gguf_path).is_file():
        size_gb = Path(gguf_path).stat().st_size / 1024**3
        print(f"Pushing GGUF Q4_K_M ({size_gb:.2f} GB) ...")
        api.upload_file(
            path_or_fileobj=gguf_path,
            path_in_repo=Path(gguf_path).name,
            repo_id=repo_id,
            repo_type="model",
            commit_message="Add GGUF Q4_K_M for llama.cpp / edge inference",
        )
        print("  ✓ GGUF uploaded")

    # 5. Render and push model card
    eval_results = {}
    if eval_json and Path(eval_json).is_file():
        eval_results = json.loads(Path(eval_json).read_text())

    if _TEMPLATE.is_file():
        card = _render_card(eval_results, repo_id)
        api.upload_file(
            path_or_fileobj=card.encode("utf-8"),
            path_in_repo="README.md",
            repo_id=repo_id,
            repo_type="model",
            commit_message="Add model card with evaluation metrics",
        )
        print("  ✓ Model card uploaded")

    print(f"\n✓ Published: https://huggingface.co/{repo_id}")


def main():
    p = argparse.ArgumentParser(description="Push Axiom model to HuggingFace Hub")
    p.add_argument("--model-path", required=True)
    p.add_argument("--repo-id",    default="orivael/axiom-qwen2.5-coder-1.5b")
    p.add_argument("--gguf-path",  default=None)
    p.add_argument("--lora-path",  default=None)
    p.add_argument("--eval-json",  default=None)
    p.add_argument("--token",      default=None)
    p.add_argument("--public",     action="store_true", help="Push as public repo")
    args = p.parse_args()

    push_model(
        model_path=args.model_path,
        repo_id=args.repo_id,
        gguf_path=args.gguf_path,
        lora_path=args.lora_path,
        eval_json=args.eval_json,
        token=args.token,
        private=not args.public,
    )


if __name__ == "__main__":
    main()
