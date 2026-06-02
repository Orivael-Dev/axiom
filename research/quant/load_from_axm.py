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
    clean: bool = False,
    drop_uncertain: bool = False,
    kv_cache_path: Optional[str] = None,
    save_kv_cache: Optional[str] = None,
    kv_token_id: Optional[str] = None,
) -> dict:
    """Load model from .axm, verify, generate, return latency stats.

    KV cache params
    ---------------
    kv_cache_path   Path to a signed .kvcache.pt file produced by a prior
                    run with save_kv_cache.  If provided, the prompt prefix
                    KV state is loaded (and verified) before generation,
                    skipping the prefill compute for the cached tokens.
    save_kv_cache   Path to write a signed KVCacheEntry after the first
                    forward pass.  Does not affect generation timing.
    kv_token_id     EventToken.id to embed in the KVCacheEntry signature.
                    Defaults to the container fingerprint.
    """
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

    # ── KV cache: save (build signed prefix cache for this prompt) ─────
    kv_saved_path: Optional[str] = None
    if save_kv_cache:
        from axiom_event_token.kv_cache import KVCacheEntry
        _tid = kv_token_id or container.fingerprint()
        # Pre-fill covers prompt tokens [0..N-2] so generate() can process
        # the last token as the continuation seed with full context.
        _prefix_ids = inputs.input_ids[:, :-1]
        if _prefix_ids.shape[1] > 0:
            with torch.no_grad():
                _kv_out = model(_prefix_ids, use_cache=True)
            _entry = KVCacheEntry.from_past_key_values(
                _kv_out.past_key_values, token_id=_tid, layer_slot="text",
            )
            _entry.save(Path(save_kv_cache), _kv_out.past_key_values)
            kv_saved_path = save_kv_cache
            print(f"[kv] signed KV cache saved → {save_kv_cache}  "
                  f"({input_len - 1} prefix tokens, {_entry.n_layers} layers)")
        else:
            print("[kv] prompt too short to build a meaningful prefix cache (< 2 tokens) — skipped")

    # ── KV cache: load (verify + restore prefix for fast prefill) ──────
    _pkv: Optional[object] = None
    kv_hit = False
    kv_prefill_skipped_tokens = 0
    if kv_cache_path:
        from axiom_event_token.kv_cache import KVCacheEntry
        _entry, _raw = KVCacheEntry.load(Path(kv_cache_path), verify=True)
        _pkv = KVCacheEntry.to_past_key_values(_raw, device=device, dtype=dtype)
        kv_hit = True
        kv_prefill_skipped_tokens = _entry.seq_len
        print(f"[kv] ✓ verified KV cache from {kv_cache_path}  "
              f"({_entry.seq_len} cached tokens, skipping prefill)")

    run_results = []
    for i in range(n_runs):
        if torch.cuda.is_available():
            torch.cuda.synchronize()

        if _pkv is not None:
            # Fast-prefill path: past_key_values covers the prompt prefix
            # [0..N-2]; generate() processes only the last prompt token
            # (position N-1) as continuation seed, then generates new tokens.
            _seed_ids = inputs.input_ids[:, -1:]
            _attn_mask = torch.ones(
                1, input_len, device=device, dtype=torch.long,
            )

            t_ttft = time.monotonic()
            with torch.no_grad():
                first = model.generate(
                    input_ids=_seed_ids, past_key_values=_pkv,
                    attention_mask=_attn_mask,
                    max_new_tokens=1, do_sample=False,
                    pad_token_id=tokenizer.eos_token_id,
                )
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            ttft_s = time.monotonic() - t_ttft

            t_gen = time.monotonic()
            with torch.no_grad():
                output = model.generate(
                    input_ids=_seed_ids, past_key_values=_pkv,
                    attention_mask=_attn_mask,
                    max_new_tokens=n_tokens, do_sample=False,
                    pad_token_id=tokenizer.eos_token_id,
                )
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            gen_s = time.monotonic() - t_gen
            # output = [seed_token, gen_token_1, ..., gen_token_N]
            n_new = output.shape[1] - 1

        else:
            # Standard path: full prefill on every run.

            # Time to first token (TTFT)
            t_ttft = time.monotonic()
            with torch.no_grad():
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
              f"({tok_per_s:.1f} tok/s)"
              + (" [kv cache hit]" if kv_hit else ""))

    text = tokenizer.decode(output[0], skip_special_tokens=True)

    # ── Optional: run the generation through the constitutional trajectory ──
    # filter (ORVL-016 intent gate) to drop noise steps — looping repeats,
    # blocked (HARM/DECEIVE), and UNCERTAIN filler.
    clean_report = None
    cleaned_text = None
    if clean:
        from research.quant.trajectory_filter import clean_generation
        cr = clean_generation(text, drop_uncertain=drop_uncertain)
        cleaned_text = cr.cleaned_text
        clean_report = cr.to_dict()
        print(f"[clean] trajectory filter: {cr.n_steps} steps → "
              f"kept {cr.n_kept}, dropped {cr.n_dropped} "
              f"{cr.dropped_reasons or ''}"
              + ("  ⚠️ BLOCKED step present" if cr.blocked else ""))

    avg_ttft   = sum(r["ttft_ms"]  for r in run_results) / len(run_results)
    avg_tps    = sum(r["tok_per_s"] for r in run_results) / len(run_results)

    # ── Peak memory (high-water mark over the whole process: load + gen) ──
    import resource
    # ru_maxrss is KiB on Linux, bytes on macOS — normalize to MB.
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_mb = round(ru / 1024 / (1024 if sys.platform == "darwin" else 1), 1)
    cuda_peak_mb = None
    if torch.cuda.is_available():
        cuda_peak_mb = round(torch.cuda.max_memory_allocated() / (1024 ** 2), 1)

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
        "memory": {
            "peak_rss_mb":   peak_rss_mb,
            "cuda_peak_mb":  cuda_peak_mb,
        },
        "runs":              run_results,
        "prompt":            prompt,
        "generated_text":    text,
        "cleaned_text":      cleaned_text,
        "trajectory_filter": clean_report,
        "kv_cache": {
            "hit":                   kv_hit,
            "prefill_skipped_tokens": kv_prefill_skipped_tokens,
            "saved_path":            kv_saved_path,
        },
    }

    print(f"\n[load] ── summary ─────────────────────────────────────────")
    print(f"  scheme         : {scheme}  ({bpw_theoretical:.1f} bpw theoretical)")
    print(f"  open+verify    : {open_s + verify_s:.2f}s")
    print(f"  model load     : {model_load_s:.1f}s")
    print(f"  avg TTFT       : {avg_ttft:.0f} ms")
    print(f"  avg throughput : {avg_tps:.1f} tok/s")
    print(f"  peak RSS       : {peak_rss_mb:.0f} MB"
          + (f"  | CUDA peak: {cuda_peak_mb:.0f} MB" if cuda_peak_mb else ""))
    print(f"\n── generated ──────────────────────────────────────────────")
    print(text)
    if cleaned_text is not None:
        print(f"\n── cleaned (trajectory filter) ────────────────────────────")
        print(cleaned_text)
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
    p.add_argument("--clean", action="store_true",
                   help="filter generation through the ORVL-016 intent gate")
    p.add_argument("--drop-uncertain", action="store_true",
                   help="with --clean, also drop UNCERTAIN filler steps")
    p.add_argument("--kv-cache", default=None,
                   help="load a signed KV cache (.kvcache.pt) to skip prompt prefill")
    p.add_argument("--save-kv-cache", default=None,
                   help="sign and save the prompt KV cache to this path after loading")
    p.add_argument("--kv-token-id", default=None,
                   help="EventToken.id to bind into the KV cache signature "
                        "(default: container fingerprint)")
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
        clean=args.clean,
        drop_uncertain=args.drop_uncertain,
        kv_cache_path=args.kv_cache,
        save_kv_cache=args.save_kv_cache,
        kv_token_id=args.kv_token_id,
    )
    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(stats, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
