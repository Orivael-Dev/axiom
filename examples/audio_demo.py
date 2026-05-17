#!/usr/bin/env python3
"""Synthesize a "glass cup shattered" clip in-process and run the ambient
audio agent on it. Prints the signed AudioReport as JSON.

Two reasons this synthesizes instead of reading a real recording:

  1. We commit zero binary fixtures — stdlib-only, repo stays small.
  2. The Phase A acceptance test is to prove the classifier produces
     the right CATEGORICAL output (sharp_transient + glass-like +
     scattered_fragments) on a stimulus we control end-to-end.

Run:
    export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
    python3 examples/audio_demo.py
"""
from __future__ import annotations

import math
import os
import random
import struct
import sys
import tempfile
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SAMPLE_RATE = 16_000


def _write_wav(path: str, samples: list[float]) -> None:
    """Write 16-bit mono PCM. Samples are clipped to [-1, 1]."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for s in samples:
            s_clipped = max(-1.0, min(1.0, s))
            frames.extend(struct.pack("<h", int(s_clipped * 32767)))
        w.writeframes(bytes(frames))


def synthesize_glass_shatter() -> list[float]:
    """Build a plausible glass-shatter stimulus.

    Anatomy:
      1. Sharp transient at t=0 — 5ms broadband click
      2. High-frequency tone-clusters at 4–8 kHz decaying over 200ms
      3. Three secondary onsets (fragments scattering) at 80ms / 160ms / 280ms
      4. Quiet noise floor afterward (1s total length)
    """
    rng = random.Random(42)
    n_samples = int(1.0 * SAMPLE_RATE)
    out = [0.0] * n_samples

    # 1. Sharp broadband click — 5ms of white noise
    click_len = int(0.005 * SAMPLE_RATE)
    for i in range(click_len):
        out[i] += (rng.random() * 2 - 1) * 0.9

    # 2. High-frequency tone clusters (4 kHz / 5.5 kHz / 7 kHz) decaying
    for tone_hz in (4000, 5500, 7000):
        for i in range(int(0.2 * SAMPLE_RATE)):
            t = i / SAMPLE_RATE
            decay = math.exp(-t * 15)  # fast exponential decay
            out[i] += 0.25 * math.sin(2 * math.pi * tone_hz * t) * decay

    # 3. Three secondary impacts (scattered fragments)
    for delay_s in (0.080, 0.160, 0.280):
        start = int(delay_s * SAMPLE_RATE)
        for i in range(int(0.04 * SAMPLE_RATE)):  # 40ms each
            if start + i >= n_samples:
                break
            # High-frequency tinkle: noise gated by exponential decay
            t = i / SAMPLE_RATE
            decay = math.exp(-t * 80)
            out[start + i] += (rng.random() * 2 - 1) * 0.5 * decay

    # 4. Quiet noise floor
    for i in range(n_samples):
        out[i] += (rng.random() * 2 - 1) * 0.003

    return out


def main() -> int:
    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit(
            "AXIOM_MASTER_KEY must be set. Generate one:\n"
            "  export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
        )

    from axiom_audio import classify_clip

    samples = synthesize_glass_shatter()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        _write_wav(tmp_path, samples)
        report = classify_clip(tmp_path)
        print(report.to_json(indent=2))
        print(file=sys.stderr)
        print(f"verified: {report.verify()}", file=sys.stderr)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
