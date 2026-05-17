"""Tests for the Phase A tempo / BPM estimator.

Synthesizes metronomes at known BPMs and asserts the agent recovers
the right tempo within tolerance. Tempo has objective ground truth, so
these are tighter tests than the material classifier — assert numeric
correctness within ±3 BPM.
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

SAMPLE_RATE = 16_000
BPM_TOLERANCE = 3.0   # ±3 BPM is well below human just-noticeable difference (~5%)


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith(("axiom_audio", "axiom_signing", "axiom_event_token")):
            sys.modules.pop(mod, None)
    yield


# ─── WAV helper ─────────────────────────────────────────────────────────


def _write_wav(path: Path, samples: list[float], sr: int = SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for s in samples:
            s = max(-1.0, min(1.0, s))
            frames.extend(struct.pack("<h", int(s * 32767)))
        w.writeframes(bytes(frames))


# ─── Stimulus: clean metronome at `bpm` for `duration_s` seconds ────────


def synth_metronome(bpm: float, duration_s: float = 4.0) -> list[float]:
    """Periodic clicks at the given BPM. Each click is a 10ms broadband
    burst with sharp envelope — easy for the onset detector to find."""
    n = int(duration_s * SAMPLE_RATE)
    out = [0.0] * n
    period_samples = int(60.0 * SAMPLE_RATE / bpm)
    click_len = int(0.010 * SAMPLE_RATE)
    # Drive the clicks with a deterministic pseudo-random pattern so
    # every test invocation produces an identical signal.
    state = 1
    pos = 0
    while pos + click_len < n:
        for i in range(click_len):
            state = (state * 1103515245 + 12345) & 0x7fffffff
            r = (state / 0x7fffffff) * 2 - 1
            # Sharp linear decay over the click
            decay = 1.0 - (i / click_len)
            out[pos + i] += r * 0.85 * decay
        pos += period_samples
    return out


def synth_random_clicks(duration_s: float = 4.0, seed: int = 7) -> list[float]:
    """Clicks at TRULY random intervals in [180ms, 900ms] — no
    detectable period. Used to verify the agent reports low stability
    when there's no rhythm to find."""
    import random
    rng = random.Random(seed)
    n = int(duration_s * SAMPLE_RATE)
    out = [0.0] * n
    click_len = int(0.010 * SAMPLE_RATE)
    pos = 0
    state = 1
    while pos + click_len < n:
        for i in range(click_len):
            state = (state * 1103515245 + 12345) & 0x7fffffff
            r = (state / 0x7fffffff) * 2 - 1
            out[pos + i] += r * 0.85 * (1.0 - i / click_len)
        gap_ms = rng.uniform(180, 900)
        pos += int(gap_ms / 1000 * SAMPLE_RATE)
    return out


# ─── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bpm", [60, 90, 100, 120, 150, 180])
def test_metronome_bpm_recovered_within_tolerance(isolated, tmp_path, bpm):
    """Acceptance test: synthesized metronome at BPM → agent reports BPM ± 3."""
    from axiom_audio import classify_tempo_clip
    wav = tmp_path / f"metronome_{bpm}.wav"
    _write_wav(wav, synth_metronome(bpm))
    r = classify_tempo_clip(str(wav))
    estimated = r.payload["bpm"]
    assert abs(estimated - bpm) <= BPM_TOLERANCE, (
        f"expected {bpm} BPM ± {BPM_TOLERANCE}, got {estimated} "
        f"(stability={r.payload['tempo_stability']})"
    )


def test_metronome_yields_high_confidence(isolated, tmp_path):
    """A clean 120 BPM metronome should produce confidence > 0.5."""
    from axiom_audio import classify_tempo_clip
    wav = tmp_path / "m120.wav"
    _write_wav(wav, synth_metronome(120))
    r = classify_tempo_clip(str(wav))
    assert r.confidence > 0.5, f"confidence {r.confidence} too low on clean metronome"
    assert r.payload["tempo_stability"] > 0.5


def test_silent_clip_reports_no_tempo(isolated, tmp_path):
    """Silence → bpm=0, confidence=0, debug.reason populated."""
    from axiom_audio import classify_tempo_clip
    wav = tmp_path / "silence.wav"
    _write_wav(wav, [0.0] * SAMPLE_RATE)
    r = classify_tempo_clip(str(wav))
    assert r.payload["bpm"] == 0.0
    assert r.confidence == 0.0
    assert "reason" in r.payload["debug"]


def test_single_click_reports_no_tempo(isolated, tmp_path):
    """One isolated click cannot define a tempo — agent must refuse, not guess."""
    from axiom_audio import classify_tempo_clip
    samples = [0.0] * SAMPLE_RATE
    state = 1
    for i in range(int(0.010 * SAMPLE_RATE)):
        state = (state * 1103515245 + 12345) & 0x7fffffff
        samples[i] = ((state / 0x7fffffff) * 2 - 1) * 0.85
    wav = tmp_path / "single.wav"
    _write_wav(wav, samples)
    r = classify_tempo_clip(str(wav))
    assert r.payload["bpm"] == 0.0
    assert r.confidence == 0.0


def test_random_clicks_have_lower_stability_than_metronome(isolated, tmp_path):
    """Truly random click train should score lower stability than a
    clean metronome. Quantitative comparison — we don't lock in an
    absolute number, just that the metric differentiates them."""
    from axiom_audio import classify_tempo_clip
    steady = tmp_path / "steady.wav"
    _write_wav(steady, synth_metronome(120))
    random_clip = tmp_path / "random.wav"
    _write_wav(random_clip, synth_random_clicks())

    r_steady = classify_tempo_clip(str(steady))
    r_random = classify_tempo_clip(str(random_clip))
    assert r_steady.payload["tempo_stability"] > r_random.payload["tempo_stability"], (
        f"steady stability {r_steady.payload['tempo_stability']} "
        f"not greater than random {r_random.payload['tempo_stability']}"
    )


def test_tempo_report_signature_verifies(isolated, tmp_path):
    from axiom_audio import classify_tempo_clip
    wav = tmp_path / "m.wav"
    _write_wav(wav, synth_metronome(120))
    r = classify_tempo_clip(str(wav))
    assert r.verify() is True


def test_tampered_tempo_report_fails_verify(isolated, tmp_path):
    from axiom_audio import TempoReport, classify_tempo_clip
    wav = tmp_path / "m.wav"
    _write_wav(wav, synth_metronome(120))
    original = classify_tempo_clip(str(wav))
    tampered = TempoReport(
        payload={**original.payload, "bpm": 999.99},
        confidence=original.confidence,
        signature=original.signature,
    )
    assert tampered.verify() is False


def test_tempo_signature_uses_dedicated_namespace(isolated, tmp_path):
    """TempoReport must verify under axiom-tempo-v1 — NOT axiom-audio-v1.
    Defends against replay where a forger swaps an AudioReport-shaped
    payload into a TempoReport context with a different namespace.
    """
    from axiom_audio import TempoReport
    from axiom_audio.tempo import TEMPO_KEY_NS
    from axiom_audio.report import AUDIO_KEY_NS
    assert TEMPO_KEY_NS == b"axiom-tempo-v1"
    assert TEMPO_KEY_NS != AUDIO_KEY_NS


def test_tempo_agent_integrates_with_event_token_coordinator(isolated, tmp_path):
    """End-to-end: TempoAgent selectively activated alongside text +
    governance produces a verifiable EventToken with a populated tempo
    layer."""
    from axiom_event_token import Coordinator
    wav = tmp_path / "m120.wav"
    _write_wav(wav, synth_metronome(120))
    coord = Coordinator()
    token = coord.compose(
        text="The metronome is at 120 BPM.",
        audio={"wav_path": str(wav)},
        activate=("text", "tempo", "governance"),
    )
    assert token.verify() is True
    assert token.tempo is not None
    assert abs(token.tempo.payload["bpm"] - 120) <= BPM_TOLERANCE
    # Tempo agent appears in the governance evidence trace
    assert "tempo" in token.governance.payload["evidence_trace"]
    # Tempo was NOT activated by default — only when explicitly listed
    token_default = coord.compose(text="hi", activate=("text", "governance"))
    assert token_default.tempo is None


def test_tempo_selective_activation_can_run_standalone(isolated, tmp_path):
    """Tempo alone — no text, no audio agent. Proves the layer is a
    true peer (not nested inside the audio layer)."""
    from axiom_event_token import Coordinator
    wav = tmp_path / "m.wav"
    _write_wav(wav, synth_metronome(100))
    coord = Coordinator()
    token = coord.compose(
        audio={"wav_path": str(wav)},
        activate=("tempo",),
    )
    assert token.verify() is True
    assert token.tempo is not None
    assert token.text is None
    assert token.audio is None
    assert abs(token.tempo.payload["bpm"] - 100) <= BPM_TOLERANCE
