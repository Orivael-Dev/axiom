"""Tests for the Phase A ambient audio agent.

Synthesizes WAV stimuli in tmp_path — no binary fixtures committed.
Each stimulus is designed to drive ONE expected classification.

Phase A acceptance, from the plan:
  feed a synthesized glass-shatter WAV → agent returns
  (sharp_transient, glass-like, scattered_fragments, confidence > 0.6)
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


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_audio", "axiom_signing", "axiom_event_token",
        )):
            sys.modules.pop(mod, None)
    yield


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


# ─── Stimulus generators ────────────────────────────────────────────────


def _silence(duration_s: float = 0.5) -> list[float]:
    return [0.0] * int(duration_s * SAMPLE_RATE)


def _glass_shatter() -> list[float]:
    """Sharp broadband click + HF tone clusters + secondary scatter."""
    rng = random.Random(42)
    n = int(1.0 * SAMPLE_RATE)
    out = [0.0] * n
    # Primary click — 5ms broadband
    click_len = int(0.005 * SAMPLE_RATE)
    for i in range(click_len):
        out[i] += (rng.random() * 2 - 1) * 0.9
    # HF tones decaying
    for tone_hz in (4000, 5500, 7000):
        for i in range(int(0.2 * SAMPLE_RATE)):
            t = i / SAMPLE_RATE
            decay = math.exp(-t * 15)
            out[i] += 0.25 * math.sin(2 * math.pi * tone_hz * t) * decay
    # Scatter onsets at 80 / 160 / 280 ms
    for delay_s in (0.080, 0.160, 0.280):
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


def _soft_thud() -> list[float]:
    """Low-frequency thud: fabric/cushion. Centroid low, hf_ratio low."""
    rng = random.Random(7)
    n = int(0.5 * SAMPLE_RATE)
    out = [0.0] * n
    # Slow attack low-frequency burst
    for i in range(int(0.05 * SAMPLE_RATE)):
        t = i / SAMPLE_RATE
        env_attack = t / 0.04 if t < 0.04 else 1.0
        # ~200 Hz dominant tone with noise
        out[i] += 0.6 * env_attack * (
            0.7 * math.sin(2 * math.pi * 200 * t)
            + 0.3 * (rng.random() * 2 - 1) * 0.2  # tiny noise
        )
    # Slow decay
    peak_amp = 0.6
    for i in range(int(0.05 * SAMPLE_RATE), n):
        t = (i - int(0.05 * SAMPLE_RATE)) / SAMPLE_RATE
        decay = math.exp(-t * 8)
        out[i] += peak_amp * decay * math.sin(2 * math.pi * 200 * (i / SAMPLE_RATE))
    return out


def _metal_ring() -> list[float]:
    """Single sharp transient + sustained narrow tone — metallic ring."""
    rng = random.Random(13)
    n = int(1.0 * SAMPLE_RATE)
    out = [0.0] * n
    # Click
    click_len = int(0.003 * SAMPLE_RATE)
    for i in range(click_len):
        out[i] += (rng.random() * 2 - 1) * 0.8
    # Long sustained narrow tone at 3.5 kHz
    for i in range(n):
        t = i / SAMPLE_RATE
        decay = math.exp(-t * 2)  # slow decay
        out[i] += 0.7 * decay * math.sin(2 * math.pi * 3500 * t)
    return out


def _periodic_clicks() -> list[float]:
    """Six evenly-spaced clicks at 100ms intervals — periodic rhythm."""
    rng = random.Random(99)
    n = int(0.8 * SAMPLE_RATE)
    out = [0.0] * n
    for k in range(6):
        start = int(k * 0.100 * SAMPLE_RATE)
        for i in range(int(0.005 * SAMPLE_RATE)):
            if start + i >= n:
                break
            out[start + i] += (rng.random() * 2 - 1) * 0.7
    return out


# ─── Tests ──────────────────────────────────────────────────────────────


# 1. The Phase A acceptance test
def test_glass_shatter_meets_phase_a_acceptance(isolated, tmp_path):
    from axiom_audio import classify_clip

    wav = tmp_path / "shatter.wav"
    _write_wav(wav, _glass_shatter())
    r = classify_clip(str(wav))

    assert r.payload["impact_profile"] == "sharp_transient"
    assert r.payload["material_signature"] == "glass-like"
    assert r.payload["decay_pattern"] == "scattered_fragments"
    assert r.confidence > 0.6, f"confidence {r.confidence} below acceptance"


# 2. Signature verifies
def test_audio_report_signature_verifies(isolated, tmp_path):
    from axiom_audio import classify_clip
    wav = tmp_path / "shatter.wav"
    _write_wav(wav, _glass_shatter())
    r = classify_clip(str(wav))
    assert r.verify() is True


# 3. Tampering breaks the signature
def test_tampered_payload_fails_verify(isolated, tmp_path):
    from axiom_audio import AudioReport, classify_clip

    wav = tmp_path / "shatter.wav"
    _write_wav(wav, _glass_shatter())
    original = classify_clip(str(wav))

    tampered = AudioReport(
        payload={**original.payload, "material_signature": "TAMPERED"},
        confidence=original.confidence,
        signature=original.signature,  # stale
    )
    assert tampered.verify() is False


# 4. Silence is detected as silence
def test_silent_clip_returns_silence(isolated, tmp_path):
    from axiom_audio import classify_clip
    wav = tmp_path / "silence.wav"
    _write_wav(wav, _silence(0.3))
    r = classify_clip(str(wav))
    assert r.payload["impact_profile"] == "silence"
    assert r.payload["material_signature"] == "unknown"


# 5. Soft low-frequency thud → fabric/wood (NOT glass-like)
def test_soft_thud_not_classified_as_glass(isolated, tmp_path):
    from axiom_audio import classify_clip
    wav = tmp_path / "thud.wav"
    _write_wav(wav, _soft_thud())
    r = classify_clip(str(wav))
    assert r.payload["material_signature"] != "glass-like"
    # And the impact profile must NOT be a sharp glass-style transient
    assert r.payload["impact_profile"] != "sharp_transient"


# 6. Metallic ring is detected as metal-like (resonant peak)
def test_metal_ring_classified_as_metal(isolated, tmp_path):
    from axiom_audio import classify_clip
    wav = tmp_path / "ring.wav"
    _write_wav(wav, _metal_ring())
    r = classify_clip(str(wav))
    assert r.payload["material_signature"] == "metal-like"


# 7. Periodic clicks → rhythm = "periodic"
def test_periodic_clicks_detected_as_periodic_rhythm(isolated, tmp_path):
    from axiom_audio import classify_clip
    wav = tmp_path / "clicks.wav"
    _write_wav(wav, _periodic_clicks())
    r = classify_clip(str(wav))
    assert r.payload["rhythm"] == "periodic"


# 8. JSON roundtrip preserves signature
def test_roundtrip_json_serialize_deserialize(isolated, tmp_path):
    import json
    from axiom_audio import AudioReport, classify_clip

    wav = tmp_path / "shatter.wav"
    _write_wav(wav, _glass_shatter())
    original = classify_clip(str(wav))

    raw = original.to_json()
    restored = AudioReport.from_dict(json.loads(raw))
    assert restored.signature == original.signature
    assert restored.verify() is True


# Sections 9 + 10 (AudioAgent in the event-token Coordinator) live with
# the event_token bonded-pair PR — the Coordinator's `audio` agent
# registration ships there, not here.
