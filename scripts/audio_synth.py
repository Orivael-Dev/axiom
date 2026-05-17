#!/usr/bin/env python3
"""Synthesize a labeled audio library to disk.

Produces WAV files you can play in any media player (VLC, Windows
Media Player, QuickTime, the browser) to verify each stimulus sounds
like what its label claims. Same generators the harness + tempo
tests use, so what you hear in your laptop's media player is exactly
what the classifiers run on.

Usage:

  python3 scripts/audio_synth.py --out ./samples
      # Writes ~20 clips into ./samples/{glass-like,metal-like,wood-like,
      # fabric-like,background,metronome}/*.wav

  python3 scripts/audio_synth.py --out ./samples --only metronome
      # Writes only the metronome set (5 BPMs)

Then `scp -r box:axiom/samples ./` to your laptop and play.

This is intentionally separate from `audio_harness.py`:
  - audio_synth.py    → produce stimuli for HUMAN listening
  - audio_harness.py  → measure CLASSIFIER accuracy on labeled stimuli
"""
from __future__ import annotations

import argparse
import struct
import sys
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Reuse the harness synthesizers — single source of truth so what we
# write to disk is exactly what the harness + tests use.
import audio_harness as h

# Reuse the tempo test's metronome synthesizer too.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "_tempo_tests", REPO_ROOT / "tests" / "test_axiom_tempo.py"
)
_tt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tt)
synth_metronome = _tt.synth_metronome
synth_random_clicks = _tt.synth_random_clicks


SAMPLE_RATE = h.SAMPLE_RATE


# Voice stimuli — reuse the same synth helper the voice tests use,
# pulled via the same importlib trick that picks up the metronome.
_vspec = importlib.util.spec_from_file_location(
    "_voice_tests", REPO_ROOT / "tests" / "test_axiom_voice.py"
)
_vt = importlib.util.module_from_spec(_vspec)
_vspec.loader.exec_module(_vt)
synth_voice_at_pitch = _vt.synth_voice_at_pitch


def _voice_with_silence_padding(f0_hz: float, voice_s: float = 1.5,
                                lead_s: float = 0.6, trail_s: float = 0.6,
                                jitter: float = 0.05) -> list[float]:
    """Voice clip wrapped in dead air — illustrates the VAD/gate's job."""
    pre = [0.0] * int(lead_s * SAMPLE_RATE)
    post = [0.0] * int(trail_s * SAMPLE_RATE)
    return pre + synth_voice_at_pitch(f0_hz, voice_s, jitter) + post


CATEGORY_BUILDERS = {
    "glass-like":  [("glass_01", h.synth_glass, (1,)),
                    ("glass_02", h.synth_glass, (2,)),
                    ("glass_03", h.synth_glass, (3,)),
                    ("glass_04", h.synth_glass, (4,))],
    "metal-like":  [("metal_01", h.synth_metal, (11,)),
                    ("metal_02", h.synth_metal, (12,)),
                    ("metal_03", h.synth_metal, (13,))],
    "wood-like":   [("wood_01",  h.synth_wood,  (21,)),
                    ("wood_02",  h.synth_wood,  (22,)),
                    ("wood_03",  h.synth_wood,  (23,))],
    "fabric-like": [("fabric_01", h.synth_fabric, (31,)),
                    ("fabric_02", h.synth_fabric, (32,))],
    "background":  [("background_01", h.synth_background, (41,)),
                    ("background_02", h.synth_background, (42,))],
    "metronome":   [(f"metronome_{bpm}bpm", synth_metronome, (bpm,))
                    for bpm in (60, 90, 120, 150, 180)] + [
                    ("metronome_random_clicks", synth_random_clicks, ())],
    "voice":       [("voice_low_110hz",       synth_voice_at_pitch, (110,)),
                    ("voice_mid_180hz",       synth_voice_at_pitch, (180,)),
                    ("voice_high_280hz",      synth_voice_at_pitch, (280,)),
                    ("voice_monotone",        synth_voice_at_pitch, (180, 1.5, 0.0)),
                    ("voice_excited",         synth_voice_at_pitch, (180, 1.5, 0.6)),
                    ("voice_with_silence_padding",
                                              _voice_with_silence_padding, (180,))],
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out", type=Path, default=Path("./samples"),
                    help="Output directory (default: ./samples)")
    ap.add_argument("--only", action="append", default=[],
                    choices=list(CATEGORY_BUILDERS),
                    help="Only generate this category (repeatable). "
                         "Default: generate every category.")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    categories = args.only or list(CATEGORY_BUILDERS)

    total = 0
    for cat in categories:
        cat_dir = args.out / cat
        cat_dir.mkdir(exist_ok=True)
        for name, fn, fn_args in CATEGORY_BUILDERS[cat]:
            samples = fn(*fn_args)
            out_path = cat_dir / f"{name}.wav"
            _write_wav(out_path, samples)
            total += 1
            print(f"  {out_path}  ({len(samples) / SAMPLE_RATE:.2f}s)")

    print()
    print(f"  Wrote {total} clip(s) to {args.out}")
    print("  Copy to your laptop:")
    print(f"      scp -r <your-box>:{args.out.resolve()} ./")
    print("  Then double-click any WAV in your file browser.")
    print()
    return 0


def _write_wav(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for s in samples:
            s = max(-1.0, min(1.0, s))
            frames.extend(struct.pack("<h", int(s * 32767)))
        w.writeframes(bytes(frames))


if __name__ == "__main__":
    sys.exit(main())
