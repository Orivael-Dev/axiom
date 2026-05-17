# Audio measurement dataset

Drop real recordings here to drive the Phase A gate verdict. The
audio harness (`scripts/audio_harness.py`) walks this directory.

## Layout

One folder per material label. The folder name IS the expected label.
A folder called `background` holds negative examples (any transient
verdict on a background clip is a false positive).

```
audio_dataset/
  glass-like/    *.wav   — cup breaks, bottle clinks, window cracks
  metal-like/    *.wav   — coin drops, key jingles, pan strikes
  wood-like/     *.wav   — knocks on doors, drumsticks, blocks
  fabric-like/   *.wav   — cushion drops, cloth thuds, soft falls
  background/    *.wav   — room tone, kitchen hum, TV chatter
```

## File format

- 16-bit PCM WAV, mono preferred (stereo is auto-downmixed)
- 16 kHz sample rate ideal; other rates work, the harness reads
  whatever the WAV header says
- 0.5 to 3 seconds per clip; longer clips work but slow the FFT down

## Running the gate

```bash
export AXIOM_MASTER_KEY=...
python3 scripts/audio_harness.py --dataset ./audio_dataset \
    --output-json results.json \
    --markdown results.md
```

Exit code 0 = all three gates passed; non-zero = at least one gate
failed. CI can gate Phase B greenlight on this exit code.

## Phase A gate thresholds

| Metric | Threshold |
|---|---|
| Material accuracy on positive clips | ≥ 80% |
| Latency p95 per 1-second clip       | < 100 ms |
| False-positive rate on background    | ≤ 5% |

## Source ideas for real recordings

- **Free, no-license-fuss**: Freesound.org (CC-BY clips for each
  material), Pixabay sound effects, Mixkit, Zapsplat
- **Easy DIY**: phone voice memos in a quiet room — drop a cup
  on linoleum, knock on a wooden door, etc.
- **Suggested minimum** for a meaningful gate: 20 clips per material
  (80 positives total) + 50 background clips
