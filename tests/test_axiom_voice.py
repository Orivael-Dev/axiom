"""Tests for the Phase B voice agent.

Synthesizes voice-like stimuli at known fundamental frequencies and
verifies the agent recovers pitch + register + prosody + voicing
ratio. Same time-domain autocorrelation algorithm as tempo, lag range
mapped to the adult vocal F0 band (50–400 Hz).
"""
from __future__ import annotations

import math
import random
import struct
import sys
import wave
from pathlib import Path

import pytest

SAMPLE_RATE = 16_000
PITCH_TOLERANCE_HZ = 8.0   # F0 estimation within ±8 Hz on clean stimuli


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


# ─── Stimulus synth ─────────────────────────────────────────────────────


def synth_voice_at_pitch(
    f0_hz: float, duration_s: float = 1.5, pitch_jitter: float = 0.0,
    syllable_rate_hz: float = 4.0,
) -> list[float]:
    """Speech-like voiced signal at the given F0.

    Builds: fundamental + 2nd + 3rd + 4th harmonics, with an
    amplitude envelope at `syllable_rate_hz` to mimic syllable
    onsets. `pitch_jitter` is the multiplicative jitter applied to
    F0 — 0.0 = perfectly monotone, 0.05 = ±5% melodic variation.
    """
    rng = random.Random(int(f0_hz))
    n = int(duration_s * SAMPLE_RATE)
    out = [0.0] * n
    # Slow F0 drift, frame by frame
    f0_drift = [f0_hz * (1 + (rng.random() * 2 - 1) * pitch_jitter)
                for _ in range(int(duration_s * 20))]  # one F0 per 50ms
    phase = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        # F0 for this 50ms block
        block_idx = min(len(f0_drift) - 1, int(t * 20))
        f = f0_drift[block_idx]
        phase += 2 * math.pi * f / SAMPLE_RATE
        # Glottal-pulse approximation: fundamental + decaying harmonics
        v = (
            0.6 * math.sin(phase)
            + 0.3 * math.sin(2 * phase)
            + 0.15 * math.sin(3 * phase)
            + 0.05 * math.sin(4 * phase)
        )
        # Syllable envelope: full-wave-rectified sinusoid, raised slightly
        # so we have brief energy minima between syllables
        env = 0.3 + 0.7 * abs(math.sin(math.pi * syllable_rate_hz * t))
        out[i] = v * env * 0.6
    return out


def _silence(duration_s: float) -> list[float]:
    return [0.0] * int(duration_s * SAMPLE_RATE)


# ─── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("f0_hz,expected_register", [
    (110, "low"),
    (180, "mid"),
    (280, "high"),
])
def test_voice_pitch_estimated_within_tolerance(
    isolated, tmp_path, f0_hz, expected_register,
):
    """Synthesized voice at F0 → agent recovers F0 ± 8 Hz and the right register."""
    from axiom_audio import classify_voice_clip
    wav = tmp_path / f"v_{f0_hz}.wav"
    _write_wav(wav, synth_voice_at_pitch(f0_hz))
    r = classify_voice_clip(str(wav))
    assert r.payload["is_speech"] is True
    estimated = r.payload["pitch_hz_mean"]
    assert abs(estimated - f0_hz) <= PITCH_TOLERANCE_HZ, (
        f"expected F0 ≈ {f0_hz} Hz ± {PITCH_TOLERANCE_HZ}, got {estimated}"
    )
    assert r.payload["speaker_register"] == expected_register


def test_silence_returns_no_speech(isolated, tmp_path):
    from axiom_audio import classify_voice_clip
    wav = tmp_path / "silence.wav"
    _write_wav(wav, _silence(2.0))
    r = classify_voice_clip(str(wav))
    assert r.payload["is_speech"] is False
    assert r.payload["voicing_ratio"] == 0.0
    assert r.payload["pitch_hz_mean"] == 0.0


def test_voice_with_silence_padding_isolates_speech(isolated, tmp_path):
    """0.5s silence + 1.0s voice + 0.5s silence → voicing_ratio ≈ 0.5
    AND F0 is correctly estimated despite the dead air on both ends."""
    from axiom_audio import classify_voice_clip
    samples = _silence(0.5) + synth_voice_at_pitch(180) + _silence(0.5)
    wav = tmp_path / "padded.wav"
    _write_wav(wav, samples)
    r = classify_voice_clip(str(wav))
    assert r.payload["is_speech"] is True
    assert 0.35 < r.payload["voicing_ratio"] < 0.65
    assert abs(r.payload["pitch_hz_mean"] - 180) <= PITCH_TOLERANCE_HZ


def test_monotone_voice_classified_as_monotone(isolated, tmp_path):
    """No pitch jitter → coefficient of variation < 6% → 'monotone'."""
    from axiom_audio import classify_voice_clip
    wav = tmp_path / "monotone.wav"
    _write_wav(wav, synth_voice_at_pitch(180, pitch_jitter=0.0))
    r = classify_voice_clip(str(wav))
    assert r.payload["prosody"] == "monotone"


def test_excited_voice_classified_as_excited(isolated, tmp_path):
    """Heavy pitch jitter → CV > 20% → 'excited'.

    Uses jitter=0.6 (±60% per 50ms block) so the measured F0 CV
    clearly exceeds the 20% threshold, not just brushing it.
    """
    from axiom_audio import classify_voice_clip
    wav = tmp_path / "excited.wav"
    _write_wav(wav, synth_voice_at_pitch(180, pitch_jitter=0.6))
    r = classify_voice_clip(str(wav))
    assert r.payload["prosody"] == "excited", (
        f"got {r.payload['prosody']} (CV={r.payload['debug']['pitch_cv']})"
    )


def test_voice_signature_verifies(isolated, tmp_path):
    from axiom_audio import classify_voice_clip
    wav = tmp_path / "v.wav"
    _write_wav(wav, synth_voice_at_pitch(180))
    r = classify_voice_clip(str(wav))
    assert r.verify() is True


def test_tampered_voice_report_fails_verify(isolated, tmp_path):
    from axiom_audio import VoiceReport, classify_voice_clip
    wav = tmp_path / "v.wav"
    _write_wav(wav, synth_voice_at_pitch(180))
    original = classify_voice_clip(str(wav))
    tampered = VoiceReport(
        payload={**original.payload, "pitch_hz_mean": 999.0},
        confidence=original.confidence, signature=original.signature,
    )
    assert tampered.verify() is False


def test_voice_uses_dedicated_namespace(isolated):
    from axiom_audio.voice import VOICE_KEY_NS
    from axiom_audio.vad import VAD_KEY_NS
    from axiom_audio.report import AUDIO_KEY_NS
    from axiom_audio.tempo import TEMPO_KEY_NS
    assert VOICE_KEY_NS == b"axiom-voice-v1"
    assert VOICE_KEY_NS != VAD_KEY_NS
    assert VOICE_KEY_NS != AUDIO_KEY_NS
    assert VOICE_KEY_NS != TEMPO_KEY_NS


def test_voice_agent_integrates_with_event_token_coordinator(isolated, tmp_path):
    from axiom_event_token import Coordinator
    wav = tmp_path / "v.wav"
    _write_wav(wav, synth_voice_at_pitch(180))
    coord = Coordinator()
    token = coord.compose(
        audio={"wav_path": str(wav)},
        activate=("voice", "governance"),
    )
    assert token.verify() is True
    assert token.voice is not None
    assert token.voice.payload["is_speech"] is True
    assert "voice" in token.governance.payload["evidence_trace"]


def test_voice_can_selectively_activate_with_vad(isolated, tmp_path):
    """VAD and Voice are independent peers — both can activate together
    and produce signed reports."""
    from axiom_event_token import Coordinator
    wav = tmp_path / "v.wav"
    _write_wav(wav, _silence(0.3) + synth_voice_at_pitch(180) + _silence(0.3))
    coord = Coordinator()
    token = coord.compose(
        audio={"wav_path": str(wav)},
        activate=("vad", "voice", "governance"),
    )
    assert token.verify() is True
    assert token.vad is not None
    assert token.voice is not None
    # VAD reports a region; Voice reports speech detected
    assert token.vad.payload["region_count"] >= 1
    assert token.voice.payload["is_speech"] is True
