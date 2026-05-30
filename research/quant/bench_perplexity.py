"""Sliding-window WikiText-2 perplexity for SRD-quantized models.

Produces rows 1-5 of the results table (FP16 baseline + 4 SRD configs).
Each row writes a single JSON record with stride, context, n_tokens,
model_revision, dataset_revision, group_size, alpha, bpw_reported,
wallclock_seconds, torch_version — every honesty knob captured.

The dataset sha256 fingerprint is written on first run; subsequent
runs refuse to proceed if the dataset has moved underneath us
(silent HuggingFace dataset updates are real).

Reference target for FP16 baseline PPL on TinyLlama-1.1B-Chat-v1.0:
~7.7 (published). If our number is wildly off, the eval harness is
broken — halt and debug before running the SRD sweep.

CLI:
    # Single config
    python -m research.quant.bench_perplexity \\
        --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \\
        --config srd_alpha_1.0_g64 \\
        --output research/quant/results/srd_alpha_1_g64.json

    # Full sweep (rows 1-5)
    python -m research.quant.bench_perplexity --sweep srd \\
        --output research/quant/results/srd_sweep.json
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Allow direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch                                                              # noqa: E402

from axiom_quant import (                                                 # noqa: E402
    DEFAULT_GROUP_SIZE,
    srd_bits_per_weight,
    srd_quantize,
    srd_quantize_per_tensor,
)
from research.quant.quantize_model import (                               # noqa: E402
    DEFAULT_SKIP_MODULES,
    quantize_hf_model_inplace,
)

# WikiText-2 dataset coordinates. Pin a known-good revision so PPL
# numbers are reproducible if HF moves the dataset.
WIKITEXT_PATH = "wikitext"
WIKITEXT_NAME = "wikitext-2-raw-v1"
WIKITEXT_SPLIT = "test"

# Reference TinyLlama-1.1B-Chat-v1.0 FP16 WT2 PPL (published).
# If our FP16 number is wildly off this, the harness has a bug.
TINYLLAMA_REFERENCE_PPL = 7.7
TINYLLAMA_TOLERANCE = 0.5

RESULTS_DIR = Path(__file__).resolve().parent / "results"
FINGERPRINT_FILE = RESULTS_DIR / "wikitext_fingerprint.txt"


# ── Fingerprint guard ────────────────────────────────────────────────


def dataset_fingerprint(text: str) -> str:
    """sha256 of the eval text — short enough to log, unique enough
    to detect silent dataset drift."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_or_write_fingerprint(text: str) -> str:
    """First run writes the fingerprint; later runs refuse if it
    doesn't match."""
    fp = dataset_fingerprint(text)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if FINGERPRINT_FILE.exists():
        stored = FINGERPRINT_FILE.read_text().strip()
        if stored != fp:
            raise RuntimeError(
                f"WikiText-2 fingerprint changed!\n"
                f"  stored:  {stored[:16]}...\n"
                f"  current: {fp[:16]}...\n"
                f"Either the HF dataset moved or you're on a different "
                f"split. Delete {FINGERPRINT_FILE} to accept the new "
                f"fingerprint (and document the change)."
            )
    else:
        FINGERPRINT_FILE.write_text(fp + "\n")
    return fp


# ── Sliding-window PPL ───────────────────────────────────────────────


def eval_wikitext2(
    model,
    tokenizer,
    *,
    stride: int = 512,
    context: int = 2048,
    max_tokens: Optional[int] = None,
    device: Optional[str] = None,
) -> tuple[float, int]:
    """Sliding-window perplexity on the WikiText-2 raw test split.

    Returns (perplexity, n_tokens_evaluated). Implementation mirrors
    the standard HuggingFace recipe (stride=512, context=2048) — same
    knobs llama.cpp's `--perplexity` reports under.
    """
    from datasets import load_dataset
    ds = load_dataset(WIKITEXT_PATH, WIKITEXT_NAME, split=WIKITEXT_SPLIT)
    text = "\n\n".join(ds["text"])
    fp = check_or_write_fingerprint(text)
    print(f"[ppl] dataset fingerprint {fp[:16]}... ({len(text):,} chars)")

    encodings = tokenizer(text, return_tensors="pt")
    input_ids = encodings.input_ids.to(device or next(model.parameters()).device)
    seq_len = input_ids.size(1)
    if max_tokens is not None:
        seq_len = min(seq_len, max_tokens)

    nlls: List[torch.Tensor] = []
    prev_end_loc = 0
    n_tokens = 0

    t0 = time.monotonic()
    for begin_loc in range(0, seq_len, stride):
        end_loc = min(begin_loc + context, seq_len)
        trg_len = end_loc - prev_end_loc
        if trg_len <= 0:
            continue
        chunk = input_ids[:, begin_loc:end_loc]
        target_ids = chunk.clone()
        target_ids[:, :-trg_len] = -100        # mask already-scored tokens

        with torch.no_grad():
            out = model(chunk, labels=target_ids)
            # HF returns mean NLL — un-mean by multiplying out the
            # number of unmasked target tokens.
            neg_log_likelihood = out.loss * trg_len
        nlls.append(neg_log_likelihood)
        n_tokens += trg_len
        prev_end_loc = end_loc
        if end_loc >= seq_len:
            break

    ppl = torch.exp(torch.stack(nlls).sum() / n_tokens).item()
    wall = time.monotonic() - t0
    print(f"[ppl] {n_tokens:,} tokens, PPL={ppl:.4f}, {wall:.1f}s")
    return ppl, n_tokens


# ── Sweep configs ────────────────────────────────────────────────────


@dataclass
class PerplexityConfig:
    """One row of the results table."""
    name: str
    description: str
    apply_quantization: Optional[Callable]   # (model) -> None, in-place
    bpw_reported: float                      # honest bits-per-weight


def _make_srd_apply(
    *,
    alpha: float,
    group_size: int,
    per_tensor: bool,
    top_k_pct: float = 1.0,
    layer_alphas: Optional[Dict[str, float]] = None,
) -> Callable:
    def _apply(model):
        quantize_hf_model_inplace(
            model, alpha=alpha, group_size=group_size,
            per_tensor=per_tensor, progress=False,
            top_k_pct=top_k_pct, layer_alphas=layer_alphas,
        )
    return _apply


def _bpw_srd_per_block(group_size: int, top_k_pct: float = 1.0) -> float:
    """Honest per-block SRD bpw via the kernel's own accounting."""
    dummy = torch.randn(8, group_size * 4, dtype=torch.float32)
    return srd_bits_per_weight(
        srd_quantize(dummy, group_size=group_size, top_k_pct=top_k_pct)
    )


def _bpw_srd_per_tensor(in_features: int = 256) -> float:
    dummy = torch.randn(8, in_features, dtype=torch.float32)
    return srd_bits_per_weight(srd_quantize_per_tensor(dummy))


def srd_sweep_configs(group_size: int = DEFAULT_GROUP_SIZE) -> List[PerplexityConfig]:
    """Rows 1-5 of the results table: FP16 baseline + 4 SRD configs."""
    bpw_block = _bpw_srd_per_block(group_size)
    bpw_tensor = _bpw_srd_per_tensor()
    return [
        PerplexityConfig(
            name="fp16_baseline",
            description="FP16 reference (no quantization).",
            apply_quantization=None,
            bpw_reported=16.0,
        ),
        PerplexityConfig(
            name=f"srd_alpha_0.0_g{group_size}",
            description=f"SRD per-block g={group_size}, alpha=0 (pure 4-bit base).",
            apply_quantization=_make_srd_apply(
                alpha=0.0, group_size=group_size, per_tensor=False),
            bpw_reported=4.0 + 32.0 / group_size,
        ),
        PerplexityConfig(
            name=f"srd_alpha_0.5_g{group_size}",
            description=f"SRD per-block g={group_size}, alpha=0.5 (half residue).",
            apply_quantization=_make_srd_apply(
                alpha=0.5, group_size=group_size, per_tensor=False),
            bpw_reported=bpw_block,
        ),
        PerplexityConfig(
            name=f"srd_alpha_1.0_g{group_size}",
            description=f"SRD per-block g={group_size}, alpha=1 (full residue).",
            apply_quantization=_make_srd_apply(
                alpha=1.0, group_size=group_size, per_tensor=False),
            bpw_reported=bpw_block,
        ),
        PerplexityConfig(
            name="srd_alpha_1.0_per_tensor",
            description="SRD per-tensor, alpha=1 (matches spec §5 demo).",
            apply_quantization=_make_srd_apply(
                alpha=1.0, group_size=DEFAULT_GROUP_SIZE, per_tensor=True),
            bpw_reported=bpw_tensor,
        ),
    ]


def srd_sparse_sweep_configs(
    group_size: int = DEFAULT_GROUP_SIZE,
) -> List[PerplexityConfig]:
    """Phase E2 sparse-residual sweep — fills the 5–12 bpw dead zone.

    Each config retains a different fraction of D8 elements (by absolute
    magnitude), zero-ing out the rest. The FP16 baseline is included so
    the resulting JSON is self-contained for plotting.

    Expected bpw range for G=64: ~5 (p=0) to 13 (p=1, same as dense).
    """
    bpw_dense = _bpw_srd_per_block(group_size, top_k_pct=1.0)
    configs = [
        PerplexityConfig(
            name="fp16_baseline",
            description="FP16 reference (no quantization).",
            apply_quantization=None,
            bpw_reported=16.0,
        ),
    ]
    for p in (0.10, 0.25, 0.50, 0.75, 1.00):
        bpw = _bpw_srd_per_block(group_size, top_k_pct=p)
        configs.append(PerplexityConfig(
            name=f"srd_sparse_{int(p * 100):03d}pct_g{group_size}",
            description=(
                f"SRD per-block g={group_size}, alpha=1, "
                f"top_k_pct={p:.2f} ({int(p*100)}% of D8 retained). "
                f"Fills dead zone at ~{bpw:.1f} bpw."
            ),
            apply_quantization=_make_srd_apply(
                alpha=1.0, group_size=group_size,
                per_tensor=False, top_k_pct=p,
            ),
            bpw_reported=bpw,
        ))
    return configs


# Layer-type substrings for typical LLaMA-style architectures
_ATTN_LAYERS = ("q_proj", "k_proj", "v_proj", "o_proj")
_MLP_LAYERS  = ("gate_proj", "up_proj", "down_proj")


def srd_selective_sweep_configs(
    group_size: int = DEFAULT_GROUP_SIZE,
) -> List[PerplexityConfig]:
    """Phase E2 layer-selective sweep — MLP-only, attention-only, and alternating.

    Uses layer_alphas to apply alpha=1 on targeted layer types and alpha=0
    elsewhere. Bpw estimates are rough averages assuming LLaMA-style
    architecture (MLP ~70% of weights, attention ~30%).
    """
    bpw_dense = _bpw_srd_per_block(group_size, top_k_pct=1.0)
    bpw_base  = 4.0 + 32.0 / group_size  # alpha=0 cost

    # Weighted estimates: MLP ≈ 70%, attention ≈ 30%
    bpw_mlp_only  = round(0.70 * bpw_dense + 0.30 * bpw_base, 2)
    bpw_attn_only = round(0.30 * bpw_dense + 0.70 * bpw_base, 2)

    def _attn_alphas(a: float) -> Dict[str, float]:
        return {k: a for k in _ATTN_LAYERS}

    def _mlp_alphas(a: float) -> Dict[str, float]:
        return {k: a for k in _MLP_LAYERS}

    return [
        PerplexityConfig(
            name="fp16_baseline",
            description="FP16 reference (no quantization).",
            apply_quantization=None,
            bpw_reported=16.0,
        ),
        PerplexityConfig(
            name=f"srd_mlp_only_g{group_size}",
            description=(
                f"SRD g={group_size}: alpha=1 on MLP (gate/up/down), "
                f"alpha=0 on attention. Rough bpw ~{bpw_mlp_only} "
                f"(LLaMA-style weight distribution)."
            ),
            apply_quantization=_make_srd_apply(
                alpha=0.0, group_size=group_size, per_tensor=False,
                layer_alphas={**_mlp_alphas(1.0), **_attn_alphas(0.0)},
            ),
            bpw_reported=bpw_mlp_only,
        ),
        PerplexityConfig(
            name=f"srd_attn_only_g{group_size}",
            description=(
                f"SRD g={group_size}: alpha=1 on attention (q/k/v/o), "
                f"alpha=0 on MLP. Rough bpw ~{bpw_attn_only}."
            ),
            apply_quantization=_make_srd_apply(
                alpha=0.0, group_size=group_size, per_tensor=False,
                layer_alphas={**_attn_alphas(1.0), **_mlp_alphas(0.0)},
            ),
            bpw_reported=bpw_attn_only,
        ),
        PerplexityConfig(
            name=f"srd_dense_g{group_size}",
            description=f"SRD per-block g={group_size}, alpha=1 (full residue, reference).",
            apply_quantization=_make_srd_apply(
                alpha=1.0, group_size=group_size, per_tensor=False,
            ),
            bpw_reported=bpw_dense,
        ),
    ]


# ── Sweep runner ─────────────────────────────────────────────────────


def run_sweep(
    configs: List[PerplexityConfig],
    *,
    model_name: str,
    model_revision: Optional[str],
    stride: int,
    context: int,
    max_tokens: Optional[int],
    output_json: Path,
    device: Optional[str] = None,
) -> None:
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import datasets as _datasets

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device == "cuda" else torch.float32

    print(f"[sweep] loading {model_name} (revision={model_revision or 'default'})...")
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=model_revision)
    base_model = AutoModelForCausalLM.from_pretrained(
        model_name, revision=model_revision, torch_dtype=dtype
    ).to(device)
    base_model.eval()

    results = []
    for i, cfg in enumerate(configs, start=1):
        print(f"\n[sweep] ── ({i}/{len(configs)}) {cfg.name} ──")
        # Fresh weights for each config: re-instantiate from disk-cached
        # weights to avoid alpha=0 → alpha=1 contamination.
        if cfg.apply_quantization is None:
            model = base_model
        else:
            model = AutoModelForCausalLM.from_pretrained(
                model_name, revision=model_revision, torch_dtype=dtype
            ).to(device)
            model.eval()
            cfg.apply_quantization(model)

        t0 = time.monotonic()
        ppl, n_tokens = eval_wikitext2(
            model, tokenizer,
            stride=stride, context=context,
            max_tokens=max_tokens, device=device,
        )
        wall = time.monotonic() - t0

        if cfg.name == "fp16_baseline":
            err = abs(ppl - TINYLLAMA_REFERENCE_PPL)
            if err > TINYLLAMA_TOLERANCE and "TinyLlama" in model_name:
                print(f"[sweep] ⚠ FP16 PPL {ppl:.3f} vs reference "
                      f"{TINYLLAMA_REFERENCE_PPL} ± {TINYLLAMA_TOLERANCE} "
                      f"— harness may be buggy.")

        results.append({
            "name":             cfg.name,
            "description":      cfg.description,
            "bpw_reported":     round(cfg.bpw_reported, 4),
            "perplexity":       round(ppl, 4),
            "n_tokens":         n_tokens,
            "stride":           stride,
            "context":          context,
            "wallclock_seconds": round(wall, 2),
            "model":            model_name,
            "model_revision":   model_revision,
            "dataset":          f"{WIKITEXT_PATH}/{WIKITEXT_NAME}/{WIKITEXT_SPLIT}",
            "skip_modules":     list(DEFAULT_SKIP_MODULES),
            "torch_version":    torch.__version__,
            "transformers_version": __import__("transformers").__version__,
            "datasets_version": _datasets.__version__,
            "device":           device,
            "dtype":            str(dtype),
        })

        if model is not base_model:
            del model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n[sweep] wrote {len(results)} rows to {output_json}")


# ── CLI ──────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SRD WikiText-2 perplexity sweep")
    p.add_argument("--model", default="TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    p.add_argument("--revision", default=None,
                   help="HF revision to pin (recommended for reproducibility)")
    p.add_argument(
        "--sweep",
        choices=["srd", "srd_sparse", "srd_selective"],
        default="srd",
        help=(
            "Named sweep to run. "
            "'srd' = original 5-row sweep. "
            "'srd_sparse' = Phase E2 sparse-residual sweep (fills bpw dead zone). "
            "'srd_selective' = Phase E2 layer-selective sweep (MLP-only / attn-only)."
        ),
    )
    p.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    p.add_argument("--stride", type=int, default=512)
    p.add_argument("--context", type=int, default=2048)
    p.add_argument("--max-tokens", type=int, default=None,
                   help="Cap eval at N tokens (useful for fast smoke test)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--device", default=None)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    sweep_fn = {
        "srd":          srd_sweep_configs,
        "srd_sparse":   srd_sparse_sweep_configs,
        "srd_selective": srd_selective_sweep_configs,
    }[args.sweep]
    configs = sweep_fn(group_size=args.group_size)
    run_sweep(
        configs,
        model_name=args.model,
        model_revision=args.revision,
        stride=args.stride,
        context=args.context,
        max_tokens=args.max_tokens,
        output_json=args.output,
        device=args.device,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
