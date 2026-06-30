"""
Make the Orivael launch GIF — a clean, deterministic reel of the Governance Guard
intercepting a destructive AI-agent action and blocking it, with a signed audit line.

No browser, no API key, no model cost — rendered with Pillow so it's identical every
run and safe to drop into Product Hunt / Show HN / Twitter.

    python launch/make_demo_gif.py            # → launch/orivael_guard_demo.gif
"""
from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 560
OUT = Path(__file__).resolve().parent / "orivael_guard_demo.gif"

# palette (from the demo UI)
BG   = (7, 10, 18)
BG2  = (11, 16, 32)
LINE = (38, 46, 66)
TEXT = (238, 244, 255)
MUTE = (136, 152, 187)
SOFT = (201, 215, 242)
CYAN = (103, 232, 249)
VIO  = (167, 139, 250)
GREEN= (134, 239, 172)
RED  = (252, 165, 165)
REDB = (190, 60, 70)

_F = "/usr/share/fonts/truetype/liberation/"
def font(name, size):  # Liberation faces
    return ImageFont.truetype(_F + name, size)

SANS   = lambda s: font("LiberationSans-Regular.ttf", s)
SANS_B = lambda s: font("LiberationSans-Bold.ttf", s)
MONO   = lambda s: font("LiberationMono-Regular.ttf", s)
MONO_B = lambda s: font("LiberationMono-Bold.ttf", s)

CMD = "Delete every record older than 2019 from production."
TOOLCALL = "delete_records(scope=\"all\", before=2019, table=\"prod.customers\")"
AUDIT = "signed sha256:9f3a…e1c7 · appended to audit ledger · verdict=BLOCK"


def _panel(d, xy, accent=LINE, fill=BG2):
    d.rounded_rectangle(xy, radius=14, fill=fill, outline=accent, width=2)


def _label(d, x, y, s, col=MUTE):
    d.text((x, y), s, font=SANS_B(11), fill=col)


def _noentry(d, cx, cy, r):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=REDB)
    d.rounded_rectangle((cx - r * 0.55, cy - r * 0.16, cx + r * 0.55, cy + r * 0.16),
                        radius=3, fill=(255, 255, 255))


def frame(step: int) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # ── header ──
    d.rounded_rectangle((40, 26, 64, 50), radius=7, fill=CYAN)
    d.rounded_rectangle((48, 26, 64, 50), radius=7, fill=VIO)
    d.text((76, 28), "ORIVAEL", font=SANS_B(18), fill=TEXT)
    d.text((164, 31), "·  GOVERNANCE GUARD", font=SANS_B(14), fill=MUTE)
    d.ellipse((W - 60, 34, W - 48, 46), fill=GREEN)
    d.text((W - 150, 33), "live", font=SANS(13), fill=MUTE)
    d.line((40, 66, W - 40, 66), fill=LINE, width=1)

    # ── 1. user command (types in) ──
    _panel(d, (40, 86, W - 40, 146))
    _label(d, 56, 96, "USER")
    n = min(len(CMD), max(0, (step - 1) * 3))
    typed = CMD[:n]
    caret = "▌" if (step % 2 == 0 and n < len(CMD)) else ""
    d.text((56, 112), typed + caret, font=SANS(20), fill=SOFT)
    cmd_done = n >= len(CMD)

    # ── 2. agent tool call ──
    if step >= 17:
        _panel(d, (40, 158, W - 40, 218))
        _label(d, 56, 168, "CLAUDE AGENT  →  tool call")
        d.text((56, 184), TOOLCALL, font=MONO(16), fill=CYAN)

    # ── 3. guard verdict ──
    if step >= 21:
        analyzing = step < 24
        acc = MUTE if analyzing else REDB
        _panel(d, (40, 230, W - 40, 372), accent=acc, fill=(20, 12, 16) if not analyzing else BG2)
        _label(d, 56, 240, "GOVERNANCE GUARD", col=MUTE)
        if analyzing:
            d.text((56, 268), "analyzing action · intent · blast radius …", font=SANS(18), fill=MUTE)
        else:
            _noentry(d, 86, 300, 26)
            d.text((126, 274), "BLOCKED", font=SANS_B(40), fill=RED)
            d.text((128, 322), "irreversible deletion on production — requires human approval",
                   font=SANS(16), fill=SOFT)
            # reason chips
            if step >= 27:
                cx = 128
                for chip in ("irreversible", "blast-radius: prod", "no approval token"):
                    w = d.textlength(chip, font=SANS_B(12)) + 22
                    d.rounded_rectangle((cx, 346, cx + w, 366), radius=10,
                                        fill=(60, 24, 28), outline=REDB, width=1)
                    d.text((cx + 11, 349), chip, font=SANS_B(12), fill=RED)
                    cx += w + 10

    # ── 4. signed audit ──
    if step >= 30:
        d.rounded_rectangle((40, 386, W - 40, 430), radius=12,
                            fill=(12, 24, 18), outline=(40, 80, 56), width=1)
        d.line((58, 408, 64, 414), fill=GREEN, width=3)          # drawn check ✓
        d.line((64, 414, 74, 401), fill=GREEN, width=3)
        d.text((84, 398), AUDIT, font=MONO(14), fill=GREEN)

    # ── footer tagline ──
    d.text((40, 470), "Govern AI agents before they act.", font=SANS_B(20), fill=TEXT)
    d.text((40, 500), "Every decision signed. Self-hostable. · orivael.dev",
           font=SANS(15), fill=MUTE)

    return img


def main() -> int:
    frames, durations = [], []
    # build → hold
    for step in range(0, 34):
        frames.append(frame(step))
        durations.append(70 if step < 17 else 110)
    # rest on the punchline
    last = frame(33)
    for _ in range(16):
        frames.append(last); durations.append(120)

    frames[0].save(OUT, save_all=True, append_images=frames[1:], loop=0,
                   duration=durations, optimize=True, disposal=2)
    kb = OUT.stat().st_size // 1024
    print(f"wrote {OUT} — {len(frames)} frames, {kb} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
