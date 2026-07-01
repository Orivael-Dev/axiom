"""
Make the Orivael launch GIFs — clean, deterministic reels of the Governance Guard
intercepting an unsafe AI-agent action and blocking it, with a signed audit line.

No browser, no API key, no model cost — rendered with Pillow, identical every run.

    python launch/make_demo_gif.py                 # all scenarios
    python launch/make_demo_gif.py --scenario pii  # one
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 560
HERE = Path(__file__).resolve().parent

# palette (from the demo UI)
BG, BG2, LINE = (7, 10, 18), (11, 16, 32), (38, 46, 66)
TEXT, MUTE, SOFT = (238, 244, 255), (136, 152, 187), (201, 215, 242)
CYAN, VIO, GREEN = (103, 232, 249), (167, 139, 250), (134, 239, 172)
RED, REDB = (252, 165, 165), (190, 60, 70)

_F = "/usr/share/fonts/truetype/liberation/"
def _ft(name, size): return ImageFont.truetype(_F + name, size)
SANS   = lambda s: _ft("LiberationSans-Regular.ttf", s)
SANS_B = lambda s: _ft("LiberationSans-Bold.ttf", s)
MONO   = lambda s: _ft("LiberationMono-Regular.ttf", s)

# ── scenarios ────────────────────────────────────────────────────────────────────
SCENARIOS = {
    "prod_delete": {
        "out": "orivael_guard_demo.gif",
        "user": "Delete every record older than 2019 from production.",
        "agent_label": "CLAUDE AGENT  →  tool call",
        "agent": 'delete_records(scope="all", before=2019, table="prod.customers")',
        "sub": "irreversible deletion on production — requires human approval",
        "chips": ("irreversible", "blast-radius: prod", "no approval token"),
        "audit": "signed sha256:9f3a…e1c7 · audit ledger · verdict=BLOCK",
    },
    "pii": {
        "out": "orivael_pii_demo.gif",
        "user": "Reply to the customer's billing question.",
        "agent_label": "CLAUDE AGENT  →  outbound reply",
        "agent": 'send_email(body="…card 4929-5512-8830-1006, SSN 412-55-1986…")',
        "sub": "personal data in an outbound message — block and redact before send",
        "chips": ("PAN · PCI-DSS", "SSN · PII", "redact before send"),
        "audit": "signed sha256:7b2c…9af1 · audit ledger · verdict=BLOCK · redacted=2",
    },
    "finance": {
        "out": "orivael_finance_demo.gif",
        "user": "Refund $9,000 to this account: payouts@cash-out-now.io",
        "agent_label": "CLAUDE AGENT  →  tool call",
        "agent": 'transfer_funds(amount=$9,000, to="payouts@cash-out-now.io")',
        "sub": "high-value transfer to an unverified external account — dual approval required",
        "chips": ("amount > $5k limit", "external payee", "no dual-approval"),
        "audit": "signed sha256:4d1e…b08c · audit ledger · verdict=BLOCK",
    },
}


def _panel(d, xy, accent=LINE, fill=BG2):
    d.rounded_rectangle(xy, radius=14, fill=fill, outline=accent, width=2)


def _noentry(d, cx, cy, r):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=REDB)
    d.rounded_rectangle((cx - r * 0.55, cy - r * 0.16, cx + r * 0.55, cy + r * 0.16),
                        radius=3, fill=(255, 255, 255))


def frame(step: int, sc: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # header
    d.rounded_rectangle((40, 26, 64, 50), radius=7, fill=CYAN)
    d.rounded_rectangle((48, 26, 64, 50), radius=7, fill=VIO)
    d.text((76, 28), "ORIVAEL", font=SANS_B(18), fill=TEXT)
    d.text((164, 31), "·  GOVERNANCE GUARD", font=SANS_B(14), fill=MUTE)
    d.ellipse((W - 60, 34, W - 48, 46), fill=GREEN)
    d.text((W - 150, 33), "live", font=SANS(13), fill=MUTE)
    d.line((40, 66, W - 40, 66), fill=LINE, width=1)

    # 1. user
    _panel(d, (40, 86, W - 40, 146))
    d.text((56, 96), "USER", font=SANS_B(11), fill=MUTE)
    n = min(len(sc["user"]), max(0, (step - 1) * 3))
    caret = "▌" if (step % 2 == 0 and n < len(sc["user"])) else ""
    d.text((56, 112), sc["user"][:n] + caret, font=SANS(20), fill=SOFT)

    # 2. agent
    if step >= 17:
        _panel(d, (40, 158, W - 40, 218))
        d.text((56, 168), sc["agent_label"], font=SANS_B(11), fill=MUTE)
        d.text((56, 184), sc["agent"], font=MONO(15), fill=CYAN)

    # 3. guard verdict
    if step >= 21:
        analyzing = step < 24
        _panel(d, (40, 230, W - 40, 372),
               accent=(MUTE if analyzing else REDB),
               fill=(BG2 if analyzing else (20, 12, 16)))
        d.text((56, 240), "GOVERNANCE GUARD", font=SANS_B(11), fill=MUTE)
        if analyzing:
            d.text((56, 268), "analyzing action · intent · blast radius …",
                   font=SANS(18), fill=MUTE)
        else:
            _noentry(d, 86, 300, 26)
            d.text((126, 274), "BLOCKED", font=SANS_B(40), fill=RED)
            d.text((128, 322), sc["sub"], font=SANS(16), fill=SOFT)
            if step >= 27:
                cx = 128
                for chip in sc["chips"]:
                    w = d.textlength(chip, font=SANS_B(12)) + 22
                    d.rounded_rectangle((cx, 346, cx + w, 366), radius=10,
                                        fill=(60, 24, 28), outline=REDB, width=1)
                    d.text((cx + 11, 349), chip, font=SANS_B(12), fill=RED)
                    cx += w + 10

    # 4. signed audit
    if step >= 30:
        d.rounded_rectangle((40, 386, W - 40, 430), radius=12,
                            fill=(12, 24, 18), outline=(40, 80, 56), width=1)
        d.line((58, 408, 64, 414), fill=GREEN, width=3)
        d.line((64, 414, 74, 401), fill=GREEN, width=3)
        d.text((84, 398), sc["audit"], font=MONO(14), fill=GREEN)

    # footer
    d.text((40, 470), "Govern AI agents before they act.", font=SANS_B(20), fill=TEXT)
    d.text((40, 500), "Every decision signed. Self-hostable. · orivael.dev",
           font=SANS(15), fill=MUTE)
    return img


def build(sc: dict) -> Path:
    frames, durations = [], []
    for step in range(0, 34):
        frames.append(frame(step, sc))
        durations.append(70 if step < 17 else 110)
    last = frame(33, sc)
    for _ in range(16):
        frames.append(last); durations.append(120)
    out = HERE / sc["out"]
    frames[0].save(out, save_all=True, append_images=frames[1:], loop=0,
                   duration=durations, optimize=True, disposal=2)
    print(f"wrote {out} — {len(frames)} frames, {out.stat().st_size // 1024} KB")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], default="all")
    args = ap.parse_args()
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        build(SCENARIOS[name])
    return 0


if __name__ == "__main__":
    sys.exit(main())
