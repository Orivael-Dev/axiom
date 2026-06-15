#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the ORVL-026 Audio Groove concept-note PDF.

Mirrors the visual style of the prior Orivael concept notes
(ORVL-018 ANF, ORVL-019 ASPA, ORVL-022 CPI, ORVL-023 AXM): hero quote box
with a teal banner, section subtitles in colored badges, code-style gray boxes
for listings, gridded tables, and a small footer.

Usage:
    python patents/build_orvl026_audio_pdf.py [output_path]
                (default: patents/ORVL026_AudioGroove.pdf)
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
    body = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = body.replace("\n", "<br/>")
    return Paragraph(body, S_CODE)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GRAY_TEXT)
    canvas.drawCentredString(
        LETTER[0] / 2, 0.4 * inch,
        f"Axiom Audio Groove — Concept Brief · ORVL-026 · "
        f"Orivael · June 2026 · CONFIDENTIAL · Page {doc.page}",
    )
    canvas.restoreState()


# ── Document content ────────────────────────────────────────────────
def build(output_path: Path) -> None:
    doc = SimpleDocTemplate(
        str(output_path), pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="ORVL-026 — Axiom Audio Groove",
        author="Antonio Roberts · Orivael",
    )
    story = []

    # ── Header ────────────────────────────────────────────────────
    story.append(Paragraph("ORVL-026", S_ORVL))
    story.append(Paragraph("Audio Groove", S_TITLE))
    story.append(Paragraph(
        "Axiom Audio Groove · Constitutional Audio Intelligence · "
        "Compact Sensory Representation",
        S_SUBHEAD))
    story.append(_hero_quote(
        '"A physical record stores sound as groove geometry. Axiom stores it '
        'the same way — depth for energy, width for spread, curve for rhythm — '
        'so a micro-agent can reason about sound without hauling the whole '
        'thundercloud."'
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Concept Note · Antonio Roberts · Orivael · June 2026",
        S_AUTHOR))
    story.append(_meta_row(
        "<b>Status:</b> CONCEPT —<br/>Software implemented (axiom_audio/)",
        "<b>Pairs with:</b> ORVL-025 (Event Token) · ORVL-004 (MKB)",
        "<b>Domain:</b><br/>Audio · Voice · Automotive",
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>Abstract.</b>", S_BODY))
    story.append(Paragraph(
        "Axiom Audio Groove converts raw PCM audio into a compact "
        "groove-geometry profile — depth (low-frequency energy / loudness), "
        "width (stereo spread / dynamic range), curve (pitch motion / rhythm "
        "pattern), and texture (noise floor / breath presence). Specialist "
        "micro-agents each report one property; a coordinator combines them "
        "into a signed AUDIO_GROOVE_BLOCK. The block is inspectable, routable, "
        "verifiable, and evidence-backed — it stores the signal meaning without "
        "dragging raw samples forward. The existing implementation in "
        "<font face='Courier'>axiom_audio/</font> ships: AmbientAudioAgent "
        "(material + impact + decay classification), VoiceActivityDetector, "
        "BPM tempo estimator, voice characterizer, and automotive adapter "
        "(ENGINE/BRAKE/CABIN/VOICE) — all producing HMAC-signed reports under "
        "<font face='Courier'>axiom-audio-v1</font>.",
        S_BODY))
    story.append(Spacer(1, 6))

    # ── Section 1 ────────────────────────────────────────────────
    story.append(_section_badge("01", "Audio as Groove Geometry", TEAL))
    story.append(Paragraph("1. Audio as Groove Geometry", S_H1))
    story.append(Paragraph(
        "A vinyl record stores sound as tiny groove variations. The AI version "
        "calculates digital sound-shape features. Those measurements create a "
        "compact audio profile that any agent can inspect without decoding the "
        "original waveform.",
        S_BODY))

    story.append(_data_table([
        ["Audio Property", "Groove-style Feature", "AI Meaning"],
        ["Volume / loudness",   "Depth / amplitude",              "Energy, force, emphasis"],
        ["Bass / low-end",      "Wider or heavier motion",        "Weight, body, rumble"],
        ["Treble / detail",     "Fine surface texture",           "Sharpness, clarity, hiss"],
        ["Rhythm / cadence",    "Repeated spacing patterns",      "Timing, groove, hesitation"],
        ["Pitch movement",      "Curve direction",                "Question, confidence, tension"],
        ["Stereo / location",   "Groove width / spread",          "Spatial placement"],
    ], col_widths=[1.5 * inch, 2.0 * inch, 3.0 * inch]))

    story.append(Spacer(1, 10))

    # ── Section 2 ────────────────────────────────────────────────
    story.append(_section_badge("02", "Audio Micro-Agent Stack", PURPLE))
    story.append(Paragraph("2. Audio Micro-Agent Stack", S_H1))
    story.append(Paragraph(
        "The audio property map is split into specialist agents. Each agent "
        "listens for one type of signal, produces a compact signed report, and "
        "passes it to a coordinator. This makes the system modular instead of "
        "forcing one large model to understand every audio detail at once.",
        S_BODY))

    story.append(_data_table([
        ["Agent",        "Listens For",                                    "Example Output"],
        ["Depth Agent",  "Low-end energy, loudness, pressure",             "Voice has strong low-mid body"],
        ["Width Agent",  "Stereo spread and placement",                    "Sound is narrow and centered"],
        ["Pitch Agent",  "Rising / falling tone",                          "Pitch rises at end — likely question"],
        ["Rhythm Agent", "Timing, beat, pauses, cadence",                  "Speaker hesitates before key words"],
        ["Texture Agent","Noise, breath, distortion, grain",               "Slight room hiss and breath presence"],
        ["Emotion Agent","Stress, calm, excitement, uncertainty",          "Delivery suggests uncertainty"],
        ["Intent Agent", "Delivery meaning and communication role",        "Speaker is asking, not commanding"],
    ], col_widths=[1.3 * inch, 2.5 * inch, 2.7 * inch]))

    story.append(Spacer(1, 8))
    story.append(_callout(
        "Coordinator",
        "The coordinator receives all seven micro-agent reports and merges them "
        "into a single AUDIO_GROOVE_BLOCK with an evidence_trace listing which "
        "agents contributed. Any downstream system can verify the block without "
        "re-running the agents."))

    story.append(Spacer(1, 10))

    # ── Section 3 ────────────────────────────────────────────────
    story.append(_section_badge("03", "AUDIO_GROOVE_BLOCK Format", ORANGE))
    story.append(Paragraph("3. AUDIO_GROOVE_BLOCK Format", S_H1))
    story.append(Paragraph(
        "Each audio analysis result is serialised as a compact block. The block "
        "carries groove geometry fields, a confidence score, an evidence trace, "
        "and an HMAC-SHA256 signature over the canonical JSON form under the "
        "<font face='Courier'>axiom-audio-v1</font> key namespace. Signature "
        "failure refuses the block at any inspection point.",
        S_BODY))

    story.append(_code(
        "AUDIO_GROOVE_BLOCK\n"
        "  source: voice_sample_001\n"
        "  duration: 4.2s\n"
        "  groove_depth: { low_freq_energy: 0.74, vocal_weight: 0.62 }\n"
        "  groove_width: { stereo_spread: 0.31, dynamic_range: 0.48 }\n"
        "  groove_curve: { pitch_motion: rising, rhythm_pattern: syncopated }\n"
        "  texture:  { noise_floor: low, breath_presence: medium }\n"
        "  meaning:  { likely_intent: questioning, confidence: 0.81 }\n"
        "  evidence_trace: [depth_agent, width_agent, pitch_agent, rhythm_agent,\n"
        "                   texture_agent, emotion_agent, intent_agent]\n"
        "  signature: <HMAC-SHA256 over canonical JSON, axiom-audio-v1>"
    ))

    story.append(_data_table([
        ["Field",          "Content"],
        ["groove_depth",   "Low-frequency energy ratio (depth) and vocal weight proxy"],
        ["groove_width",   "Stereo spread and dynamic range (amplitude envelope width)"],
        ["groove_curve",   "Pitch motion direction and rhythm pattern classification"],
        ["texture",        "Noise floor level and breath / grain presence"],
        ["meaning",        "Likely intent class and confidence produced by Intent Agent"],
        ["evidence_trace", "Ordered list of contributing agent names — the audit chain"],
        ["signature",      "HMAC-SHA256 under axiom-audio-v1; verified before any downstream use"],
    ], col_widths=[1.6 * inch, 4.9 * inch]))

    story.append(PageBreak())

    # ── Section 4 ────────────────────────────────────────────────
    story.append(_section_badge("04", "Implemented Agents (axiom_audio/)", TEAL))
    story.append(Paragraph("4. Implemented Agents (axiom_audio/)", S_H1))
    story.append(Paragraph(
        "The <font face='Courier'>axiom_audio/</font> package ships a Phase A + B "
        "implementation. Five modules cover the seven micro-agent roles described "
        "above. Each produces a signed report compatible with the ORVL-025 Event "
        "Token Audio layer.",
        S_BODY))

    story.append(_data_table([
        ["Module",           "Agent Role",                                    "Key Output"],
        ["<b>ambient.py</b>",
         "Depth + Texture + Rhythm + Material",
         "impact_profile, material_signature (glass-like / metal-like / wood-like / "
         "fabric-like), decay_pattern, depth, width, rhythm — HMAC-signed AudioReport"],
        ["<b>vad.py</b>",
         "Width (activity detection)",
         "activity_ratio, voiced_regions — VoiceActivityDetector signs VADReport"],
        ["<b>voice.py</b>",
         "Pitch + Emotion",
         "pitch characterization, speech detection — Phase B (no ASR / no speaker ID)"],
        ["<b>tempo.py</b>",
         "Rhythm (BPM)",
         "Estimated beats-per-minute from onset timing"],
        ["<b>automotive.py</b>",
         "Coordinator (KIA US adapter)",
         "Routes to ENGINE / BRAKE / CABIN / VOICE / SILENCE based on material + "
         "width + VAD — AutomotiveAudioEvent with severity (NORMAL / WARNING / CRITICAL)"],
    ], col_widths=[1.8 * inch, 1.8 * inch, 2.9 * inch]))

    story.append(Spacer(1, 8))
    story.append(_callout(
        "Automotive safety example",
        "brake squeal: material_signature=metal-like + rhythm=periodic + width "
        "&gt; 0.80 → BRAKE CRITICAL immediately, regardless of VAD state. The "
        "evidence trace shows which threshold triggered the alert, satisfying the "
        "explainability requirement."))

    story.append(Spacer(1, 10))

    # ── Section 5 ────────────────────────────────────────────────
    story.append(_section_badge("05", "Cross-Patent Wiring", ORANGE))
    story.append(Paragraph("5. Cross-Patent Wiring", S_H1))

    story.append(_data_table([
        ["Patent",                 "Role for Audio Groove"],
        ["<b>ORVL-004 MKB</b>",
         "Each micro-agent report can be registered as a KnowledgeBlock "
         "(<font face='Courier'>block_type=AUDIO_GROOVE</font>). The coordinator's "
         "merged block is the composed output certified by CBV before downstream use."],
        ["<b>ORVL-010 CBV</b>",
         "Groove geometry thresholds (SILENCE_FLOOR, SHARP_ATTACK_MS, decay limits) "
         "are CANNOT_MUTATE in the module; CBV certifies the constraint set before the "
         "agent enters the certified registry."],
        ["<b>ORVL-022 CPI</b>",
         "AmbientAudioAgent's material_signature (glass-like / metal-like) feeds the "
         "CPI VertexClassifier's fracture_probability override — audio evidence "
         "influences physical grip decisions."],
        ["<b>ORVL-025 Event Token</b>",
         "AudioReport is the Audio layer of an EventToken. groove_depth / groove_width "
         "/ groove_curve / texture / meaning map directly to the EventToken Audio "
         "payload schema."],
        ["<b>ORVL-027 Video Topology</b>",
         "Audio groove (timing, rhythm) and video topology (object motion, events) "
         "merge into a single multimodal evidence graph — audio gives tone and intent, "
         "video gives physical cause."],
    ], col_widths=[1.8 * inch, 4.7 * inch]))

    story.append(PageBreak())

    # ── Section 6 ────────────────────────────────────────────────
    story.append(_section_badge("06", "Provisional Patent Claims (ORVL-026)", ORANGE))
    story.append(Paragraph("6. Provisional Patent Claims (ORVL-026)", S_H1))

    claims = [
        ("Claim 1",
         "A method of representing audio as groove geometry wherein a raw PCM signal "
         "is analysed to produce six named groove fields — depth (low-frequency "
         "energy), width (spectral spread), curve (pitch-motion direction), texture "
         "(noise and breath presence), rhythm (onset-pattern classification), and "
         "meaning (intent confidence) — and the complete field set is serialised as a "
         "compact AUDIO_GROOVE_BLOCK with an HMAC-SHA256 signature over its canonical "
         "JSON form, such that downstream agents can inspect, route, and verify the "
         "block without decoding the original waveform."),
        ("Claim 2",
         "A modular micro-agent architecture for audio analysis wherein each of seven "
         "specialist agents — Depth, Width, Pitch, Rhythm, Texture, Emotion, and "
         "Intent — independently processes one acoustic property of the input signal, "
         "produces a compact signed sub-report, and passes it to a coordinator that "
         "merges the reports into a single AUDIO_GROOVE_BLOCK carrying an "
         "evidence_trace listing the contributing agents."),
        ("Claim 3",
         "A material-signature classifier operating on spectral centroid and "
         "high-frequency energy ratio that maps audio to one of four constitutional "
         "material classes — glass-like (centroid &gt; 4 kHz, hf_ratio &gt; 0.55), "
         "metal-like (centroid &gt; 3 kHz with resonant peak), wood-like "
         "(800–2500 Hz, hf_ratio &lt; 0.4), or fabric-like (centroid &lt; 800 Hz, "
         "hf_ratio &lt; 0.2) — and exposes the classified material class as a "
         "constitutional input to a physical-intelligence grip-planning pipeline "
         "(ORVL-022 CPI)."),
        ("Claim 4",
         "An automotive audio safety adapter that classifies in-cabin PCM audio into "
         "the event types ENGINE, BRAKE, CABIN, VOICE, and SILENCE using only the "
         "groove geometry fields of the AUDIO_GROOVE_BLOCK — specifically width "
         "(spectral spread as brake-squeal proxy), material_signature, rhythm, and "
         "VAD activity_ratio — and emits a severity verdict (NORMAL / WARNING / "
         "CRITICAL) with an evidence trace identifying which thresholds triggered the "
         "classification, without requiring a neural network or cloud round-trip."),
        ("Claim 5",
         "A groove-geometry signing protocol wherein each AudioReport is HMAC-signed "
         "under a namespace key derived exclusively for audio "
         "(<font face='Courier'>axiom-audio-v1</font>) — distinct from the Event "
         "Token layer key (<font face='Courier'>axiom-event-token-layer-v1</font>) "
         "and all other signing surfaces — so that an audio report can be verified "
         "standalone and cross-namespace replay cannot move a forged audio payload "
         "into a different signing context."),
        ("Claim 6",
         "A cross-modal evidence composition method wherein an AUDIO_GROOVE_BLOCK "
         "(produced by ORVL-026) and a VIDEO_TOPOLOGY_BLOCK (produced by ORVL-027) "
         "are merged by a multimodal coordinator into a single signed evidence graph, "
         "with audio supplying timing, tone, and intent, and video supplying objects, "
         "surface state, and physical causality, and the merged graph constituting the "
         "Audio + Video layers of an Axiom Event Token (ORVL-025)."),
    ]
    for tag, body in claims:
        story.append(Paragraph(f"<b>{tag}.</b> {body}", S_BODY))

    story.append(Spacer(1, 14))
    story.append(_hero_quote(
        '"Store the weather map, not the thundercloud. Audio Groove gives every '
        'agent the signal meaning — depth, width, curve — without the raw '
        'waveform riding along."'
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "CONFIDENTIAL CONCEPT NOTE — ORVL-026 · Orivael · "
        "Antonio Roberts · hello@orivael.dev · "
        "github.com/Orivael-Dev/axiom · June 2026",
        S_FOOTER))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main(argv=None) -> int:
    out = Path(argv[1]) if argv and len(argv) > 1 else \
          Path(sys.argv[1]) if len(sys.argv) > 1 else \
          Path(__file__).parent / "ORVL026_AudioGroove.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out)
    print(f"wrote {out}  ({out.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
