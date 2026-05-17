"""Stdlib-only DSP primitives for the ambient audio agent.

Phase A keeps the surface tiny. Five operations:

  load_wav            — 16-bit PCM WAV → list[float] in [-1, 1]
  envelope            — RMS amplitude envelope over fixed-size hops
  detect_onsets       — frames where envelope jumps above an adaptive threshold
  fft_magnitude       — magnitude spectrum of a frame, in-place power-of-2 FFT
  spectral_centroid_hz — frequency-weighted mean of |X(f)|
  high_freq_ratio     — energy above a cutoff / total energy
  decay_slope_db      — dB-per-second slope of the envelope after a peak

All pure Python with `math` and `array`. Designed for clips of a few
seconds at 16 kHz; we never run FFT in a tight loop.
"""
from __future__ import annotations

import math
import struct
import wave
from array import array
from typing import Sequence

DEFAULT_HOP_MS = 10  # envelope frame size in ms — 160 samples at 16 kHz


# ─── WAV I/O ────────────────────────────────────────────────────────────


def load_wav(path: str) -> tuple[list[float], int]:
    """Decode a 16-bit PCM WAV file → (samples in [-1, 1], sample_rate).

    Mono only. Stereo is mixed to mono by averaging.
    """
    with wave.open(path, "rb") as w:
        n_channels = w.getnchannels()
        sample_width = w.getsampwidth()
        sample_rate = w.getframerate()
        n_frames = w.getnframes()
        raw = w.readframes(n_frames)

    if sample_width != 2:
        raise ValueError(f"Only 16-bit PCM is supported (got {sample_width * 8}-bit)")

    # Interpret as signed 16-bit ints
    ints = array("h")
    ints.frombytes(raw)
    if n_channels == 2:
        # Average L+R pairs into a mono stream
        ints = array("h", [(ints[i] + ints[i + 1]) // 2 for i in range(0, len(ints), 2)])

    inv = 1.0 / 32768.0
    samples = [s * inv for s in ints]
    return samples, sample_rate


# ─── Envelope + onsets ──────────────────────────────────────────────────


def envelope(
    samples: Sequence[float],
    sample_rate: int,
    hop_ms: int = DEFAULT_HOP_MS,
) -> tuple[list[float], int]:
    """Non-overlapping RMS envelope in `hop_ms` chunks.

    Returns (env, hop_samples) so callers can map frame index → time.
    """
    hop = max(1, int(sample_rate * hop_ms / 1000))
    out: list[float] = []
    for i in range(0, len(samples), hop):
        chunk = samples[i:i + hop]
        if not chunk:
            break
        s = 0.0
        for x in chunk:
            s += x * x
        out.append(math.sqrt(s / len(chunk)))
    return out, hop


def detect_onsets(env: Sequence[float], *, rel_threshold: float = 2.0) -> list[int]:
    """Indices in `env` where amplitude jumps to >= rel_threshold * trailing-median.

    Trailing median is over the last 10 frames; ignores frames within
    50ms of a prior onset to avoid double-triggering on a single attack.
    """
    onsets: list[int] = []
    window = []
    cooldown_frames = 5  # ~50ms at 10ms hops
    last_onset = -cooldown_frames - 1
    for i, x in enumerate(env):
        window.append(x)
        if len(window) > 10:
            window.pop(0)
        if len(window) < 3:
            continue
        # Use a sorted-middle approximation of the median
        sorted_w = sorted(window[:-1])  # exclude current frame from baseline
        if not sorted_w:
            continue
        med = sorted_w[len(sorted_w) // 2]
        thresh = max(med * rel_threshold, 1e-4)
        if x >= thresh and (i - last_onset) > cooldown_frames:
            onsets.append(i)
            last_onset = i
    return onsets


# ─── FFT (Cooley-Tukey radix-2, pure Python) ────────────────────────────


def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


def fft_magnitude(samples: Sequence[float]) -> list[float]:
    """Magnitude spectrum of a real-valued frame.

    Zero-pads to the next power of 2. Returns the first N/2+1 bins
    (the unique half of the symmetric spectrum). O(N log N).
    """
    n = _next_pow2(len(samples))
    # Apply a Hann window first to reduce spectral leakage.
    if len(samples) > 1:
        windowed = [
            samples[i] * 0.5 * (1 - math.cos(2 * math.pi * i / (len(samples) - 1)))
            for i in range(len(samples))
        ]
    else:
        windowed = list(samples)
    # Pad with zeros up to n
    real: list[float] = windowed + [0.0] * (n - len(windowed))
    imag: list[float] = [0.0] * n

    # Iterative Cooley-Tukey
    # Bit-reverse permutation
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            real[i], real[j] = real[j], real[i]
            imag[i], imag[j] = imag[j], imag[i]

    size = 2
    while size <= n:
        half = size >> 1
        angle = -2 * math.pi / size
        wr = math.cos(angle)
        wi = math.sin(angle)
        for i in range(0, n, size):
            cur_wr = 1.0
            cur_wi = 0.0
            for k in range(half):
                a = i + k
                b = a + half
                tr = cur_wr * real[b] - cur_wi * imag[b]
                ti = cur_wr * imag[b] + cur_wi * real[b]
                real[b] = real[a] - tr
                imag[b] = imag[a] - ti
                real[a] += tr
                imag[a] += ti
                cur_wr, cur_wi = (
                    cur_wr * wr - cur_wi * wi,
                    cur_wr * wi + cur_wi * wr,
                )
        size <<= 1

    half_n = n // 2 + 1
    return [math.hypot(real[i], imag[i]) for i in range(half_n)]


# ─── Spectral descriptors ───────────────────────────────────────────────


def spectral_centroid_hz(mag: Sequence[float], sample_rate: int) -> float:
    """Frequency-weighted mean of the magnitude spectrum, in Hz.

    Quick proxy for "brightness". Glass shatter centroids land high
    (>4 kHz); thuds land low (<800 Hz).
    """
    n_bins = len(mag)
    if n_bins == 0:
        return 0.0
    bin_hz = (sample_rate / 2) / (n_bins - 1) if n_bins > 1 else 0.0
    total = 0.0
    weighted = 0.0
    for i, m in enumerate(mag):
        total += m
        weighted += m * (i * bin_hz)
    return weighted / total if total > 0 else 0.0


def high_freq_ratio(
    mag: Sequence[float], sample_rate: int, *, cutoff_hz: float = 2000.0,
) -> float:
    """Fraction of spectral energy above `cutoff_hz`. In [0, 1]."""
    n_bins = len(mag)
    if n_bins <= 1:
        return 0.0
    bin_hz = (sample_rate / 2) / (n_bins - 1)
    cutoff_bin = int(cutoff_hz / bin_hz) if bin_hz > 0 else n_bins
    total = sum(m * m for m in mag)
    high = sum(m * m for m in mag[cutoff_bin:])
    return high / total if total > 0 else 0.0


# ─── Decay slope ────────────────────────────────────────────────────────


def decay_slope_db(env: Sequence[float], peak_idx: int, hop_ms: int) -> float:
    """dB-per-second slope of the envelope after `peak_idx`.

    Fits a line through the next ~300ms after the peak. Negative values
    indicate decay; positive indicates a sustained / re-rising tone.
    Returns 0.0 if there aren't enough frames after the peak.
    """
    tail = list(env[peak_idx + 1: peak_idx + 1 + int(300 / hop_ms)])
    if len(tail) < 3:
        return 0.0
    # Convert to dB relative to the peak
    peak_amp = env[peak_idx] if env[peak_idx] > 1e-6 else 1e-6
    db = [20 * math.log10(max(x, 1e-6) / peak_amp) for x in tail]
    # Simple least-squares fit
    n = len(db)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(db) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, db))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope_per_frame = num / den if den != 0 else 0.0
    frames_per_sec = 1000.0 / hop_ms
    return slope_per_frame * frames_per_sec
