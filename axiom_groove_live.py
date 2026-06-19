"""axiom_groove_live.py — Real-time vowel formant analyzer

Captures microphone audio, extracts F1/F2/F3 every FRAME_MS milliseconds
via LPC root-finding, classifies the nearest IPA vowel from GrooveAgent
presets on the Bark perceptual scale, and prints a live scrolling display.

No cloud, no Whisper — pure formant math.

Dependencies:
  pip install numpy sounddevice

Usage:
  python3 axiom_groove_live.py                  # mic, 16kHz, 100ms frames
  python3 axiom_groove_live.py --fs 44100        # higher sample rate
  python3 axiom_groove_live.py --frame-ms 150    # longer analysis window
  python3 axiom_groove_live.py --synth           # no mic — synthetic test
  python3 axiom_groove_live.py --no-color        # plain text
"""

import sys
import math
import time
import argparse
import queue
from typing import Optional

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_FS        = 16000   # Hz — standard narrowband speech
DEFAULT_FRAME_MS  = 100     # ms — analysis frame
DEFAULT_LPC_ORDER = 14      # LPC prediction order (rule of thumb: 2 + fs/1000)
PRE_EMPHASIS      = 0.85    # first-order high-pass coefficient
# 0.85 (not the usual 0.97) preserves low F1 in high-tongue/rounded vowels
# where F1 can fall below 400 Hz — 0.97 would attenuate it to ~15% of amplitude
MIN_FORMANT_HZ    = 90      # ignore roots below this
MAX_BW_HZ         = 500     # maximum formant bandwidth to keep
ENERGY_FLOOR      = 0.003   # RMS below this → treat as silence


# ── LPC formant extraction (numpy-only) ───────────────────────────────────────

def _preemphasis(x: "np.ndarray") -> "np.ndarray":
    out = x.copy()
    out[1:] -= PRE_EMPHASIS * x[:-1]
    return out


def _lpc_coeffs(signal: "np.ndarray", order: int) -> "np.ndarray":
    """Autocorrelation LPC via symmetric Toeplitz solve (numpy only)."""
    windowed = signal * np.hamming(len(signal))
    corr = np.correlate(windowed, windowed, mode='full')
    r = corr[len(windowed) - 1: len(windowed) + order + 1]
    # Build symmetric Toeplitz matrix
    idx = np.arange(order)
    R = r[np.abs(idx[:, None] - idx[None, :])]
    b = -r[1: order + 1]
    try:
        a = np.linalg.solve(R, b)
    except np.linalg.LinAlgError:
        return np.zeros(order)
    return a


def extract_formants(signal: "np.ndarray", fs: int,
                     order: int = DEFAULT_LPC_ORDER) -> list[float]:
    """Extract formant frequencies (Hz) from a speech frame.

    Returns formants sorted by frequency. May return fewer than 4 if the
    frame is too short, too noisy, or contains silence.
    """
    if len(signal) < order + 2:
        return []

    pre = _preemphasis(signal)
    a = _lpc_coeffs(pre, order)

    # LPC polynomial: A(z) = 1 + a₁z⁻¹ + … + aₚz⁻ᵖ
    # Poles of H(z)=1/A(z) → roots of [1, a₁, …, aₚ]
    poly = np.concatenate([[1.0], a])
    roots = np.roots(poly)

    # Keep upper half of z-plane (positive frequency)
    roots = roots[np.imag(roots) >= 0]

    # Frequency from angle (Hz), bandwidth from magnitude
    angles = np.angle(roots)
    freqs  = angles * fs / (2.0 * math.pi)
    mag    = np.abs(roots)
    # Bandwidth: BW = -ln(r)·fs/π; guard against mag=0
    bw     = -np.log(np.maximum(mag, 1e-10)) * fs / math.pi

    mask  = (freqs > MIN_FORMANT_HZ) & (bw < MAX_BW_HZ) & (bw > 0)
    freqs = np.sort(freqs[mask])
    return freqs.tolist()


# ── Bark perceptual scale ─────────────────────────────────────────────────────

def _bark(f: float) -> float:
    """Traunmüller (1990) Bark scale — perceptually uniform frequency."""
    return 26.81 / (1.0 + 1960.0 / max(f, 1.0)) - 0.53


def _bark_distance(f1: float, f2: float,
                   t1: float, t2: float) -> float:
    return math.sqrt((_bark(f1) - _bark(t1)) ** 2 +
                     (_bark(f2) - _bark(t2)) ** 2)


# ── Vowel classification ──────────────────────────────────────────────────────

def _build_targets(tract_length_cm: float = 17.0) -> dict[str, list[float]]:
    """Precompute ArticulatorySim formants for each vowel preset."""
    from axiom_groove_agent import ArticulatorySim, VOWEL_PRESETS
    sim = ArticulatorySim(tract_length_cm)
    return {name: sim.vowel(name).formants for name in VOWEL_PRESETS}


def nearest_vowel(formants: list[float],
                  targets: dict[str, list[float]]) -> tuple[str, float]:
    """Return (vowel, bark_distance) for the nearest preset match."""
    if len(formants) < 2:
        return "?", 99.0
    f1, f2 = formants[0], formants[1]
    best, best_d = "/ə/", 99.0
    for name, tf in targets.items():
        if len(tf) < 2:
            continue
        d = _bark_distance(f1, f2, tf[0], tf[1])
        if d < best_d:
            best_d = d
            best   = name
    return best, best_d


# ── Confidence from Bark distance ─────────────────────────────────────────────
#
# Bark distance of 0 = perfect match; 5 Bark ≈ a vowel shifted a full quadrant.
# Map [0, 5] → [1.0, 0.0] linearly and clamp.

def _confidence(bark_dist: float) -> float:
    return max(0.0, 1.0 - bark_dist / 5.0)


# ── Terminal display ──────────────────────────────────────────────────────────

_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"

def _bar(value: float, width: int = 12) -> str:
    filled = max(0, min(width, round(value * width)))
    return "█" * filled + "░" * (width - filled)


def _col(conf: float, no_color: bool) -> tuple[str, str]:
    if no_color:
        return "", ""
    if conf >= 0.80:
        return _GREEN, _RESET
    if conf >= 0.50:
        return _YELLOW, _RESET
    return _RED, _RESET


def print_frame(elapsed_ms: float, formants: list[float],
                vowel: str, conf: float, no_color: bool = False) -> None:
    f = [formants[i] if i < len(formants) else 0 for i in range(3)]
    col, rst = _col(conf, no_color)
    print(f"{col}[{elapsed_ms:7.0f}ms]  "
          f"F1={f[0]:4.0f}  F2={f[1]:4.0f}  F3={f[2]:4.0f}  "
          f"→  {vowel:<4}  conf={conf:.2f}  {_bar(conf)}{rst}",
          flush=True)


def print_silence(elapsed_ms: float, no_color: bool = False) -> None:
    dim, rst = (_DIM, _RESET) if not no_color else ("", "")
    print(f"{dim}[{elapsed_ms:7.0f}ms]  [ silence ]{rst}", flush=True)


# ── Synthetic test mode (no mic required) ─────────────────────────────────────

def _apply_resonator(sig: "np.ndarray", freq: float, fs: int,
                     bandwidth: float = 80.0) -> "np.ndarray":
    """Second-order all-pole resonator (formant filter).

    y[n] = x[n] + 2r·cos(θ)·y[n-1] - r²·y[n-2]
    where r = exp(-π·BW/fs), θ = 2π·f/fs
    """
    r     = math.exp(-math.pi * bandwidth / fs)
    theta = 2.0 * math.pi * freq / fs
    a1    = 2.0 * r * math.cos(theta)
    a2    = -(r ** 2)
    out   = np.zeros(len(sig))
    for i in range(len(sig)):
        prev1 = out[i - 1] if i >= 1 else 0.0
        prev2 = out[i - 2] if i >= 2 else 0.0
        out[i] = sig[i] + a1 * prev1 + a2 * prev2
    return out


def _synth_signal(vowel: str, fs: int, n_samples: int,
                  tract_length_cm: float = 17.0,
                  f0: int = 120) -> "np.ndarray":
    """Voiced speech-like signal: pulse train at F0 filtered through formant resonators.

    This matches how LPC was designed — harmonic source × vocal tract filter —
    giving correct formant structure even for low-F1 rounded vowels (/u/ /ʊ/ /o/).
    """
    from axiom_groove_agent import ArticulatorySim
    sim    = ArticulatorySim(tract_length_cm)
    result = sim.vowel(vowel)

    # Glottal source: unit impulses at F0 period (simplified Rosenberg pulse)
    pulse  = np.zeros(n_samples)
    period = int(fs / f0)
    for k in range(0, n_samples, period):
        pulse[k] = 1.0

    # Cascade of second-order resonators (one per formant)
    signal = pulse
    for freq in result.formants[:4]:
        signal = _apply_resonator(signal, freq, fs)

    signal += np.random.default_rng(42).standard_normal(n_samples) * 0.02
    mx = np.max(np.abs(signal))
    return signal / (mx + 1e-9)


def run_synth(fs: int = DEFAULT_FS, frame_ms: int = DEFAULT_FRAME_MS,
              order: int = DEFAULT_LPC_ORDER, tract_length_cm: float = 17.0,
              no_color: bool = False) -> None:
    """Validate the LPC pipeline with synthetic sine-mixture vowels."""
    from axiom_groove_agent import VOWEL_PRESETS

    frame_size = int(fs * frame_ms / 1000)
    targets    = _build_targets(tract_length_cm)

    print(f"  Synth test — {len(VOWEL_PRESETS)} vowels  "
          f"(fs={fs} Hz, frame={frame_ms} ms, LPC order={order})\n")
    print(f"  {'Inp':<5} {'Got':<5} {'F1':>6} {'F2':>6} {'F3':>6}  "
          f"{'Conf':>5}  {'Match':<5}")
    print("  " + "─" * 52)

    correct = 0
    for vowel in VOWEL_PRESETS:
        # Generate 3 frames, use middle one to avoid edge transients
        signal = _synth_signal(vowel, fs, frame_size * 3, tract_length_cm)
        frame  = signal[frame_size: frame_size * 2]

        formants = extract_formants(frame, fs, order)
        detected, dist = nearest_vowel(formants, targets)
        conf = _confidence(dist)
        ok   = detected == vowel

        col, rst = _col(1.0 if ok else 0.0, no_color)
        f = [formants[i] if i < len(formants) else 0 for i in range(3)]
        mark = "✓" if ok else "✗"
        print(f"  {col}{vowel:<5} {detected:<5} "
              f"{f[0]:>6.0f} {f[1]:>6.0f} {f[2]:>6.0f}  "
              f"{conf:>5.2f}  {mark}{rst}")
        correct += ok

    print(f"\n  {correct}/{len(VOWEL_PRESETS)} correctly classified\n")


# ── Live mic mode ─────────────────────────────────────────────────────────────

def run_live(fs: int = DEFAULT_FS, frame_ms: int = DEFAULT_FRAME_MS,
             order: int = DEFAULT_LPC_ORDER, tract_length_cm: float = 17.0,
             no_color: bool = False) -> None:
    """Open the default mic and print formant analysis until Ctrl-C."""
    try:
        import sounddevice as sd
    except ImportError:
        print("  sounddevice not installed. Run:  pip install sounddevice")
        print("  Or use --synth to test without a mic.")
        return

    frame_size = int(fs * frame_ms / 1000)
    targets    = _build_targets(tract_length_cm)
    audio_q: queue.Queue = queue.Queue()
    buffer    = np.zeros(0, dtype=np.float32)
    start     = time.time()
    n_frames  = 0

    def _callback(indata, frames, time_info, status):
        audio_q.put(indata[:, 0].copy())

    print(f"  fs={fs} Hz  frame={frame_ms} ms  LPC order={order}  "
          f"L={tract_length_cm:.1f} cm")
    print(f"  {'─'*60}")

    try:
        with sd.InputStream(samplerate=fs, channels=1, dtype='float32',
                            blocksize=512, callback=_callback):
            while True:
                try:
                    chunk = audio_q.get(timeout=3.0)
                except queue.Empty:
                    print("  [ mic timeout — check device ]")
                    continue

                buffer = np.append(buffer, chunk)

                while len(buffer) >= frame_size:
                    frame  = buffer[:frame_size]
                    buffer = buffer[frame_size:]

                    elapsed = (time.time() - start) * 1000
                    rms     = float(np.sqrt(np.mean(frame ** 2)))

                    if rms < ENERGY_FLOOR:
                        print_silence(elapsed, no_color)
                        n_frames += 1
                        continue

                    formants = extract_formants(frame, fs, order)

                    if len(formants) < 2:
                        print_silence(elapsed, no_color)
                        n_frames += 1
                        continue

                    vowel, dist = nearest_vowel(formants, targets)
                    conf = _confidence(dist)
                    print_frame(elapsed, formants, vowel, conf, no_color)
                    n_frames += 1

    except KeyboardInterrupt:
        print(f"\n  Stopped — {n_frames} frames analysed.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Axiom Groove Live — real-time vowel formant analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 axiom_groove_live.py                  # live mic
  python3 axiom_groove_live.py --fs 44100        # 44.1kHz
  python3 axiom_groove_live.py --frame-ms 150    # slower frames, more stable
  python3 axiom_groove_live.py --synth           # no mic — synth test
  python3 axiom_groove_live.py --no-color        # plain text
""",
    )
    p.add_argument("--fs",       type=int,   default=DEFAULT_FS,
                   help=f"Sample rate Hz (default {DEFAULT_FS})")
    p.add_argument("--frame-ms", type=int,   default=DEFAULT_FRAME_MS,
                   help=f"Frame length ms (default {DEFAULT_FRAME_MS})")
    p.add_argument("--order",    type=int,   default=DEFAULT_LPC_ORDER,
                   help=f"LPC order (default {DEFAULT_LPC_ORDER})")
    p.add_argument("--length",   type=float, default=17.0,
                   help="Vocal tract length cm (default 17.0 — adult)")
    p.add_argument("--synth",    action="store_true",
                   help="Test with synthetic sine-mixture vowels (no mic)")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI color")
    args = p.parse_args()

    print("═" * 66)
    print("  AXIOM Groove Live  |  Vowel Formant Analyzer")
    print(f"  LPC order={args.order}  |  tract={args.length}cm  |  Ctrl-C to stop")
    print("═" * 66)
    print()

    if not _NUMPY:
        print("  numpy not installed. Run:  pip install numpy")
        return 1

    if args.synth:
        run_synth(args.fs, args.frame_ms, args.order, args.length, args.no_color)
    else:
        run_live(args.fs, args.frame_ms, args.order, args.length, args.no_color)

    print("═" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
