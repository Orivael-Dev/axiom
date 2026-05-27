#!/usr/bin/env python3
"""Audio Phase A measurement harness.

Drives the gate metrics from docs/training/audio-phase-a.md:

  1. Material accuracy on labeled positive clips    — target ≥ 80%
  2. Latency on a 1-second clip (p95)               — target < 100 ms
  3. False-positive rate on background clips        — target < 5%
     (any sharp_transient or soft_transient verdict on a background
     clip counts as a false positive)
  4. Tempo accuracy on metronome clips (--tempo-*)  — target ≥ 80%
     within ±3 BPM. ONLY evaluated when a tempo-* subdirectory is
     present in the dataset, OR --demo is used.

Usage:

  # Built-in synthetic demo — covers materials AND tempo.
  AXIOM_MASTER_KEY=... python3 scripts/audio_harness.py --demo

  # Real labeled directory.
  AXIOM_MASTER_KEY=... python3 scripts/audio_harness.py --dataset ./audio_dataset

Dataset layout (one folder per label; 'background' = negatives;
'tempo-NNN' = a metronome at NNN BPM):

  audio_dataset/
    glass-like/    *.wav    expected verdict = glass-like
    metal-like/    *.wav
    wood-like/     *.wav
    fabric-like/   *.wav
    background/    *.wav    negatives — transient verdict = false positive
    tempo-60/      *.wav    expected BPM = 60   (folder name carries the BPM)
    tempo-120/     *.wav
    ...

Exit code is 0 only when all applicable gate thresholds are met. CI
can gate Phase B greenlight on this script's exit code.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import struct
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ─── Gate thresholds (from docs/training/audio-phase-a.md) ─────────────

MATERIAL_ACCURACY_GATE = 0.80   # ≥ 80% of positive clips classified correctly
LATENCY_P95_GATE_MS = 100.0     # 95th percentile under 100 ms / clip
FALSE_POSITIVE_GATE = 0.05      # ≤ 5% of background clips flagged transient
TEMPO_ACCURACY_GATE = 0.80      # ≥ 80% of tempo clips within ±BPM_TOL
TEMPO_BPM_TOLERANCE = 3.0       # ±3 BPM tolerance on metronome clips

SAMPLE_RATE = 16_000
TRANSIENT_LABELS = {"sharp_transient", "soft_transient"}


# ─── Data types ─────────────────────────────────────────────────────────


@dataclass
class ClipResult:
    path: str
    expected_material: Optional[str]   # None for background clips
    is_background: bool
    predicted_material: str
    predicted_impact: str
    predicted_decay: str
    confidence: float
    latency_ms: float
    hit: Optional[bool]  # None for background; True/False for positive clips


@dataclass
class TempoClipResult:
    path: str
    expected_bpm: float
    predicted_bpm: float
    tempo_stability: float
    confidence: float
    latency_ms: float
    hit: bool   # |expected - predicted| <= TEMPO_BPM_TOLERANCE


# ─── WAV I/O for synthetic demo dataset ─────────────────────────────────


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


# ─── Synthetic stimuli (parameterized so each call is a distinct clip) ─


def synth_glass(seed: int, n_scatter: int = 3) -> list[float]:
    rng = random.Random(seed)
    n = SAMPLE_RATE
    out = [0.0] * n
    # Primary click — 5ms broadband
    for i in range(int(0.005 * SAMPLE_RATE)):
        out[i] += (rng.random() * 2 - 1) * 0.9
    # 3 HF tones, randomized in this seed's vicinity of 4-7 kHz
    for tone_hz in (
        rng.uniform(3800, 4200),
        rng.uniform(5300, 5700),
        rng.uniform(6800, 7200),
    ):
        for i in range(int(0.2 * SAMPLE_RATE)):
            t = i / SAMPLE_RATE
            out[i] += 0.25 * math.sin(2 * math.pi * tone_hz * t) * math.exp(-t * 15)
    # Scattered secondary impacts
    delays = sorted(rng.uniform(0.05, 0.32) for _ in range(n_scatter))
    for delay_s in delays:
        start = int(delay_s * SAMPLE_RATE)
        for i in range(int(0.04 * SAMPLE_RATE)):
            if start + i >= n:
                break
            t = i / SAMPLE_RATE
            out[start + i] += (rng.random() * 2 - 1) * 0.5 * math.exp(-t * 80)
    # Noise floor
    for i in range(n):
        out[i] += (rng.random() * 2 - 1) * 0.003
    return out


def synth_metal(seed: int) -> list[float]:
    rng = random.Random(seed)
    n = SAMPLE_RATE
    out = [0.0] * n
    # Sharp click
    for i in range(int(0.003 * SAMPLE_RATE)):
        out[i] += (rng.random() * 2 - 1) * 0.8
    # Long sustained narrow tone — fundamental varies per seed
    fundamental = rng.uniform(3200, 3800)
    for i in range(n):
        t = i / SAMPLE_RATE
        out[i] += 0.7 * math.exp(-t * 2) * math.sin(2 * math.pi * fundamental * t)
    return out


def synth_wood(seed: int) -> list[float]:
    rng = random.Random(seed)
    n = int(0.6 * SAMPLE_RATE)
    out = [0.0] * n
    # Quick attack — short noise burst — then mid-frequency decay
    for i in range(int(0.012 * SAMPLE_RATE)):
        out[i] += (rng.random() * 2 - 1) * 0.6
    fundamental = rng.uniform(900, 1400)
    for i in range(n):
        t = i / SAMPLE_RATE
        decay = math.exp(-t * 25)
        out[i] += 0.45 * decay * math.sin(2 * math.pi * fundamental * t)
    return out


def synth_fabric(seed: int) -> list[float]:
    rng = random.Random(seed)
    n = int(0.5 * SAMPLE_RATE)
    out = [0.0] * n
    fundamental = rng.uniform(180, 280)
    # Slow attack — purely low-frequency tone, no broadband noise
    for i in range(int(0.04 * SAMPLE_RATE)):
        t = i / SAMPLE_RATE
        attack = t / 0.035 if t < 0.035 else 1.0
        out[i] += 0.6 * attack * math.sin(2 * math.pi * fundamental * t)
    # Long low-freq decay
    for i in range(int(0.04 * SAMPLE_RATE), n):
        t = (i - int(0.04 * SAMPLE_RATE)) / SAMPLE_RATE
        out[i] += 0.6 * math.exp(-t * 8) * math.sin(2 * math.pi * fundamental * (i / SAMPLE_RATE))
    return out


def synth_metronome(bpm: float, duration_s: float = 4.0) -> list[float]:
    """Periodic clicks at the given BPM. Each click is a 10ms broadband
    burst with sharp linear-decay envelope — easy for the onset
    detector to find. Deterministic LCG for byte-identical output
    across runs."""
    n = int(duration_s * SAMPLE_RATE)
    out = [0.0] * n
    period_samples = int(60.0 * SAMPLE_RATE / bpm)
    click_len = int(0.010 * SAMPLE_RATE)
    state = 1
    pos = 0
    while pos + click_len < n:
        for i in range(click_len):
            state = (state * 1103515245 + 12345) & 0x7fffffff
            r = (state / 0x7fffffff) * 2 - 1
            out[pos + i] += r * 0.85 * (1.0 - i / click_len)
        pos += period_samples
    return out


def synth_background(seed: int) -> list[float]:
    """Room-tone style noise with occasional gentle hum, NO transients."""
    rng = random.Random(seed)
    n = SAMPLE_RATE
    out = [0.0] * n
    for i in range(n):
        t = i / SAMPLE_RATE
        # Pink-ish noise + faint 60 Hz hum + faint TV-like mids
        out[i] = (
            0.015 * (rng.random() * 2 - 1)
            + 0.008 * math.sin(2 * math.pi * 60 * t)
            + 0.005 * math.sin(2 * math.pi * 440 * t + rng.random())
        )
    return out


def build_demo_dataset(root: Path) -> None:
    """Write ~20 synthetic clips into a directory tree for demo runs.

    Layout includes BOTH material categories AND tempo-NNN folders
    so the harness exercises every gate in one run.
    """
    plan = {
        "glass-like":  [(synth_glass, [(1,), (2,), (3,), (4,)])],
        "metal-like":  [(synth_metal, [(11,), (12,), (13,)])],
        "wood-like":   [(synth_wood,  [(21,), (22,), (23,)])],
        "fabric-like": [(synth_fabric, [(31,), (32,)])],
        "background":  [(synth_background, [(41,), (42,)])],
    }
    for label, groups in plan.items():
        d = root / label
        d.mkdir(parents=True, exist_ok=True)
        for fn, arglist in groups:
            for i, args in enumerate(arglist):
                _write_wav(d / f"{label}_{i:02d}.wav", fn(*args))
    # Metronome clips — one folder per BPM, named tempo-NNN
    for bpm in (60, 90, 120, 150, 180):
        d = root / f"tempo-{bpm}"
        d.mkdir(parents=True, exist_ok=True)
        _write_wav(d / f"metronome_{bpm}bpm.wav", synth_metronome(bpm))


# ─── Discovery + execution ──────────────────────────────────────────────


def discover_clips(
    dataset_root: Path,
) -> tuple[list[tuple[Path, Optional[str], bool]], list[tuple[Path, float]]]:
    """Walk dataset_root and split clips into material vs. tempo lists.

    Material/background folders: glass-like, metal-like, wood-like,
    fabric-like, background. Tempo folders: tempo-NNN where NNN is the
    expected BPM. Unrecognized folder names are ignored with a warning.

    Returns (material_clips, tempo_clips) where:
      material_clips = [(path, expected_label_or_None, is_background), ...]
      tempo_clips    = [(path, expected_bpm), ...]
    """
    if not dataset_root.exists():
        raise SystemExit(f"Dataset directory does not exist: {dataset_root}")
    material: list[tuple[Path, Optional[str], bool]] = []
    tempo: list[tuple[Path, float]] = []
    for sub in sorted(dataset_root.iterdir()):
        if not sub.is_dir():
            continue
        label = sub.name.strip()
        if label.startswith("tempo-"):
            try:
                bpm = float(label[len("tempo-"):])
            except ValueError:
                print(f"  WARN: ignoring folder with bad tempo label: {sub.name}")
                continue
            for wav in sorted(sub.glob("*.wav")):
                tempo.append((wav, bpm))
        else:
            is_bg = (label == "background")
            for wav in sorted(sub.glob("*.wav")):
                expected = None if is_bg else label
                material.append((wav, expected, is_bg))
    return material, tempo


def evaluate_clip(path: Path, expected: Optional[str], is_bg: bool) -> ClipResult:
    from axiom_audio import classify_clip
    t0 = time.perf_counter()
    report = classify_clip(str(path))
    latency_ms = (time.perf_counter() - t0) * 1000
    predicted = report.payload["material_signature"]
    hit: Optional[bool]
    if is_bg:
        hit = None
    else:
        hit = (predicted == expected)
    return ClipResult(
        path=str(path), expected_material=expected, is_background=is_bg,
        predicted_material=predicted,
        predicted_impact=report.payload["impact_profile"],
        predicted_decay=report.payload["decay_pattern"],
        confidence=report.confidence,
        latency_ms=round(latency_ms, 2),
        hit=hit,
    )


def evaluate_tempo_clip(path: Path, expected_bpm: float) -> TempoClipResult:
    from axiom_audio import classify_tempo_clip
    t0 = time.perf_counter()
    report = classify_tempo_clip(str(path))
    latency_ms = (time.perf_counter() - t0) * 1000
    predicted = report.payload["bpm"]
    return TempoClipResult(
        path=str(path), expected_bpm=expected_bpm,
        predicted_bpm=predicted,
        tempo_stability=report.payload["tempo_stability"],
        confidence=report.confidence,
        latency_ms=round(latency_ms, 2),
        hit=abs(predicted - expected_bpm) <= TEMPO_BPM_TOLERANCE,
    )


# ─── Aggregation + reporting ────────────────────────────────────────────


def summarize(
    results: list[ClipResult],
    tempo_results: list[TempoClipResult] | None = None,
) -> dict:
    tempo_results = tempo_results or []
    positives = [r for r in results if not r.is_background]
    backgrounds = [r for r in results if r.is_background]

    # Material accuracy
    hits = sum(1 for r in positives if r.hit)
    accuracy = hits / len(positives) if positives else 0.0

    # Per-label precision (predicted == label / total predicted as label)
    # + recall (predicted == label / total expected as label)
    labels = sorted({r.expected_material for r in positives if r.expected_material})
    per_label = {}
    for label in labels:
        expected_count = sum(1 for r in positives if r.expected_material == label)
        predicted_count = sum(1 for r in positives if r.predicted_material == label)
        true_pos = sum(1 for r in positives
                       if r.expected_material == label and r.predicted_material == label)
        precision = true_pos / predicted_count if predicted_count else 0.0
        recall = true_pos / expected_count if expected_count else 0.0
        per_label[label] = {
            "expected": expected_count,
            "predicted": predicted_count,
            "true_positive": true_pos,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
        }

    # Latency — material + tempo clips together, since they share the
    # same FFT-driven pipeline and the gate is "per-clip ms"
    latencies = [r.latency_ms for r in results] + [
        t.latency_ms for t in tempo_results
    ]
    latency_stats = {
        "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
        "median": round(statistics.median(latencies), 2) if latencies else 0.0,
        "p95": round(_percentile(latencies, 95), 2) if latencies else 0.0,
        "p99": round(_percentile(latencies, 99), 2) if latencies else 0.0,
        "max": round(max(latencies), 2) if latencies else 0.0,
    }

    # False-positive rate: background clips that yielded a transient verdict
    fp_count = sum(1 for r in backgrounds if r.predicted_impact in TRANSIENT_LABELS)
    fp_rate = fp_count / len(backgrounds) if backgrounds else 0.0

    # Tempo accuracy (when tempo clips were provided)
    tempo_summary = None
    if tempo_results:
        tempo_hits = sum(1 for t in tempo_results if t.hit)
        tempo_accuracy = tempo_hits / len(tempo_results)
        tempo_summary = {
            "clips":              len(tempo_results),
            "accuracy":           round(tempo_accuracy, 3),
            "tolerance_bpm":      TEMPO_BPM_TOLERANCE,
            "per_clip":           [
                {
                    "path":           t.path,
                    "expected_bpm":   t.expected_bpm,
                    "predicted_bpm":  t.predicted_bpm,
                    "stability":      t.tempo_stability,
                    "confidence":     t.confidence,
                    "hit":            t.hit,
                }
                for t in tempo_results
            ],
        }

    # Gate verdicts
    gates = {
        "material_accuracy": {
            "value": round(accuracy, 3),
            "threshold": MATERIAL_ACCURACY_GATE,
            "pass": accuracy >= MATERIAL_ACCURACY_GATE,
        },
        "latency_p95_ms": {
            "value": latency_stats["p95"],
            "threshold": LATENCY_P95_GATE_MS,
            "pass": latency_stats["p95"] < LATENCY_P95_GATE_MS,
        },
        "false_positive_rate": {
            "value": round(fp_rate, 3),
            "threshold": FALSE_POSITIVE_GATE,
            "pass": fp_rate <= FALSE_POSITIVE_GATE,
        },
    }
    if tempo_summary is not None:
        gates["tempo_accuracy"] = {
            "value": tempo_summary["accuracy"],
            "threshold": TEMPO_ACCURACY_GATE,
            "pass": tempo_summary["accuracy"] >= TEMPO_ACCURACY_GATE,
        }
    overall_pass = all(g["pass"] for g in gates.values())

    summary = {
        "clips_total": len(results) + len(tempo_results),
        "clips_positive": len(positives),
        "clips_background": len(backgrounds),
        "clips_tempo": len(tempo_results),
        "material_accuracy": round(accuracy, 3),
        "per_label": per_label,
        "latency_ms": latency_stats,
        "false_positive_rate": round(fp_rate, 3),
        "false_positives": fp_count,
        "gates": gates,
        "overall_pass": overall_pass,
        "results": [asdict(r) for r in results],
    }
    if tempo_summary is not None:
        summary["tempo"] = tempo_summary
    return summary


def _percentile(xs: list[float], p: float) -> float:
    """Nearest-rank percentile. xs need not be sorted."""
    if not xs:
        return 0.0
    sorted_xs = sorted(xs)
    k = max(0, min(len(sorted_xs) - 1, int(math.ceil(p / 100 * len(sorted_xs))) - 1))
    return sorted_xs[k]


def print_report(summary: dict, *, quiet: bool = False) -> None:
    print()
    breakdown = (
        f"positives={summary['clips_positive']}, "
        f"background={summary['clips_background']}"
    )
    if summary.get("clips_tempo"):
        breakdown += f", tempo={summary['clips_tempo']}"
    print(f"  Clips evaluated:         {summary['clips_total']} ({breakdown})")
    print()
    print("  ── Gates ─────────────────────────────────────────────")
    for name, g in summary["gates"].items():
        mark = "PASS" if g["pass"] else "FAIL"
        if name == "material_accuracy":
            print(f"  [{mark}] material accuracy   {g['value']:.1%}  (threshold ≥ {g['threshold']:.0%})")
        elif name == "latency_p95_ms":
            print(f"  [{mark}] latency p95         {g['value']:>6.1f} ms (threshold < {g['threshold']:.0f} ms)")
        elif name == "false_positive_rate":
            print(f"  [{mark}] false-positive rate {g['value']:.1%}  (threshold ≤ {g['threshold']:.0%})")
        elif name == "tempo_accuracy":
            print(f"  [{mark}] tempo accuracy      {g['value']:.1%}  (threshold ≥ {g['threshold']:.0%}, ±{TEMPO_BPM_TOLERANCE:.0f} BPM)")
    print()
    if quiet:
        print(f"  Overall: {'PASS' if summary['overall_pass'] else 'FAIL'}")
        return

    print("  ── Per-material breakdown ────────────────────────────")
    print(f"  {'label':<14}{'expected':>10}{'predicted':>11}{'true+':>8}{'precision':>11}{'recall':>9}")
    for label, stats in summary["per_label"].items():
        print(f"  {label:<14}{stats['expected']:>10}{stats['predicted']:>11}"
              f"{stats['true_positive']:>8}{stats['precision']:>11.3f}{stats['recall']:>9.3f}")
    print()
    print("  ── Latency (ms) ──────────────────────────────────────")
    L = summary["latency_ms"]
    print(f"  mean {L['mean']:>6.1f}   median {L['median']:>6.1f}   "
          f"p95 {L['p95']:>6.1f}   p99 {L['p99']:>6.1f}   max {L['max']:>6.1f}")
    print()
    if summary.get("tempo"):
        print("  ── Tempo (BPM) ───────────────────────────────────────")
        t = summary["tempo"]
        print(f"  Clips: {t['clips']}   accuracy: {t['accuracy']:.1%}   "
              f"tolerance: ±{t['tolerance_bpm']:.0f} BPM")
        for clip in t["per_clip"]:
            mark = "✓" if clip["hit"] else "✗"
            print(f"  {mark}  expected={clip['expected_bpm']:>6.1f}  "
                  f"predicted={clip['predicted_bpm']:>6.1f}  "
                  f"stability={clip['stability']:.2f}")
        print()
    print(f"  Overall: {'PASS' if summary['overall_pass'] else 'FAIL'}")
    print()


def markdown_report(summary: dict) -> str:
    lines = []
    overall = "PASS" if summary["overall_pass"] else "FAIL"
    lines.append(f"# Audio Phase A — measurement run ({overall})")
    lines.append("")
    lines.append(f"Clips evaluated: **{summary['clips_total']}** "
                 f"(positives={summary['clips_positive']}, background={summary['clips_background']})")
    lines.append("")
    lines.append("## Gates")
    lines.append("")
    lines.append("| Metric | Value | Threshold | Verdict |")
    lines.append("|---|---|---|---|")
    for name, g in summary["gates"].items():
        verdict = "PASS" if g["pass"] else "FAIL"
        if name == "material_accuracy":
            lines.append(f"| material accuracy | {g['value']:.1%} | ≥ {g['threshold']:.0%} | {verdict} |")
        elif name == "latency_p95_ms":
            lines.append(f"| latency p95 | {g['value']:.1f} ms | < {g['threshold']:.0f} ms | {verdict} |")
        elif name == "false_positive_rate":
            lines.append(f"| false-positive rate | {g['value']:.1%} | ≤ {g['threshold']:.0%} | {verdict} |")
        elif name == "tempo_accuracy":
            lines.append(f"| tempo accuracy | {g['value']:.1%} | ≥ {g['threshold']:.0%} (±{TEMPO_BPM_TOLERANCE:.0f} BPM) | {verdict} |")
    lines.append("")
    lines.append("## Per-material breakdown")
    lines.append("")
    lines.append("| Label | Expected | Predicted | True+ | Precision | Recall |")
    lines.append("|---|---|---|---|---|---|")
    for label, s in summary["per_label"].items():
        lines.append(f"| {label} | {s['expected']} | {s['predicted']} | {s['true_positive']} | "
                     f"{s['precision']:.3f} | {s['recall']:.3f} |")
    lines.append("")
    lines.append("## Latency (ms)")
    lines.append("")
    L = summary["latency_ms"]
    lines.append(f"mean {L['mean']:.1f} · median {L['median']:.1f} · "
                 f"p95 {L['p95']:.1f} · p99 {L['p99']:.1f} · max {L['max']:.1f}")
    lines.append("")
    return "\n".join(lines)


# ─── Entry point ────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, default=Path("./audio_dataset"),
                    help="Root dir of labeled clips (default: ./audio_dataset)")
    ap.add_argument("--demo", action="store_true",
                    help="Synthesize a small in-process suite + run on that")
    ap.add_argument("--output-json", type=Path, default=Path("audio_harness_results.json"),
                    help="Where to write the summary JSON")
    ap.add_argument("--markdown", type=Path, default=None,
                    help="Also write a Markdown report to this path")
    ap.add_argument("--quiet", action="store_true",
                    help="Only print gate verdicts + overall pass/fail")
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit(
            "AXIOM_MASTER_KEY must be set. Generate one:\n"
            "  export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
        )

    tmp_demo: Optional[Path] = None
    if args.demo:
        tmp_demo = Path(tempfile.mkdtemp(prefix="axiom_audio_demo_"))
        build_demo_dataset(tmp_demo)
        dataset_root = tmp_demo
        print(f"  Built synthetic demo dataset at {dataset_root}")
    else:
        dataset_root = args.dataset

    try:
        material_clips, tempo_clips = discover_clips(dataset_root)
        if not material_clips and not tempo_clips:
            sys.exit(f"No .wav files found under {dataset_root}. "
                     "Pass --demo to run the synthetic suite, or populate the directory.")

        results: list[ClipResult] = [
            evaluate_clip(path, expected, is_bg)
            for path, expected, is_bg in material_clips
        ]
        tempo_results: list[TempoClipResult] = [
            evaluate_tempo_clip(path, expected_bpm)
            for path, expected_bpm in tempo_clips
        ]

        summary = summarize(results, tempo_results)
        print_report(summary, quiet=args.quiet)

        args.output_json.write_text(json.dumps(summary, indent=2))
        print(f"  JSON summary: {args.output_json}")
        if args.markdown:
            args.markdown.write_text(markdown_report(summary))
            print(f"  Markdown:     {args.markdown}")
        print()

        return 0 if summary["overall_pass"] else 1
    finally:
        # Best-effort cleanup of the demo tmp dir
        if tmp_demo and tmp_demo.exists():
            import shutil
            shutil.rmtree(tmp_demo, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
