"""SRD selective sidecar — hallucination benchmark.

Compares three inference modes on TruthfulQA (MC1 accuracy) and
WikiText-2 perplexity for each SLM:

  baseline   : vanilla Q4_K_M (no sidecar)
  full_srd   : SRD D8 applied to ALL layers
  selective  : SRD D8 applied to reasoning chunk only (this paper's claim)

TruthfulQA MC1 directly measures hallucination — the model picks the
truthful answer from a set that includes plausible-sounding falsehoods.
WikiText-2 PPL measures general language quality.

Models tested:
  SmolLM2-135M, Qwen2.5-Coder-0.5B, Gemma3-1B, TinyLlama-1.1B

CLI:
    # Single model, all three modes
    python -m research.quant.bench_sidecar_hallucination \\
        --model HuggingFaceTB/SmolLM2-135M-Instruct \\
        --sidecar /path/to/smollm2_135m.srd4 \\
        --output results/smollm2_sidecar_bench.json

    # Full sweep (all 4 models) — requires sidecar files on disk
    python -m research.quant.bench_sidecar_hallucination --sweep \\
        --sidecar-dir /path/to/srd4_files/ \\
        --output results/sidecar_hallucination_sweep.json

    # Dry run (checks imports, skips model load)
    python -m research.quant.bench_sidecar_hallucination --dry-run

Output JSON schema:
    {
      "model": "...",
      "mode": "baseline|full_srd|selective",
      "truthfulqa_mc1": 0.XX,      # higher = less hallucination
      "wikitext2_ppl": XX.X,       # lower = better language quality
      "reasoning_layers_corrected": N,
      "d8_overhead_mb": XX.X,
      "wallclock_s": XX.X,
      "torch_version": "...",
      "timestamp": "..."
    }
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch
import torch.nn as nn

from research.quant.quantize_model import quantize_hf_model_inplace
from research.quant.srd_selective_sidecar import (
    apply_sidecar_to_reasoning_layers,
    reasoning_layer_ids,
    sidecar_ram_mb,
)

# ── Model registry ────────────────────────────────────────────────────────

MODELS = {
    "smollm2-135m": {
        "hf_id":   "HuggingFaceTB/SmolLM2-135M-Instruct",
        "sidecar": "smollm2_135m_srd4.srd4",
        "n_layers": 30, "hidden": 576, "intermediate": 1536,
    },
    "qwen25-0p5b": {
        "hf_id":   "Qwen/Qwen2.5-Coder-0.5B-Instruct",
        "sidecar": "qwen25_coder_0p5b_srd4.srd4",
        "n_layers": 24, "hidden": 896, "intermediate": 4864,
    },
    "gemma3-1b": {
        "hf_id":   "google/gemma-3-1b-it",
        "sidecar": "gemma3_1b_srd4.srd4",
        "n_layers": 18, "hidden": 1152, "intermediate": 6912,
    },
    "tinyllama-1b": {
        "hf_id":   "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        "sidecar": "tinyllama_1b_srd4.srd4",
        "n_layers": 22, "hidden": 2048, "intermediate": 5632,
    },
}

# ── WikiText-2 perplexity ─────────────────────────────────────────────────

def _wikitext2_ppl(
    model: nn.Module,
    tokenizer,
    *,
    n_tokens: int = 4096,
    stride: int = 512,
    device: str = "cuda",
) -> float:
    """Sliding-window WikiText-2 perplexity (same method as bench_perplexity.py)."""
    from datasets import load_dataset

    ds  = load_dataset("wikitext", "wikitext-2-raw-v1", split="test")
    raw = "\n\n".join(t for t in ds["text"] if t.strip())
    enc = tokenizer(raw, return_tensors="pt")
    ids = enc.input_ids[0][:n_tokens].to(device)

    seq_len = ids.size(0)
    max_len = model.config.max_position_embeddings
    window  = min(max_len, 1024)

    nlls, count = [], 0
    for begin in range(0, seq_len, stride):
        end     = min(begin + window, seq_len)
        inp     = ids[begin:end].unsqueeze(0)
        tgt_len = end - max(begin, window - stride)
        with torch.no_grad():
            out = model(inp, labels=inp)
            nll = out.loss * tgt_len
        nlls.append(nll)
        count += tgt_len
        if end == seq_len:
            break

    return math.exp(sum(nlls).item() / count)


# ── TruthfulQA MC1 ───────────────────────────────────────────────────────

def _truthfulqa_mc1(
    model: nn.Module,
    tokenizer,
    *,
    n_questions: int = 200,
    device: str = "cuda",
) -> float:
    """TruthfulQA MC1 accuracy — fraction of questions where the model
    assigns highest log-prob to the correct (truthful) answer.

    Uses the first n_questions from the validation split to keep
    runtime manageable. Full set = 817 questions.
    """
    from datasets import load_dataset

    ds = load_dataset("truthful_qa", "multiple_choice", split="validation")
    correct, total = 0, 0

    for row in list(ds)[:n_questions]:
        question  = row["question"]
        choices   = row["mc1_targets"]["choices"]
        labels    = row["mc1_targets"]["labels"]   # 1 = correct, 0 = incorrect
        true_idx  = labels.index(1)

        log_probs = []
        for choice in choices:
            prompt = f"Q: {question}\nA: {choice}"
            enc    = tokenizer(prompt, return_tensors="pt").to(device)
            with torch.no_grad():
                out = model(**enc, labels=enc["input_ids"])
            log_probs.append(-out.loss.item())   # higher = more likely

        predicted = log_probs.index(max(log_probs))
        if predicted == true_idx:
            correct += 1
        total += 1

    return correct / total if total else 0.0


# ── Main benchmark runner ─────────────────────────────────────────────────

def run_benchmark(
    model_key: str,
    sidecar_path: Optional[Path],
    *,
    n_wikitext_tokens: int = 4096,
    n_truthfulqa: int = 200,
    output_path: Optional[Path] = None,
    hf_token: str = "",
) -> List[dict]:
    """Run all three modes for one model. Returns list of result dicts."""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    cfg    = MODELS[model_key]
    hf_id  = cfg["hf_id"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype  = torch.float16 if device == "cuda" else torch.float32

    print(f"\n{'='*60}")
    print(f"  {model_key}  ({hf_id})")
    print(f"{'='*60}")

    load_kw = {"torch_dtype": dtype, "device_map": "auto"}
    if hf_token:
        load_kw["token"] = hf_token

    results = []

    for mode in ("baseline", "selective", "full_srd"):
        print(f"\n  Mode: {mode}")

        tok = AutoTokenizer.from_pretrained(
            hf_id, **({"token": hf_token} if hf_token else {})
        )
        model = AutoModelForCausalLM.from_pretrained(hf_id, **load_kw)
        model.eval()

        # Apply SRD quantization first (fake-quant to Q4-equivalent weights)
        quantize_hf_model_inplace(model, alpha=1.0, group_size=64, progress=False)

        reasoning_corrected = 0
        d8_overhead_mb      = 0.0

        if mode == "selective" and sidecar_path and sidecar_path.exists():
            reasoning_corrected = apply_sidecar_to_reasoning_layers(
                model, sidecar_path, verbose=True,
            )
            est = sidecar_ram_mb(
                cfg["n_layers"], cfg["hidden"], cfg["intermediate"],
            )
            d8_overhead_mb = est["total_MB"]

        elif mode == "full_srd" and sidecar_path and sidecar_path.exists():
            # Apply D8 to ALL layers (not just reasoning chunk)
            from research.quant.srd_selective_sidecar import (
                load_sidecar, apply_d8_correction,
            )
            sidecar = load_sidecar(sidecar_path)
            for name, module in model.named_modules():
                if isinstance(module, nn.Linear) and name in sidecar:
                    D8, S8 = sidecar[name]
                    with torch.no_grad():
                        module.weight.data.copy_(
                            apply_d8_correction(module.weight.data, D8, S8)
                        )
                    reasoning_corrected += 1

        t0  = time.monotonic()
        ppl = _wikitext2_ppl(model, tok, n_tokens=n_wikitext_tokens, device=device)
        mc1 = _truthfulqa_mc1(model, tok, n_questions=n_truthfulqa, device=device)
        elapsed = time.monotonic() - t0

        result = {
            "model":                     model_key,
            "hf_id":                     hf_id,
            "mode":                      mode,
            "truthfulqa_mc1":            round(mc1,  4),
            "wikitext2_ppl":             round(ppl,  3),
            "reasoning_layers_corrected": reasoning_corrected,
            "d8_overhead_mb":            round(d8_overhead_mb, 1),
            "wallclock_s":               round(elapsed, 1),
            "torch_version":             torch.__version__,
            "timestamp":                 datetime.datetime.utcnow().isoformat(),
        }
        results.append(result)

        print(f"    TruthfulQA MC1 : {mc1:.3f}  (higher = less hallucination)")
        print(f"    WikiText-2 PPL : {ppl:.2f}  (lower = better)")
        print(f"    D8 overhead    : {d8_overhead_mb:.1f} MB")
        print(f"    Time           : {elapsed:.0f}s")

        del model

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if output_path.exists():
            existing = json.loads(output_path.read_text())
        output_path.write_text(json.dumps(existing + results, indent=2))
        print(f"\n  Results appended to {output_path}")

    return results


def _print_summary(all_results: List[dict]) -> None:
    print("\n" + "="*80)
    print("SUMMARY — TruthfulQA MC1 (higher = less hallucination)\n")
    print(f"  {'Model':<22} {'baseline':>10} {'selective':>10} {'full_srd':>10} {'Δ selective':>12}")
    print("  " + "-"*68)

    by_model: Dict[str, Dict[str, dict]] = {}
    for r in all_results:
        by_model.setdefault(r["model"], {})[r["mode"]] = r

    for model_key, modes in by_model.items():
        base = modes.get("baseline", {}).get("truthfulqa_mc1", float("nan"))
        sel  = modes.get("selective", {}).get("truthfulqa_mc1", float("nan"))
        full = modes.get("full_srd",  {}).get("truthfulqa_mc1", float("nan"))
        delta = sel - base if not math.isnan(sel) and not math.isnan(base) else float("nan")
        print(f"  {model_key:<22} {base:>10.3f} {sel:>10.3f} {full:>10.3f} "
              f"{delta:>+11.3f}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SRD sidecar hallucination benchmark")
    p.add_argument("--model",       default="smollm2-135m",
                   choices=list(MODELS), help="Model key")
    p.add_argument("--sidecar",     default=None,
                   help="Path to .srd4 sidecar file")
    p.add_argument("--sidecar-dir", default=None,
                   help="Directory with sidecar files (--sweep mode)")
    p.add_argument("--output",      default="results/sidecar_hallucination.json")
    p.add_argument("--n-wikitext",  type=int, default=4096)
    p.add_argument("--n-truthfulqa", type=int, default=200)
    p.add_argument("--sweep",       action="store_true",
                   help="Run all 4 models")
    p.add_argument("--dry-run",     action="store_true",
                   help="Check imports only, skip model load")
    p.add_argument("--hf-token",    default="", help="HuggingFace access token")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.dry_run:
        print("Imports OK — dry run complete")
        return 0

    out_path = Path(args.output)

    if args.sweep:
        sidecar_dir = Path(args.sidecar_dir) if args.sidecar_dir else Path(".")
        all_results = []
        for key, cfg in MODELS.items():
            sp = sidecar_dir / cfg["sidecar"]
            all_results += run_benchmark(
                key, sp if sp.exists() else None,
                n_wikitext_tokens=args.n_wikitext,
                n_truthfulqa=args.n_truthfulqa,
                output_path=out_path,
                hf_token=args.hf_token,
            )
        _print_summary(all_results)
    else:
        sp = Path(args.sidecar) if args.sidecar else None
        results = run_benchmark(
            args.model, sp,
            n_wikitext_tokens=args.n_wikitext,
            n_truthfulqa=args.n_truthfulqa,
            output_path=out_path,
            hf_token=args.hf_token,
        )
        _print_summary(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
