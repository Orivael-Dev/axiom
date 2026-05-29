"""Generate docs/colab_kquant_rerun.pdf — step-by-step Colab guide
for running the llama.cpp K-quant local rerun benchmark.

Addresses the most common Colab failure: building llama.cpp from
source. The guide uses pre-built binaries from the GitHub releases
page instead, which works reliably on Colab T4/A100/L4.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).resolve().parents[1] / "docs" / "colab_kquant_rerun.pdf"

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

# ── Colours ──────────────────────────────────────────────────────────
TEAL       = colors.HexColor("#2c8a90")
DARK_TEAL  = colors.HexColor("#0d4f54")
ORANGE     = colors.HexColor("#c87029")
CELL_BG    = colors.HexColor("#f4f4f4")
WARN_BG    = colors.HexColor("#fff8e1")
WARN_BORDER= colors.HexColor("#f0a500")
CODE_BG    = colors.HexColor("#1e1e1e")
CODE_FG    = colors.HexColor("#d4d4d4")
LIGHT_GREY = colors.HexColor("#e8e8e8")
MID_GREY   = colors.HexColor("#888888")

# ── Styles ────────────────────────────────────────────────────────────
base = getSampleStyleSheet()

def S(name, **kw):
    return ParagraphStyle(name, **kw)

title_style = S("Title",
    fontSize=22, leading=28, textColor=DARK_TEAL,
    spaceAfter=4, fontName="Helvetica-Bold", alignment=TA_LEFT)

subtitle_style = S("Sub",
    fontSize=11, leading=15, textColor=MID_GREY,
    spaceAfter=2, fontName="Helvetica", alignment=TA_LEFT)

h1 = S("H1",
    fontSize=14, leading=18, textColor=DARK_TEAL,
    spaceBefore=10, spaceAfter=4, fontName="Helvetica-Bold")

h2 = S("H2",
    fontSize=11, leading=14, textColor=TEAL,
    spaceBefore=6, spaceAfter=3, fontName="Helvetica-Bold")

body = S("Body",
    fontSize=9.5, leading=14, textColor=colors.HexColor("#222222"),
    spaceAfter=4, fontName="Helvetica")

code_style = S("Code",
    fontSize=8.2, leading=12, textColor=CODE_FG,
    fontName="Courier", spaceAfter=0,
    leftIndent=6, rightIndent=6)

note_style = S("Note",
    fontSize=9, leading=13, textColor=colors.HexColor("#444444"),
    fontName="Helvetica-Oblique", spaceAfter=2)

warn_style = S("Warn",
    fontSize=9, leading=13, textColor=colors.HexColor("#7a4f00"),
    fontName="Helvetica-Bold", spaceAfter=2)

step_label = S("StepLabel",
    fontSize=8.5, leading=11, textColor=colors.white,
    fontName="Helvetica-Bold", alignment=TA_CENTER)

# ── Helpers ───────────────────────────────────────────────────────────

def HR():
    return HRFlowable(width="100%", thickness=0.5,
                      color=LIGHT_GREY, spaceAfter=6, spaceBefore=6)

def gap(h=4):
    return Spacer(1, h * mm)


def code_block(lines: list[str], label: str = "") -> Table:
    """Dark-background code block, optional label in top-right."""
    content = "\n".join(lines)
    cell_content = Paragraph(
        content.replace("\n", "<br/>").replace(" ", "&nbsp;"),
        code_style,
    )
    if label:
        lbl = Paragraph(
            f'<font color="#888888" size="7">{label}</font>',
            S("lbl", fontSize=7, leading=9, textColor=MID_GREY,
              fontName="Helvetica", alignment=4),  # 4 = RIGHT
        )
        inner = Table([[cell_content, lbl]],
                      colWidths=["85%", "15%"])
        inner.setStyle(TableStyle([
            ("VALIGN",  (0,0), (-1,-1), "TOP"),
            ("PADDING", (0,0), (-1,-1), 0),
        ]))
        data = [[inner]]
    else:
        data = [[cell_content]]

    t = Table(data, colWidths=[PAGE_W - 2 * MARGIN - 2])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("ROUNDEDCORNERS", [4]),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    return t


def step_header(n: int, title: str) -> Table:
    """Numbered step badge + title row."""
    badge = Table([[Paragraph(str(n), step_label)]],
                  colWidths=[8 * mm], rowHeights=[8 * mm])
    badge.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), TEAL),
        ("ROUNDEDCORNERS", [4]),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    title_p = Paragraph(title, h1)
    row = Table([[badge, title_p]],
                colWidths=[12 * mm, PAGE_W - 2 * MARGIN - 14 * mm])
    row.setStyle(TableStyle([
        ("VALIGN",  (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",  (1, 0), (1, 0), 6),
    ]))
    return row


def warn_box(text: str) -> Table:
    p = Paragraph(f"⚠  {text}", warn_style)
    t = Table([[p]], colWidths=[PAGE_W - 2 * MARGIN - 2])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), WARN_BG),
        ("LINECOLOR",     (0, 0), (-1, -1), WARN_BORDER),
        ("BOX",           (0, 0), (-1, -1), 1, WARN_BORDER),
        ("ROUNDEDCORNERS", [3]),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    return t


def info_box(text: str) -> Table:
    p = Paragraph(text, note_style)
    t = Table([[p]], colWidths=[PAGE_W - 2 * MARGIN - 2])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#eef7f7")),
        ("LINECOLOR",     (0, 0), (-1, -1), TEAL),
        ("BOX",           (0, 0), (-1, -1), 1, TEAL),
        ("ROUNDEDCORNERS", [3]),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    return t


# ── Document content ──────────────────────────────────────────────────

def build() -> list:
    story = []

    # ── Title block ───────────────────────────────────────────────────
    story += [
        gap(6),
        Paragraph("SRD Benchmark — K-Quant Rerun", title_style),
        Paragraph("Google Colab step-by-step guide · llama.cpp pre-built binaries",
                  subtitle_style),
        gap(2),
        HR(),
        gap(3),
    ]

    story += [
        Paragraph("What this guide does", h1),
        Paragraph(
            "The first SRD benchmark run used <b>cited</b> K-quant numbers from the "
            "llama.cpp README (Q4_K_M, Q5_K_M, Q6_K, Q8_0). To make the comparison "
            "airtight — same model, same dataset split, same stride — we need to "
            "measure those numbers locally using llama.cpp's own "
            "<font face='Courier'>llama-perplexity</font> tool.",
            body),
        Paragraph(
            "This guide avoids building llama.cpp from source (the main Colab "
            "failure point). It downloads a pre-built Ubuntu binary from the "
            "llama.cpp GitHub releases page instead. Total runtime on a T4 is "
            "roughly <b>25–40 minutes</b>.",
            body),
        gap(2),
        info_box(
            "Prerequisite: you already have research/quant/results/srd_sweep.json "
            "from the first Colab run. This guide adds kquant_rerun.json alongside "
            "it and regenerates the plot."
        ),
        gap(4),
    ]

    # ── Step 1 ────────────────────────────────────────────────────────
    story += [
        step_header(1, "Open a GPU runtime"),
        gap(2),
        Paragraph(
            "In the Colab menu: <b>Runtime → Change runtime type → T4 GPU</b>. "
            "Then run the cell below to confirm the GPU is visible and note the "
            "CUDA version — you need this to pick the right binary in Step 2.",
            body),
        gap(2),
        code_block([
            "!nvidia-smi",
            "!nvcc --version | grep 'release'",
        ], label="Cell 1"),
        gap(2),
        Paragraph(
            "Expected output: a table showing the T4 with ~15 GB VRAM, and a line "
            "like <font face='Courier'>release 12.x</font>. "
            "If you see <i>No GPU found</i>, the runtime type wasn't saved — "
            "repeat the menu step and reconnect.",
            note_style),
        gap(5),
    ]

    # ── Step 2 ────────────────────────────────────────────────────────
    story += [
        step_header(2, "Download pre-built llama.cpp binaries"),
        gap(2),
        warn_box(
            "Do NOT build from source on Colab. The CUDA toolkit version on Colab "
            "frequently mismatches what llama.cpp's cmake expects, causing silent "
            "CPU-fallback builds or outright failures. Pre-built binaries are "
            "faster and reliable."
        ),
        gap(3),
        Paragraph(
            "The cell below fetches the latest release tag from the GitHub API, "
            "downloads the matching Ubuntu CUDA binary zip, and installs the two "
            "binaries we need "
            "(<font face='Courier'>llama-perplexity</font> and "
            "<font face='Courier'>llama-quantize</font>):",
            body),
        gap(2),
        code_block([
            "import urllib.request, json, zipfile, os, stat, pathlib",
            "",
            "# Get latest release tag",
            "api = 'https://api.github.com/repos/ggerganov/llama.cpp/releases/latest'",
            "with urllib.request.urlopen(api) as r:",
            "    tag = json.load(r)['tag_name']   # e.g. 'b5012'",
            "print('Latest llama.cpp release:', tag)",
            "",
            "# Download Ubuntu CUDA binary zip",
            "zip_name = f'llama-{tag}-bin-ubuntu-x64.zip'",
            "url = (f'https://github.com/ggerganov/llama.cpp'",
            "       f'/releases/download/{tag}/{zip_name}')",
            "print('Downloading', zip_name, '...')",
            "urllib.request.urlretrieve(url, '/tmp/llama.zip')",
            "",
            "# Extract and make binaries executable",
            "os.makedirs('/opt/llama_bin', exist_ok=True)",
            "with zipfile.ZipFile('/tmp/llama.zip') as z:",
            "    for name in z.namelist():",
            "        if name.endswith(('llama-perplexity', 'llama-quantize',",
            "                          'llama-perplexity.exe',",
            "                          'llama-quantize.exe')):",
            "            z.extract(name, '/opt/llama_bin')",
            "            full = pathlib.Path('/opt/llama_bin') / name",
            "            full.chmod(full.stat().st_mode | stat.S_IEXEC)",
            "",
            "# Verify",
            "!ls -lh /opt/llama_bin/",
            "!/opt/llama_bin/llama-perplexity --version 2>&1 | head -2",
        ], label="Cell 2"),
        gap(2),
        Paragraph(
            "If the download fails with a 404, the release zip may use a slightly "
            "different name (some releases append <font face='Courier'>-cuda12</font>). "
            "Check the assets list on the releases page and update <font face='Courier'>"
            "zip_name</font> accordingly.",
            note_style),
        gap(5),
    ]

    # ── Step 3 ────────────────────────────────────────────────────────
    story += [
        step_header(3, "Get convert-hf-to-gguf.py and install Python deps"),
        gap(2),
        Paragraph(
            "The conversion script lives in the llama.cpp repo root (not in the "
            "binary zip). A shallow clone is fast — we only need the script, "
            "not the full history.",
            body),
        gap(2),
        code_block([
            "!git clone --depth 1 https://github.com/ggerganov/llama.cpp /opt/llama_src",
            "",
            "# Python deps for the convert script + our research harness",
            "!pip install -q transformers datasets sentencepiece protobuf",
            "!pip install -q torch --index-url https://download.pytorch.org/whl/cu121",
            "",
            "# Clone the Axiom repo (skip if already mounted via Drive)",
            "!git clone --depth 1 https://github.com/orivael-dev/axiom /content/axiom",
            "%cd /content/axiom",
        ], label="Cell 3"),
        gap(2),
        Paragraph(
            "If you already have the Axiom repo on Drive, mount it with "
            "<font face='Courier'>from google.colab import drive; "
            "drive.mount('/content/drive')</font> and "
            "<font face='Courier'>%cd /content/drive/MyDrive/axiom</font> instead "
            "of the clone.",
            note_style),
        gap(5),
    ]

    # ── Step 4 ────────────────────────────────────────────────────────
    story += [
        step_header(4, "Save the WikiText-2 test split"),
        gap(2),
        Paragraph(
            "llama-perplexity reads the eval text from a plain file. "
            "We use the same HuggingFace dataset split our SRD sweep used, "
            "joined with double newlines — the standard WikiText convention.",
            body),
        gap(2),
        code_block([
            "from datasets import load_dataset",
            "",
            "ds = load_dataset('wikitext', 'wikitext-2-raw-v1', split='test')",
            "text = '\\n\\n'.join(ds['text'])",
            "",
            "wikitext_path = '/tmp/wikitext2_test.txt'",
            "with open(wikitext_path, 'w') as f:",
            "    f.write(text)",
            "",
            "print(f'Saved {len(text):,} chars to {wikitext_path}')",
        ], label="Cell 4"),
        gap(5),
    ]

    # ── Step 5 ────────────────────────────────────────────────────────
    story += [
        step_header(5, "Run the K-quant rerun benchmark"),
        gap(2),
        Paragraph(
            "This cell runs <font face='Courier'>bench_llamacpp.py</font> in "
            "<font face='Courier'>--rerun-locally</font> mode. It will:",
            body),
        Paragraph("&nbsp;&nbsp;1. Download TinyLlama from HuggingFace (~2 GB).", body),
        Paragraph("&nbsp;&nbsp;2. Convert to GGUF F16 using "
                  "<font face='Courier'>convert-hf-to-gguf.py</font>.", body),
        Paragraph("&nbsp;&nbsp;3. Quantize to Q4_K_M, Q5_K_M, Q6_K, Q8_0 "
                  "using <font face='Courier'>llama-quantize</font>.", body),
        Paragraph("&nbsp;&nbsp;4. Run <font face='Courier'>llama-perplexity</font> "
                  "on each (~5–8 min each).", body),
        gap(2),
        code_block([
            "import subprocess, sys",
            "",
            "result = subprocess.run([",
            "    sys.executable, '-m', 'research.quant.bench_llamacpp',",
            "    '--rerun-locally',",
            "    '--llama-bin',      '/opt/llama_bin',",
            "    '--llama-src',      '/opt/llama_src',",
            "    '--wikitext-file',  '/tmp/wikitext2_test.txt',",
            "    '--gguf-dir',       '/tmp/srd_gguf',",
            "    '--output',         'research/quant/results/kquant_rerun.json',",
            "], check=False)",
            "",
            "if result.returncode != 0:",
            "    print('benchmark exited with code', result.returncode)",
            "else:",
            "    print('Done — results in research/quant/results/kquant_rerun.json')",
        ], label="Cell 5"),
        gap(2),
        warn_box(
            "Note on stride: llama-perplexity defaults to stride = context (2048), "
            "while the SRD sweep used stride 512. Our results file flags this as "
            "'rerun_local'. A small PPL difference (~0.05–0.1) between methods is "
            "expected and does not affect the 1.51 PPL gap finding."
        ),
        gap(5),
    ]

    # ── Step 6 ────────────────────────────────────────────────────────
    story += [
        step_header(6, "Regenerate the plot with local numbers"),
        gap(2),
        Paragraph(
            "Once the rerun JSON exists, regenerate the scatter plot so it "
            "shows locally-measured K-quant points instead of cited ones. "
            "The orange SRD squares don't change — only the teal K-quant line updates.",
            body),
        gap(2),
        code_block([
            "!python -m research.quant.plot_results \\",
            "  --inputs research/quant/results/srd_sweep.json,\\",
            "           research/quant/results/kquant_rerun.json \\",
            "  --output  docs/srd_perplexity_vs_bpw.png",
            "",
            "# Display inline",
            "from IPython.display import Image",
            "Image('docs/srd_perplexity_vs_bpw.png')",
        ], label="Cell 6"),
        gap(5),
    ]

    # ── Step 7 ────────────────────────────────────────────────────────
    story += [
        step_header(7, "Download results and push"),
        gap(2),
        Paragraph(
            "Download both result files to your machine, or commit them "
            "directly from Colab.",
            body),
        gap(2),
        code_block([
            "from google.colab import files",
            "",
            "files.download('research/quant/results/kquant_rerun.json')",
            "files.download('docs/srd_perplexity_vs_bpw.png')",
        ], label="Cell 7a — download"),
        gap(3),
        Paragraph("Or push straight to the branch:", body),
        gap(2),
        code_block([
            "!git config user.email 'you@example.com'",
            "!git config user.name  'Your Name'",
            "!git add research/quant/results/kquant_rerun.json \\",
            "         docs/srd_perplexity_vs_bpw.png",
            "!git commit -m 'results: K-quant local rerun on Colab T4'",
            "!git push origin claude/srd-prototype-benchmark-JRtv1",
        ], label="Cell 7b — push"),
        gap(5),
    ]

    # ── Troubleshooting ───────────────────────────────────────────────
    story += [PageBreak()]

    story += [
        Paragraph("Troubleshooting", h1),
        gap(2),
    ]

    issues = [
        (
            "Cell 2: 404 downloading the zip",
            "Some releases use a different filename suffix. Open "
            "github.com/ggerganov/llama.cpp/releases/latest in a browser, "
            "look at the Assets list, find the Ubuntu x64 zip name, and "
            "replace zip_name in the cell with the exact filename shown.",
        ),
        (
            "Cell 2: llama-perplexity not found in the zip",
            "Older releases named it llama-perplexity (no prefix) or "
            "perplexity. Print z.namelist() inside the with block to see "
            "what's actually in the zip, then update the endswith() check "
            "to match.",
        ),
        (
            "Cell 3: torch install takes too long",
            "Skip the torch install if it's already present: "
            "python -c 'import torch' to check. Colab's base image "
            "usually has torch pre-installed.",
        ),
        (
            "Cell 5: convert-hf-to-gguf.py not found",
            "The script expects --llama-src pointing at the repo root "
            "where the script lives. Confirm it exists: "
            "ls /opt/llama_src/convert-hf-to-gguf.py. "
            "If missing, the clone in Cell 3 may have failed — re-run it.",
        ),
        (
            "Cell 5: llama-perplexity CUDA error / falls back to CPU",
            "This happens if the binary was compiled for a different CUDA "
            "version. Check nvcc --version (Cell 1) and confirm the binary "
            "release matches. If mismatched, pass --device cpu to "
            "bench_llamacpp.py (adds ~5× wallclock but still works).",
        ),
        (
            "Cell 5: TinyLlama download hangs",
            "HuggingFace rate-limits anonymous downloads. Add "
            "--hf-token <your_token> to the bench_llamacpp.py call, "
            "or set HF_TOKEN in the env before running.",
        ),
        (
            "Cell 5: llama-quantize: command not found",
            "The binary may be nested inside a subdirectory in the zip. "
            "Run find /opt/llama_bin -name llama-quantize to locate it, "
            "then symlink it: ln -s <found_path> /opt/llama_bin/llama-quantize",
        ),
    ]

    for title, detail in issues:
        story += [
            Paragraph(f"<b>{title}</b>", body),
            Paragraph(detail, note_style),
            gap(3),
        ]

    # ── Expected timing ───────────────────────────────────────────────
    story += [
        HR(),
        gap(2),
        Paragraph("Expected timing on Colab T4", h2),
        gap(2),
    ]

    timing_data = [
        ["Step", "Approx. time"],
        ["Cell 2: download pre-built binaries",    "< 1 min"],
        ["Cell 3: clone + pip install",            "3–5 min"],
        ["Cell 4: save WikiText-2",                "< 1 min"],
        ["Cell 5: GGUF conversion (F16)",          "3–5 min"],
        ["Cell 5: llama-quantize × 4",             "2–4 min"],
        ["Cell 5: llama-perplexity × 4 (GPU)",     "20–30 min"],
        ["Cell 6: plot",                           "< 1 min"],
        ["Total",                                  "~30–45 min"],
    ]
    t = Table(timing_data,
              colWidths=[PAGE_W - 2 * MARGIN - 50 * mm, 50 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), TEAL),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, CELL_BG]),
        ("BACKGROUND",    (0, -1), (-1, -1), colors.HexColor("#eef7f7")),
        ("FONTNAME",      (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.3, LIGHT_GREY),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("BOX",           (0, 0), (-1, -1), 0.5, LIGHT_GREY),
    ]))
    story += [t, gap(4)]

    # ── Footer note ───────────────────────────────────────────────────
    story += [
        HR(),
        Paragraph(
            "After the run, paste kquant_rerun.json back into this session "
            "and the write-up will update automatically.",
            note_style),
    ]

    return story


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="SRD Benchmark — K-Quant Colab Rerun Guide",
        author="Axiom",
    )
    doc.build(build())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
