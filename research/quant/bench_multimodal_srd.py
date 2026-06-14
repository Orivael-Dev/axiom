"""Multimodal SRD benchmark — TextVQA exact-match accuracy.

Compares four correction modes on SmolVLM-256M (Idefics3):

  baseline        : vanilla Q4 fake-quant, no correction
  lm_only         : selective SRD on language backbone reasoning layers only
  connector_lm    : connector + LM selective (no vision encoder)
  all_bands       : vision + connector + LM selective (full multi-band)

TextVQA measures visual factual accuracy — the model must read text in
an image and answer a question about it. Hallucination in the visual
grounding path shows up clearly here (wrong text read → wrong answer).

CLI
---
  # Quick test, 100 questions
  python research/quant/bench_multimodal_srd.py --n-questions 100

  # Full validation set (~5000 questions, ~2 hr on T4)
  python research/quant/bench_multimodal_srd.py --full

  # Dry run (checks imports + component detection, no inference)
  python research/quant/bench_multimodal_srd.py --dry-run

Output JSON schema:
  {
    "model":        "smolvlm-256m",
    "mode":         "baseline|lm_only|connector_lm|all_bands",
    "textvqa_acc":  0.XX,
    "n_questions":  N,
    "bands_corrected": {...},
    "wallclock_s":  XX.X,
    "timestamp":    "..."
  }
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch
import torch.nn as nn

from research.quant.quantize_model import quantize_hf_model_inplace
from research.quant.srd_multimodal import apply_multiband_srd, detect_components

MODEL_ID = "HuggingFace/SmolVLM-Instruct"

# Correction band sets per mode
MODES = {
    "baseline":     None,           # no correction at all after fake-quant
    "lm_only":      "lm",
    "connector_lm": "connector,lm",
    "all_bands":    "all",
}


# ── TextVQA loader ────────────────────────────────────────────────────────

def _load_textvqa(n: int) -> list:
    """Load n examples from TextVQA validation split.

    Each item: {"image": PIL.Image, "question": str, "answers": list[str]}
    """
    from datasets import load_dataset

    ds = load_dataset("textvqa", split="validation", trust_remote_code=True)
    items = []
    for row in list(ds)[:n]:
        items.append({
            "image":    row["image"],
            "question": row["question"],
            "answers":  row["answers"],
        })
    return items


# ── TextVQA evaluator ─────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """VQA-style normalization: lowercase, strip punctuation, collapse spaces."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _textvqa_acc(
    model: nn.Module,
    processor,
    items: list,
    *,
    device: str = "cuda",
    max_new_tokens: int = 20,
) -> float:
    """Evaluate TextVQA exact-match accuracy on the given items.

    Exact match: model answer (normalized) equals any of the 10 ground-truth
    answers (normalized) — same as standard VQA accuracy.
    """
    correct = 0
    for item in items:
        prompt = processor.apply_chat_template(
            [{"role": "user", "content": [
                {"type": "image"},
                {"type": "text", "text": item["question"]},
            ]}],
            add_generation_prompt=True,
        )
        inputs = processor(
            text=prompt,
            images=[item["image"]],
            return_tensors="pt",
        ).to(device)

        with torch.no_grad():
            out_ids = model.generate(**inputs, max_new_tokens=max_new_tokens,
                                     do_sample=False)
        # Decode only newly generated tokens
        generated = out_ids[0][inputs["input_ids"].shape[1]:]
        answer = processor.decode(generated, skip_special_tokens=True).strip()

        norm_pred = _normalize(answer)
        if any(_normalize(gt) == norm_pred for gt in item["answers"]):
            correct += 1

    return correct / len(items) if items else 0.0


# ── Main benchmark runner ─────────────────────────────────────────────────

def run_benchmark(
    n_questions: int = 100,
    *,
    output_path: Optional[Path] = None,
    hf_token: str = "",
    dry_run: bool = False,
) -> List[dict]:
    try:
        from transformers import AutoProcessor, AutoModelForVision2Seq
    except ImportError as _e:
        import transformers as _tf
        raise RuntimeError(
            f"transformers {_tf.__version__} is too old — AutoModelForVision2Seq "
            f"requires >= 4.36.0 (SmolVLM/Idefics3 support).\n"
            f"Fix: run  !pip install -q --upgrade 'transformers>=4.36.0' accelerate  "
            f"then restart the Colab runtime and re-run."
        ) from _e

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype  = torch.float16 if device == "cuda" else torch.float32

    print(f"\n{'='*65}")
    print(f"  SmolVLM-256M multimodal SRD benchmark")
    print(f"  n_questions={n_questions}  device={device}")
    print(f"{'='*65}")

    kw = {"torch_dtype": dtype, "device_map": "auto"}
    if hf_token:
        kw["token"] = hf_token

    print(f"\nLoading processor...")
    processor = AutoProcessor.from_pretrained(MODEL_ID,
                                              **({"token": hf_token} if hf_token else {}))

    if dry_run:
        print("\nLoading model for component detection only...")
        model = AutoModelForVision2Seq.from_pretrained(MODEL_ID, **kw)
        comps = detect_components(model)
        print(f"\nComponents detected:")
        print(f"  vision    → {comps.vision_prefix}  ({comps.n_lm_layers} LM layers)")
        print(f"  connector → {comps.connector_prefix}")
        print(f"  lm        → {comps.lm_prefix}")
        counts = {"vision": 0, "connector": 0, "lm": 0, "other": 0}
        for name, mod in model.named_modules():
            if isinstance(mod, nn.Linear):
                b = comps.band_for(name) or "other"
                counts[b] = counts.get(b, 0) + 1
        print(f"  Linear layers: {counts}")
        del model
        return []

    print(f"\nLoading {n_questions} TextVQA items...")
    items = _load_textvqa(n_questions)

    results = []

    for mode, bands in MODES.items():
        print(f"\n  ─── Mode: {mode} ───")

        model = AutoModelForVision2Seq.from_pretrained(MODEL_ID, **kw)
        model.eval()

        # Degrade all layers to Q4 baseline (alpha=0 = pure Q4, no residuals)
        quantize_hf_model_inplace(model, alpha=0.0, group_size=64, progress=False)

        bands_corrected: Dict[str, int] = {}

        if bands is not None:
            band_results = apply_multiband_srd(
                model, bands=bands, group_size=64, alpha=1.0, verbose=True,
            )
            bands_corrected = {b: r.corrected for b, r in band_results.items()}

        t0 = time.monotonic()
        acc = _textvqa_acc(model, processor, items, device=device)
        elapsed = time.monotonic() - t0

        result = {
            "model":            "smolvlm-256m",
            "hf_id":            MODEL_ID,
            "mode":             mode,
            "textvqa_acc":      round(acc, 4),
            "n_questions":      len(items),
            "bands_corrected":  bands_corrected,
            "wallclock_s":      round(elapsed, 1),
            "torch_version":    torch.__version__,
            "timestamp":        datetime.datetime.utcnow().isoformat(),
        }
        results.append(result)

        print(f"    TextVQA acc : {acc:.3f}  (exact match, {len(items)} questions)")
        print(f"    Bands       : {bands_corrected}")
        print(f"    Time        : {elapsed:.0f}s")

        del model

    _print_summary(results)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if output_path.exists():
            existing = json.loads(output_path.read_text())
        output_path.write_text(json.dumps(existing + results, indent=2))
        print(f"\nResults saved to {output_path}")

    return results


# ── Summary table ─────────────────────────────────────────────────────────

def _print_summary(results: List[dict]) -> None:
    print("\n" + "="*70)
    print("SUMMARY — TextVQA exact-match accuracy (↑ better)\n")
    print(f"  {'Mode':<20} {'TextVQA acc':>13} {'Δ baseline':>12} {'Bands':>6}")
    print("  " + "-"*55)

    base_acc = next((r["textvqa_acc"] for r in results if r["mode"] == "baseline"), None)

    for r in results:
        acc   = r["textvqa_acc"]
        delta = f"{acc - base_acc:+.3f}" if base_acc is not None and r["mode"] != "baseline" else "—"
        n_bands = len(r["bands_corrected"])
        print(f"  {r['mode']:<20} {acc:>13.3f} {delta:>12} {n_bands:>6}")


# ── CLI ───────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multimodal SRD benchmark — TextVQA")
    p.add_argument("--n-questions", type=int, default=100)
    p.add_argument("--full",       action="store_true",
                   help="Run full validation set (~5000 questions)")
    p.add_argument("--output",     default="results/multimodal_srd.json")
    p.add_argument("--hf-token",   default="")
    p.add_argument("--dry-run",    action="store_true")
    return p.parse_args()


def main() -> int:
    args  = _parse_args()
    n     = 5000 if args.full else args.n_questions
    run_benchmark(
        n_questions=n,
        output_path=Path(args.output),
        hf_token=args.hf_token,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
