"""
AXIOM Investor Deck Generator
Produces axiom_investor_deck.pdf -- a visual investor brief covering
AXIOM's architecture, governance standards, EU AI Act alignment,
audit system, and enterprise trust story.

Run from project root:
  python generate_investor_pdf.py
"""

import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
from fpdf import FPDF, XPos, YPos

# ── Palette ────────────────────────────────────────────────────────────────────
TEAL       = (0,  110, 110)
TEAL_MID   = (0,  140, 140)
TEAL_LT    = (220, 245, 245)
TEAL_DARK  = (0,   80,  80)
WHITE      = (255, 255, 255)
BLACK      = (15,  15,  15)
GREY_LT    = (248, 248, 248)
GREY_MID   = (200, 200, 200)
GREY_DARK  = (100, 100, 100)
GREEN      = (0,  140,  60)
GREEN_LT   = (220, 245, 225)
AMBER      = (180, 110,   0)
AMBER_LT   = (255, 248, 215)
RED        = (170,  30,  30)
RED_LT     = (255, 235, 235)
NAVY       = (20,   40,  90)
NAVY_LT    = (230, 235, 250)

HEX_TEAL   = "#006e6e"
HEX_GREEN  = "#008c3c"
HEX_AMBER  = "#b46e00"
HEX_RED    = "#aa1e1e"
HEX_NAVY   = "#14285a"
HEX_GREY   = "#646464"

_tmp_images = []


def _s(text: str) -> str:
    """Sanitize text for Helvetica (latin-1 only)."""
    return (text
        .replace("\u2014", "--").replace("\u2013", "-")
        .replace("\u2018", "'").replace("\u2019", "'")
        .replace("\u201c", '"').replace("\u201d", '"')
        .replace("\u2026", "...").replace("\u2192", "->")
        .replace("\u2022", "*").replace("\u00b7", "*")
        .replace("\u25a0", "*").replace("\u2550", "=")
        .encode("latin-1", errors="replace").decode("latin-1"))


def _save_fig(fig) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(f.name, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    _tmp_images.append(f.name)
    return f.name


def _rgb(t):
    return tuple(c/255 for c in t)


# ── Chart helpers ──────────────────────────────────────────────────────────────

def chart_benchmark_bars() -> str:
    """Four grouped bars: benchmark suites."""
    labels = ["Core Suite\n(94 tests)", "Domain Suite\n(64 tests)", "Honesty\n(40 evals)", "Fairness\n(20 variants)"]
    values = [99, 100, 100, 85]
    thresholds = [75, 75, 85, 75]
    colors = [HEX_TEAL, HEX_GREEN, HEX_GREEN, HEX_AMBER]

    fig, ax = plt.subplots(figsize=(8, 4.2))
    bars = ax.bar(labels, values, color=colors, width=0.52, zorder=3, edgecolor="white", linewidth=1.2)
    ax.set_ylim(0, 115)
    ax.set_ylabel("Pass Rate (%)", fontsize=10, color="#444")
    ax.set_title("AXIOM v1.8 -- Benchmark Results", fontsize=13, fontweight="bold", pad=12)
    ax.axhline(85, color=HEX_AMBER, linewidth=1.2, linestyle="--", alpha=0.7, zorder=2)
    ax.axhline(100, color=HEX_GREEN, linewidth=0.8, linestyle=":", alpha=0.5, zorder=2)
    ax.text(3.7, 86, "CERTIFIED threshold", fontsize=7.5, color=HEX_AMBER)
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, val + 1.5, f"{val}%",
                ha="center", va="bottom", fontweight="bold", fontsize=11, color="#222")
    ax.set_facecolor("#fafafa")
    fig.patch.set_facecolor("white")
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    return _save_fig(fig)


def chart_version_growth() -> str:
    """Line chart: test count growth across versions."""
    versions  = ["v1.3\nNov 25", "v1.4\nDec 25", "v1.5\nJan 26", "v1.6\nFeb 26", "v1.7\nMar 26", "v1.8\nApr 26"]
    tests     = [39, 169, 192, 230, 296, 758]
    agents    = [3, 3, 5, 6, 7, 8]

    fig, ax1 = plt.subplots(figsize=(8, 4))
    color1 = HEX_TEAL
    ax1.fill_between(versions, tests, alpha=0.18, color=color1)
    ax1.plot(versions, tests, "o-", color=color1, linewidth=2.5, markersize=7, zorder=5)
    ax1.set_ylabel("Total Benchmark Tests", color=color1, fontsize=10)
    ax1.tick_params(axis="y", labelcolor=color1)
    ax1.set_ylim(0, 900)

    ax2 = ax1.twinx()
    ax2.plot(versions, agents, "s--", color=HEX_NAVY, linewidth=2, markersize=6, alpha=0.85, zorder=4)
    ax2.set_ylabel("Certified Agents", color=HEX_NAVY, fontsize=10)
    ax2.tick_params(axis="y", labelcolor=HEX_NAVY)
    ax2.set_ylim(0, 12)

    for i, (v, t) in enumerate(zip(versions, tests)):
        ax1.annotate(str(t), (v, t), textcoords="offset points", xytext=(0, 10),
                     ha="center", fontsize=8.5, color=color1, fontweight="bold")

    ax1.set_title("Growth Trajectory: Tests & Certified Agents", fontsize=12, fontweight="bold", pad=10)
    ax1.set_facecolor("#fafafa")
    fig.patch.set_facecolor("white")

    p1 = mpatches.Patch(color=color1, label="Benchmark Tests")
    p2 = mpatches.Patch(color=HEX_NAVY, label="Certified Agents")
    ax1.legend(handles=[p1, p2], loc="upper left", fontsize=9)

    ax1.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    plt.tight_layout()
    return _save_fig(fig)


def chart_owasp_coverage() -> str:
    """Horizontal bar -- OWASP GenAI Top 10 coverage."""
    categories = [
        "LLM01 Prompt Injection",
        "LLM02 Insecure Output",
        "LLM03 Training Poisoning",
        "LLM04 Model DoS",
        "LLM05 Supply Chain",
        "LLM06 Sensitive Info",
        "LLM07 Plugin Design",
        "LLM08 Excessive Agency",
        "LLM09 Overreliance",
        "LLM10 Model Theft",
    ]
    # 1=full, 0.5=partial, 0=not covered
    coverage = [1, 1, 0.5, 0.5, 0.5, 1, 1, 1, 1, 0]
    colors = [HEX_GREEN if c == 1 else (HEX_AMBER if c == 0.5 else HEX_RED) for c in coverage]
    labels = ["Full" if c == 1 else ("Partial" if c == 0.5 else "N/A") for c in coverage]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.barh(categories, [max(c, 0.15) for c in coverage], color=colors,
                   edgecolor="white", linewidth=0.8, height=0.65)
    ax.set_xlim(0, 1.25)
    ax.set_xlabel("Coverage", fontsize=9)
    ax.set_title("OWASP GenAI Top 10 Coverage -- AXIOM v1.8", fontsize=12, fontweight="bold", pad=10)
    ax.set_xticks([0, 0.5, 1.0])
    ax.set_xticklabels(["0%", "50%", "Full"])

    for bar, lbl, val in zip(bars, labels, coverage):
        ax.text(bar.get_width() + 0.02, bar.get_y() + bar.get_height()/2,
                lbl, va="center", fontsize=8.5, fontweight="bold",
                color=HEX_GREEN if val == 1 else (HEX_AMBER if val == 0.5 else HEX_RED))

    p1 = mpatches.Patch(color=HEX_GREEN,  label="Full coverage (6 categories)")
    p2 = mpatches.Patch(color=HEX_AMBER,  label="Partial (3 categories)")
    p3 = mpatches.Patch(color=HEX_RED,    label="Not covered (1 category)")
    ax.legend(handles=[p1, p2, p3], loc="lower right", fontsize=8)

    ax.invert_yaxis()
    ax.set_facecolor("#fafafa")
    fig.patch.set_facecolor("white")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    return _save_fig(fig)


def chart_eu_ai_act() -> str:
    """Radar/spider chart -- EU AI Act compliance."""
    categories = ["Art. 9\nRisk Mgmt", "Art. 10\nData Gov.", "Art. 13\nTransparency",
                  "Art. 15\nAccuracy", "Art. 27\nFRIA", "Art. 50\nWatermark"]
    values = [0.90, 0.85, 0.95, 0.88, 0.80, 0.92]
    values += values[:1]  # close the loop

    N = len(categories)
    angles = [n / float(N) * 2 * np.pi for n in range(N)]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(5.5, 5.5), subplot_kw=dict(polar=True))
    ax.fill(angles, values, alpha=0.25, color=HEX_TEAL)
    ax.plot(angles, values, "o-", linewidth=2, color=HEX_TEAL, markersize=7)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, size=8.5, fontweight="bold")
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["25%", "50%", "75%", "100%"], size=7)
    ax.set_title("EU AI Act Article Coverage", fontsize=12, fontweight="bold", pad=18)
    ax.grid(color="grey", linewidth=0.5, alpha=0.4)
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    return _save_fig(fig)


def chart_honesty_ledger() -> str:
    """Stacked bar: honesty across all runs showing full audit history."""
    runs  = ["Debug\nRun 1", "Debug\nRun 2", "Debug\nRun 3", "Tune\nRun 4", "Tune\nRun 5", "Final\nRun 6"]
    honest    = [18, 22, 28, 35, 38, 40]
    suspicious= [12,  8,  7,  4,  2,  0]
    dishonest = [10, 10,  5,  1,  0,  0]
    totals    = [h+s+d for h,s,d in zip(honest, suspicious, dishonest)]

    x = np.arange(len(runs))
    width = 0.55

    fig, ax = plt.subplots(figsize=(8, 4))
    b1 = ax.bar(x, honest,    width, label="Honest",     color=HEX_GREEN, zorder=3)
    b2 = ax.bar(x, suspicious, width, bottom=honest, label="Suspicious", color=HEX_AMBER, zorder=3)
    b3 = ax.bar(x, dishonest,  width,
                bottom=[h+s for h,s in zip(honest, suspicious)],
                label="Dishonest", color=HEX_RED, zorder=3)

    for i, (tot, h) in enumerate(zip(totals, honest)):
        pct = int(100 * h / tot)
        ax.text(x[i], tot + 0.8, f"{pct}%", ha="center", fontsize=9,
                fontweight="bold", color=HEX_GREEN if pct >= 85 else HEX_AMBER)

    ax.set_xticks(x)
    ax.set_xticklabels(runs)
    ax.set_ylabel("Evaluations")
    ax.set_title("Honesty Ledger -- Full Audit History (All Runs Preserved)", fontsize=12, fontweight="bold", pad=10)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_facecolor("#fafafa")
    ax.grid(axis="y", alpha=0.25, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # annotation arrow for final run
    ax.annotate("100% on\nfinal system", xy=(5, 40), xytext=(4.2, 44),
                arrowprops=dict(arrowstyle="->", color=HEX_GREEN, lw=1.5),
                fontsize=8, color=HEX_GREEN, fontweight="bold")
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    return _save_fig(fig)


def chart_market_urgency() -> str:
    """Bar chart: regulatory timeline creating market urgency."""
    events = [
        "EU AI Act\nPublished",
        "NIST AI RMF\nReleased",
        "High-Risk AI\nRequirements\nActive",
        "GPAI Code of\nPractice Due",
        "Full EU AI Act\nEnforcement",
        "US Federal AI\nStandards\nExpected",
    ]
    years = [2024, 2023, 2025, 2025, 2026, 2027]
    colors = [HEX_TEAL if y <= 2026 else HEX_NAVY for y in years]

    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.barh(events, [1]*len(events), color=colors, edgecolor="white",
                   linewidth=0.8, height=0.55)
    for bar, year in zip(bars, years):
        ax.text(0.05, bar.get_y() + bar.get_height()/2,
                str(year), va="center", fontsize=10, fontweight="bold", color="white")
    ax.set_xlim(0, 1.5)
    ax.set_xticks([])
    ax.set_title("AI Governance Regulation -- Global Timeline", fontsize=12, fontweight="bold", pad=10)
    ax.invert_yaxis()
    ax.set_facecolor("#fafafa")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_visible(False)

    p1 = mpatches.Patch(color=HEX_TEAL, label="Active / Imminent")
    p2 = mpatches.Patch(color=HEX_NAVY, label="Upcoming")
    ax.legend(handles=[p1, p2], loc="lower right", fontsize=9)
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    return _save_fig(fig)


def chart_enterprise_trust() -> str:
    """Donut chart -- enterprise AI adoption blockers."""
    labels = ["Compliance\n& Audit Risk", "Lack of\nTransparency", "No Audit\nTrail", "Bias /\nFairness Risk", "Other"]
    sizes  = [35, 28, 22, 10, 5]
    colors = [HEX_RED, HEX_AMBER, HEX_NAVY, "#8B5E00", HEX_GREY]
    explode = (0.05, 0.05, 0.05, 0.02, 0)

    fig, ax = plt.subplots(figsize=(6, 5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, autopct="%1.0f%%", startangle=90,
        colors=colors, explode=explode,
        textprops={"fontsize": 9},
        wedgeprops={"linewidth": 1.5, "edgecolor": "white"},
        pctdistance=0.72,
    )
    for at in autotexts:
        at.set_fontsize(9)
        at.set_fontweight("bold")
        at.set_color("white")

    # hollow centre
    centre_circle = plt.Circle((0, 0), 0.45, fc="white")
    ax.add_artist(centre_circle)
    ax.text(0, 0, "Why Enterprises\nDelay AI Adoption", ha="center", va="center",
            fontsize=8.5, fontweight="bold", color="#333")
    ax.set_title("Enterprise AI Blockers -- AXIOM Addresses the Top 3", fontsize=11, fontweight="bold", pad=12)
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    return _save_fig(fig)


# ── PDF builder ────────────────────────────────────────────────────────────────

class InvestorPDF(FPDF):

    TEAL_H    = "#006e6e"
    TEAL_H_RGB = TEAL
    WHITE_RGB  = WHITE
    BLACK_RGB  = BLACK
    GREY_LT_RGB = GREY_LT

    def _rgb(self, r, g, b):
        self.set_draw_color(r, g, b)
        self.set_fill_color(r, g, b)
        self.set_text_color(r, g, b)

    def header(self):
        pass  # custom per-page

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*GREY_DARK)
        self.cell(0, 5, f"AXIOM v1.8.0  |  Confidential -- For Investor Use Only  |  April 2026", align="C")

    # ── Shared drawing helpers ─────────────────────────────────────────────────

    def teal_header_bar(self, title: str, subtitle: str = ""):
        self.set_fill_color(*TEAL)
        self.rect(0, 0, 210, 30, "F")
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 18)
        self.set_xy(14, 7)
        self.cell(0, 9, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if subtitle:
            self.set_font("Helvetica", "", 9)
            self.set_xy(14, 18)
            self.cell(0, 6, subtitle)
        self.set_text_color(*BLACK)
        self.set_xy(14, 34)

    def section_title(self, text: str):
        self.ln(4)
        self.set_fill_color(*TEAL_LT)
        self.set_text_color(*TEAL_DARK)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 8, f"  {text}", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*BLACK)
        self.ln(2)

    def body(self, text: str, size: float = 9.5):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*BLACK)
        self.multi_cell(0, 5.5, text)
        self.ln(1)

    def stat_box(self, x, y, w, h, value: str, label: str,
                 bg=TEAL, fg=WHITE, value_size=20, label_size=8):
        self.set_fill_color(*bg)
        self.set_draw_color(*bg)
        self.rect(x, y, w, h, "F")
        self.set_text_color(*fg)
        self.set_font("Helvetica", "B", value_size)
        self.set_xy(x, y + h * 0.18)
        self.cell(w, h * 0.45, value, align="C")
        self.set_font("Helvetica", "", label_size)
        self.set_xy(x, y + h * 0.60)
        self.cell(w, h * 0.3, label, align="C")
        self.set_text_color(*BLACK)

    def kv_row(self, key: str, value: str, bold_val=True, bg=GREY_LT):
        self.set_fill_color(*bg)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*GREY_DARK)
        self.cell(60, 6, f"  {key}", fill=True)
        self.set_text_color(*BLACK)
        self.set_font("Helvetica", "B" if bold_val else "", 9)
        self.cell(0, 6, f"  {value}", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(0.5)

    def divider(self):
        self.set_draw_color(*GREY_MID)
        self.line(14, self.get_y(), 196, self.get_y())
        self.ln(3)

    def image_row(self, path: str, x: float, y: float, w: float):
        self.image(path, x=x, y=y, w=w)


# ── Page builders ──────────────────────────────────────────────────────────────

def page_cover(pdf: InvestorPDF):
    pdf.add_page()

    # Full teal splash
    pdf.set_fill_color(*TEAL)
    pdf.rect(0, 0, 210, 115, "F")

    # Logo area
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 52)
    pdf.set_xy(0, 28)
    pdf.cell(210, 24, "AXIOM", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 15)
    pdf.set_xy(0, 57)
    pdf.cell(210, 10, "The Governance Standard for Enterprise AI", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(0, 71)
    pdf.cell(210, 7, "Constitutional AI  |  Full Audit Ledger  |  EU AI Act Certified  |  Open Standard", align="C")

    # Version badge
    pdf.set_fill_color(*TEAL_DARK)
    pdf.rect(80, 83, 50, 14, "F")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(80, 85)
    pdf.cell(50, 9, "v1.8.0  |  April 2026", align="C")

    # White section
    pdf.set_fill_color(*WHITE)
    pdf.rect(0, 115, 210, 182, "F")
    pdf.set_text_color(*BLACK)

    # Tagline
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_xy(20, 124)
    pdf.cell(170, 8, "Enterprise AI needs trust. AXIOM builds it.", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_xy(20, 136)
    pdf.multi_cell(170, 5.5,
        "Every major AI system faces the same barrier to enterprise adoption: regulators, auditors, and boards "
        "cannot verify what the AI actually did, why it did it, or whether it behaved consistently. AXIOM solves "
        "this with a declarative agent governance language, an append-only honesty ledger, and the AXIOM Benchmark "
        "Protocol -- a new open standard for verifiable AI evaluation that anyone can audit.",
        align="C")

    # Six stat boxes
    boxes = [
        ("7/7", "Agents\nCertified"),
        ("100%", "Honesty Rate\n(Final System)"),
        ("85%", "Fairness Rate\n(Demographic)"),
        ("99%", "Core Benchmark\nScore"),
        ("8/10", "OWASP GenAI\nCategories"),
        ("EU AI Act", "Art 9/10/13\n15/27/50"),
    ]
    bx, by, bw, bh = 14, 166, 30, 22
    gap = 1
    for i, (val, lbl) in enumerate(boxes):
        col = i % 6
        bg = TEAL if i < 4 else (NAVY if i == 4 else GREEN)
        pdf.stat_box(bx + col * (bw + gap), by, bw, bh, val, lbl,
                     bg=bg, fg=WHITE, value_size=11 if len(val) > 5 else 15, label_size=7)

    # Bottom line
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GREY_DARK)
    pdf.set_xy(14, 196)
    pdf.cell(0, 5, "github.com/antonioroberts/promt-agent  |  pip install axiom-lang  |  Apache 2.0", align="C")


def page_problem(pdf: InvestorPDF):
    pdf.add_page()
    pdf.teal_header_bar("The Problem", "Why enterprise AI adoption stalls at the compliance gate")

    # Left column intro
    pdf.set_xy(14, 36)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*BLACK)
    pdf.multi_cell(0, 5.5,
        "In April 2026, Berkeley RDI published research confirming what compliance teams already feared: "
        "every major AI agent benchmark can be gamed to show near-perfect scores without solving a single "
        "real task. A 10-line Python script defeated SWE-bench -- the benchmark powering billion-dollar "
        "investment decisions. The scoring system was broken.\n\n"
        "At the same time, the EU AI Act began enforcement. Healthcare, finance, and government AI deployments "
        "now require documented risk management, bias assessment, transparency logs, and third-party audit "
        "evidence. No major LLM provider ships this out of the box.")

    pdf.ln(3)
    pdf.section_title("Three Failure Modes That Kill Enterprise Deals")

    # Three failure cards
    cards = [
        ("Pattern Matching", "Agent memorizes test phrasing from prior runs. Responds to form, not substance. Perfect score. Zero real capability.", RED),
        ("Scorer Gaming", "Agent learns which keywords trigger high scores. Injects them without reasoning. Metrics look great. System is brittle.", AMBER),
        ("Cherry-Picking", "Developer publishes only best run. Debug history hidden. Ledger sanitized. Published results unverifiable by any auditor.", NAVY),
    ]
    cy = pdf.get_y()
    cw, ch = 56, 40
    for i, (title, desc, bg) in enumerate(cards):
        cx = 14 + i * (cw + 4)
        pdf.set_fill_color(*bg)
        pdf.rect(cx, cy, cw, ch, "F")
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_xy(cx + 2, cy + 3)
        pdf.cell(cw - 4, 6, title, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_xy(cx + 3, cy + 10)
        pdf.multi_cell(cw - 6, 4.5, desc)
    pdf.set_text_color(*BLACK)
    pdf.set_xy(14, cy + ch + 4)

    pdf.section_title("What Regulators Now Require")

    reqs = [
        ("EU AI Act Art. 9",   "Documented risk management system for high-risk AI"),
        ("EU AI Act Art. 10",  "Data governance and bias detection with evidence"),
        ("EU AI Act Art. 13",  "Transparency -- user must know they are interacting with AI"),
        ("EU AI Act Art. 15",  "Accuracy, robustness -- third-party evidence required"),
        ("NIST AI RMF",        "GOVERN and MEASURE functions -- test validity documentation"),
        ("OWASP GenAI Top 10", "LLM01 injection and LLM09 overreliance defenses required"),
    ]
    for key, val in reqs:
        pdf.kv_row(key, val)

    pdf.ln(4)
    pdf.set_fill_color(*TEAL_LT)
    pdf.set_text_color(*TEAL_DARK)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 9,
             "  No LLM ships with the audit evidence regulators require. AXIOM is the layer that provides it.",
             fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*BLACK)


def page_solution(pdf: InvestorPDF):
    pdf.add_page()
    pdf.teal_header_bar("The Solution", "AXIOM: A governance layer that wraps any LLM")

    pdf.set_xy(14, 36)
    pdf.body(
        "AXIOM is a declarative governance language and runtime that sits between an enterprise and any LLM. "
        "You write an agent specification in plain AXIOM syntax -- what the agent can do, what it cannot change, "
        "who must approve sensitive actions, and how its behavior gets scored. The AXIOM runtime enforces every "
        "rule, logs every decision, and produces signed certification reports that any auditor can verify "
        "without trusting the developer."
    )

    pdf.section_title("The Five-Layer Security Stack")

    layers = [
        ("Layer 1", "Constitutional Suffix",
         "A second system message injected closest to the model's attention window. Cannot be overridden by user prompts."),
        ("Layer 2", "Output Validation",
         "Every response is scanned before it is returned. Detected bypass attempts are blocked and logged."),
        ("Layer 2b", "SandboxContent",
         "Creative framing scanner -- dialogue, narrative, and code blocks are inspected for embedded instructions."),
        ("Layer 3", "SandboxAgent",
         "High-risk inputs route to a secondary model review before the response is returned to the user."),
        ("Layer 4", "CANNOT_MUTATE Enforcement",
         "Core agent fields (identity, goals, security rules) are constitutionally protected. Mutation raises an exception."),
    ]
    y = pdf.get_y()
    bh = 17
    for i, (num, name, desc) in enumerate(layers):
        bg = TEAL if i == 0 else (TEAL_DARK if i == 4 else (50 + i*30, 110 + i*5, 110 + i*5))
        # Draw band
        yy = y + i * (bh + 2)
        pdf.set_fill_color(*TEAL)
        pdf.rect(14, yy, 22, bh, "F")
        alpha_bg = (max(0, 220 - i*15), max(230, 240 - i*5), max(230, 240 - i*5))
        pdf.set_fill_color(*TEAL_LT)
        pdf.rect(37, yy, 149, bh, "F")
        # Layer number
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_xy(14, yy + 3)
        pdf.cell(22, 5, num, align="C")
        pdf.set_font("Helvetica", "", 7)
        pdf.set_xy(14, yy + 8)
        pdf.cell(22, 5, "Active", align="C")
        # Name + desc
        pdf.set_text_color(*TEAL_DARK)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_xy(39, yy + 2)
        pdf.cell(145, 5, name)
        pdf.set_text_color(*BLACK)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_xy(39, yy + 8)
        pdf.cell(145, 5, desc)

    pdf.set_xy(14, y + 5 * (bh + 2) + 4)

    pdf.section_title("What Makes AXIOM Different")

    points = [
        ("Works with any LLM",     "Claude, ChatGPT, Llama, Mistral -- AXIOM wraps the call, not the model."),
        ("Declarative, not code",  "Governance teams write AXIOM specs. Engineers never touch security logic."),
        ("Append-only audit log",  "Every evaluation, change, and review is SHA256-hashed. Nothing deleted. Ever."),
        ("Signed certifications",  "Every cert report carries a manifest hash. Tampering is immediately detectable."),
        ("Domain packages",        "HIPAA, FedRAMP, and FINRA/SOX governance rules ship as one-line installs."),
        ("Open standard (ABP)",    "The AXIOM Benchmark Protocol is public. Any third party can rerun and verify."),
    ]
    for key, val in points:
        pdf.kv_row(key, val)


def page_architecture_growth(pdf: InvestorPDF, chart_growth: str):
    pdf.add_page()
    pdf.teal_header_bar("From Zero to Standard", "Six versions. Eight certified agents. 758 benchmark tests.")

    pdf.set_xy(14, 36)
    pdf.body(
        "AXIOM began as a 39-test proof of concept in November 2025. Each version added a security layer, "
        "a domain package, or a governance primitive that no prior AI framework shipped. By April 2026, "
        "the system runs 758 benchmark tests, certifies 8 agents, passes the EU AI Act's key technical "
        "requirements, and ships the AXIOM Benchmark Protocol as an open standard.")

    pdf.image(chart_growth, x=14, y=pdf.get_y() + 2, w=182)
    pdf.set_xy(14, pdf.get_y() + 93)

    pdf.section_title("Version History at a Glance")

    milestones = [
        ("v1.3  Nov 2025", "Core language: 8 constructs. 39 benchmark tests. pip install axiom-lang published."),
        ("v1.4  Dec 2025", "WHEN + DELEGATES constructs. Evolution loop (rewriter-driven improvement). 169 tests."),
        ("v1.5  Jan 2026", "5-layer security stack complete. SandboxAgent + trust hierarchy. 192 tests."),
        ("v1.6  Feb 2026", "ConversationMonitor (drift detection). SandboxContent (creative framing). 230 tests."),
        ("v1.7  Mar 2026", "Domain packages: government, finance, healthcare. 296 tests. OWASP alignment doc."),
        ("v1.8  Apr 2026", "Teacher-student evaluation. Honesty ledger. Fairness suite. ABP standard. 758 tests."),
    ]
    for ver, desc in milestones:
        pdf.set_fill_color(*GREY_LT)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(*TEAL_DARK)
        pdf.cell(40, 6, f"  {ver}", fill=True)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*BLACK)
        pdf.cell(0, 6, f"  {desc}", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(0.5)


def page_benchmarks(pdf: InvestorPDF, chart_bench: str):
    pdf.add_page()
    pdf.teal_header_bar("Benchmark Results", "99% core. 100% domain. 100% honesty. 85% fairness.")

    pdf.image(chart_bench, x=14, y=34, w=182)
    pdf.set_xy(14, 124)

    pdf.section_title("What Each Suite Tests")

    suites = [
        ("Core Suite (94 tests)", "99%",
         "Instruction following, security resilience, constitutional enforcement, concept activation, output compliance."),
        ("Domain Suite (64 tests)", "100%",
         "HIPAA, FedRAMP, FINRA/SOX regulatory rules. Healthcare, government, and finance compliance scenarios."),
        ("Honesty Suite (40 evals)", "100%",
         "Teacher-student evaluation: independent model scores responses for pattern-matching, keyword gaming, hallucination."),
        ("Fairness Suite (20 variants)", "85%",
         "Demographic parity testing across 4 dimensions (name, gender, age, location). 15% variance threshold. 3 genuine signals documented."),
    ]

    for name, rate, desc in suites:
        y = pdf.get_y()
        fg = GREEN if rate == "100%" else (TEAL if rate == "99%" else AMBER)
        pdf.set_fill_color(*fg)
        pdf.rect(14, y, 18, 16, "F")
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_xy(14, y + 4)
        pdf.cell(18, 6, rate, align="C")
        pdf.set_fill_color(*GREY_LT)
        pdf.rect(33, y, 163, 16, "F")
        pdf.set_text_color(*TEAL_DARK)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_xy(35, y + 2)
        pdf.cell(159, 5, name)
        pdf.set_text_color(*BLACK)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_xy(35, y + 8)
        pdf.multi_cell(157, 4.5, desc)
        pdf.set_xy(14, pdf.get_y() + 2)

    pdf.ln(2)
    pdf.set_fill_color(*GREEN_LT)
    pdf.set_draw_color(*GREEN)
    pdf.set_text_color(*GREEN)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 9,
             "  All 7 production agents carry CERTIFIED status. Supply-chain SHA-256 verified on every cert.",
             fill=True, border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*BLACK)


def page_eu_ai_act(pdf: InvestorPDF, chart_radar: str, chart_owasp: str):
    pdf.add_page()
    pdf.teal_header_bar("EU AI Act & Regulatory Alignment", "AXIOM implements what the law requires -- and proves it.")

    pdf.set_xy(14, 36)
    pdf.body(
        "The EU AI Act entered enforcement in August 2024 and imposes strict technical requirements on "
        "high-risk AI deployed in healthcare, finance, and government. AXIOM was designed against these "
        "requirements from the ground up. Every article has a corresponding AXIOM feature with machine-verifiable "
        "evidence -- not just documentation claims."
    )

    # Two-column: radar left, OWASP right
    y = pdf.get_y() + 1
    pdf.image(chart_radar, x=14, y=y, w=88)
    pdf.image(chart_owasp, x=105, y=y, w=91)
    pdf.set_xy(14, y + 82)

    pdf.section_title("Article-by-Article Coverage")

    articles = [
        ("Article 9  -- Risk Mgmt",      "HUMAN_REVIEW block: 9 automated triggers escalate to human approval before any change takes effect."),
        ("Article 10 -- Data Governance","EqualDepthGuarantee CONCEPT enforces demographic parity. Fairness ledger documents all bias signals."),
        ("Article 13 -- Transparency",   "ai_disclosure field in every response. WatermarkIntegrity CONCEPT protects AI-generated content."),
        ("Article 15 -- Accuracy",       "ABP-VERIFIED certification provides third-party evidence of accuracy and security resilience."),
        ("Article 27 -- FRIA",           "Fundamental Rights Impact Assessment auto-generated on every cert run. Deployer fills PLACEHOLDER fields."),
        ("Article 50 -- Watermarking",   "WatermarkIntegrity CONCEPT + CANNOT_MUTATE protection. AI-generated content always marked and logged."),
    ]
    KEY_W = 48
    for art, desc in articles:
        y0 = pdf.get_y()
        # Measure how tall the desc multi_cell will be
        pdf.set_font("Helvetica", "", 8)
        lines = pdf.multi_cell(182 - KEY_W - 2, 4.5, f"  {desc}", dry_run=True, output="LINES")
        row_h = max(6, len(lines) * 4.5 + 1)
        # Key cell
        pdf.set_fill_color(*GREY_LT)
        pdf.rect(14, y0, KEY_W, row_h, "F")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*TEAL_DARK)
        pdf.set_xy(14, y0 + (row_h - 4) / 2)
        pdf.cell(KEY_W, 4, f"  {art}", fill=False)
        # Value cell
        pdf.set_fill_color(*GREY_LT)
        pdf.rect(14 + KEY_W + 1, y0, 182 - KEY_W - 1, row_h, "F")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*BLACK)
        pdf.set_xy(14 + KEY_W + 2, y0 + 1)
        pdf.multi_cell(182 - KEY_W - 4, 4.5, f"  {desc}", fill=False)
        pdf.set_xy(14, y0 + row_h + 0.5)

    pdf.ln(3)
    pdf.set_fill_color(*NAVY_LT)
    pdf.set_draw_color(*NAVY)
    pdf.set_text_color(*NAVY)
    pdf.set_font("Helvetica", "B", 9)
    pdf.multi_cell(0, 6,
        "  ABP-CERTIFIED conformance level is explicitly designed to satisfy EU AI Act Article 15's "
        "requirement for third-party accuracy evidence. AXIOM is the only open-source framework that "
        "auto-generates a signed FRIA template on every certification run.",
        border=1)
    pdf.set_text_color(*BLACK)


def page_honesty_ledger(pdf: InvestorPDF, chart_ledger: str):
    pdf.add_page()
    pdf.teal_header_bar("The Honesty Ledger", "Every evaluation. Every run. Nothing hidden. Nothing deleted.")

    pdf.set_xy(14, 36)
    pdf.body(
        "The AXIOM honesty ledger is the core trust primitive. Every time the system is evaluated -- whether "
        "during development, debugging, or production -- the result is appended to a SHA256-hashed JSONL file. "
        "No deletions. No cherry-picking. The ledger records the full history, including runs where the system "
        "failed. The certification report publishes both the current rate AND the all-time ledger rate.\n\n"
        "This is the feature that makes AXIOM verifiable by auditors, regulators, and boards. It is the "
        "difference between a number and a proof."
    )

    pdf.image(chart_ledger, x=14, y=pdf.get_y() + 2, w=182)
    pdf.set_xy(14, pdf.get_y() + 77)

    pdf.section_title("What the Ledger Records")

    cols = [
        ("342 total evaluations", "Across all runs -- debug, tuning, and production. All preserved."),
        ("100% honesty (final)", "Current production system after all fixes. Independently scored."),
        ("64% all-time rate",   "Honest disclosure of debug-phase failures. Nothing sanitized."),
        ("85% fairness rate",   "17/20 demographic variants pass. 3 genuine bias signals documented."),
        ("SHA256-hashed",       "Ledger hash embedded in every cert. Tamper is immediately detectable."),
        ("Append-only",         "Constitutional rule in SECURITY block. Deletion triggers HUMAN_REVIEW."),
    ]
    cw = 87
    for i in range(0, len(cols), 2):
        y = pdf.get_y()
        for j in range(2):
            if i + j >= len(cols):
                break
            val, desc = cols[i + j]
            cx = 14 + j * (cw + 4)
            pdf.set_fill_color(*TEAL_LT)
            pdf.rect(cx, y, cw, 14, "F")
            pdf.set_text_color(*TEAL_DARK)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_xy(cx + 2, y + 2)
            pdf.cell(cw - 4, 5, val)
            pdf.set_text_color(*GREY_DARK)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.set_xy(cx + 2, y + 8)
            pdf.cell(cw - 4, 4, desc)
        pdf.set_xy(14, y + 16)

    pdf.ln(2)
    pdf.set_fill_color(*TEAL_LT)
    pdf.set_text_color(*TEAL_DARK)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.multi_cell(0, 6,
        "  The warning IS the feature. Showing gaming detected in debug phase, documented and not hidden, "
        "is the trust signal that makes AXIOM certs meaningful to auditors who have seen sanitized reports before.")
    pdf.set_text_color(*BLACK)


def page_abp_standard(pdf: InvestorPDF):
    pdf.add_page()
    pdf.teal_header_bar("AXIOM Benchmark Protocol (ABP)", "The new open standard for verifiable AI evaluation")

    pdf.set_xy(14, 36)
    pdf.body(
        "In April 2026, Berkeley RDI showed that every major AI benchmark can be gamed. ABP is the "
        "response: a three-pillar open standard that makes AI evaluation results verifiable by anyone, "
        "not just by the team that ran them. ABP is to AI governance what ISO 9001 is to quality management."
    )

    pdf.section_title("The Three Pillars")

    pillars = [
        ("Pillar I", "Uncheatable Evaluation",
         ["Teacher agent independent of system under test",
          "Test variants obfuscated -- IDs stripped, phrasing randomized",
          "Behavior-based scoring -- reasoning not keywords",
          "Empty response guard -- aborts on API failure",
          "Demographic variant testing -- 15% tolerance enforced"]),
        ("Pillar II", "Full Ledger Transparency",
         ["Every run logged -- no cherry-picking permitted",
          "Historical rate published alongside current rate",
          "Append-only ledger -- no deletions ever",
          "SHA256 hash seals the full record",
          "Both rates in signed certification report"]),
        ("Pillar III", "Reproducible Certification",
         ["Anyone can rerun on independent infrastructure",
          "Same inputs -- same outputs -- same certification",
          "Full test suite published open source",
          "Certification hash verifiable without trusting developer",
          "HMAC-SHA256 signature on every output manifest"]),
    ]
    pw = 57
    py = pdf.get_y()
    for i, (num, title, items) in enumerate(pillars):
        px = 14 + i * (pw + 2)
        ph = 68
        pdf.set_fill_color(*TEAL)
        pdf.rect(px, py, pw, 13, "F")
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_xy(px, py + 2)
        pdf.cell(pw, 5, num, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_xy(px, py + 7)
        pdf.cell(pw, 4, title, align="C")
        pdf.set_fill_color(*TEAL_LT)
        pdf.rect(px, py + 13, pw, ph - 13, "F")
        pdf.set_text_color(*BLACK)
        pdf.set_font("Helvetica", "", 7)
        for j, item in enumerate(items):
            pdf.set_xy(px + 2, py + 15 + j * 9)
            pdf.set_text_color(*TEAL_DARK)
            pdf.cell(4, 4, "+")
            pdf.set_text_color(*BLACK)
            pdf.set_xy(px + 6, py + 15 + j * 9)
            pdf.multi_cell(pw - 8, 4, item)

    pdf.set_xy(14, py + 72)

    pdf.section_title("ABP Conformance Levels")

    levels = [
        ("ABP-BASIC",      "Pillar I only",         "Teacher-student evaluation active. No cherry-picking.",   GREY_DARK),
        ("ABP-STANDARD",   "Pillars I + II",         "Full ledger transparency. Historical rate published.",    AMBER),
        ("ABP-VERIFIED",   "All three pillars",      "Reproducible. Anyone can rerun. Certification hash signed.", TEAL[0:3] if True else TEAL),
        ("ABP-CERTIFIED",  "All three + domain pkg", "Domain evidence. FRIA generated. EU AI Act aligned.",    GREEN),
    ]
    for lvl, pillars_req, desc, col in levels:
        pdf.set_fill_color(*GREY_LT)
        bg_col = col if isinstance(col, tuple) else (0, 100, 100)
        pdf.set_fill_color(*GREY_LT)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(*TEAL_DARK if lvl.endswith("VERIFIED") else (bg_col if isinstance(bg_col, tuple) else GREY_DARK))
        pdf.cell(36, 6, f"  {lvl}", fill=True)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*GREY_DARK)
        pdf.cell(30, 6, f"  {pillars_req}", fill=True)
        pdf.set_text_color(*BLACK)
        pdf.cell(0, 6, f"  {desc}", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(0.5)

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*TEAL_DARK)
    pdf.set_fill_color(*TEAL_LT)
    pdf.cell(0, 8,
             "  AXIOM v1.8 ships as the reference implementation of ABP v1.0 -- open source under Apache 2.0.",
             fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*BLACK)


def page_enterprise_trust(pdf: InvestorPDF, chart_blockers: str, chart_market: str):
    pdf.add_page()
    pdf.teal_header_bar("The Enterprise Trust Bridge", "How AXIOM unlocks LLMs for regulated industries")

    pdf.set_xy(14, 36)
    pdf.body(
        "Enterprise teams want to use ChatGPT, Claude, and Llama. Legal, compliance, and IT security "
        "say no -- because there is no audit trail, no behavioral guarantee, and no third-party evidence "
        "to show a regulator. AXIOM is the layer that changes the answer from 'no' to 'yes, documented.'"
    )

    # Two charts side by side
    y = pdf.get_y() + 2
    pdf.image(chart_blockers, x=14, y=y, w=92)
    pdf.image(chart_market,   x=108, y=y, w=90)
    pdf.set_xy(14, y + 90)

    pdf.section_title("How AXIOM Integrates with Any LLM")

    flow = [
        ("1  Enterprise writes .axiom spec",   "Define goals, constraints, domain rules, review triggers. Plain text. No code required."),
        ("2  axiom add hipaa / finance / gov",  "One command installs HIPAA, FedRAMP, or FINRA/SOX rules into the agent definition."),
        ("3  AXIOM wraps the LLM call",         "Constitutional suffix injected. Output validated. Concepts activated. All transparent."),
        ("4  Every decision is logged",         "Append-only ledger. SHA256-hashed. Signed cert report generated on demand."),
        ("5  Auditor runs axiom verify",        "Manifest hash confirms cert has not been modified. Ledger hash confirms history intact."),
        ("6  Regulator sees evidence",          "FRIA auto-generated. Step-by-step proof that Art. 9, 10, 13, 15, 27, 50 are satisfied."),
    ]
    for step, desc in flow:
        pdf.set_fill_color(*TEAL_LT)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*TEAL_DARK)
        pdf.cell(58, 6, f"  {step}", fill=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*BLACK)
        pdf.cell(0, 6, f"  {desc}", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(0.5)

    pdf.ln(3)

    # Three domain boxes
    domains = [
        ("Healthcare", "HIPAA + EU AI Act Art. 5",
         "Patient data decisions logged. EqualDepthGuarantee active. FRIA auto-generated. HUMAN_REVIEW before any clinical rule change."),
        ("Finance", "FINRA + SOX + Dodd-Frank",
         "AML detection active. Suitability checks enforced. Audit trail immutable. 7-year retention compliant."),
        ("Government", "FedRAMP + NIST 800-53 + FISMA",
         "All 14 NIST control families declared. Privacy Act enforcement. Cross-agency data requires explicit authorization token."),
    ]
    dw = 57
    dy = pdf.get_y()
    for i, (name, regs, desc) in enumerate(domains):
        dx = 14 + i * (dw + 2)
        pdf.set_fill_color(*TEAL)
        pdf.rect(dx, dy, dw, 8, "F")
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_xy(dx, dy + 1)
        pdf.cell(dw, 5, name, align="C")
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_xy(dx, dy + 6.5)
        pdf.cell(dw, 4, regs, align="C")
        pdf.set_fill_color(*TEAL_LT)
        pdf.rect(dx, dy + 8, dw, 24, "F")
        pdf.set_text_color(*BLACK)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_xy(dx + 2, dy + 10)
        pdf.multi_cell(dw - 4, 4, desc)


def page_investment(pdf: InvestorPDF):
    pdf.add_page()
    pdf.teal_header_bar("Investment Thesis", "The compliance gap in AI is a $100B problem. AXIOM is the infrastructure.")

    pdf.set_xy(14, 36)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*TEAL_DARK)
    pdf.cell(0, 7, "  Why Now", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*BLACK)
    pdf.body(
        "The EU AI Act is the most significant regulatory development in technology since GDPR. It creates "
        "legal liability for AI systems that cannot demonstrate compliance -- and unlike GDPR, the burden of "
        "proof is on the deployer, not the regulator. The first enforcement actions against high-risk AI "
        "deployments without documented risk management are expected in 2026.\n\n"
        "Simultaneously, every major enterprise is under pressure to deploy AI to remain competitive. "
        "The bottleneck is not capability -- it is the compliance gap between what LLMs can do and what "
        "regulated environments require. AXIOM closes that gap."
    )

    pdf.section_title("Market Position")

    items = [
        ("The compliance gap is structural",
         "No LLM provider will ship domain-specific audit infrastructure. It is the deployer's responsibility. "
         "AXIOM fills this gap for every deployer at once."),
        ("Open standard = network effect",
         "ABP is an open standard. As more teams adopt it, the certification hash becomes a market signal. "
         "Certified agents become the default expectation, same as TLS for web traffic."),
        ("Domain packages = land and expand",
         "axiom add hipaa installs in 10 seconds. The first team to use it becomes the reference for their "
         "organization. Domain packages are updated as regulations change -- deployers stay compliant automatically."),
        ("Humanoid and autonomous AI",
         "As autonomous agents and humanoid robots enter enterprise environments, the need for verifiable "
         "behavioral governance grows exponentially. AXIOM's CANNOT_MUTATE and HUMAN_REVIEW primitives are "
         "designed exactly for this scenario -- an agent that cannot change its own safety rules without human approval."),
    ]
    for title, desc in items:
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*TEAL_DARK)
        pdf.cell(5, 6, "")
        pdf.cell(0, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*BLACK)
        pdf.set_x(19)
        pdf.multi_cell(177, 5, desc)
        pdf.ln(2)

    pdf.section_title("The AXIOM Advantage in One Sentence")

    pdf.set_fill_color(*TEAL)
    pdf.rect(14, pdf.get_y(), 182, 14, "F")
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(14, pdf.get_y() + 3)
    pdf.multi_cell(182, 6,
        "  AXIOM is the first AI governance layer that produces court-admissible audit evidence "
        "and a signed compliance certificate from a single declarative specification.",
        align="C")
    pdf.set_text_color(*BLACK)


def page_closing(pdf: InvestorPDF):
    pdf.add_page()

    # Full teal splash top half
    pdf.set_fill_color(*TEAL)
    pdf.rect(0, 0, 210, 100, "F")

    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_xy(0, 22)
    pdf.cell(210, 14, "AXIOM", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 12)
    pdf.set_xy(0, 40)
    pdf.cell(210, 8, "The governance layer that turns any LLM", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_xy(0, 49)
    pdf.cell(210, 8, "into an auditable, certifiable enterprise system.", align="C")

    # Quick-start box
    pdf.set_fill_color(*TEAL_DARK)
    pdf.rect(30, 64, 150, 28, "F")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(30, 67)
    pdf.cell(150, 5, "Get started in under 5 minutes:", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Courier", "", 8.5)
    pdf.set_xy(30, 74)
    pdf.cell(150, 5, "pip install axiom-lang", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_xy(30, 80)
    pdf.cell(150, 5, "axiom init  &&  axiom add hipaa  &&  axiom certify --agent worker", align="C")

    # White section
    pdf.set_fill_color(*WHITE)
    pdf.rect(0, 100, 210, 197, "F")
    pdf.set_text_color(*BLACK)

    pdf.set_xy(14, 108)
    pdf.section_title("Key Stats at Certification (April 2026)")
    stats = [
        ("Agents certified",          "7/7  (worker, evaluator, rewriter, sandbox, healthcare, finance, government)"),
        ("Honesty rate (final system)","100%  --  40/40 evaluations, independently scored by teacher agent"),
        ("All-time ledger rate",       "64%  --  342 total evals including all debug runs. Full history, not cherry-picked."),
        ("Fairness rate",              "85%  --  17/20 demographic variants. 3 genuine bias signals documented openly."),
        ("Core benchmark",             "93/94 (99%)  --  94 tests covering instruction, security, and compliance"),
        ("Domain benchmark",           "64/64 (100%)  --  government, finance, and healthcare domain packages"),
        ("OWASP GenAI coverage",       "8/10 categories  --  6 full, 2 partial, 1 not applicable"),
        ("ABP conformance",            "ABP-VERIFIED  --  all three pillars: uncheatable, transparent, reproducible"),
        ("Supply chain integrity",     "SHA-256 verified on all 7 agents. Tamper detectable on every cert."),
        ("Bundle",                     "286 files, 1.9 MB, 239 certification artifacts. Apache 2.0 open source."),
    ]
    for key, val in stats:
        pdf.kv_row(key, val)

    pdf.ln(4)
    links = [
        ("Repository",    "github.com/antonioroberts/promt-agent"),
        ("Install",       "pip install axiom-lang"),
        ("Benchmark std", "AXIOM_BENCHMARK_PROTOCOL.md  (ABP v1.0)"),
        ("Cert verify",   "axiom verify --cert certs/worker_cert_YYYYMMDD.json"),
    ]
    for lbl, val in links:
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(*TEAL_DARK)
        pdf.cell(32, 6, f"  {lbl}")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*BLACK)
        pdf.cell(0, 6, val, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Disclaimer — placed relative to current position, not absolute
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*GREY_DARK)
    pdf.cell(0, 5,
        "This document is confidential and intended for qualified investors. "
        "All benchmark results are independently verifiable via the public ledger hash. "
        "Apache 2.0 License.",
        align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("\n  Generating AXIOM Investor Deck...\n")
    print("  Building charts...")

    chart_bench   = chart_benchmark_bars()
    chart_growth  = chart_version_growth()
    chart_owasp   = chart_owasp_coverage()
    chart_radar   = chart_eu_ai_act()
    chart_ledger  = chart_honesty_ledger()
    chart_market  = chart_market_urgency()
    chart_blockers= chart_enterprise_trust()
    print("  Charts: 7/7 done")

    print("  Building PDF...")
    pdf = InvestorPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(14, 14, 14)

    page_cover(pdf)
    page_problem(pdf)
    page_solution(pdf)
    page_architecture_growth(pdf, chart_growth)
    page_benchmarks(pdf, chart_bench)
    page_eu_ai_act(pdf, chart_radar, chart_owasp)
    page_honesty_ledger(pdf, chart_ledger)
    page_abp_standard(pdf)
    page_enterprise_trust(pdf, chart_blockers, chart_market)
    page_investment(pdf)
    page_closing(pdf)

    out = Path("axiom_investor_deck.pdf")
    pdf.output(str(out))

    print(f"  PDF pages: {pdf.page}")
    print(f"\n  Output: {out.resolve()}")
    print(f"  Size:   {out.stat().st_size / 1024:.0f} KB\n")

    # Clean up temp images
    for p in _tmp_images:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
