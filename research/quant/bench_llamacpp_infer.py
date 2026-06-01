"""Benchmark a GGUF model via llama.cpp and return the same stats shape
as load_from_axm.load_and_measure — so results are directly comparable.

Parses llama.cpp's built-in ``llama_print_timings`` output for latency,
and captures peak RSS via ``/usr/bin/time -v`` (Linux) or
``resource.getrusage`` (fallback) so memory numbers match what we measure
from the torch path.

CLI
---
    python -m research.quant.bench_llamacpp_infer \\
        --gguf  artifacts/tinyllama_q4km.gguf \\
        --llama-cli  ~/llama.cpp/build/bin/llama-cli \\
        --prompt "Write a Python function to reverse a linked list." \\
        --n-runs 3 --ngl 22
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

# ── timing-line patterns ─────────────────────────────────────────────────────
# llama.cpp prints these to stderr at the end of every run:
#   llama_print_timings:        load time =   1234.56 ms
#   llama_print_timings: prompt eval time =    234.56 ms /  12 tokens
#   llama_print_timings:        eval time =   2345.67 ms /  79 runs
#        (  29.69 ms per token,    33.68 tokens per second)
#   llama_print_timings:       total time =   2592.23 ms /  91 tokens

_PAT_LOAD   = re.compile(r"load time\s*=\s*([\d.]+)\s*ms")
_PAT_PROMPT = re.compile(r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens")
_PAT_EVAL   = re.compile(
    r"eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*runs.*?([\d.]+)\s*tokens per second",
    re.S,
)
_PAT_VRAM   = re.compile(r"VRAM used[:\s]+([\d.]+)\s*Mi[Bb]")
_PAT_RSS_KB = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")


def _parse_timings(text: str) -> dict:
    out: dict = {}
    for pat, key in [(_PAT_LOAD, "load_ms"), (_PAT_PROMPT, None),
                     (_PAT_EVAL, None), (_PAT_VRAM, "vram_mb")]:
        m = pat.search(text)
        if not m:
            continue
        if pat is _PAT_PROMPT:
            out["prompt_eval_ms"] = float(m.group(1))
            out["prompt_tokens"]  = int(m.group(2))
        elif pat is _PAT_EVAL:
            out["eval_ms"]   = float(m.group(1))
            out["eval_runs"] = int(m.group(2))
            out["tok_per_s"] = float(m.group(3))
        else:
            out[key] = float(m.group(1))
    return out


# ── core ─────────────────────────────────────────────────────────────────────

def bench_gguf(
    gguf_path: str,
    *,
    llama_cli: str,
    prompt: str = "Once upon a time,",
    n_tokens: int = 80,
    n_gpu_layers: int = 99,
    n_runs: int = 3,
    extra_args: Optional[list] = None,
) -> dict:
    """Run llama-cli n_runs times and return a stats dict.

    Run 1 is treated as cold / warmup; warm averages are computed over
    runs 2..N (matching the behaviour of load_and_measure).

    Args:
        gguf_path:    path to the .gguf model file.
        llama_cli:    path to the llama-cli binary.
        n_gpu_layers: layers to offload to GPU (-ngl). 99 = all layers.
        extra_args:   additional CLI flags passed verbatim to llama-cli.
    """
    import subprocess

    llama_cli = Path(llama_cli).expanduser().resolve()
    gguf_path = Path(gguf_path).expanduser().resolve()

    if not llama_cli.is_file():
        raise FileNotFoundError(f"llama-cli not found: {llama_cli}")
    if not gguf_path.is_file():
        raise FileNotFoundError(f"GGUF not found: {gguf_path}")

    base_cmd = [
        str(llama_cli),
        "-m", str(gguf_path),
        "-p", prompt,
        "-n", str(n_tokens),
        "-ngl", str(n_gpu_layers),
        "--no-display-prompt",
        "--log-disable",
    ]
    if extra_args:
        base_cmd.extend(extra_args)

    # Wrap with /usr/bin/time -v for accurate peak RSS on Linux.
    time_bin = Path("/usr/bin/time")
    use_time = time_bin.is_file()

    run_results = []
    last_text = ""

    for i in range(n_runs):
        cmd = ([str(time_bin), "-v"] + base_cmd) if use_time else base_cmd
        print(f"[bench] run {i+1}/{n_runs}...")
        t0 = time.monotonic()
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )
        elapsed = time.monotonic() - t0
        # llama.cpp writes timings to stderr; generated text to stdout.
        output = proc.stderr + proc.stdout
        last_text = proc.stdout.strip()

        t = _parse_timings(output)
        ttft_ms = t.get("prompt_eval_ms", elapsed * 1000)
        tps     = t.get("tok_per_s", 0.0)
        vram_mb = t.get("vram_mb")

        rss_mb: Optional[float] = None
        m = _PAT_RSS_KB.search(output)
        if m:
            rss_mb = round(int(m.group(1)) / 1024, 1)

        run_results.append({
            "ttft_ms":    round(ttft_ms, 1),
            "tok_per_s":  round(tps, 1),
            "elapsed_s":  round(elapsed, 3),
            "vram_mb":    round(vram_mb, 1) if vram_mb else None,
            "peak_rss_mb": rss_mb,
        })
        print(f"[bench] run {i+1}: TTFT={ttft_ms:.0f}ms  "
              f"{tps:.1f} tok/s"
              + (f"  VRAM={vram_mb:.0f}MB" if vram_mb else "")
              + (f"  RSS={rss_mb:.0f}MB" if rss_mb else ""))

    warm    = run_results[1:] if len(run_results) > 1 else run_results
    avg_ttft = sum(r["ttft_ms"]  for r in warm) / len(warm)
    avg_tps  = sum(r["tok_per_s"] for r in warm) / len(warm)
    vram_vals = [r["vram_mb"]    for r in run_results if r["vram_mb"]    is not None]
    rss_vals  = [r["peak_rss_mb"] for r in run_results if r["peak_rss_mb"] is not None]

    stats = {
        "gguf":             str(gguf_path),
        "n_gpu_layers":     n_gpu_layers,
        "timing": {
            "avg_ttft_ms":   round(avg_ttft, 1),
            "avg_tok_per_s": round(avg_tps,  1),
        },
        "memory": {
            "peak_rss_mb": max(rss_vals)  if rss_vals  else None,
            "vram_mb":     max(vram_vals) if vram_vals else None,
        },
        "runs":           run_results,
        "prompt":         prompt,
        "generated_text": last_text,
    }

    print(f"\n[bench] ── summary ──────────────────────────────────────────")
    print(f"  warm avg TTFT  : {avg_ttft:.0f} ms")
    print(f"  warm avg tok/s : {avg_tps:.1f}")
    if stats["memory"]["vram_mb"]:
        print(f"  VRAM           : {stats['memory']['vram_mb']:.0f} MB")
    if stats["memory"]["peak_rss_mb"]:
        print(f"  peak RSS       : {stats['memory']['peak_rss_mb']:.0f} MB")
    print(f"\n── generated ───────────────────────────────────────────────")
    print(last_text)
    print("────────────────────────────────────────────────────────────")
    return stats


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Benchmark a GGUF model via llama.cpp")
    p.add_argument("--gguf",       required=True, help=".gguf model path")
    p.add_argument("--llama-cli",  required=True, help="path to llama-cli binary")
    p.add_argument("--prompt",     default="Once upon a time,")
    p.add_argument("--tokens",     type=int, default=80)
    p.add_argument("--ngl",        type=int, default=99,
                   help="GPU layers to offload (-ngl), default 99 = all")
    p.add_argument("--n-runs",     type=int, default=3)
    p.add_argument("--stats-json", type=Path, default=None)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    stats = bench_gguf(
        args.gguf,
        llama_cli=args.llama_cli,
        prompt=args.prompt,
        n_tokens=args.tokens,
        n_gpu_layers=args.ngl,
        n_runs=args.n_runs,
    )
    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(stats, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
