"""A/B compare FP16 vs SRD on one model: pack both, load both, diff output.

Runs the full pipeline twice — once as an FP16 baseline, once SRD-quantized
at a chosen top_k_pct — then loads each signed .axm back and generates from
the same prompt. Prints a side-by-side table (size / bpw / TTFT / tok/s) and
both generations so you can eyeball whether quantization changed the answer.

The first generation run per model is discarded as cold-start warmup, so the
reported TTFT is the warm number (run 1 alone is dominated by CUDA/kernel
init and is not representative).

CLI:
    python -m research.quant.ab_compare \\
        --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \\
        --srd-top-k-pct 0.25 \\
        --prompt "Write a Python function to reverse a linked list." \\
        --tokens 120 \\
        --workdir /content

Outputs <workdir>/ab_<modelname>_fp16.axm, <...>_srd.axm and, if
--stats-json is given, a combined results JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.quant.pack_to_axm import pack_model          # noqa: E402
from research.quant.load_from_axm import load_and_measure   # noqa: E402

# Number of generation runs; run 1 is warmup and dropped from the average.
_N_RUNS = 3


def _warm_latency(stats: dict) -> dict:
    """Recompute TTFT/tok-s from runs 2..N, discarding the cold first run."""
    runs = stats.get("runs", [])
    warm = runs[1:] if len(runs) > 1 else runs
    if not warm:
        return {"warm_ttft_ms": None, "warm_tok_per_s": None}
    return {
        "warm_ttft_ms":   round(sum(r["ttft_ms"]   for r in warm) / len(warm), 1),
        "warm_tok_per_s": round(sum(r["tok_per_s"] for r in warm) / len(warm), 1),
    }


def ab_compare(
    model_name: str,
    *,
    srd_top_k_pct: float,
    prompt: str,
    n_tokens: int,
    group_size: int,
    workdir: Path,
    compresslevel: int,
    model_revision: Optional[str] = None,
) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    short = Path(model_name).name.replace("/", "_")
    fp16_axm = str(workdir / f"ab_{short}_fp16.axm")
    srd_axm  = str(workdir / f"ab_{short}_srd.axm")

    # ── Pack both ──────────────────────────────────────────────────────
    print(f"\n{'='*64}\n[ab] PACK 1/2 — FP16 baseline\n{'='*64}")
    pack_fp16 = pack_model(
        model_name=model_name, output_path=fp16_axm,
        srd_top_k_pct=None, group_size=group_size,
        model_revision=model_revision, hardware_map="gpu",
        compresslevel=compresslevel,
    )

    print(f"\n{'='*64}\n[ab] PACK 2/2 — SRD top_k_pct={srd_top_k_pct}\n{'='*64}")
    pack_srd = pack_model(
        model_name=model_name, output_path=srd_axm,
        srd_top_k_pct=srd_top_k_pct, group_size=group_size,
        model_revision=model_revision, hardware_map="gpu",
        compresslevel=compresslevel,
    )

    # ── Load + generate from both ──────────────────────────────────────
    print(f"\n{'='*64}\n[ab] LOAD 1/2 — FP16\n{'='*64}")
    load_fp16 = load_and_measure(
        fp16_axm, prompt=prompt, n_tokens=n_tokens, n_runs=_N_RUNS,
    )
    print(f"\n{'='*64}\n[ab] LOAD 2/2 — SRD\n{'='*64}")
    load_srd = load_and_measure(
        srd_axm, prompt=prompt, n_tokens=n_tokens, n_runs=_N_RUNS,
    )

    warm_fp16 = _warm_latency(load_fp16)
    warm_srd  = _warm_latency(load_srd)

    # ── Comparison table ───────────────────────────────────────────────
    def _row(label: str, pack: dict, warm: dict) -> str:
        return (
            f"{label:<16} "
            f"{pack['bpw_theoretical']:>6.1f} "
            f"{pack['size']['archive_mb']:>9.0f} "
            f"{pack['timing']['axm_pack_s']:>8.1f} "
            f"{(warm['warm_ttft_ms'] or 0):>9.0f} "
            f"{(warm['warm_tok_per_s'] or 0):>8.1f}"
        )

    hdr = (f"{'Variant':<16} {'bpw':>6} {'size_MB':>9} {'pack_s':>8} "
           f"{'TTFT_ms':>9} {'tok/s':>8}")
    print(f"\n{'='*64}\n[ab] ── SUMMARY ──\n{'='*64}")
    print(hdr)
    print("─" * len(hdr))
    print(_row("FP16 baseline", pack_fp16, warm_fp16))
    print(_row(f"SRD {pack_srd['bpw_theoretical']:.0f}bpw", pack_srd, warm_srd))
    print("─" * len(hdr))
    if pack_fp16['size']['archive_mb'] and pack_srd['size']['theoretical_mb']:
        real_ratio = (pack_srd['size']['theoretical_mb']
                      / pack_fp16['size']['archive_mb'])
        print(f"SRD real-packed would be {real_ratio*100:.0f}% of FP16 "
              f"({pack_srd['size']['theoretical_mb']:.0f} MB vs "
              f"{pack_fp16['size']['archive_mb']:.0f} MB)")

    # ── Side-by-side generations ───────────────────────────────────────
    print(f"\n{'='*64}\n[ab] ── GENERATIONS (same prompt) ──\n{'='*64}")
    print(f"\n── FP16 ──────────────────────────────────────────────────")
    print(load_fp16["generated_text"])
    print(f"\n── SRD {pack_srd['bpw_theoretical']:.0f}bpw ──────────────"
          f"────────────────────────────────")
    print(load_srd["generated_text"])
    print("─" * 64)
    match = load_fp16["generated_text"] == load_srd["generated_text"]
    print(f"\n[ab] exact-match output: {match}")
    if not match:
        print("[ab] outputs differ — read both above to judge if SRD "
              "degraded quality or just diverged stylistically.")

    return {
        "model":          model_name,
        "srd_top_k_pct":  srd_top_k_pct,
        "prompt":         prompt,
        "exact_match":    match,
        "fp16": {"pack": pack_fp16, "warm": warm_fp16,
                 "text": load_fp16["generated_text"]},
        "srd":  {"pack": pack_srd,  "warm": warm_srd,
                 "text": load_srd["generated_text"]},
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A/B compare FP16 vs SRD on one model")
    p.add_argument("--model", default="TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    p.add_argument("--revision", default=None)
    p.add_argument("--srd-top-k-pct", type=float, default=0.25,
                   help="SRD sparsity (0.25 = ~7 bpw)")
    p.add_argument("--group-size", type=int, default=64)
    p.add_argument("--prompt",
                   default="Write a Python function to reverse a linked list.")
    p.add_argument("--tokens", type=int, default=120)
    p.add_argument("--workdir", type=Path, default=Path("/content"))
    p.add_argument("--compresslevel", type=int, default=1,
                   choices=range(0, 10), metavar="[0-9]")
    p.add_argument("--stats-json", type=Path, default=None)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    result = ab_compare(
        args.model,
        srd_top_k_pct=args.srd_top_k_pct,
        prompt=args.prompt,
        n_tokens=args.tokens,
        group_size=args.group_size,
        workdir=args.workdir,
        compresslevel=args.compresslevel,
        model_revision=args.revision,
    )
    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\n[ab] stats written to {args.stats_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
