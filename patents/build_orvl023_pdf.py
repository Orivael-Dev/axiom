#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the ORVL-023 (.AXM) concept-note PDF.

Mirrors the visual style of the prior Orivael concept notes
(ORVL-018 ANF, ORVL-019 ASPA, ORVL-022 CPI): hero quote box with
a teal banner, section subtitles in colored badges, code-style
gray boxes for listings, gridded tables, and a small footer.

Usage:
    python patents/build_orvl023_pdf.py [output_path]
                (default: patents/ORVL023_AXM.pdf)

BUG-003: UTF-8 throughout.
"""

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether,
)


# ── Palette — picked to match the existing ORVL PDFs ─────────────────
TEAL_DARK   = colors.HexColor("#0d4f54")
TEAL        = colors.HexColor("#2c8a90")
TEAL_LIGHT  = colors.HexColor("#e6f4f5")
PURPLE      = colors.HexColor("#5a3e8a")
PURPLE_LITE = colors.HexColor("#efeaf7")
ORANGE      = colors.HexColor("#c87029")
ORANGE_LITE = colors.HexColor("#fbeede")
GRAY_LITE   = colors.HexColor("#f4f4f4")
GRAY_BORDER = colors.HexColor("#cccccc")
GRAY_TEXT   = colors.HexColor("#666666")
NAVY        = colors.HexColor("#1d3557")


# ── Paragraph styles ────────────────────────────────────────────────
_styles = getSampleStyleSheet()

def _style(name, parent="Normal", **kw):
    base = _styles[parent]
    return ParagraphStyle(name=name, parent=base, **kw)

S_TITLE     = _style("Title",  parent="Title", fontSize=28, leading=34,
                     alignment=1, textColor=NAVY, spaceAfter=4)
S_ORVL      = _style("OrvlId", parent="Heading1", fontSize=11, leading=14,
                     alignment=1, textColor=GRAY_TEXT, spaceAfter=0)
S_SUBHEAD   = _style("SubHead", parent="Normal", fontSize=10, leading=13,
                     alignment=1, textColor=TEAL_DARK, spaceAfter=14)
S_QUOTE     = _style("Quote", parent="Italic", fontSize=13, leading=18,
                     alignment=1, textColor=NAVY, spaceAfter=6)
S_AUTHOR    = _style("Author", parent="Italic", fontSize=10, leading=12,
                     alignment=1, textColor=GRAY_TEXT, spaceAfter=14)
S_H1        = _style("H1", parent="Heading1", fontSize=16, leading=20,
                     textColor=TEAL_DARK, spaceBefore=10, spaceAfter=8)
S_H2        = _style("H2", parent="Heading2", fontSize=12, leading=16,
                     textColor=TEAL_DARK, spaceBefore=8, spaceAfter=4)
S_BODY      = _style("Body", parent="Normal", fontSize=10, leading=14,
                     textColor=colors.black, spaceAfter=6)
S_BULLET    = _style("Bullet", parent="Normal", fontSize=10, leading=14,
                     leftIndent=14, bulletIndent=2, spaceAfter=2)
S_CODE      = _style("Code", parent="Code", fontSize=8.5, leading=11,
                     leftIndent=6, rightIndent=6, textColor=NAVY,
                     spaceAfter=8, spaceBefore=4, backColor=GRAY_LITE,
                     borderColor=GRAY_BORDER, borderWidth=0.5,
                     borderPadding=8, fontName="Courier")
S_CALLOUT_T = _style("CalloutTitle", parent="Normal", fontSize=10,
                     leading=13, textColor=TEAL_DARK,
                     fontName="Helvetica-Bold", spaceAfter=2)
S_CALLOUT   = _style("Callout", parent="Normal", fontSize=9.5, leading=12,
                     textColor=NAVY, leftIndent=8, rightIndent=8,
                     spaceAfter=8, spaceBefore=2, backColor=TEAL_LIGHT,
                     borderColor=TEAL, borderWidth=0,
                     borderPadding=10, leftBorder=4)
S_FOOTER    = _style("Footer", parent="Normal", fontSize=8, leading=10,
                     alignment=1, textColor=GRAY_TEXT)


# ── Helpers ──────────────────────────────────────────────────────────
def _hero_quote(text):
    """Italicised quote inside a soft border. Matches the hero quote
    boxes in ORVL-018/019/022."""
    cell = Paragraph(text, S_QUOTE)
    t = Table([[cell]], colWidths=[6.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TEAL_LIGHT),
        ("BOX",        (0, 0), (-1, -1), 1.2, NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 18),
        ("RIGHTPADDING",(0, 0), (-1, -1), 18),
        ("TOPPADDING",  (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING",(0,0), (-1, -1), 14),
    ]))
    return t


def _meta_row(left, mid, right):
    """Three-cell metadata strip (Status / Builds-on / Domains)."""
    def _cell(label_html):
        return Paragraph(label_html, S_BODY)
    t = Table([[_cell(left), _cell(mid), _cell(right)]],
                colWidths=[2.0 * inch, 2.5 * inch, 1.6 * inch])
    t.setStyle(TableStyle([
        ("BOX",          (0, 0), (-1, -1), 0.5, GRAY_BORDER),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, GRAY_BORDER),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
    ]))
    return t


def _section_badge(number, title, color_bg):
    """Badge at the start of each major section (matches the
    coloured numbered tabs in the prior PDFs)."""
    badge = Paragraph(
        f'<font color="white"><b>{number} | {title.upper()}</b></font>',
        ParagraphStyle("badge", parent=S_BODY, fontSize=9, leading=11,
                        textColor=colors.white,
                        backColor=color_bg, borderPadding=6,
                        leftIndent=0, spaceAfter=4),
    )
    t = Table([[badge]], colWidths=[2.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), color_bg),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",(0, 0), (-1, -1), 8),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0,0), (-1, -1), 4),
    ]))
    return t


def _data_table(rows, col_widths=None, header_bg=NAVY):
    """Table with NAVY header row + alternating row backgrounds."""
    n_cols = len(rows[0])
    cw = col_widths or ([6.5 / n_cols * inch] * n_cols)
    body = [[Paragraph(c, S_BODY) for c in row] for row in rows]
    t = Table(body, colWidths=cw, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, 0), 10),
        ("BOX",        (0, 0), (-1, -1), 0.5, GRAY_BORDER),
        ("INNERGRID",  (0, 0), (-1, -1), 0.4, GRAY_BORDER),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",(0, 0), (-1, -1), 6),
        ("RIGHTPADDING",(0,0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0,0),(-1, -1), 6),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), GRAY_LITE))
    t.setStyle(TableStyle(style))
    return t


def _callout(title, body):
    """Soft-coloured box with a bold title + paragraph body."""
    inner = [Paragraph(f"<b>{title}</b>", S_CALLOUT_T),
              Paragraph(body, ParagraphStyle("calloutBody", parent=S_BODY,
                          fontSize=9.5, leading=13, textColor=NAVY))]
    t = Table([[inner]], colWidths=[6.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), TEAL_LIGHT),
        ("LINEBEFORE",   (0, 0), (0, -1),  3, TEAL),
        ("LEFTPADDING",  (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING",   (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
    ]))
    return t


def _code(text):
    """Pre-style code listing inside a gray box."""
    # ReportLab needs <br/> to break lines in Paragraph; convert and
    # entitise angle brackets so the < / > don't get parsed as tags.
    body = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = body.replace("\n", "<br/>")
    return Paragraph(body, S_CODE)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GRAY_TEXT)
    canvas.drawCentredString(
        LETTER[0] / 2, 0.4 * inch,
        f"Axiom eXchange Model (.AXM) — Concept Brief · ORVL-023 · "
        f"Orivael · May 2026 · CONFIDENTIAL · Page {doc.page}",
    )
    canvas.restoreState()


# ── Document content ────────────────────────────────────────────────
def build(output_path: Path) -> None:
    doc = SimpleDocTemplate(
        str(output_path), pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="ORVL-023 — Axiom eXchange Model (.AXM)",
        author="Antonio Roberts · Orivael",
    )
    story = []

    # ── Header ────────────────────────────────────────────────────
    story.append(Paragraph("ORVL-023", S_ORVL))
    story.append(Paragraph(".AXM", S_TITLE))
    story.append(Paragraph(
        "Axiom eXchange Model · Modular Execution-Graph Container · "
        "Hybrid Trust Model · Successor to GGUF",
        S_SUBHEAD))
    story.append(_hero_quote(
        '"A GPU asks: how fast can we multiply matrices? '
        '.AXM asks: which skill, which proof, which trajectory — '
        'and only those."'
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Concept Note · Antonio Roberts · Orivael · May 2026",
        S_AUTHOR))
    story.append(_meta_row(
        "<b>Status:</b> CONCEPT —<br/>Software emulator shipped",
        "<b>Pairs with:</b> ORVL-004 (MKB) · "
        "ORVL-018 (ANF) · ORVL-019 (Mobile)",
        "<b>Trust model:</b><br/>Hybrid (open + signed)",
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>Abstract.</b>", S_BODY))
    story.append(Paragraph(
        "The Axiom eXchange Model (.AXM) is a software-emulated successor "
        "to GGUF-style model containers. Where GGUF is a flat file of "
        "quantised weights, .AXM treats a model as a <i>living execution "
        "graph</i>: an always-resident Core Logic Module + lazy-loaded "
        "Skill Delegates + pre-compiled Trajectory Blocks + a Vector-Vertex "
        "DB + a Proof Ledger + a Hardware Map. Each sub-module is "
        "HMAC-signed independently under a hybrid trust model (open "
        "container, signed delegates). The container drives the existing "
        "AXIOM stack on every operation: skill delegates land in the MKB "
        "BlockRegistry as <font face='Courier'>block_type=AXM_SKILL</font>; "
        "every proof entry is verified through the ANF governance "
        "coprocessor; the mobile NeuralComputeBlock loads delegates "
        "lazily as the active task's intent class changes. The promise "
        "is energy-proportional intelligence: the file does not awaken "
        "the whole cathedral — it lights only the rooms needed for the "
        "current problem.",
        S_BODY))
    story.append(Spacer(1, 6))

    # ── Section 1 — Beyond Static Tensors ─────────────────────────
    story.append(_section_badge("01", "Beyond Static Tensors", TEAL))
    story.append(Paragraph(
        "1. The .AXM Architecture: Beyond Static Tensors", S_H1))
    story.append(Paragraph(
        "Current formats like GGUF are essentially flat files containing "
        "headers and quantised weights. A self-designed AI format would "
        "treat the model as a living graph — partitioned, hardware-aware, "
        "and able to load only the logic needed for the current task.",
        S_BODY))

    story.append(Paragraph("Bit-Depth Elasticity (Dynamic Quantisation)", S_H2))
    story.append(Paragraph(
        "Critical reasoning layers stay at 16-bit BF16 for precision; "
        "stylistic / knowledge layers drop to 1.5-bit or 2-bit. A 70B-class "
        "model fits into 24 GB VRAM while preserving most of the intelligence "
        "of its higher-precision form — the north-star promise of elastic "
        "storage.",
        S_BODY))

    story.append(Paragraph("Integrated Trajectory Blocks", S_H2))
    story.append(Paragraph(
        "Instead of storing only weights, .AXM contains a HISTORY segment "
        "of pre-compiled trajectories — verified motion paths, reasoning "
        "traces, or tool-use routes the system can reuse instantly. The "
        "format stores not only what the model knows, but also proven "
        "pathways for acting on that knowledge.",
        S_BODY))

    story.append(_callout(
        "Design target",
        "An AI-designed model file: the cathedral does not need to wake all "
        "at once. Only the rooms relevant to the current intent class light "
        "up; the rest stay cold on disk."))

    story.append(PageBreak())

    # ── Section 2 — Six modules + container layout ───────────────
    story.append(_section_badge("02", "Partitioned Modular Storage", PURPLE))
    story.append(Paragraph(
        "2. Six Sub-Modules + Container Layout", S_H1))
    story.append(_data_table([
        ["Module", "Purpose", "Runtime behavior"],
        ["<b>Core Logic Module</b><br/>(The Axiom)",
          "Tiny ultra-fast 1B–3B reasoning core",
          "Always resident — handles routing, safety, verification, intent"],
        ["<b>Skill Delegates</b>",
          "Task-specific plugin files, each HMAC-signed",
          "Loaded into VRAM only when a WHEN-condition matches"],
        ["<b>Trajectory Blocks</b><br/>(HISTORY)",
          "Verified solution paths, action patterns, tool chains",
          "Pulled when a task matches prior proven behavior"],
        ["<b>Vector-Vertex DB</b>",
          "Geometry primitive ↔ semantic class map",
          "Vision-to-pattern acceleration; format-level class lookup"],
        ["<b>Proof Ledger</b>",
          "Per-module HMAC signatures + content hashes",
          "Verified before any skill activates at runtime"],
        ["<b>Hardware Map</b>",
          "Chip-aware dispatch (cpu / gpu / npu / fpga / compile_on_load)",
          "Chosen at load time, drives the ANF dispatch path"],
    ], col_widths=[1.5 * inch, 2.3 * inch, 2.7 * inch]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Container layout on disk:", S_H2))
    story.append(_code(
        "my_model.axm/\n"
        "├── header.json               ← Semantic State Header (signed)\n"
        "├── core/core.json            ← Core Logic Module manifest\n"
        "├── delegates/                ← lazy-loaded on WHEN match\n"
        "│   ├── pii_redactor/skill.json\n"
        "│   ├── anf_governance/skill.json\n"
        "│   └── vector_recall/skill.json\n"
        "├── trajectories/             ← pre-compiled reasoning paths\n"
        "│   └── history.jsonl\n"
        "├── vertices.json             ← Vector-Vertex DB\n"
        "└── proofs/ledger.jsonl       ← per-module HMAC + sha256"
    ))

    story.append(PageBreak())

    # ── Section 3 — Semantic State Header ────────────────────────
    story.append(_section_badge("03", "Semantic State Header", TEAL))
    story.append(Paragraph("3. The State-Space Header", S_H1))
    story.append(Paragraph(
        "GGUF has a practical key-value header. .AXM goes further: a "
        "Semantic State Header that describes execution paths, hardware "
        "dispatch, proof conditions, and vertex/vector indexes — the "
        "metadata is itself an executable contract.",
        S_BODY))
    story.append(_data_table([
        ["Feature",      ".GGUF",                          ".AXM"],
        ["Indexing",     "Metadata strings (name, version, arch).",
                          "Vertex-mapped indexes tied to spatial / semantic / task-state clusters."],
        ["Quantization", "Uniform schemes such as Q4_K_M.",
                          "Non-linear, per-layer elasticity driven by task importance."],
        ["Execution",    "CPU/GPU offloading decided by runtime.",
                          "Direct hardware mapping with chip-aware JIT execution paths."],
        ["Verification", "Simple file hashes and metadata checks.",
                          "Per-module HMAC signatures + ANF-coprocessor proof verification."],
    ], col_widths=[1.0 * inch, 2.5 * inch, 3.0 * inch]))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Header sketch (as serialised in <code>header.json</code>):", S_H2))
    story.append(_code(
        "{\n"
        '  "format_version" : "0.1-concept",\n'
        '  "core_logic"     : "axiom_core_3b",\n'
        '  "quant_map"      : "elastic_per_layer",\n'
        '  "hardware_map"   : "compile_on_load",\n'
        '  "delegates"      : ["pii_redactor", "anf_governance", "vector_recall"],\n'
        '  "safety_proofs"  : true,\n'
        '  "signature"      : "<HMAC-SHA256 over canonical payload>"\n'
        "}"
    ))

    story.append(PageBreak())

    # ── Section 4 — MKB / ANF / Mobile wiring ────────────────────
    story.append(_section_badge("04", "Cross-Patent Wiring", ORANGE))
    story.append(Paragraph(
        "4. Wiring Into Three Existing Patents", S_H1))
    story.append(Paragraph(
        ".AXM is not a stand-alone artefact. The container exists to "
        "exercise three patents already in the AXIOM portfolio. Every "
        "operation drives them.",
        S_BODY))
    story.append(_data_table([
        ["Patent",                    "Role for AXM"],
        ["<b>ORVL-004 MKB</b>",
          "Each loaded Skill Delegate registers as a "
          "<font face='Courier'>KnowledgeBlock</font> with "
          "<font face='Courier'>block_type=&quot;AXM_SKILL&quot;</font> in the "
          "existing <font face='Courier'>BlockRegistry</font>. AXM is the "
          "on-disk container; MKB is the live registry."],
        ["<b>ORVL-018 ANF</b>",
          "<font face='Courier'>verify_proofs()</font> drives "
          "<font face='Courier'>GovernanceCoprocessorEmulator.process()</font> "
          "once per proof entry. The header's <font face='Courier'>hardware_map</font> "
          "selects the ANF dispatch class."],
        ["<b>ORVL-019 Mobile</b>",
          "<font face='Courier'>NeuralComputeBlock(axm_container=…)</font>; "
          "<font face='Courier'>pre_classify()</font> lazy-loads delegates whose "
          "WHEN-conditions match the classifier's intent class — the same "
          "power model the phone already uses."],
    ], col_widths=[1.5 * inch, 5.0 * inch]))

    story.append(Spacer(1, 8))
    story.append(_callout(
        "Energy-proportional intelligence",
        "An <b>INFORM</b> task lights the smooth-convergence pathway "
        "(20 ANF cores active, two skill delegates loaded). A <b>HARM</b> "
        "task lights only the boundary-detection fast-path (5 ANF cores, "
        "one delegate). Safe inference uses more compute than dangerous "
        "inference detected — the inverse of every other AI system."))

    story.append(PageBreak())

    # ── Section 5 — Hybrid trust + verify pipeline ───────────────
    story.append(_section_badge("05", "Hybrid Trust Model", TEAL))
    story.append(Paragraph(
        "5. Hybrid Trust Model — Open Container, Signed Delegates", S_H1))
    story.append(Paragraph(
        "The AXM brief's strategic fork (proprietary / open / hybrid) is "
        "answered with hybrid. Three derived keys handle three independent "
        "signing surfaces — every artefact is verifiable; no artefact is "
        "encrypted; ecosystem partners can ship signed delegates without "
        "key exchange.",
        S_BODY))
    story.append(_data_table([
        ["Layer",            "Key",                                         "What it signs"],
        ["Container",        "derive_key(b'axiom-axm-container-v1')",       "header.json + AXMRouteResult"],
        ["Delegates",        "derive_key(b'axiom-axm-delegate-v1')",        "each delegate's skill.json"],
        ["Proofs / vectors / trajectories",
                              "derive_key(b'axiom-axm-proof-v1')",
                              "ledger.jsonl + vertices.json + history.jsonl"],
    ], col_widths=[1.7 * inch, 2.8 * inch, 2.0 * inch]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Verification flow (sample run):", S_H2))
    story.append(_code(
        "$ python -m axiom_axm verify /tmp/starter.axm\n"
        "{\n"
        '  "verified": true,\n'
        '  "proofs_checked": 6,\n'
        '  "fingerprint": "f5481d89"\n'
        "}\n"
        "\n"
        "$ python -m axiom_axm route /tmp/starter.axm "
        "\"explain transformers\"\n"
        "{\n"
        '  "task": "explain transformers",\n'
        '  "intent_class": "INFORM",  "confidence": 0.55,\n'
        '  "loaded_skills":  ["anf_governance", "pii_redactor"],\n'
        '  "skipped_skills": ["vector_recall"],\n'
        '  "anf_distance": 0.000,  "anf_cores_active": 20,\n'
        '  "signature": "<64-char HMAC>"\n'
        "}"
    ))

    story.append(PageBreak())

    # ── Section 6 — Patent claims ────────────────────────────────
    story.append(_section_badge("06", "Provisional Patent Claims", ORANGE))
    story.append(Paragraph("6. Provisional Patent Claims (ORVL-023)", S_H1))

    claims = [
        ("Claim 1",
          "A model container architecture wherein a model is partitioned into "
          "an always-resident Core Logic Module and a set of independently "
          "HMAC-signed Skill Delegates, with each delegate gated by a "
          "WHEN-condition over a classifier-emitted intent class, such that "
          "VRAM residency is bounded by the union of delegates whose "
          "WHEN-conditions match the current task — yielding compute and "
          "memory proportional to task complexity rather than model size."),
        ("Claim 2",
          "A Semantic State Header that replaces flat metadata with an "
          "executable contract describing per-layer quantisation elasticity, "
          "hardware-dispatch class, declared delegate set, and a safety-proof "
          "requirement flag, wherein the header is HMAC-signed under a "
          "container-scoped key and load-time signature failure refuses any "
          "subsequent module access."),
        ("Claim 3",
          "A Proof Ledger embedded in the container — one HMAC-signed entry "
          "per sub-module bound to a content SHA-256 of that sub-module's "
          "on-disk file — verified via a governance-coprocessor pipeline "
          "before any skill delegate may be activated, providing "
          "cryptographic binding between disk integrity and runtime "
          "permission."),
        ("Claim 4",
          "Lazy WHEN-triggered skill activation in which a classifier emits "
          "an intent class per task, the container compares the intent class "
          "against per-delegate WHEN conditions, and matched delegates are "
          "registered with an external Modular Knowledge Block registry as "
          "first-class blocks of type AXM_SKILL — giving the runtime "
          "uniform inspection of statically-shipped and dynamically-composed "
          "skills."),
        ("Claim 5",
          "A Vector-Vertex Database embedded at the container level mapping "
          "perception classes (e.g. CYLINDRICAL, FRAGILE, PLANAR) to vertex "
          "primitive clusters, signed under the proof key and consulted "
          "before geometric inference — collapsing the perception-to-skill "
          "lookup into a format-level table read."),
        ("Claim 6",
          "A hybrid trust model in which the container header and per-skill "
          "delegate manifests are signed under distinct derived keys, and "
          "the runtime refuses to load any artefact whose signature does not "
          "verify under the corresponding key — enabling third-party "
          "delegate publication without key exchange while preserving "
          "load-time integrity guarantees."),
    ]
    for tag, body in claims:
        story.append(Paragraph(f"<b>{tag}.</b> {body}", S_BODY))

    story.append(PageBreak())

    # ── Section 7 — Portfolio relationship + closer ──────────────
    story.append(_section_badge("07", "Patent Portfolio Relationship", PURPLE))
    story.append(Paragraph("7. Patent Portfolio Relationship", S_H1))
    story.append(_data_table([
        ["Patent",              "Role in ORVL-023"],
        ["<b>ORVL-001</b> Constitutional Language",
          "<font face='Courier'>.axiom</font> spec at <font face='Courier'>axiom_files/core/axiom_axm.axiom</font> "
          "is the constitutional contract for the container."],
        ["<b>ORVL-004</b> MKB",
          "Each loaded delegate registers as a KnowledgeBlock — the live "
          "registry uniformly inspects shipped + composed skills."],
        ["<b>ORVL-005</b> Latent / MonotonicGate",
          "AXM Trajectory Blocks store proven reasoning trajectories that "
          "the gate can short-circuit to without re-derivation."],
        ["<b>ORVL-010</b> CANNOT_MUTATE",
          "Container header, format_version, and proof_ledger are declared "
          "CANNOT_MUTATE in the .axiom spec — runtime enforces frozen-dataclass "
          "discipline."],
        ["<b>ORVL-014</b> Constitutional World Model",
          "Vector-Vertex DB provides the geometry primitives that the "
          "world model uses for perception-to-skill lookup."],
        ["<b>ORVL-016</b> Intent Typing",
          "The classifier whose intent class drives WHEN matching."],
        ["<b>ORVL-018</b> ANF",
          "Governance Coprocessor verifies the Proof Ledger; Hardware Map "
          "selects ANF dispatch path."],
        ["<b>ORVL-019</b> Mobile / ASPA",
          "Phone's NeuralComputeBlock accepts an AXM container and lazy-loads "
          "delegates per intent class on-device."],
        ["<b>ORVL-022</b> CPI",
          "Physical skill delegates (Wrap-Grip, Pinch-Pressure, etc.) ship "
          "as AXM Skill Delegates; vertex classes match the AXM Vector-Vertex DB."],
    ], col_widths=[2.0 * inch, 4.5 * inch]))

    story.append(Spacer(1, 14))
    story.append(_hero_quote(
        '"Twenty-three patents. One constitutional AI architecture. '
        'From language reasoning to physical motion to model containers — '
        'the same geometry, lit one room at a time."'
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "CONFIDENTIAL CONCEPT NOTE — ORVL-023 · Orivael · "
        "Antonio Roberts · hello@orivael.dev · "
        "github.com/Orivael-Dev/axiom · May 2026",
        S_FOOTER))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main(argv=None) -> int:
    out = Path(argv[1]) if argv and len(argv) > 1 else \
          Path(sys.argv[1]) if len(sys.argv) > 1 else \
          Path(__file__).parent / "ORVL023_AXM.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out)
    print(f"wrote {out}  ({out.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
