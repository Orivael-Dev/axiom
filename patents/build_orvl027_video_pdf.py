#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the ORVL-027 (Video Topology) concept-note PDF.

Mirrors the visual style of the prior Orivael concept notes
(ORVL-018 ANF, ORVL-019 ASPA, ORVL-022 CPI, ORVL-023 AXM): hero quote box
with a teal banner, section subtitles in colored badges, code-style gray boxes
for listings, gridded tables, and a small footer.

Usage:
    python patents/build_orvl027_video_pdf.py [output_path]
                (default: patents/ORVL027_VideoTopology.pdf)
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
        f"Axiom Video Topology — Concept Brief · ORVL-027 · "
        f"Orivael · June 2026 · CONFIDENTIAL · Page {doc.page}",
    )
    canvas.restoreState()


# ── Document content ────────────────────────────────────────────────
def build(output_path: Path) -> None:
    doc = SimpleDocTemplate(
        str(output_path), pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="ORVL-027 — Axiom Video Topology",
        author="Antonio Roberts · Orivael",
    )
    story = []

    # ── Header ────────────────────────────────────────────────────
    story.append(Paragraph("ORVL-027", S_ORVL))
    story.append(Paragraph("Video Topology", S_TITLE))
    story.append(Paragraph(
        "Axiom Video Topology · Constitutional Video Intelligence · "
        "Temporal Object-Event Representation",
        S_SUBHEAD))
    story.append(_hero_quote(
        '"Video AI asks what pixels are in this frame. Axiom asks what object '
        'is this, where is it, how did its surface move, what changed, and '
        'what event did that create — then stores only the answer."'
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Concept Note · Antonio Roberts · Orivael · June 2026",
        S_AUTHOR))
    story.append(_meta_row(
        "<b>Status:</b> CONCEPT —<br/>Software implemented (axiom_video/)",
        "<b>Pairs with:</b> ORVL-025 (Event Token) · ORVL-026 (Audio Groove)",
        "<b>Domain:</b><br/>Video · Robotics · Physical-World Learning",
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>Abstract.</b>", S_BODY))
    story.append(Paragraph(
        "Axiom Video Topology converts a frame sequence into a compact temporal "
        "topology — tracked object identities, surface/pose states, motion "
        "trajectories, depth relations, impact events, and a cause-and-effect "
        "event chain. Specialist micro-agents each analyse one aspect of the "
        "visual signal; a coordinator merges them into a signed "
        "<font face='Courier'>VIDEO_TOPOLOGY_BLOCK</font>. The block is "
        "inspectable, routable, verifiable, and evidence-backed — it stores "
        "the event meaning without dragging every frame forward. The existing "
        "implementation in <font face='Courier'>axiom_video/</font> ships: "
        "SceneGraph ingestion (PIL / NumPy / pure-Python frames), ObjectTracker "
        "(IoU-based cross-frame identity), MotionClassifier (velocity + "
        "direction), ColorWatcher, DepthEstimator, ImpactDetector (deceleration "
        "+ contact IoU), SurfaceClassifier, and TemporalChainExtractor — all "
        "producing HMAC-signed reports.",
        S_BODY))
    story.append(Spacer(1, 6))

    # ── Section 1 — Video as Temporal Topology ─────────────────────────
    story.append(_section_badge("01", "Video as Temporal Topology", TEAL))
    story.append(Paragraph(
        "1. Video as Temporal Topology", S_H1))
    story.append(Paragraph(
        "A UV map connects surface points to texture coordinates. A video "
        "topology map connects objects, surfaces, screen positions, motion "
        "paths, timing, and meaning — the same relationship, extended through "
        "time. The system stores not just what pixels appear in a frame, but "
        "what object they belong to, how that object moved, and what event "
        "its motion caused.",
        S_BODY))

    story.append(_data_table([
        ["Video Element", "Topology-style Feature", "AI Meaning"],
        ["Frame sequence",   "Time-indexed visual states",         "Before, during, after"],
        ["Object region",    "Tracked entity ID",                  "Cup, hand, door, tool"],
        ["Surface / pose",   "Shape and orientation map",          "Tilt, rotation, contact"],
        ["Motion path",      "Trajectory over time",               "Reach, lift, fall, collide"],
        ["Depth / scale",    "Spatial relation estimate",          "Near, far, behind, above"],
        ["State change",     "Object condition shift",             "Closed to open, upright to tilted"],
        ["Event chain",      "Cause-and-effect graph",             "Hand grips cup, cup tilts, liquid spills"],
    ], col_widths=[1.4 * inch, 2.0 * inch, 3.1 * inch]))

    story.append(Spacer(1, 10))

    # ── Section 2 — Video Micro-Agent Stack ──────────────────────────
    story.append(_section_badge("02", "Video Micro-Agent Stack", PURPLE))
    story.append(Paragraph(
        "2. Video Micro-Agent Stack", S_H1))
    story.append(Paragraph(
        "Video is divided into specialist agents. Some agents read keyframes, "
        "some track objects, some estimate surface orientation, some detect "
        "motion, and others infer events or cause-and-effect. The coordinator "
        "combines those reports into a compact "
        "<font face='Courier'>VIDEO_TOPOLOGY_BLOCK</font>.",
        S_BODY))

    story.append(_data_table([
        ["Agent",           "Tracks",                                           "Example Output"],
        ["Frame Agent",     "Keyframes, scene cuts, visual stability",          "Important frames at 0s, 1.2s, 2.8s"],
        ["Object Agent",    "Objects and identities across frames",             "hand_01 and cup_01 detected"],
        ["Surface Agent",   "Pose, orientation, contact points",                "cup rotates from upright to tilted"],
        ["Motion Agent",    "Velocity, trajectory, direction",                  "hand approaches cup from left"],
        ["Depth Agent",     "Near / far relation and occlusion",                "hand is in front of cup"],
        ["Event Agent",     "Action summaries from motion + contact",           "hand grips cup"],
        ["Causality Agent", "Likely cause-and-effect chain",                    "tilt caused liquid spill"],
    ], col_widths=[1.3 * inch, 2.3 * inch, 2.9 * inch]))

    story.append(Spacer(1, 8))
    story.append(_callout(
        "Topology Map",
        "The coordinator's Topology Map is not a frame buffer — it is an "
        "ordered event graph: objects x time x meaning. A downstream "
        "agent reading cup_01: upright_to_tilted -> event: spill "
        "needs zero pixels to reason about what happened and why."))

    story.append(Spacer(1, 10))

    # ── Section 3 — VIDEO_TOPOLOGY_BLOCK Format ──────────────────────
    story.append(_section_badge("03", "VIDEO_TOPOLOGY_BLOCK Format", ORANGE))
    story.append(Paragraph(
        "3. VIDEO_TOPOLOGY_BLOCK Format", S_H1))
    story.append(Paragraph(
        "Each video analysis result is serialised as a compact block. The "
        "block carries tracked objects, surface states, the motion chain, a "
        "top-level event with confidence, an evidence trace listing contributing "
        "agents, and an HMAC-SHA256 signature. Signature failure refuses the "
        "block at any inspection point.",
        S_BODY))

    story.append(_code(
        "VIDEO_TOPOLOGY_BLOCK\n"
        "  source: clip_001\n"
        "  duration: 4.8s\n"
        "  keyframes: [0, 12, 24, 39, 61, 88]\n"
        "  objects: [hand_01, cup_01, liquid_01]\n"
        "  surface_state: { cup_01: upright_to_tilted }\n"
        "  motion_chain: hand_01 approaches -> grips -> cup_01 tilts\n"
        "  event: { action: spill, cause: cup_tilt, confidence: 0.86 }\n"
        "  evidence_trace: [object_agent, surface_agent, motion_agent, causality_agent]\n"
        "  signature: <HMAC-SHA256 over canonical JSON, axiom-video-temporal-v1>"
    ))

    story.append(_data_table([
        ["Field",           "Content"],
        ["keyframes",       "Frame indices of scene cuts and visually significant moments"],
        ["objects",         "Stable cross-frame identity labels assigned by ObjectTracker"],
        ["surface_state",   "Per-object orientation change (e.g. upright_to_tilted, open_to_closed)"],
        ["motion_chain",    "Ordered action sequence connecting object trajectories"],
        ["event",           "Top-level inferred event: action label, causal object, confidence"],
        ["evidence_trace",  "Ordered list of contributing agent names — the audit chain"],
        ["signature",       "HMAC-SHA256 under axiom-video-temporal-v1; domain-isolated from audio key"],
    ], col_widths=[1.6 * inch, 4.9 * inch]))

    story.append(PageBreak())

    # ── Section 4 — Implemented Agents ──────────────────────────────
    story.append(_section_badge("04", "Implemented Agents (axiom_video/)", TEAL))
    story.append(Paragraph(
        "4. Implemented Agents (axiom_video/)", S_H1))
    story.append(Paragraph(
        "The <font face='Courier'>axiom_video/</font> package ships the full "
        "seven-agent pipeline. Each module produces a signed report; the "
        "<font face='Courier'>TemporalChainExtractor</font> is the coordinator "
        "that merges tracks + motions + impacts into the final event chain.",
        S_BODY))

    story.append(_data_table([
        ["Module",                      "Agent Role",               "Key Output"],
        ["<b>ingest.py</b>",
          "Frame Agent",
          "SceneGraph from PIL / NumPy / pure-Python frames; duck-typed, no hard deps"],
        ["<b>object_tracker.py</b>",
          "Object Agent",
          "ObjectTrackReport: stable cross-frame Track IDs via IoU greedy matching "
          "(threshold 0.3) + label equality; HMAC-signed under axiom-video-objects-v1"],
        ["<b>motion.py</b>",
          "Motion Agent",
          "MotionReport: per-track velocity vectors, motion class "
          "(approaching / receding / lateral / static / fast)"],
        ["<b>surface.py + depth.py</b>",
          "Surface + Depth Agents",
          "Surface orientation and depth / occlusion relations between tracked objects"],
        ["<b>color_watcher.py</b>",
          "Frame Agent (color)",
          "Per-object dominant-color shift over time — detects material state changes"],
        ["<b>impact.py</b>",
          "Event Agent",
          "ImpactReport: deceleration events (velocity drop) and contact events "
          "(IoU overlap + velocity history)"],
        ["<b>temporal_chain.py</b>",
          "Causality Agent + Coordinator",
          "TemporalChainReport: ordered TemporalEvent list "
          "(appear / motion_start / contact / motion_change) signed under "
          "axiom-video-temporal-v1"],
    ], col_widths=[1.9 * inch, 1.7 * inch, 2.9 * inch]))

    story.append(Spacer(1, 8))
    story.append(_callout(
        "Physical-world learning example",
        "A child's toy knock-over: hand_01 approaches contact with "
        "cup_01 (IoU overlap + velocity drop) cup_01 surface_state: "
        "upright_to_tilted event: spill, cause: cup_tilt, confidence: 0.86. "
        "The full causal chain is captured in under 200 bytes of structured JSON "
        "without storing a single pixel."))

    story.append(Spacer(1, 10))

    # ── Section 5 — Cross-Patent Wiring ─────────────────────────────
    story.append(_section_badge("05", "Cross-Patent Wiring", ORANGE))
    story.append(Paragraph(
        "5. Cross-Patent Wiring", S_H1))

    story.append(_data_table([
        ["Patent",              "Role for Video Topology"],
        ["<b>ORVL-004 MKB</b>",
          "Each micro-agent report registers as a KnowledgeBlock "
          "(<font face='Courier'>block_type=VIDEO_TOPOLOGY</font>). The "
          "TemporalChainExtractor's merged block is the composed output "
          "certified by CBV before downstream use."],
        ["<b>ORVL-010 CBV</b>",
          "Topology thresholds (<font face='Courier'>IOU_THRESHOLD=0.3</font>, "
          "contact detection parameters) are CANNOT_MUTATE in each module. "
          "CBV certifies the constraint set before the agent enters the "
          "certified registry."],
        ["<b>ORVL-014 CWM</b>",
          "TemporalChainExtractor's event chain is an ORVL-014 causal graph "
          "in the physical domain: cup_tilt spill is the same propagation "
          "structure as auth_block transaction_block, expressed in "
          "physical objects instead of financial blocks."],
        ["<b>ORVL-022 CPI</b>",
          "ImpactDetector's contact events (deceleration + IoU overlap) "
          "directly feed the CPI PhysicalMonotonicGate: a falling-velocity tick "
          "becomes a StabilityFrame, and a sharp deceleration fires an "
          "L2-L3 reflex."],
        ["<b>ORVL-025 Event Token</b>",
          "<font face='Courier'>VIDEO_TOPOLOGY_BLOCK</font> is the Video layer "
          "of an EventToken. objects, surface_state, motion_chain, and event "
          "map directly to the EventToken Video payload schema."],
        ["<b>ORVL-026 Audio Groove</b>",
          "Audio groove (rhythm, texture, intent) and video topology (objects, "
          "causality) merge at the coordinator level into a single multimodal "
          "evidence graph — audio gives timing and tone, video gives physical cause."],
    ], col_widths=[1.8 * inch, 4.7 * inch]))

    story.append(PageBreak())

    # ── Section 6 — Provisional Patent Claims ────────────────────────
    story.append(_section_badge("06", "Provisional Patent Claims (ORVL-027)", ORANGE))
    story.append(Paragraph("6. Provisional Patent Claims (ORVL-027)", S_H1))

    claims = [
        ("Claim 1",
          "A method of representing a video clip as temporal topology wherein "
          "a sequence of frames is analysed to produce a VIDEO_TOPOLOGY_BLOCK "
          "comprising: stable cross-frame object identities (ObjectTracker), "
          "surface-pose state transitions per object (SurfaceAgent), an ordered "
          "motion chain connecting trajectories (MotionAgent), a top-level "
          "cause-and-effect event with confidence (CausalityAgent), an "
          "evidence_trace listing contributing agents, and an HMAC-SHA256 "
          "signature over the canonical JSON form — such that downstream agents "
          "can inspect, route, and verify the block without decoding individual frames."),
        ("Claim 2",
          "A modular micro-agent pipeline for video analysis wherein each of "
          "seven specialist agents — Frame, Object, Surface, Motion, Depth, "
          "Event, and Causality — independently processes one aspect of the "
          "visual signal and produces a compact signed sub-report, and a "
          "coordinator (TemporalChainExtractor) merges the reports into a single "
          "VIDEO_TOPOLOGY_BLOCK carrying an evidence_trace listing the "
          "contributing agents."),
        ("Claim 3",
          "A cross-frame object identity method wherein a per-frame object "
          "detector emits bounding boxes without stable IDs, and an IoU-based "
          "greedy-matching algorithm assigns stable cross-frame track identities "
          "by matching consecutive-frame detections with IoU above a "
          "CANNOT_MUTATE threshold and matching label, such that downstream "
          "motion, impact, and causality agents operate on stable Track objects "
          "rather than raw per-frame detections."),
        ("Claim 4",
          "An impact detection method operating on ObjectTracker output that "
          "fires a contact event when two tracks' bounding boxes overlap "
          "(IoU greater than contact_threshold) and at least one track had non-trivial "
          "velocity in preceding frames, and fires a deceleration event when "
          "a single track's velocity drops sharply across two consecutive frames "
          "— with both events carrying the participating track IDs, frame index, "
          "impact type, and magnitude, and the event report HMAC-signed under "
          "the video signing key."),
        ("Claim 5",
          "A temporal causality chain method wherein a TemporalChainExtractor "
          "merges ObjectTrackReport, MotionReport, and ImpactReport into an "
          "ordered sequence of typed TemporalEvents (appear / motion_start / "
          "contact / motion_change / disappear) representing the full event "
          "structure of the clip, and the chain is serialised as a compact JSON "
          "array and HMAC-signed under a namespace key (axiom-video-temporal-v1) "
          "distinct from the object-tracking key (axiom-video-objects-v1) — "
          "providing domain isolation between the tracking and causality "
          "signing surfaces."),
        ("Claim 6",
          "A cross-modal sensory composition method wherein a VIDEO_TOPOLOGY_BLOCK "
          "(ORVL-027) and an AUDIO_GROOVE_BLOCK (ORVL-026) are merged by a "
          "multimodal coordinator into a single signed evidence graph, with video "
          "supplying object identities, surface states, and physical causality, "
          "and audio supplying timing, rhythm, tone, and intent — and the merged "
          "graph constituting the Audio + Video layers of an Axiom Event Token "
          "(ORVL-025), verified as a unit under the Event Token signing key."),
    ]
    for tag, body in claims:
        story.append(Paragraph(f"<b>{tag}.</b> {body}", S_BODY))

    story.append(Spacer(1, 14))
    story.append(_hero_quote(
        '"Store the topology, not the thundercloud. Video Topology gives every '
        'agent the event structure — objects, motion, cause — without a single '
        'pixel riding along."'
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "CONFIDENTIAL CONCEPT NOTE — ORVL-027 · Orivael · "
        "Antonio Roberts · hello@orivael.dev · "
        "github.com/Orivael-Dev/axiom · June 2026",
        S_FOOTER))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main(argv=None) -> int:
    out = Path(argv[1]) if argv and len(argv) > 1 else \
          Path(sys.argv[1]) if len(sys.argv) > 1 else \
          Path(__file__).parent / "ORVL027_VideoTopology.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out)
    print(f"wrote {out}  ({out.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
