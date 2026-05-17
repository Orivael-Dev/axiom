# Axiom Audio — Phase A (ambient / physical-event)

Internal training note. Reflects what shipped in
`axiom_audio/` and the Phase B / C runway.

## What Phase A is

The **ambient / physical-event audio agent**. Takes a short mono WAV
clip and emits a signed `AudioReport` with six fields that match the
Audio layer of `AXIOM_EVENT_TOKEN`:

| Field                | Values                                              |
|---                   |---                                                  |
| `impact_profile`     | sharp_transient · soft_transient · sustained · silence |
| `material_signature` | glass-like · metal-like · wood-like · fabric-like · unknown |
| `decay_pattern`      | scattered_fragments · smooth_decay · reverberant · unknown |
| `depth`              | float 0–1 (low-frequency energy ratio)              |
| `width`              | float 0–1 (spectral spread across frames)           |
| `rhythm`             | single_impact · periodic · irregular                |

A `debug` block carries the underlying DSP numbers (attack_ms,
centroid_hz, high_freq_ratio, onset_count, peak_amp_db, …) so anyone
inspecting an audit report can trace WHY the classifier landed where
it did.

The classifier is **rule-based**, not ML — chosen on purpose so:
- The whole module stays stdlib-only (no numpy / scipy / librosa)
- Every verdict is explainable from the debug block
- The patent claim shape (signed + selective + auditable) is the
  point; the underlying engine is replaceable

## Acceptance test

The Phase A bar, hardcoded in `tests/test_axiom_audio.py`:

> Feed a synthesized glass-shatter WAV → the agent returns
> `(sharp_transient, glass-like, scattered_fragments)` with confidence
> > 0.6.

The shatter stimulus is built in-process from primitives (broadband
click + decaying HF tones + secondary scatter bursts + noise floor)
so no binary fixture lives in the repo.

Current measurement: confidence **0.875** on the canonical stimulus,
debug shows centroid ≈ 4.6 kHz, hf_ratio = 0.995, decay = -119 dB/s.

## How it wires into the 3D event token

`axiom_event_token.agents.AudioAgent` now branches:

```python
if inputs["audio"].get("wav_path"):
    # Real Phase A path — runs DSP on the file
    audio_report = axiom_audio.classify_clip(wav_path)
    return LayerReport.signed(agent="audio", payload=audio_report.payload, ...)
else:
    # Stub fallback — echoes caller-provided fields, original behaviour
    ...
```

So existing event-token callers still work. Anything that wants real
audio just passes `audio={"wav_path": "/path/to/clip.wav"}` to
`Coordinator.compose()`.

## Signing

Fresh HMAC namespace `axiom-audio-v1` — separate from
`axiom-event-token-layer-v1` so:
- An AudioReport verifies standalone, without pulling in the event-token package
- A forged audio payload can't be replayed into a different signing context

When the event-token AudioAgent wraps the AudioReport into a
LayerReport, that LayerReport is re-signed under
`axiom-event-token-layer-v1` per the existing pattern.

## What ISN'T in Phase A

- **Voice analysis** — Phase B. Will be its own `axiom_audio/voice.py`
  with: speaker characteristics, prosody, emotion proxies, pitch
  contour. Reuses `AudioReport`.
- **Music analysis** — Phase C. Tempo, key, harmonic density,
  instrument family. Reuses `AudioReport`.
- **Real ML models** — All three families currently rule-based. A
  future phase can drop in a small CNN behind the same API without
  any caller changes.
- **Long-form audio** — Phase A is sized for clips of a few seconds
  (toy-bot voice replies, ambient event captures). Streaming +
  long-form is Phase D.

## What ships with this module

```
axiom_audio/
  __init__.py          public exports
  features.py          stdlib DSP primitives (FFT, envelope, onsets, centroid)
  ambient.py           AmbientAudioAgent — rule-based classifier
  report.py            signed AudioReport dataclass + namespace
examples/
  audio_demo.py        synthesize glass-shatter + print signed report
tests/
  test_axiom_audio.py  10 tests covering acceptance + signing + integration
```

## What to measure before Phase B

The "measure" gate in the staged plan. Drive these before voice ships:

1. **Real-recording accuracy** — feed 20 actual recordings (cups,
   doors, fabric drops, drumsticks, glass) — what fraction get the
   right material_signature? Target: ≥ 80% on the four named
   categories.
2. **Latency** — currently the FFT is pure-Python and the demo
   runs in ~30ms for a 1-second clip. If real customer clips push
   us past 100ms, drop in numpy behind the `fft_magnitude`
   function without changing the API.
3. **False-positive rate on background noise** — feed 50 clips of
   room tone / TV chatter / kitchen background. How often do we
   wrongly emit a `sharp_transient`? Target: < 5%.

Once those three pass, Phase B (voice) is greenlit.
