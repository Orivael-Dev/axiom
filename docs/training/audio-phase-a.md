# Axiom Audio — Phase A + B (ambient + tempo + VAD + voice)

Internal training note. Reflects what shipped in `axiom_audio/` and
the Phase C runway.

Four agents now in production behind the AXIOM_EVENT_TOKEN Coordinator:

- **AmbientAudioAgent** — what kind of event happened (glass shatter,
  metal ring, wood knock, fabric thud) → fuzzy categorical labels
- **TempoEstimator** — at what BPM is the rhythm → numeric ground truth
- **VoiceActivityDetector** — silence/dead-air gate, returns the
  active timeline regions
- **VoiceAgent** — pitch + prosody + speaker register + voicing
  ratio + syllable rate (NOT speech-to-text — that's a separate
  layer for later)

The tempo agent serves as the **numeric-truth anchor** for the audio
testing library: a 120 BPM metronome IS 120 BPM, so you can validate
agent correctness against an absolute number, not just a subjective
material label.

The VAD agent serves three jobs simultaneously:

1. Standalone activation — emit a signed timeline of active regions
   in a long recording (chunking for streaming).
2. Preprocess inside VoiceAgent — silence is gated out before pitch
   analysis, so a 5-second clip with 200ms of speech in the middle
   produces the same verdict as a 200ms speech-only clip.
3. Audit-visible dead-air metric — "this 5-second clip is 80%
   silence" surfaces as a first-class fact in the EventToken trace.

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
  __init__.py            public exports
  features.py            stdlib DSP primitives (FFT, envelope, onsets, centroid, ZCR)
  ambient.py             AmbientAudioAgent — rule-based material classifier
  tempo.py               TempoEstimator — autocorrelation BPM (axiom-tempo-v1)
  vad.py                 VoiceActivityDetector — energy + ZCR gate (axiom-vad-v1)
  voice.py               VoiceAgent — autocorrelation F0 + prosody (axiom-voice-v1)
  report.py              signed AudioReport + namespace (axiom-audio-v1)
axiom_event_token/
  agents.py              5 audio agents (AudioAgent + TempoAgent + VADAgent +
                         VoiceAgent + GovernanceAgent) registered with Coordinator
examples/
  audio_demo.py          synthesize glass-shatter + print signed report
scripts/
  audio_synth.py         write labeled WAV stimuli to disk for HUMAN listening
                         (now 26 clips: materials + background + metronome + voice)
  audio_harness.py       measure CLASSIFIER accuracy against gate thresholds
                         (now 4 gates: material + latency + FP rate + tempo)
tests/
  test_axiom_audio.py    10 tests — ambient classifier
  test_axiom_tempo.py    15 tests — BPM accuracy ±3 across [60, 90, 100, 120, 150, 180]
  test_axiom_vad.py      13 tests — silence detection, region merging, blip filtering
  test_axiom_voice.py    12 tests — F0 estimation, register, prosody, VAD integration
  test_audio_harness.py  3 tests — harness smoke
```

## Listening to the test stimuli (playback workflow)

The server is headless and the synth stimuli need ear-validation, so
the workflow is: synthesize to disk → copy to your laptop → play in
any media player.

```bash
# On the server:
python3 scripts/audio_synth.py --out ./samples
# Writes 20 WAV files into ./samples/{glass-like,metal-like,wood-like,
# fabric-like,background,metronome}/*.wav  (~32s of audio total)

# From your laptop:
scp -r box:axiom/samples ./
# Double-click any .wav — Windows Media Player, VLC, QuickTime,
# Chrome, Firefox all play 16-bit mono PCM natively.
```

`audio_synth.py` reuses the exact same generators that the harness +
tempo tests use, so what you hear is byte-identical to what the
classifiers run on.

## What to measure before Phase B

The "measure" gate in the staged plan. Driven by
`scripts/audio_harness.py` — one CLI that takes a labeled dataset
and reports against all three gate thresholds.

| Metric | Threshold | What it tests |
|---|---|---|
| Material accuracy | ≥ 80% | Positive clips classified into the right material |
| Latency p95       | < 100 ms | One classification on a 1-second clip |
| False-positive rate | ≤ 5% | Background clips falsely flagged as a transient |
| Tempo accuracy    | ≥ 80% within ±3 BPM | Metronome clips estimated correctly |

### Demo run (no recordings needed)

```bash
export AXIOM_MASTER_KEY=...
python3 scripts/audio_harness.py --demo
```

Synthesizes ~14 clips in process and runs the full gate. Current
demo measurement: 100% accuracy, 44.6 ms p95 latency, 0% FP rate.
This validates the methodology + the classifier on purpose-built
stimuli; it is NOT the real-world gate.

### Real-data run (the actual gate)

Drop labeled WAV files into `audio_dataset/` (layout documented in
`audio_dataset/README.md`):

```
audio_dataset/
  glass-like/    *.wav
  metal-like/    *.wav
  wood-like/     *.wav
  fabric-like/   *.wav
  background/    *.wav
  tempo-60/      *.wav    metronome at 60 BPM
  tempo-90/      *.wav
  tempo-120/     *.wav
  ...                     one folder per BPM you have recordings for
```

Suggested minimum: 20 clips per material (80 positives) + 50
background clips. Then:

```bash
python3 scripts/audio_harness.py --dataset ./audio_dataset \
    --output-json results.json \
    --markdown results.md
```

Exit code 0 ⇒ all three gates passed and Phase B (voice) is
greenlit. Non-zero ⇒ at least one gate failed; the JSON output has
per-clip predictions so you can see which calls the classifier
botched.

### When latency fails the gate

The pure-Python FFT is the hot path. If the p95 climbs past 100 ms
on real-customer clips:
- Drop in numpy behind `axiom_audio.features.fft_magnitude` — no
  caller changes needed
- OR swap to scipy.fft if a numpy dep is already pulled in

### When material accuracy fails the gate

The per-label breakdown in the harness output shows which
categories miss. The fix is usually one of:
- Loosen / tighten the threshold in `axiom_audio.ambient._classify_material`
  (e.g. wood's `800 < centroid < 2500 Hz` window)
- Add a new branch (e.g. plastic-like) if a category is collapsing
  into "unknown"
- Pull in a real recording, run it through the harness with
  `--output-json`, and inspect the `debug` block to see which
  numeric feature is off

### When false-positive rate fails the gate

Background clips firing transient verdicts means the onset
detector's adaptive threshold is too sensitive. Tune
`detect_onsets(rel_threshold=...)` upward, or lengthen the
trailing-median window, in `axiom_audio.features.detect_onsets`.
