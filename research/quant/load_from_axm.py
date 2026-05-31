"""Load a model from a signed .axm archive and measure inference latency.

Verifies the proof ledger (including weights/manifest.json integrity),
extracts the weights, loads via HuggingFace, and runs a timed generation.

CLI:
    python -m research.quant.load_from_axm \\
        --container artifacts/qwen7b_srd_7bpw.axm \\
        --prompt "Write a Python function to reverse a linked list." \\
        --tokens 120

    # Latency-focused (measure TTFT + throughput)
    python -m research.quant.load_from_axm \\
        --container artifacts/qwen7b_fp16.axm \\
        --bench-latency --n-runs 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch                                               # noqa: E402

from axiom_axm import AXMContainer, AXMError               # noqa: E402


def load_and_measure(
    container_path: str,
    *,
    prompt: str = "Once upon a time,",
    n_tokens: int = 80,
    n_runs: int = 1,
    device: Optional[str] = None,
) -> dict:
    """Load model from .axm, verify, generate, return latency stats."""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype  = torch.float16 if device == "cuda" else torch.float32

    # ── Load and verify container ──────────────────────────────────────
    print(f"[load] opening {container_path}...")
    t0 = time.monotonic()
    container = AXMContainer.from_path(container_path)
    open_s = time.monotonic() - t0

    print(f"[load] verifying proofs ({len(container.proofs)} entries)...")
    t1 = time.monotonic()
    ok = container.verify_proofs()
    verify_s = time.monotonic() - t1
    if not ok:
        raise AXMError("proof verification failed — container may be tampered")
    print(f"[load] verified ✓  fingerprint={container.fingerprint()}  ({verify_s:.2f}s)")

    # Read quant_map from header
    qmap = container.header.quant_map
    bpw_theoretical = (
        qmap.get("bpw", 16.0) if isinstance(qmap, dict) else 16.0
    )
    scheme = qmap.get("scheme", "unknown") if isinstance(qmap, dict) else str(qmap)

    # ── Locate weights ─────────────────────────────────────────────────
    weights_path = container.weights_path
    if weights_path is None:
        raise AXMError(
            "No weights/ directory in this container. "
            "Re-pack with pack_to_axm.py to include model weights."
        )
    print(f"[load] weights at {weights_path}")

    # ── Load model ─────────────────────────────────────────────────────
    is_packed = bool(qmap.get("packed", False)) if isinstance(qmap, dict) else False
    from research.quant.srd_realpack import is_real_packed, load_real_packed
    real = is_packed or is_real_packed(weights_path)
    print(f"[load] loading model from weights/ ({scheme}, ~{bpw_theoretical:.1f} bpw"
          f"{', E3 real-packed' if real else ''})...")
    t2 = time.monotonic()
    if real:
        # E3 real-packed: unpack W4 + sparse-D8 → FP16, reconstruct from config
        model, tokenizer = load_real_packed(weights_path, device=device, dtype=dtype)
        if tokenizer is None:
            tokenizer = AutoTokenizer.from_pretrained(str(weights_path))
    else:
        tokenizer = AutoTokenizer.from_pretrained(str(weights_path))
        model = AutoModelForCausalLM.from_pretrained(
            str(weights_path), torch_dtype=dtype,
        ).to(device)
    model.eval()
    model_load_s = time.monotonic() - t2
    print(f"[load] model loaded in {model_load_s:.1f}s")

    # ── Inference latency measurement ──────────────────────────────────
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    input_len = inputs.input_ids.shape[1]

    run_results = []
    for i in range(n_runs):
        if torch.cuda.is_available():
            torch.cuda.synchronize()

        # Time to first token (TTFT)
        t_ttft = time.monotonic()
        with torch.no_grad():
            # Generate just 1 token for TTFT
            first = model.generate(
                **inputs, max_new_tokens=1, do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        ttft_s = time.monotonic() - t_ttft

        # Full generation for throughput
        t_gen = time.monotonic()
        with torch.no_grad():
            output = model.generate(
                **inputs, max_new_tokens=n_tokens, do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        gen_s = time.monotonic() - t_gen

        n_new = output.shape[1] - input_len
        tok_per_s = n_new / gen_s if gen_s > 0 else 0.0

        run_results.append({
            "ttft_ms":    round(ttft_s * 1000, 1),
            "gen_s":      round(gen_s, 3),
            "tokens_out": n_new,
            "tok_per_s":  round(tok_per_s, 1),
        })
        print(f"[load] run {i+1}/{n_runs}: "
              f"TTFT={ttft_s*1000:.0f}ms  "
              f"{n_new} tokens in {gen_s:.2f}s  "
              f"({tok_per_s:.1f} tok/s)")

    text = tokenizer.decode(output[0], skip_special_tokens=True)

    avg_ttft   = sum(r["ttft_ms"]  for r in run_results) / len(run_results)
    avg_tps    = sum(r["tok_per_s"] for r in run_results) / len(run_results)

    stats = {
        "container":         container_path,
        "fingerprint":       container.fingerprint(),
        "scheme":            scheme,
        "bpw_theoretical":   bpw_theoretical,
        "quant_map":         qmap,
        "timing": {
            "container_open_s":  round(open_s, 3),
            "proof_verify_s":    round(verify_s, 3),
            "model_load_s":      round(model_load_s, 2),
            "avg_ttft_ms":       round(avg_ttft, 1),
            "avg_tok_per_s":     round(avg_tps, 1),
        },
        "runs":              run_results,
        "prompt":            prompt,
        "generated_text":    text,
    }

    print(f"\n[load] ── summary ─────────────────────────────────────────")
    print(f"  scheme         : {scheme}  ({bpw_theoretical:.1f} bpw theoretical)")
    print(f"  open+verify    : {open_s + verify_s:.2f}s")
    print(f"  model load     : {model_load_s:.1f}s")
    print(f"  avg TTFT       : {avg_ttft:.0f} ms")
    print(f"  avg throughput : {avg_tps:.1f} tok/s")
    print(f"\n── generated ──────────────────────────────────────────────")
    print(text)
    print("────────────────────────────────────────────────────────────")
    return stats


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Load a model from a .axm archive and measure latency")
    p.add_argument("--container", required=True, help=".axm archive path")
    p.add_argument("--prompt", default="Once upon a time, in a small village,")
    p.add_argument("--tokens", type=int, default=80)
    p.add_argument("--n-runs", type=int, default=1,
                   help="Number of generation runs for averaging latency")
    p.add_argument("--device", default=None)
    p.add_argument("--stats-json", type=Path, default=None)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    stats = load_and_measure(
        args.container,
        prompt=args.prompt,
        n_tokens=args.tokens,
        n_runs=args.n_runs,
        device=args.device,
    )
    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(stats, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
