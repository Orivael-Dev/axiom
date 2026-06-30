#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the ORVL-001 Bonded Authority Token documents.

Two outputs, matching the visual style of the other Orivael ORVL notes
(teal hero box, colored section badges, gridded tables, gray code boxes):

  --mode patent  → patents/ORVL001_BondedTokens.pdf        (disclosure + claim seeds)
  --mode brief   → docs/ORVL001_BondedTokens_Brief.pdf     (2-page client/investor)
  (default: build both)

Content is grounded in axiom_event_token/bonded_pair.py. UTF-8 throughout.

Usage:
    pip install reportlab
    python patents/build_orvl001_pdf.py
"""
import argparse
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

# ── Palette (matches existing ORVL PDFs) ──────────────────────────────
TEAL_DARK  = colors.HexColor("#0d4f54")
TEAL       = colors.HexColor("#2c8a90")
TEAL_LIGHT = colors.HexColor("#e6f4f5")
PURPLE     = colors.HexColor("#5a3e8a")
ORANGE     = colors.HexColor("#c87029")
GRAY_LITE  = colors.HexColor("#f4f4f4")
GRAY_BORD  = colors.HexColor("#cccccc")
GRAY_TEXT  = colors.HexColor("#666666")
NAVY       = colors.HexColor("#1d3557")

_ss = getSampleStyleSheet()

def _st(name, parent="Normal", **kw):
    return ParagraphStyle(name=name, parent=_ss[parent], **kw)

S_TITLE  = _st("t", "Title", fontSize=22, leading=26, textColor=TEAL_DARK, spaceAfter=2)
S_SUB    = _st("s", fontSize=12, leading=15, textColor=PURPLE, spaceAfter=10)
S_H      = _st("h", "Heading2", fontSize=13, leading=16, textColor=TEAL_DARK,
               spaceBefore=12, spaceAfter=5)
S_BODY   = _st("b", fontSize=10, leading=14, textColor=colors.HexColor("#222222"), spaceAfter=5)
S_SMALL  = _st("sm", fontSize=8.5, leading=11, textColor=GRAY_TEXT)
S_QUOTE  = _st("q", fontSize=11.5, leading=15, textColor=TEAL_DARK)
S_CODE   = _st("c", "Code", fontSize=8.5, leading=11.5, textColor=NAVY)
S_CELL   = _st("cell", fontSize=9, leading=12)
S_CELLH  = _st("cellh", fontSize=9, leading=12, textColor=colors.white)


def _hero(text):
    cell = Paragraph(text, S_QUOTE)
    t = Table([[cell]], colWidths=[6.5 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TEAL_LIGHT),
        ("LINEBELOW", (0, 0), (-1, -1), 2, TEAL),
        ("LINEABOVE", (0, 0), (-1, -1), 2, TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


def _table(rows, widths, header_bg=NAVY):
    body = [[Paragraph(c, S_CELLH if i == 0 else S_CELL) for c in row]
            for i, row in enumerate(rows)]
    t = Table(body, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("GRID", (0, 0), (-1, -1), 0.5, GRAY_BORD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for r in range(1, len(rows)):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), GRAY_LITE))
    t.setStyle(TableStyle(style))
    return t


def _codebox(lines):
    p = Paragraph("<br/>".join(lines), S_CODE)
    t = Table([[p]], colWidths=[6.5 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GRAY_LITE),
        ("BOX", (0, 0), (-1, -1), 0.5, GRAY_BORD),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GRAY_TEXT)
    canvas.drawString(0.9 * inch, 0.5 * inch,
                      "ORVL-001 — Orivael Bonded Authority Tokens · Confidential draft")
    canvas.drawRightString(7.6 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _doc(path, story):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    d = SimpleDocTemplate(str(path), pagesize=LETTER,
                          leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                          topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                          title="ORVL-001 Bonded Authority Tokens")
    d.build(story, onFirstPage=_footer, onLaterPages=_footer)
    print(f"wrote {path}")


# ── Patent disclosure ─────────────────────────────────────────────────

def build_patent(path):
    s = []
    s += [Paragraph("ORVL-001", S_SUB),
          Paragraph("Bonded Authority Tokens", S_TITLE),
          Paragraph("Co-Signed Paired Tokens with an Atomic, Append-Only State "
                    "Register for Live Revocation of Agent Authority", S_SUB)]
    s += [_table([
        ["Field", "Value"],
        ["Inventor / Architect", "Antonio Roberts"],
        ["Assignee / Project", "Orivael / Axiom Framework"],
        ["Status", "Technical disclosure draft for attorney review — not a filed application"],
        ["Reference impl.", "axiom_event_token/bonded_pair.py"],
    ], [1.6 * inch, 5.0 * inch]), Spacer(1, 8)]

    s += [_hero("Two tokens are minted together, each cryptographically referencing the "
                "other. Their authority does not live in the token bytes — it lives in an "
                "append-only, hash-chained state register. Presenting either half to a gate "
                "resolves the pair's <b>current</b> state, so authority is checked against "
                "live state, not an inherited grant. Flipping the pair to REVOKED ends the "
                "primary's authority instantly — <b>without rotating the primary or its "
                "signing key.</b>")]

    s += [Paragraph("1 · Abstract", S_H),
          Paragraph(
              "Systems and methods are disclosed for governing the authority of software "
              "agents using a bonded pair of cryptographic tokens. Two tokens — a primary "
              "and a mirror — are minted together, share a common pair identifier, and each "
              "carries a deterministic reference to the other. Token authority state is "
              "stored not in the signed token bytes but in an append-only, hash-chained "
              "ledger maintained by a single writer. A gate presented with either half "
              "consults the ledger for the pair's current state and grants, modifies, or "
              "refuses the requested action accordingly. A state transition to a terminal "
              "REVOKED state ends the primary's authority atomically and immediately, "
              "without rotating the primary token or its signing key. The primitive supports "
              "action-with-live-monitor, two-party atomic commit, and long-lived "
              "authorization with instant revocation.", S_BODY)]

    s += [Paragraph("2 · Field", S_H),
          Paragraph("Authorization and access control for autonomous and agentic AI "
                    "systems; cryptographic tokens; revocation; tamper-evident audit "
                    "ledgers; runtime governance of agent actions.", S_BODY)]

    s += [Paragraph("3 · Background &amp; Problem", S_H),
          Paragraph("Conventional bearer credentials (API keys, OAuth tokens, JWTs) bind "
                    "authority into the token itself. Revoking such a credential generally "
                    "requires rotating a key or maintaining an out-of-band denylist, and a "
                    "long-lived grant cannot be atomically paired with a live monitor. For "
                    "agentic systems — where authority must be narrow, observable, and "
                    "revocable mid-task — this is inadequate: a compromised or drifting agent "
                    "keeps acting until its key is rotated, and there is no second party with "
                    "standing to halt it without tearing down the grant.", S_BODY)]

    s += [Paragraph("4 · Summary of the Invention", S_H),
          Paragraph("The bonded authority token separates <b>identity</b> (the signed token "
                    "bytes) from <b>authority state</b> (an external, append-only register):",
                    S_BODY),
          _table([
              ["Element", "Description"],
              ["Bonded pair", "Two tokens minted together via mint_pair(): a primary "
               "(AXIOM-BP-&lt;id&gt;-A) and a mirror (-B), sharing a pair_id, each "
               "referencing the other's token_id."],
              ["Independent signing", "Each token is HMAC-SHA256 signed under "
               "axiom-bonded-pair-token-v1; the ledger under axiom-bonded-pair-ledger-v1."],
              ["State register", "Authority state (ACTIVE_VALIDATED, ACTIVE_PENDING, "
               "SUSPENDED, REVOKED, EXPIRED) lives in a hash-chained ledger, not in the "
               "tokens. transition() is the only mutation surface."],
              ["Live resolution", "A gate presented either half resolves the pair's current "
               "state from the ledger before permitting an action."],
              ["Revocation w/o rotation", "Holding the mirror is sufficient to flip the pair "
               "to REVOKED — the primary and its key are never rotated."],
              ["Tamper-evidence", "The ledger is hash-chained; a single byte flip breaks "
               "verify_chain() from that entry forward."],
          ], [1.5 * inch, 5.1 * inch])]

    s += [PageBreak()]
    s += [Paragraph("5 · Detailed Embodiments", S_H),
          Paragraph("<b>5.1 Action + live monitor.</b> The primary carries the command to "
                    "execute; the mirror is held by a live security monitor. If the monitor "
                    "observes unsafe behavior it flips the pair to REVOKED, short-circuiting "
                    "the primary's authority mid-action — no key rotation, no redeploy.", S_BODY),
          Paragraph("<b>5.2 Two-party atomic commit.</b> Both halves must transition together; "
                    "transition() is the single mutation surface, so neither party can advance "
                    "authority unilaterally. Useful for dual-control / four-eyes operations.", S_BODY),
          Paragraph("<b>5.3 Long-lived authorization with live revocation.</b> The primary is a "
                    "durable grant; the mirror is the revocation handle. Authority can be ended "
                    "instantly by flipping the register, without rotating the long-lived grant or "
                    "its signing key — the property conventional bearer tokens lack.", S_BODY)]

    s += [Paragraph("6 · Representative Data Flow", S_H),
          _codebox([
              "mint_pair(payload_primary, payload_mirror)",
              "  → primary  AXIOM-BP-&lt;pid&gt;-A   (role=primary, partner=-B)   [HMAC]",
              "  → mirror   AXIOM-BP-&lt;pid&gt;-B   (role=mirror,  partner=-A)   [HMAC]",
              "ledger.init_pair(pid)            → state = ACTIVE_VALIDATED   (chained entry)",
              "gate(token)  → ledger.current_state(pid)  → ACTIVE_VALIDATED → ALLOW",
              "ledger.transition(pid, REVOKED) [present mirror]   → chained, signed entry",
              "gate(primary) → ledger.current_state(pid) → REVOKED → DENY  (key unchanged)",
          ])]

    s += [Paragraph("7 · Distinction From Conventional Systems", S_H),
          _table([
              ["Conventional", "Limitation", "Bonded token"],
              ["API key / bearer token", "Revoke = rotate key / denylist", "Revoke = flip "
               "register; key never rotates"],
              ["JWT (state in token)", "Stale until expiry; no live state", "State is an "
               "external live register; resolved per request"],
              ["OAuth grant", "No co-bound monitor with standing to halt", "Mirror halts the "
               "primary atomically"],
              ["Audit log", "Often mutable / after-the-fact", "Append-only, hash-chained; "
               "verify_chain() detects any flip"],
          ], [1.5 * inch, 2.3 * inch, 2.8 * inch])]

    s += [Paragraph("8 · Representative Claim Seed Set", S_H),
          Paragraph("<i>Drafting note: claim seeds for counsel; narrow/broaden after "
                    "prior-art review.</i>", S_SMALL),
          Paragraph("<b>Independent system claim.</b> A system for governing agent authority, "
                    "comprising: one or more processors; a token minting module configured to "
                    "mint a bonded pair of tokens minted together and sharing a pair identifier, "
                    "a first (primary) token and a second (mirror) token each carrying a "
                    "deterministic reference to the other and an independent cryptographic "
                    "signature; an append-only, hash-chained state register storing an authority "
                    "state of the pair separately from the token bytes; and a gate configured to, "
                    "upon presentation of either token, resolve the current authority state of the "
                    "pair from the register and permit, modify, or refuse a requested action based "
                    "thereon, wherein a transition of the register to a revoked state terminates "
                    "the authority of the primary token without rotating the primary token or its "
                    "signing key.", S_BODY),
          Paragraph("<b>Dependent seeds.</b> (2) wherein the mirror token is sufficient to "
                    "effect the revoked transition. (3) wherein both tokens must transition "
                    "together via a single mutation surface (two-party commit). (4) wherein the "
                    "register is hash-chained such that altering any entry breaks verification "
                    "from that entry forward. (5) wherein the mirror is held by a monitoring "
                    "process that flips the pair upon detecting an unsafe action. (6) wherein "
                    "each token and each register entry is HMAC-signed under a distinct derived "
                    "key. (7) wherein the authority state is one of active, pending, suspended, "
                    "revoked, or expired. (8) wherein the agent is an autonomous AI agent and the "
                    "action is a tool call.", S_BODY),
          Paragraph("<b>Independent method claim.</b> A method comprising: minting a primary and "
                    "a mirror token together with a shared pair identifier and mutual references; "
                    "recording an initial authority state in an append-only hash-chained register; "
                    "receiving a request accompanied by either token; resolving the current state "
                    "from the register; and permitting or refusing the request based on the state, "
                    "wherein presenting the mirror transitions the register to a revoked state that "
                    "terminates the primary's authority without rotating the primary or its key.",
                    S_BODY),
          Paragraph("<b>Computer-readable medium claim.</b> A non-transitory medium storing "
                    "instructions that cause the operations of the method claim.", S_BODY)]

    s += [Paragraph("9 · Implementation Notes", S_H),
          Paragraph("HMAC-SHA256 over canonical JSON; tokens and ledger signed under distinct "
                    "namespaces (axiom-bonded-pair-token-v1, axiom-bonded-pair-ledger-v1); "
                    "append-only JSONL ledger with a single writer; verify_chain() validates the "
                    "hash chain. This is a co-signed token pair with an atomic state register — "
                    "not entanglement; the design is defensible to an auditor on those terms.", S_BODY),
          Spacer(1, 6),
          Paragraph("This document should be reviewed by patent counsel before any public "
                    "disclosure or filing.", S_SMALL)]

    _doc(path, s)


# ── Technical brief (2 pages) ─────────────────────────────────────────

def build_brief(path):
    s = []
    s += [Paragraph("ORVL-001 · Technical Brief", S_SUB),
          Paragraph("Bonded Authority Tokens", S_TITLE),
          Paragraph("Revoke an agent's authority instantly — without rotating a single key.",
                    S_SUB)]
    s += [_hero("Most credentials put authority <i>in</i> the token, so revoking one means "
                "rotating a key or maintaining a denylist. Bonded tokens put authority in a "
                "live, tamper-evident register instead. Mint two tokens together — a "
                "<b>grant</b> and a <b>revocation handle</b> — and you can kill the grant "
                "mid-action by flipping the register. The key never changes.")]

    s += [Paragraph("How it works", S_H),
          Paragraph("• <b>Minted as a pair.</b> A primary (the grant) and a mirror (the "
                    "handle) are minted together, each signed, each referencing the other.", S_BODY),
          Paragraph("• <b>State lives outside the token.</b> Authority state — active, "
                    "suspended, revoked — sits in an append-only, hash-chained ledger, not in "
                    "the token bytes.", S_BODY),
          Paragraph("• <b>Checked live, every request.</b> A gate handed either token resolves "
                    "the pair's <i>current</i> state from the ledger before allowing an action — "
                    "so authority is checked against now, not an inherited setup.", S_BODY),
          Paragraph("• <b>Revoke without rotation.</b> Present the mirror, flip the pair to "
                    "REVOKED, and the primary is dead instantly. No key rotation, no redeploy, "
                    "no denylist to propagate.", S_BODY)]

    s += [Paragraph("State register", S_H),
          _codebox([
              "ACTIVE_VALIDATED  ──present mirror──▶  REVOKED   (terminal)",
              "      │                                            ",
              "      └── SUSPENDED / ACTIVE_PENDING / EXPIRED     ",
              "append-only · hash-chained · one byte-flip breaks verify_chain()",
          ])]

    s += [Paragraph("Three ways teams use it", S_H),
          _table([
              ["Pattern", "What it gives you"],
              ["Action + live monitor", "The grant executes; a monitor holds the handle and "
               "can halt it mid-action the instant it misbehaves."],
              ["Two-party commit", "Both halves must move together — built-in dual-control / "
               "four-eyes for high-blast-radius operations."],
              ["Long-lived grant, live kill", "Issue durable authority but keep an instant "
               "off-switch that never touches the underlying key."],
          ], [1.9 * inch, 4.7 * inch])]

    s += [Paragraph("Why it matters for agentic AI", S_H),
          Paragraph("Agent authority has to be narrow, observable, and revocable <i>mid-task</i>. "
                    "A bearer token can't do that — a drifting or compromised agent keeps acting "
                    "until you rotate its key. Bonded tokens give a second party standing to stop "
                    "it atomically, and every transition is signed into a tamper-evident chain an "
                    "auditor can verify. It pairs directly with Axiom's guest-key delegation "
                    "(scoped, expiring grants) and signed audit ledgers.", S_BODY)]

    s += [Paragraph("Status", S_H),
          Paragraph("Implemented in <font face='Courier'>axiom_event_token/bonded_pair.py</font> "
                    "with a CLI (<font face='Courier'>axiom_bonded_pair_cli.py</font>), a runnable "
                    "demo, and an integration test suite. Patent disclosure: ORVL-001.", S_BODY),
          Spacer(1, 6),
          Paragraph("Orivael / Axiom Framework · Confidential.", S_SMALL)]

    _doc(path, s)


def main():
    ap = argparse.ArgumentParser(description="Build ORVL-001 bonded-token PDFs")
    ap.add_argument("--mode", choices=["patent", "brief", "both"], default="both")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.mode in ("patent", "both"):
        build_patent(root / "patents" / "ORVL001_BondedTokens.pdf")
    if args.mode in ("brief", "both"):
        build_brief(root / "docs" / "ORVL001_BondedTokens_Brief.pdf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
