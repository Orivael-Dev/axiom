"""Perplexity-vs-bits-per-weight scatter for SRD + K-quant rows.

Reads the JSON files produced by bench_perplexity.py and
bench_llamacpp.py, draws a single matplotlib scatter, writes a PNG.

The K-quant family (Q4_K_M → Q5_K_M → Q6_K → Q8_0) is connected with
a line — that's the "Pareto frontier" the SRD points need to land at
or below to be worth pursuing per the plan's pre-committed decision
rule (SRD PPL at ~12.6 bpw < Q6_K PPL at ~6.56 bpw by ≥0.05).

CLI:
    python -m research.quant.plot_results \\
        --inputs research/quant/results/srd_sweep.json,research/quant/results/kquant_sweep.json \\
        --output docs/srd_perplexity_vs_bpw.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _classify(row: dict) -> str:
    """Bucket each row for plot coloring."""
    name = row["name"].lower()
    if "fp16" in name:
        return "fp16"
    if name.startswith("srd_sparse"):
        return "srd_sparse"
    if name.startswith("srd_mlp_only") or name.startswith("srd_attn_only"):
        return "srd_selective"
    if name.startswith("srd"):
        return "srd"
    if "llama_cpp" in name or "k_m" in name or "_k" in name or "q8_0" in name:
        return "kquant"
    return "other"


def make_plot(rows: List[dict], output: Path) -> None:
    import matplotlib.pyplot as plt

    fp16         = [r for r in rows if _classify(r) == "fp16"]
    srd          = [r for r in rows if _classify(r) == "srd"]
    srd_sparse   = sorted([r for r in rows if _classify(r) == "srd_sparse"],
                          key=lambda r: r["bpw_reported"])
    srd_selective = [r for r in rows if _classify(r) == "srd_selective"]
    kq = sorted([r for r in rows if _classify(r) == "kquant"],
                key=lambda r: r["bpw_reported"])

    fig, ax = plt.subplots(figsize=(8, 5))

    # K-quant Pareto line
    if kq:
        ax.plot([r["bpw_reported"] for r in kq],
                [r["perplexity"] for r in kq],
                color="#2c8a90", linewidth=1.2, marker="o", markersize=7,
                label="llama.cpp K-quants", zorder=2)
        for r in kq:
            ax.annotate(r["name"].replace("llama_cpp_", ""),
                        (r["bpw_reported"], r["perplexity"]),
                        textcoords="offset points", xytext=(7, -3),
                        fontsize=8, color="#0d4f54")

    # Sparse SRD — connected Pareto line to show continuous bpw coverage
    if srd_sparse:
        ax.plot([r["bpw_reported"] for r in srd_sparse],
                [r["perplexity"] for r in srd_sparse],
                color="#8b35c7", linewidth=1.2, marker="^", markersize=7,
                label="SRD sparse residual (E2)", zorder=3)
        for r in srd_sparse:
            pct = r["name"].split("_")[2].replace("pct", "%")
            ax.annotate(pct,
                        (r["bpw_reported"], r["perplexity"]),
                        textcoords="offset points", xytext=(5, 5),
                        fontsize=7, color="#8b35c7")

    # Layer-selective SRD points
    if srd_selective:
        ax.scatter([r["bpw_reported"] for r in srd_selective],
                   [r["perplexity"] for r in srd_selective],
                   color="#2a9d2a", s=70, marker="D",
                   label="SRD layer-selective (E2)", zorder=3,
                   edgecolors="white", linewidths=1.0)
        for r in srd_selective:
            short = "MLP" if "mlp" in r["name"] else "Attn"
            ax.annotate(short,
                        (r["bpw_reported"], r["perplexity"]),
                        textcoords="offset points", xytext=(6, 4),
                        fontsize=7, color="#2a9d2a")

    # Original SRD alpha-sweep points
    if srd:
        ax.scatter([r["bpw_reported"] for r in srd],
                   [r["perplexity"] for r in srd],
                   color="#c87029", s=70, marker="s",
                   label="SRD alpha sweep (E1)", zorder=3, edgecolors="white",
                   linewidths=1.0)
        for r in srd:
            short = r["name"].replace("srd_alpha_", "α=").replace("_", " ")
            ax.annotate(short,
                        (r["bpw_reported"], r["perplexity"]),
                        textcoords="offset points", xytext=(7, 4),
                        fontsize=7, color="#c87029")

    # FP16 baseline as a horizontal reference
    if fp16:
        baseline_ppl = fp16[0]["perplexity"]
        ax.axhline(baseline_ppl, color="#1d3557", linestyle="--",
                   linewidth=0.8, alpha=0.6,
                   label=f"FP16 baseline (PPL={baseline_ppl:.2f})")

    models = {r.get("model", "") for r in rows if r.get("model")}
    model_label = next(iter(models), "").split("/")[-1] if models else "model"

    n_sparse = len(srd_sparse)
    n_select = len(srd_selective)
    ax.set_xlabel("Bits per weight (honest, incl. scale storage)")
    ax.set_ylabel("WikiText-2 perplexity (lower = better)")
    ax.set_title(
        f"SRD vs llama.cpp K-quants at matched bpw\n"
        f"{model_label}, sliding-window PPL"
    )
    ax.grid(True, alpha=0.25, linestyle=":")
    ax.legend(loc="upper right", framealpha=0.9)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=140)
    print(f"wrote {output} ({len(rows)} rows: "
          f"{len(srd)} SRD + {n_sparse} sparse + {n_select} selective + "
          f"{len(kq)} K-quant + {len(fp16)} FP16)")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SRD vs K-quant scatter plot")
    p.add_argument("--inputs", required=True,
                   help="Comma-separated JSON files from bench_perplexity / bench_llamacpp")
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    rows: List[dict] = []
    for f in args.inputs.split(","):
        path = Path(f.strip())
        if not path.exists():
            print(f"WARN: skipping missing input {path}", file=sys.stderr)
            continue
        data = json.loads(path.read_text())
        if isinstance(data, list):
            rows.extend(data)
        else:
            rows.append(data)
    if not rows:
        print("ERROR: no input rows", file=sys.stderr)
        return 2
    make_plot(rows, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
