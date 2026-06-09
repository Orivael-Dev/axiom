"""MET RAM Estimator — offline hydration budget calculator.

Estimates the Axiom MET sidecar RAM budget for any decoder-only transformer
without needing the GGUF file or a GPU.  Reads architecture from a HF
config.json or from CLI flags, then outputs the same structure produced by
Cell 7 of qwen3_1b7_srd_arm_met.ipynb.

Usage:
    # From a local HuggingFace config.json:
    python3 research/quant/met_ram_estimator.py --config path/to/config.json

    # From explicit args:
    python3 research/quant/met_ram_estimator.py \\
        --vocab-size 151936 --hidden-size 2048 --num-layers 28 \\
        --num-heads 16 --num-kv-heads 8 --intermediate-size 11008 \\
        --bpw 4.85

    # Gemma-3-1B estimate:
    python3 research/quant/met_ram_estimator.py \\
        --vocab-size 262144 --hidden-size 1152 --num-layers 18 \\
        --num-heads 4 --num-kv-heads 1 --intermediate-size 6912 \\
        --bpw 4.85 --model-id "google/gemma-3-1b-it"

    # Save to JSON (same format as .axiom_meta.json sidecar):
    python3 research/quant/met_ram_estimator.py --config config.json \\
        --output /tmp/estimate.json

Validation against Qwen3-1.7B measured sidecar:
    Embedding:    estimated 593.8 MB  measured 593.5 MB  (<0.1% error)
    early chunk:  estimated 172.5 MB  measured 175.0 MB  (~1.4% error)
    GGUF total:   estimated 1061 MB   measured 1056 MB   (<0.5% error)

Formula
-------
  embed_mb        = vocab × hidden × 2  / 1024²          (F16, always pinned)
  transformer_mb  = non_embed_params × bpw / 8 / 1024²   (quantized)
  chunk[i]_mb     = transformer_mb × FRACS[i]
  hydration[cls]  = embed_mb + sum(chunk_mb for loaded chunks)

MLP style flags:
  --mlp-style swiglu   gate + up + down  (LLaMA / Qwen / Mistral)  default
  --mlp-style gated    same as swiglu
  --mlp-style mlp      up + down only    (GPT-2, Falcon)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

# Chunk fractions — matches qwen3_1b7_srd_arm_met.ipynb Cell 7
CHUNK_FRACS = {
    "early":      0.20,
    "factual":    0.20,
    "reasoning":  0.37,
    "governance": 0.23,
}
CHUNK_ORDER = ["early", "factual", "reasoning", "governance"]

HYDRATION_POLICY = {
    "INFORM":    ["early"],
    "CLARIFY":   ["early", "governance"],
    "REFUSE":    ["early", "governance"],
    "UNCERTAIN": ["early", "governance"],
    "HARM":      ["early", "factual", "reasoning", "governance"],
    "DECEIVE":   ["early", "factual", "reasoning", "governance"],
}


@dataclass
class ModelArch:
    vocab_size:        int
    hidden_size:       int
    num_layers:        int
    num_heads:         int
    num_kv_heads:      int
    intermediate_size: int
    mlp_style:         str = "swiglu"   # "swiglu" | "mlp"
    model_id:          str = ""
    bpw:               float = 4.85


def arch_from_config(path: Path, bpw: float = 4.85, mlp_style: str = "swiglu") -> ModelArch:
    """Load architecture from a HuggingFace config.json."""
    cfg = json.loads(path.read_text())

    vocab  = cfg.get("vocab_size") or cfg.get("padded_vocab_size")
    hidden = cfg.get("hidden_size")
    layers = cfg.get("num_hidden_layers")
    heads  = cfg.get("num_attention_heads")
    kv     = cfg.get("num_key_value_heads") or heads
    inter  = cfg.get("intermediate_size")

    missing = [k for k, v in [
        ("vocab_size", vocab), ("hidden_size", hidden),
        ("num_hidden_layers", layers), ("num_attention_heads", heads),
        ("intermediate_size", inter),
    ] if v is None]
    if missing:
        raise ValueError(f"config.json missing required fields: {missing}")

    # Detect MLP style from config if not overridden
    if mlp_style == "swiglu":
        act = cfg.get("hidden_act", "").lower()
        if act in ("gelu", "relu") and cfg.get("num_experts") is None:
            mlp_style = "mlp"

    return ModelArch(
        vocab_size        = int(vocab),
        hidden_size       = int(hidden),
        num_layers        = int(layers),
        num_heads         = int(heads),
        num_kv_heads      = int(kv),
        intermediate_size = int(inter),
        mlp_style         = mlp_style,
        model_id          = cfg.get("_name_or_path", ""),
        bpw               = bpw,
    )


# ── Size formulas ─────────────────────────────────────────────────────────────

def embedding_mb(arch: ModelArch) -> float:
    """F16 vocab embedding (always pinned, never quantized in GGUF Q4_K_M)."""
    return arch.vocab_size * arch.hidden_size * 2 / (1024 ** 2)


def transformer_params(arch: ModelArch) -> int:
    """Non-embedding transformer parameter count (attention + MLP per layer)."""
    head_dim  = arch.hidden_size // arch.num_heads
    kv_dim    = arch.num_kv_heads * head_dim

    # Attention projections
    attn = (
        arch.hidden_size * arch.hidden_size   +  # q_proj
        arch.hidden_size * kv_dim             +  # k_proj
        arch.hidden_size * kv_dim             +  # v_proj
        arch.hidden_size * arch.hidden_size      # o_proj
    )

    # MLP projections
    if arch.mlp_style in ("swiglu", "gated"):
        # gate_proj + up_proj + down_proj  (SwiGLU / GeGLU)
        mlp = (
            arch.hidden_size * arch.intermediate_size +
            arch.hidden_size * arch.intermediate_size +
            arch.intermediate_size * arch.hidden_size
        )
    else:
        # up_proj + down_proj only  (GPT-2 / Falcon style)
        mlp = (
            arch.hidden_size * arch.intermediate_size +
            arch.intermediate_size * arch.hidden_size
        )

    # Layer norms: 2 × hidden per layer — small but included
    norms = 2 * arch.hidden_size

    return (attn + mlp + norms) * arch.num_layers


def transformer_mb(arch: ModelArch) -> float:
    """Total transformer weight MB at arch.bpw."""
    params = transformer_params(arch)
    return params * arch.bpw / 8 / (1024 ** 2)


def _chunk_layer_counts(num_layers: int) -> dict[str, int]:
    """Compute exact layer count per chunk from CHUNK_FRACS boundaries.

    Uses round() on target fracs to get integer layer counts, then assigns
    any remaining layers to the last chunk — matching notebook Cell 7 logic.
    The chunk MB is then derived from (count/total) × transformer_mb so
    estimates track actual per-layer weight counts rather than target fracs.
    """
    counts = {}
    used   = 0
    names  = CHUNK_ORDER
    for i, name in enumerate(names[:-1]):
        n = max(1, round(CHUNK_FRACS[name] * num_layers))
        counts[name] = n
        used += n
    counts[names[-1]] = num_layers - used   # remainder to last chunk
    return counts


def chunk_mbs(total_transformer_mb: float, num_layers: int) -> dict[str, float]:
    """Split transformer MB proportionally to exact layer counts.

    More accurate than using raw CHUNK_FRACS because all layers have equal
    weight, so the true fraction = layers_in_chunk / total_layers.
    """
    counts = _chunk_layer_counts(num_layers)
    return {
        name: round(total_transformer_mb * count / num_layers, 1)
        for name, count in counts.items()
    }


def layer_ranges(num_layers: int) -> dict[str, tuple[int, int]]:
    """Compute first/last layer index for each chunk."""
    counts = _chunk_layer_counts(num_layers)
    ranges = {}
    start  = 0
    for name in CHUNK_ORDER:
        n = counts[name]
        ranges[name] = (start, start + n - 1)
        start += n
    return ranges


def hydration_table(
    embed_mb: float,
    chunks: dict[str, float],
    storage_mbs: float = 1500,
) -> dict:
    """Compute per-intent RAM budget and UFS load latency."""
    table = {}
    for intent, loaded in HYDRATION_POLICY.items():
        tx_mb = sum(chunks[c] for c in loaded)
        total = embed_mb + tx_mb
        load_ms = tx_mb / storage_mbs * 1000
        table[intent] = {
            "chunks":         loaded,
            "transformer_mb": round(tx_mb, 1),
            "total_mb":       round(total, 1),
            "ufs_load_ms":    round(load_ms, 1),
        }
    return table


# ── Sidecar builder ───────────────────────────────────────────────────────────

def build_sidecar(
    arch:        ModelArch,
    storage_mbs: float = 1500,
    gguf_path:   str   = "",
) -> dict:
    """Return a dict matching the .axiom_meta.json sidecar format."""
    emb_mb   = embedding_mb(arch)
    tx_mb    = transformer_mb(arch)
    chunks   = chunk_mbs(tx_mb, arch.num_layers)
    ranges   = layer_ranges(arch.num_layers)

    # GGUF size estimate: token embedding stored at Q8_0 (1.0625 bpw) in most GGUF
    # Q4_K_M builds. Transformer uses mixed quantization so effective bpw is slightly
    # below the nominal; small norm/bias tensors stored at F32 add ~3% overhead.
    # Note: tied embeddings (lm_head = token_embd) stored only once — common in
    # LLaMA/Qwen family. Actual GGUF may differ ±15% from this estimate.
    embed_q8_mb = arch.vocab_size * arch.hidden_size * 1.0625 / (1024 ** 2)
    est_gguf    = round((embed_q8_mb + tx_mb) * 1.03, 0)

    tx_chunks = {}
    for name in CHUNK_ORDER:
        first, last = ranges[name]
        tx_chunks[name] = {
            "layers":      f"{first}-{last}",
            "first_layer": first,
            "last_layer":  last,
            "mb":          chunks[name],
            "precision":   "Q4_K_M",
        }

    # chunk_map: layer → chunk name
    chunk_map = {}
    for name in CHUNK_ORDER:
        first, last = ranges[name]
        for layer in range(first, last + 1):
            chunk_map[str(layer)] = name

    hyd = hydration_table(emb_mb, chunks, storage_mbs)
    peak_mb = max(e["total_mb"] for e in hyd.values())

    return {
        "axiom_version":  "1.4",
        "_estimated":     True,
        "model_id":       arch.model_id,
        "bpw":            arch.bpw,
        "gguf_mb_est":    int(est_gguf),
        "architecture": {
            "hidden_size":          arch.hidden_size,
            "num_layers":           arch.num_layers,
            "vocab_size":           arch.vocab_size,
            "num_attention_heads":  arch.num_heads,
            "num_kv_heads":         arch.num_kv_heads,
            "intermediate_size":    arch.intermediate_size,
            "mlp_style":            arch.mlp_style,
        },
        "embedding_slot": {
            "chunk":        "embedding",
            "mb":           round(emb_mb, 1),
            "precision":    "F16",
            "always_pinned": True,
            "note": (
                f"{arch.vocab_size:,}-token vocabulary — "
                f"{round(emb_mb, 0):.0f} MB F16"
            ),
        },
        "transformer_chunks": tx_chunks,
        "chunk_map":           chunk_map,
        "hydration_policy":    HYDRATION_POLICY,
        "intent_ram_budget":   hyd,
        "storage_speed_mbs":   storage_mbs,
        "between_met_floor_mb": round(emb_mb, 1),
        "peak_harm_mb":         peak_mb,
    }


# ── Pretty printer ────────────────────────────────────────────────────────────

_W = 70

def _section(title: str) -> None:
    print(f"\n{'─' * _W}")
    print(f"  {title}")
    print("─" * _W)


def _bar(mb: float, peak: float, width: int = 28) -> str:
    filled = int(mb / peak * width) if peak > 0 else 0
    return f"[{'█' * filled}{'░' * (width - filled)}] {mb:7.1f} MB"


def print_estimate(sidecar: dict, arch: ModelArch) -> None:
    emb  = sidecar["embedding_slot"]["mb"]
    tx   = sum(c["mb"] for c in sidecar["transformer_chunks"].values())
    peak = sidecar["peak_harm_mb"]

    print(f"\n{'═' * _W}")
    print(f"  MET RAM Estimate — {arch.model_id or 'model'}")
    print(f"{'═' * _W}")
    print(f"  BPW           {arch.bpw}  ({arch.mlp_style} MLP)")
    print(f"  GGUF est.     ~{sidecar['gguf_mb_est']} MB")
    print(f"  Vocab         {arch.vocab_size:,}")

    _section("MEMORY LAYOUT")
    print(f"  Embedding (F16, pinned)  {_bar(emb, peak)}")
    for name, chunk in sidecar["transformer_chunks"].items():
        print(f"  {name:<10} L{chunk['first_layer']:02d}–{chunk['last_layer']:02d} Q4_K_M  {_bar(chunk['mb'], peak)}")

    _section("HYDRATION BUDGETS (embed always + loaded chunks)")
    print(f"  {'Intent':<10}  {'Loaded':<30}  {'RAM':>8}  {'UFS':>8}")
    for intent, entry in sidecar["intent_ram_budget"].items():
        chunks_str = "+".join(entry["chunks"])
        print(f"  {intent:<10}  {chunks_str:<30}  {entry['total_mb']:>6.1f} MB  {entry['ufs_load_ms']:>5.1f} ms")

    savings_mb = peak - sidecar["intent_ram_budget"]["INFORM"]["total_mb"]
    savings_pct = savings_mb / peak * 100

    _section("KEY NUMBERS")
    print(f"  Between-MET floor:   {emb:.1f} MB  (embedding stays hot)")
    print(f"  INFORM only:         {sidecar['intent_ram_budget']['INFORM']['total_mb']:.1f} MB")
    print(f"  Peak (HARM/DECEIVE): {peak:.1f} MB")
    print(f"  Savings (INFORM vs peak): {savings_mb:.0f} MB  ({savings_pct:.1f}% less RAM for benign queries)")
    print(f"{'═' * _W}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Estimate MET RAM hydration budget for any decoder-only transformer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Validation")[0].strip(),
    )
    ap.add_argument("--config", type=Path,
                    help="Path to HuggingFace config.json (auto-fills arch args)")
    ap.add_argument("--model-id",    default="", help="Model identifier (display only)")
    ap.add_argument("--vocab-size",   type=int)
    ap.add_argument("--hidden-size",  type=int)
    ap.add_argument("--num-layers",   type=int)
    ap.add_argument("--num-heads",    type=int)
    ap.add_argument("--num-kv-heads", type=int)
    ap.add_argument("--intermediate-size", type=int)
    ap.add_argument("--bpw",         type=float, default=4.85,
                    help="Target bits-per-weight (default 4.85 ≈ Q4_K_M)")
    ap.add_argument("--mlp-style",   choices=["swiglu", "gated", "mlp"],
                    default="swiglu",
                    help="MLP variant: swiglu=gate+up+down (Qwen/LLaMA), mlp=up+down (GPT-2)")
    ap.add_argument("--storage-speed-mbs", type=float, default=1500,
                    help="Storage read speed in MB/s for UFS latency (default 1500 = UFS 3.1)")
    ap.add_argument("--output", type=Path,
                    help="Write sidecar JSON to this path (same format as .axiom_meta.json)")
    ap.add_argument("--quiet", action="store_true", help="Suppress table output")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    if args.config:
        arch = arch_from_config(args.config, bpw=args.bpw, mlp_style=args.mlp_style)
        if args.model_id:
            arch.model_id = args.model_id
    else:
        required = ["vocab_size", "hidden_size", "num_layers", "num_heads", "intermediate_size"]
        missing  = [r for r in required if getattr(args, r.replace("-","_")) is None]
        if missing:
            print(f"error: provide --config or these flags: {missing}", file=sys.stderr)
            return 1
        arch = ModelArch(
            vocab_size        = args.vocab_size,
            hidden_size       = args.hidden_size,
            num_layers        = args.num_layers,
            num_heads         = args.num_heads,
            num_kv_heads      = args.num_kv_heads or args.num_heads,
            intermediate_size = args.intermediate_size,
            mlp_style         = args.mlp_style,
            model_id          = args.model_id,
            bpw               = args.bpw,
        )

    sidecar = build_sidecar(arch, storage_mbs=args.storage_speed_mbs)

    if not args.quiet:
        print_estimate(sidecar, arch)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(sidecar, indent=2))
        print(f"\n  Saved → {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
