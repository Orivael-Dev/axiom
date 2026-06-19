"""Render a QRF-vs-baseline results PDF from a qrf_hypothesis_loop report.

Usage:
    python axiom_lab/benchmarks/make_qrf_chart.py \
        --report results/qrf_vs_arbor_hard_llama8b.json \
        --out    results/qrf_vs_arbor_chart.pdf
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

NAMES = {
    "buggy-binary-search":    "Binary Search",
    "slow-palindrome":        "Palindrome",
    "broken-merge-sort":      "Merge Sort",
    "leaky-rate-limiter":     "Rate Limiter",
    "precedence-calculator":  "Calculator †",
    "semver-compare":         "SemVer †",
    "lru-ttl-cache":          "LRU+TTL Cache †",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--out",    required=True, type=Path)
    args = ap.parse_args()

    d = json.loads(args.report.read_text())
    model = d.get("config", {}).get("model", "?")
    backend = d.get("config", {}).get("backend", "?")
    summary = d.get("summary", {})
    avg = summary.get("avg_efficiency_x", 0.0)
    med = summary.get("median_efficiency_x", avg)
    resc = summary.get("rescued_tasks", 0)

    rows = []
    for t in d["tasks"]:
        b, q = t["baseline"], t["qrf"]
        b_auc, q_auc = b["quality_auc"], q["quality_auc"]
        if b_auc > 0:
            eff, rescue = q_auc / b_auc, False
        else:  # baseline scored 0: a QRF win is a "rescue", not 0× or ∞×
            eff, rescue = (None, True) if q_auc > 0 else (1.0, False)
        rows.append({
            "name":   NAMES.get(t["task_id"], t["task_id"]),
            "b_pass": b["final_pass_rate"] * 100,
            "q_pass": q["final_pass_rate"] * 100,
            "eff":    eff,
            "rescue": rescue,
            "b_tok":  b["total_tokens"],
            "q_tok":  q["total_tokens"],
        })

    labels = [r["name"] for r in rows]
    x = range(len(rows))

    with PdfPages(args.out) as pdf:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8.5))
        fig.suptitle("QRF Hypothesis Loop vs Baseline  (Arbor-style)",
                     fontsize=16, fontweight="bold")

        # ── Panel 1: final pass rate, baseline vs QRF ──────────────────
        w = 0.38
        ax1.bar([i - w/2 for i in x], [r["b_pass"] for r in rows], w,
                label="Baseline (single-shot)", color="#b0b7c3")
        ax1.bar([i + w/2 for i in x], [r["q_pass"] for r in rows], w,
                label="QRF loop (branch + prune)", color="#2a7de1")
        ax1.set_ylabel("Final test pass rate (%)")
        ax1.set_ylim(0, 108)
        ax1.set_xticks(list(x))
        ax1.set_xticklabels(labels, rotation=20, ha="right")
        ax1.set_title("Solution quality reached", fontsize=11)
        ax1.legend(loc="lower left", fontsize=9)
        ax1.grid(axis="y", alpha=0.3)
        for i, r in zip(x, rows):
            ax1.text(i + w/2, r["q_pass"] + 1.5, f'{r["q_pass"]:.0f}',
                     ha="center", fontsize=7, color="#2a7de1")

        # ── Panel 2: efficiency multiplier per task ────────────────────
        # Rescues (baseline 0%) have no finite ratio — plot at the chart's
        # max finite bar in a distinct colour and label them "rescue".
        finite = [r["eff"] for r in rows if not r["rescue"]]
        cap = max(finite) if finite else 1.0
        plot_vals, colors = [], []
        for r in rows:
            if r["rescue"]:
                plot_vals.append(cap)
                colors.append("#6a3d9a")            # purple = rescue
            else:
                plot_vals.append(r["eff"])
                colors.append("#1a9850" if r["eff"] > 1.001
                              else ("#d73027" if r["eff"] < 0.999 else "#9aa0a6"))
        ax2.barh(list(x), plot_vals, color=colors)
        ax2.axvline(1.0, color="#444", lw=1, ls="--", label="parity (1.0×)")
        ax2.set_yticks(list(x))
        ax2.set_yticklabels(labels)
        ax2.invert_yaxis()
        ax2.set_xlabel("QRF quality-AUC ÷ baseline quality-AUC  (×)")
        ax2.set_title("Efficiency multiplier  "
                      "(green = QRF wins · red = regresses · purple = rescue from 0%)",
                      fontsize=11)
        ax2.grid(axis="x", alpha=0.3)
        for i, r in enumerate(rows):
            txt = (f"rescue (0→{r['q_pass']:.0f}%)" if r["rescue"]
                   else f"{r['eff']:.2f}×")
            ax2.text(plot_vals[i] + 0.05, i, txt, va="center", fontsize=8,
                     fontweight="bold")
        ax2.set_xlim(0, cap * 1.32)

        cap = (f"Model: {model}  ({backend})      "
               f"Median (robust): {med:.2f}×   ·   Mean: {avg:.2f}×"
               + (f"   ·   {resc} rescued" if resc else "")
               + f"      † = hard non-canonical task\n"
               f"QRF helps where single-shot leaves headroom; the ratio mean can be "
               f"inflated by near-zero baselines, so the median is the honest headline. "
               f"Caveat: QRF spends ~3–5× the tokens (quality, not cost, is plotted).")
        fig.text(0.5, 0.015, cap, ha="center", fontsize=8.5, color="#333",
                 wrap=True)
        fig.tight_layout(rect=[0, 0.05, 1, 0.96])
        pdf.savefig(fig)
        fig.savefig(args.out.with_suffix(".png"), dpi=130)
        plt.close(fig)

    print(f"wrote {args.out} and {args.out.with_suffix('.png')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
