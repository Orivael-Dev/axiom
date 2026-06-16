"""Gemma 4 12B — SRD chunk boundary calibration sweep.

Architecture-fingerprinted chunk detection for Gemma 4 12B:

  Step 1  Fast proxy pass — residual norm ratio per layer (no PPL, <5 min).
          Identifies which layers benefit most from D8 correction by measuring
          ||D8_reconstructed||_F / ||W4_reconstructed||_F for every Linear in
          the model. Peaks in this curve = precision-sensitive bands.

  Step 2  Candidate window generation from the proxy:
            auto_tight  — layers where ratio ≥ 70th percentile of all ratios
            auto_broad  — auto_tight expanded by 10% of depth on each side
            current     — hardcoded 40–77% (module default, for comparison)

  Step 3  PPL evaluation (WikiText-2, n_tokens tokens each):
            baseline    — pure Q4, zero D8
            current     — 40–77% selective D8
            auto_tight  — proxy-derived tight window
            auto_broad  — proxy-derived broad window
            full_srd    — D8 on every layer

  Output: table of (mode, start_frac, end_frac, PPL, MB, ppl/MB-efficiency)
          + recommended _REASONING_START_FRAC / _REASONING_END_FRAC to set
          in srd_selective_sidecar.py for Gemma 4 12B.

  The recommended fracs can be stored in the MODELS entry in
  bench_sidecar_hallucination.py as start_frac / end_frac.

CLI:
    # Proxy only (fast, no PPL)
    python -m research.quant.bench_gemma4_12b_chunk_sweep \\
        --model google/gemma-4-12b-it --hf-token hf_... --proxy-only \\
        --output research/quant/results/gemma4_12b_chunk_sweep.json

    # Full sweep (proxy + 5 PPL evals, ~35 min on A100 80 GB)
    python -m research.quant.bench_gemma4_12b_chunk_sweep \\
        --model google/gemma-4-12b-it --hf-token hf_... \\
        --n-tokens 4096 \\
        --output research/quant/results/gemma4_12b_chunk_sweep.json

    # Quick smoke test (1024 tokens, very fast)
    python -m research.quant.bench_gemma4_12b_chunk_sweep \\
        --model google/gemma-4-12b-it --hf-token hf_... \\
        --n-tokens 1024 --quick \\
        --output research/quant/results/gemma4_12b_chunk_sweep.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch
import torch.nn as nn

from research.quant.quantize_model import quantize_hf_model_inplace, _iter_linears
from research.quant.srd_selective_sidecar import (
    apply_d8_correction,
    reasoning_layer_ids,
    sidecar_ram_mb,
    _REASONING_START_FRAC,
    _REASONING_END_FRAC,
)
from research.quant.bench_sidecar_hallucination import (
    _count_transformer_layers,
    _layer_idx_from_name,
    _wikitext2_ppl,
)

_SEP = "─" * 76


# ── Proxy: residual norm ratio per layer ─────────────────────────────────────

def _residual_norm_ratio(pack) -> float:
    out_features, in_features = pack.W4.shape
    n_groups = in_features // pack.group_size
    W4f = pack.W4.float().view(out_features, n_groups, pack.group_size)
    D8f = pack.D8.float().view(out_features, n_groups, pack.group_size)
    base_norm  = (W4f * pack.S4.unsqueeze(-1)).norm(p="fro").item()
    resid_norm = (D8f * pack.S8.unsqueeze(-1)).norm(p="fro").item()
    return resid_norm / base_norm if base_norm > 1e-12 else 0.0


def _proxy_per_layer(
    packed: dict,
    n_layers: int,
) -> List[float]:
    """Max residual norm ratio per layer index (across all weights in the layer)."""
    layer_max: Dict[int, float] = {}
    for name, pack in packed.items():
        idx = _layer_idx_from_name(name)
        if idx is None:
            continue
        ratio = _residual_norm_ratio(pack)
        if idx not in layer_max or ratio > layer_max[idx]:
            layer_max[idx] = ratio
    return [layer_max.get(i, 0.0) for i in range(n_layers)]


# ── Candidate window derivation ───────────────────────────────────────────────

def _auto_fracs(
    ratios: List[float],
    percentile: float = 0.70,
    expansion: float = 0.10,
    min_layers: int = 3,
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Return (tight_fracs, broad_fracs) derived from the per-layer proxy.

    tight: contiguous band ≥ percentile threshold
    broad: tight expanded by expansion fraction on each side (clipped to [0,1])
    """
    n = len(ratios)
    if n < 2:
        df = (_REASONING_START_FRAC, _REASONING_END_FRAC)
        return df, df

    sorted_r = sorted(ratios)
    threshold = sorted_r[max(0, int(n * percentile) - 1)]
    above = [i for i, r in enumerate(ratios) if r >= threshold]

    if len(above) < min_layers:
        df = (_REASONING_START_FRAC, _REASONING_END_FRAC)
        return df, df

    first, last = above[0], above[-1]
    tight_s = first / n
    tight_e = (last + 1) / n

    exp_layers = max(1, round(n * expansion))
    broad_s = max(0.0, (first - exp_layers) / n)
    broad_e = min(1.0, (last + 1 + exp_layers) / n)

    return (round(tight_s, 3), round(tight_e, 3)), (round(broad_s, 3), round(broad_e, 3))


def _sidecar_mb_for_fracs(
    n_layers: int, hidden: int, intermediate: int,
    start_frac: float, end_frac: float,
) -> float:
    est = sidecar_ram_mb(n_layers, hidden, intermediate)
    n_reasoning = len(reasoning_layer_ids(n_layers, start_frac, end_frac))
    n_total     = len(reasoning_layer_ids(n_layers, 0.0, 1.0))
    if n_total == 0:
        return 0.0
    return round(est["total_MB"] * n_reasoning / n_total, 1)


# ── Single-mode PPL evaluator (loads / discards model each call) ──────────────

def _eval_mode(
    hf_id: str,
    hf_token: str,
    start_frac: Optional[float],
    end_frac: Optional[float],
    n_tokens: int,
    device: str,
    dtype,
    label: str,
) -> Tuple[float, int, float]:
    """Load model, apply selective D8, measure WikiText-2 PPL.

    Returns (ppl, n_corrected, elapsed_s).
    """
    from transformers import AutoModelForCausalLM, AutoTokenizer

    load_kw: dict = {"torch_dtype": dtype, "device_map": "auto"}
    if hf_token:
        load_kw["token"] = hf_token

    tok = AutoTokenizer.from_pretrained(
        hf_id, **({"token": hf_token} if hf_token else {})
    )
    model = AutoModelForCausalLM.from_pretrained(hf_id, **load_kw)
    model.eval()

    n_corrected = 0

    if start_frac is None:
        # baseline — pure Q4, zero D8
        quantize_hf_model_inplace(model, alpha=0.0, group_size=64, progress=False)

    elif start_frac == 0.0 and end_frac == 1.0:
        # full_srd — D8 on every layer
        packed = quantize_hf_model_inplace(model, alpha=1.0, group_size=64, progress=False)
        n_corrected = len(packed)

    else:
        # selective — apply D8 only to [start_frac, end_frac) window
        packed = quantize_hf_model_inplace(model, alpha=0.0, group_size=64, progress=False)
        n_layers     = _count_transformer_layers(model)
        r_ids        = set(reasoning_layer_ids(n_layers, start_frac, end_frac))
        for name, module in model.named_modules():
            if not isinstance(module, nn.Linear):
                continue
            if _layer_idx_from_name(name) not in r_ids:
                continue
            if name not in packed:
                continue
            pack = packed[name]
            with torch.no_grad():
                cw = apply_d8_correction(
                    module.weight.data, pack.D8, pack.S8, group_size=64,
                )
                module.weight.data.copy_(cw)
            n_corrected += 1

    t0  = time.monotonic()
    ppl = _wikitext2_ppl(model, tok, n_tokens=n_tokens, device=device)
    elapsed = time.monotonic() - t0

    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    print(f"    [{label}] PPL={ppl:.3f}  corrected={n_corrected}  t={elapsed:.0f}s")
    return ppl, n_corrected, elapsed


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Gemma 4 12B SRD chunk boundary sweep")
    ap.add_argument("--model",      default="google/gemma-4-12b-it",
                    help="HuggingFace model ID (must be Gemma 4 12B or similar)")
    ap.add_argument("--hf-token",   default="", help="HuggingFace access token")
    ap.add_argument("--n-tokens",   type=int, default=4096,
                    help="WikiText-2 tokens per PPL eval (lower = faster)")
    ap.add_argument("--proxy-only", action="store_true",
                    help="Run proxy pass only — no PPL evals")
    ap.add_argument("--quick",      action="store_true",
                    help="Alias for --n-tokens 1024 (smoke test)")
    ap.add_argument("--device",     default=None)
    ap.add_argument("--output",     type=Path,
                    default=Path("research/quant/results/gemma4_12b_chunk_sweep.json"))
    args = ap.parse_args()

    if args.quick:
        args.n_tokens = 1024

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype  = torch.float16 if device == "cuda" else torch.float32

    print(_SEP)
    print(f"  Gemma 4 12B — SRD chunk boundary calibration sweep")
    print(f"  model   : {args.model}")
    print(f"  device  : {device}  |  n_tokens : {args.n_tokens}")
    print(_SEP)

    # ── Step 1: proxy pass ────────────────────────────────────────────────────
    print("\n[1/3] Fast proxy pass (residual norm ratio per layer)...")
    from transformers import AutoModelForCausalLM, AutoTokenizer

    load_kw: dict = {"torch_dtype": dtype, "device_map": "auto"}
    if args.hf_token:
        load_kw["token"] = args.hf_token

    model = AutoModelForCausalLM.from_pretrained(args.model, **load_kw)
    model.eval()

    # Read architecture from the loaded model
    cfg       = model.config
    n_layers  = cfg.num_hidden_layers
    hidden    = cfg.hidden_size
    inter     = cfg.intermediate_size

    print(f"  Architecture: n_layers={n_layers}  hidden={hidden}  "
          f"intermediate={inter}")

    packed    = quantize_hf_model_inplace(model, alpha=1.0, group_size=64, progress=True)
    ratios    = _proxy_per_layer(packed, n_layers)

    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # Print per-layer proxy
    print(f"\n  Per-layer max residual norm ratio (0=no correction needed, 1=high):")
    for i, r in enumerate(ratios):
        bar = "█" * int(r * 40)
        print(f"    layer {i:3d}  {r:.4f}  {bar}")

    # ── Step 2: derive candidate windows ─────────────────────────────────────
    print("\n[2/3] Deriving candidate windows...")
    tight_fracs, broad_fracs = _auto_fracs(ratios, percentile=0.70, expansion=0.10)
    current_fracs = (_REASONING_START_FRAC, _REASONING_END_FRAC)

    tight_ids   = reasoning_layer_ids(n_layers, *tight_fracs)
    broad_ids   = reasoning_layer_ids(n_layers, *broad_fracs)
    current_ids = reasoning_layer_ids(n_layers, *current_fracs)

    print(f"  current  (module default)  : {current_fracs[0]:.2f}–{current_fracs[1]:.2f}"
          f"  → layers {current_ids[0] if current_ids else '?'}–{current_ids[-1] if current_ids else '?'}"
          f"  ({len(current_ids)} layers)")
    print(f"  auto_tight (proxy ≥p70)    : {tight_fracs[0]:.3f}–{tight_fracs[1]:.3f}"
          f"  → layers {tight_ids[0] if tight_ids else '?'}–{tight_ids[-1] if tight_ids else '?'}"
          f"  ({len(tight_ids)} layers)")
    print(f"  auto_broad (tight ± 10%)   : {broad_fracs[0]:.3f}–{broad_fracs[1]:.3f}"
          f"  → layers {broad_ids[0] if broad_ids else '?'}–{broad_ids[-1] if broad_ids else '?'}"
          f"  ({len(broad_ids)} layers)")

    proxy_out = {
        "model":          args.model,
        "timestamp":      datetime.datetime.utcnow().isoformat(),
        "n_layers":       n_layers,
        "hidden":         hidden,
        "intermediate":   inter,
        "per_layer_ratios": [round(r, 6) for r in ratios],
        "candidates": {
            "current":    {"start_frac": current_fracs[0], "end_frac": current_fracs[1],
                           "n_layers": len(current_ids)},
            "auto_tight": {"start_frac": tight_fracs[0],   "end_frac": tight_fracs[1],
                           "n_layers": len(tight_ids)},
            "auto_broad": {"start_frac": broad_fracs[0],   "end_frac": broad_fracs[1],
                           "n_layers": len(broad_ids)},
        },
    }

    if args.proxy_only:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(proxy_out, indent=2))
        print(f"\n  Proxy results written → {args.output}")
        print("  (--proxy-only: skipping PPL evals)")
        return 0

    # ── Step 3: PPL sweep ─────────────────────────────────────────────────────
    print(f"\n[3/3] PPL sweep (WikiText-2, n_tokens={args.n_tokens} each)...")

    # 5 modes: (label, start_frac, end_frac)
    # start_frac=None → baseline (pure Q4, no D8)
    # start_frac=0.0, end_frac=1.0 → full_srd
    modes = [
        ("baseline",   None,              None),
        ("current",    current_fracs[0],  current_fracs[1]),
        ("auto_tight", tight_fracs[0],    tight_fracs[1]),
        ("auto_broad", broad_fracs[0],    broad_fracs[1]),
        ("full_srd",   0.0,               1.0),
    ]

    ppl_results = []
    for label, sf, ef in modes:
        print(f"\n  Mode: {label}")
        ppl, n_corr, elapsed = _eval_mode(
            args.model, args.hf_token, sf, ef,
            args.n_tokens, device, dtype, label,
        )
        d8_mb = _sidecar_mb_for_fracs(
            n_layers, hidden, inter,
            sf if sf is not None else 0.0,
            ef if ef is not None else 0.0,
        )
        ppl_results.append({
            "mode":        label,
            "start_frac":  sf,
            "end_frac":    ef,
            "ppl":         round(ppl, 4),
            "n_corrected": n_corr,
            "d8_mb":       d8_mb,
            "elapsed_s":   round(elapsed, 1),
        })

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print(f"  RESULTS — Gemma 4 12B chunk boundary sweep")
    print(f"{_SEP}")
    base_ppl = next((r["ppl"] for r in ppl_results if r["mode"] == "baseline"), None)
    hdr = (f"  {'Mode':<14} {'start':>6} {'end':>6} {'layers':>7} "
           f"{'PPL':>7} {'Δ PPL':>7} {'D8 MB':>8} {'ppl/MB':>10}")
    print(hdr)
    print("  " + "─" * 72)
    for r in ppl_results:
        delta  = f"{r['ppl'] - base_ppl:+.3f}" if base_ppl else "—"
        eff    = (f"{(base_ppl - r['ppl']) / r['d8_mb']:.5f}"
                  if r['d8_mb'] > 0 and base_ppl else "—")
        sf_s   = f"{r['start_frac']:.3f}" if r['start_frac'] is not None else "—"
        ef_s   = f"{r['end_frac']:.3f}"   if r['end_frac']   is not None else "—"
        n_l    = str(r["n_corrected"])
        print(f"  {r['mode']:<14} {sf_s:>6} {ef_s:>6} {n_l:>7} "
              f"{r['ppl']:>7.3f} {delta:>7} {r['d8_mb']:>8.1f} {eff:>10}")

    # ── Recommendation ────────────────────────────────────────────────────────
    # Best = most PPL recovered per MB of D8 overhead
    candidates = [r for r in ppl_results
                  if r["mode"] not in ("baseline", "full_srd") and r["d8_mb"] > 0]
    if candidates and base_ppl:
        best = max(candidates, key=lambda r: (base_ppl - r["ppl"]) / r["d8_mb"])
    else:
        best = None

    print(f"\n{_SEP}")
    if best:
        print(f"  Recommendation: {best['mode']}")
        print(f"    start_frac = {best['start_frac']:.3f}")
        print(f"    end_frac   = {best['end_frac']:.3f}")
        print(f"    PPL gain   = {base_ppl - best['ppl']:.3f}  at {best['d8_mb']:.0f} MB overhead")
        print(f"\n  To apply: update MODELS['gemma4-12b'] in bench_sidecar_hallucination.py:")
        print(f"    \"start_frac\": {best['start_frac']},")
        print(f"    \"end_frac\":   {best['end_frac']},")
        if best["start_frac"] != current_fracs[0] or best["end_frac"] != current_fracs[1]:
            print(f"\n  NOTE: differs from current 40–77% default "
                  f"({current_fracs[0]:.2f}–{current_fracs[1]:.2f})")
            print(f"  This confirms architecture-fingerprinted boundaries outperform "
                  f"the chat/instruction-model preset for Gemma 4 12B.")
    else:
        print("  Insufficient data for recommendation — re-run with more tokens.")
    print(_SEP)

    # ── Write output ──────────────────────────────────────────────────────────
    out = {**proxy_out, "ppl_sweep": ppl_results}
    if best:
        out["recommended"] = {
            "start_frac": best["start_frac"],
            "end_frac":   best["end_frac"],
            "mode":       best["mode"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2))
    print(f"\n  Full results → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
