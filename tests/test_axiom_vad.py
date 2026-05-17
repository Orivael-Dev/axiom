"""Tests for the Voice Activity Detection / dead-air gate.

Synthesizes clips with KNOWN silent + active regions and asserts the
VAD finds them within tight time tolerance.
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

SAMPLE_RATE = 16_000
REGION_TOLERANCE_S = 0.10   # ±100ms is generous; we use 20ms hops


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith(("axiom_audio", "axiom_signing", "axiom_event_token")):
            sys.modules.pop(mod, None)
    yield


def _write_wav(path: Path, samples: list[float], sr: int = SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        frames = bytearray()
        for s in samples:
            s = max(-1.0, min(1.0, s))
            frames.extend(struct.pack("<h", int(s * 32767)))
        w.writeframes(bytes(frames))


# ─── Synth helpers ──────────────────────────────────────────────────────


def _voiced_burst(duration_s: float, fundamental_hz: float = 200.0) -> list[float]:
    """A voiced-style burst: a buzzy tone with mild modulation, 200 Hz fundamental."""
    n = int(duration_s * SAMPLE_RATE)
    out = []
    for i in range(n):
        t = i / SAMPLE_RATE
        # 200 Hz fundamental + 400 Hz harmonic + 800 Hz, classic speech-like spectrum
        v = (
            0.5 * math.sin(2 * math.pi * fundamental_hz * t)
            + 0.25 * math.sin(2 * math.pi * fundamental_hz * 2 * t)
            + 0.1 * math.sin(2 * math.pi * fundamental_hz * 4 * t)
        )
        # 2 Hz amplitude modulation = syllable envelope
        env = 0.5 + 0.5 * math.sin(2 * math.pi * 2 * t)
        out.append(v * env * 0.7)
    return out


def _silence(duration_s: float) -> list[float]:
    return [0.0] * int(duration_s * SAMPLE_RATE)


def _quiet_noise(duration_s: float, level: float = 0.001) -> list[float]:
    """Simulated room-tone floor — way below the silence floor."""
    state = 1
    out = []
    for _ in range(int(duration_s * SAMPLE_RATE)):
        state = (state * 1103515245 + 12345) & 0x7fffffff
        out.append(((state / 0x7fffffff) * 2 - 1) * level)
    return out


# ─── Tests ──────────────────────────────────────────────────────────────


def test_pure_silence_finds_no_regions(isolated, tmp_path):
    from axiom_audio import classify_vad_clip
    wav = tmp_path / "silence.wav"
    _write_wav(wav, _silence(2.0))
    r = classify_vad_clip(str(wav))
    assert r.payload["region_count"] == 0
    assert r.payload["regions"] == []
    assert r.payload["activity_ratio"] == 0.0
    assert r.payload["total_silent_s"] >= 1.9


def test_room_tone_below_floor_finds_no_regions(isolated, tmp_path):
    from axiom_audio import classify_vad_clip
    wav = tmp_path / "room.wav"
    _write_wav(wav, _quiet_noise(2.0, level=0.001))
    r = classify_vad_clip(str(wav))
    assert r.payload["region_count"] == 0


def test_single_burst_in_silence_finds_one_region(isolated, tmp_path):
    """Pattern: 0.5s silence + 1.0s voiced burst + 0.5s silence.
    Expect exactly ONE region around [0.5, 1.5]."""
    from axiom_audio import classify_vad_clip
    samples = _silence(0.5) + _voiced_burst(1.0) + _silence(0.5)
    wav = tmp_path / "burst.wav"
    _write_wav(wav, samples)
    r = classify_vad_clip(str(wav))
    assert r.payload["region_count"] == 1, r.payload["regions"]
    start, end = r.payload["regions"][0]
    assert abs(start - 0.5) < REGION_TOLERANCE_S, f"expected start ≈ 0.5, got {start}"
    assert abs(end - 1.5) < REGION_TOLERANCE_S, f"expected end ≈ 1.5, got {end}"


def test_three_bursts_with_long_gaps_finds_three_regions(isolated, tmp_path):
    """1s silent + 0.5s burst + 1s silent + 0.5s burst + 1s silent + 0.5s burst.
    Three distinct regions, gaps > 200ms → no merging."""
    from axiom_audio import classify_vad_clip
    samples = (
        _silence(1.0) + _voiced_burst(0.5)
        + _silence(1.0) + _voiced_burst(0.5)
        + _silence(1.0) + _voiced_burst(0.5)
    )
    wav = tmp_path / "three.wav"
    _write_wav(wav, samples)
    r = classify_vad_clip(str(wav))
    assert r.payload["region_count"] == 3, r.payload["regions"]


def test_two_bursts_with_short_gap_merge_into_one(isolated, tmp_path):
    """0.5s burst + 0.10s silence + 0.5s burst — 100ms gap is shorter
    than the 200ms merge threshold, so we expect ONE merged region."""
    from axiom_audio import classify_vad_clip
    samples = _voiced_burst(0.5) + _silence(0.10) + _voiced_burst(0.5)
    wav = tmp_path / "merge.wav"
    _write_wav(wav, samples)
    r = classify_vad_clip(str(wav))
    assert r.payload["region_count"] == 1, r.payload["regions"]


def test_very_short_blip_is_filtered_out(isolated, tmp_path):
    """A 30ms blip is shorter than MIN_ACTIVE_MS (80ms) — should be ignored."""
    from axiom_audio import classify_vad_clip
    samples = _silence(0.5) + _voiced_burst(0.03) + _silence(0.5)
    wav = tmp_path / "blip.wav"
    _write_wav(wav, samples)
    r = classify_vad_clip(str(wav))
    assert r.payload["region_count"] == 0


def test_activity_ratio_matches_voiced_fraction(isolated, tmp_path):
    """1.0s burst inside a 4.0s clip → activity_ratio ≈ 0.25."""
    from axiom_audio import classify_vad_clip
    samples = _silence(1.5) + _voiced_burst(1.0) + _silence(1.5)
    wav = tmp_path / "ratio.wav"
    _write_wav(wav, samples)
    r = classify_vad_clip(str(wav))
    assert 0.20 < r.payload["activity_ratio"] < 0.30


def test_signature_verifies(isolated, tmp_path):
    from axiom_audio import classify_vad_clip
    wav = tmp_path / "v.wav"
    _write_wav(wav, _silence(0.5) + _voiced_burst(0.5) + _silence(0.5))
    r = classify_vad_clip(str(wav))
    assert r.verify() is True


def test_tampered_payload_fails_verify(isolated, tmp_path):
    from axiom_audio import VADReport, classify_vad_clip
    wav = tmp_path / "v.wav"
    _write_wav(wav, _voiced_burst(0.5))
    original = classify_vad_clip(str(wav))
    tampered = VADReport(
        payload={**original.payload, "activity_ratio": 999.0},
        confidence=original.confidence, signature=original.signature,
    )
    assert tampered.verify() is False


def test_vad_uses_dedicated_namespace(isolated):
    from axiom_audio.vad import VAD_KEY_NS
    from axiom_audio.report import AUDIO_KEY_NS
    from axiom_audio.tempo import TEMPO_KEY_NS
    assert VAD_KEY_NS == b"axiom-vad-v1"
    assert VAD_KEY_NS != AUDIO_KEY_NS
    assert VAD_KEY_NS != TEMPO_KEY_NS


def test_voice_activity_regions_helper_returns_same_list(isolated, tmp_path):
    """The convenience helper used internally by VoiceAgent matches
    the regions in the full signed report."""
    from axiom_audio import classify_vad_clip, voice_activity_regions, load_wav
    wav = tmp_path / "v.wav"
    _write_wav(wav, _silence(0.5) + _voiced_burst(0.5) + _silence(0.5))
    samples, sr = load_wav(str(wav))
    regions = voice_activity_regions(samples, sr)
    full_report = classify_vad_clip(str(wav))
    # Same count + same approximate boundaries
    assert len(regions) == full_report.payload["region_count"]
    if regions:
        assert abs(regions[0][0] - full_report.payload["regions"][0][0]) < 1e-3
        assert abs(regions[0][1] - full_report.payload["regions"][0][1]) < 1e-3


def test_vad_agent_integrates_with_event_token_coordinator(isolated, tmp_path):
    from axiom_event_token import Coordinator
    wav = tmp_path / "v.wav"
    _write_wav(wav, _silence(0.5) + _voiced_burst(1.0) + _silence(0.5))
    coord = Coordinator()
    token = coord.compose(
        audio={"wav_path": str(wav)},
        activate=("vad",),
    )
    assert token.verify() is True
    assert token.vad is not None
    assert token.vad.payload["region_count"] == 1


def test_vad_off_by_default_not_in_default_activation(isolated, tmp_path):
    """VAD must not run unless explicitly activated — otherwise every
    EventToken pays the VAD cost on every call."""
    from axiom_event_token import Coordinator
    coord = Coordinator()
    token = coord.compose(text="hi", activate=("text", "governance"))
    assert token.vad is None
