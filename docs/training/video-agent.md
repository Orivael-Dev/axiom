# Training manual — Video Agent (Phase A)

> **`axiom_video/`** — modular, signed video-topology detectors.
> Four agents (object tracker / motion / impact / temporal-chain),
> each signed under its own HMAC namespace, composable through the
> event-token Coordinator. Phase A operates on **scene graphs**
> from an upstream object detector; raw-frame ingestion is a
> future concern.

## What it is

```
scene_graph (objects, bboxes, frame_index, [extras.color])
         │
         ▼
  [1] ObjectTracker          axiom-video-objects-v1
                              IoU-matched tracks + flicker filter
         │
         ▼
  [2] MotionClassifier       axiom-video-motion-v1
                              static / downward / upward / lateral /
                              accelerating / erratic
         │
         ▼
  [3] ImpactDetector         axiom-video-impact-v1
                              deceleration + contact events
         │
         ▼
  [4] TemporalChainExtractor axiom-video-temporal-v1
                              appear → motion_start → contact →
                              motion_change → disappear

  ─── parallel sensory layers (do not require [3]/[4] output) ───

  [5] TimeKeeper             axiom-video-timekeeper-v1
                              consumes [4]'s event stream;
                              rhythm score, silence detection,
                              burst detection — parallels
                              axiom_audio.tempo but over events
         │
  [6] ColorWatcher           axiom-video-color-v1
                              consumes scene_graph directly via
                              Object.extras['color']; HSV
                              partitioning → 18 color labels +
                              shift events
```

Each agent emits a signed `*Report` that the EventToken
`Coordinator` can selectively activate, sign-of-signs, and
combine with text / audio / physics verdicts.

Same architecture as audio Phase A (material / voice / VAD / tempo).

## Phase A scope — what we shipped vs deferred

| Piece | Phase A | Deferred |
|---|:---:|:---:|
| 4 core detector agents, each signed | ✅ | |
| **TimeKeeper** — rhythm + silence + burst over event stream | ✅ | |
| **ColorWatcher** — HSV partitioning + shift events | ✅ | |
| 14 synthetic reference scenes | ✅ | |
| Acceptance gates (motion / impact / signatures) | ✅ 100/100/100 | |
| EventToken integration (`VideoAgent` 6 sub-reports + summary) | ✅ | |
| Back-compat with legacy stub-shape inputs | ✅ | |
| 51 hermetic tests (25 detector + 26 time/color) | ✅ | |
| Raw-frame ingestion (frames → scene graph) | | Phase B |
| Pixel-sampling color ingester (frames → extras['color']) | | Phase B |
| Object-class fine-tuning / vision model | | n/a — customer brings |
| Fracture-pattern classifier (`radial_scatter` etc.) | | Phase B |
| Live-camera streaming demo | | Phase B |

## Who it's for

| Buyer profile | Pitch |
|---|---|
| AI-toy maker | "Camera-equipped toy detects 'reach → grip → cup tilt → fall' as a signed event chain without storing raw video. Show your COPPA reviewer the audit trail, not the frames." |
| Smart-home camera | "Replace 'the AI says it saw a person' with a signed event chain a regulator can replay. Privacy-safe — pixels never leave the device." |
| Dashcam / insurance | "Verified incident reports — every claim about a collision (deceleration event + contact event) is HMAC-signed at the edge." |
| Sports analytics | "Auto-tag actions across a full match via motion + temporal-chain agents, no manual frame review. Signed for league-level audit." |

## Why this beats monolithic VLMs

(See [`audio-agent-vs-llm.md`](audio-agent-vs-llm.md) for the
extended framing on the audio side — same advantages apply to
video.)

Quick version:

| Property | Gemini 2.5 video / GPT-4o video / Qwen2-VL | AXIOM video |
|---|:---:|:---:|
| Signed per-property verdicts | ✗ | ✅ (4 namespaces) |
| Selective activation | ✗ (full model always) | ✅ |
| Deterministic across runs | ✗ | ✅ |
| Falsifiable per agent | ✗ | ✅ (gates locked) |
| Modular replacement | ✗ | ✅ |
| Local + tiny compute | partial (small VLMs exist) | ✅ |
| Composes with event-token | ✗ | ✅ |
| Zero per-second cost | ✗ | ✅ |

Where VLMs win: open-ended scene description ("what's happening in
this video"), cross-frame semantic reasoning, language-grounded
queries. AXIOM video doesn't try to do those. **The composition
pitch is the same as audio: VLM + AXIOM > VLM alone.**

## The 14 reference scenes

`scripts/video_harness.py` ships procedural scene-graph generators
that exercise every motion class + every impact type:

| Scene | Expected motion | Expected impacts | Tests |
|---|---|---:|---|
| `static_single`         | static       | 0 | one parked object, baseline |
| `static_two`            | static       | 0 | two parked, no contact |
| `downward_freefall`     | downward     | 0 | constant +y velocity |
| `upward_throw`          | upward       | 0 | constant -y velocity |
| `lateral_slide`         | lateral      | 0 | pure +x translation |
| `diagonal_slide`        | lateral      | 0 | dx >> dy → lateral wins |
| `ball_bounce`           | downward     | 2 | throw-and-fall, decel at apex |
| `drop_and_impact_floor` | downward     | 1 | cup falls + halts at floor |
| `cup_tilt_pour`         | lateral      | 0 | subtle drift (tipping) |
| `two_objects_collide`   | lateral      | 1 | balls meet in middle |
| `handover`              | lateral      | 2 | hand contacts cup + decels |
| `flicker_track`         | static       | 0 | 1-frame ghost filtered out |
| `erratic_dance`         | erratic      | 0 | many direction changes |
| `reach_grip_tilt_fall`  | lateral      | 3 | flagship use case (hand+cup) |

Acceptance gates (locked in by `test_harness_passes_all_gates`):

| Gate | Threshold | Current |
|---|---|:---:|
| Motion classification | ≥ 85% (12/14) | **100% (14/14)** ✅ |
| Impact detection (exact match) | 100% (no false positives) | **100% (14/14)** ✅ |
| Signature verification | 100% on every report | **100% (14/14)** ✅ |

## Common workflows

### Workflow A: One-off detection on a scene graph

```python
from axiom_video import (
    ObjectTracker, MotionClassifier, ImpactDetector, TemporalChainExtractor,
)

# scene_graph comes from the customer's upstream detector (YOLO / Detectron / OpenCV)
tracks = ObjectTracker().track(scene_graph)
motions = MotionClassifier().classify(tracks)
impacts = ImpactDetector().detect(tracks, motions)
chain = TemporalChainExtractor().extract(tracks, motions, impacts)

assert tracks.verify() and motions.verify() and impacts.verify() and chain.verify()
print(motions.payload["dominant_class"])      # "downward"
print([e.type for e in chain.events])         # ["appear", "motion_start", "contact", ...]
```

### Workflow B: Via the event-token Coordinator (selective activation)

```python
from axiom_event_token import Coordinator

token = Coordinator().compose(
    video={"scene_graph": my_scene_graph},
    activate=("video", "governance"),
)
assert token.verify()
print(token.video.payload["summary"]["dominant_motion"])   # "downward"
print(token.video.payload["summary"]["n_impacts"])         # 1
```

The four sub-reports nest inside `token.video.payload`:
- `object_track_report`
- `motion_report`
- `impact_report`
- `temporal_chain_report`

### Workflow C: Run the synthetic harness

```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
python3 scripts/video_harness.py
```

Output: per-scene `✓/✗` for motion + impacts + signatures, plus a
gate summary. Exit `0` if every gate passes, `2` if any fails.

For machine-readable: `--json` flag. For a single scene:
`--scene <0-13>`.

## Key concepts

### Detector-agnostic input

AXIOM video doesn't classify pixels. It consumes the OUTPUT of an
object detector — `{frame_index, objects: [{id, label, bbox, ...}]}`
per frame. Customer brings their own tracker:

| Their stack | How they feed AXIOM |
|---|---|
| YOLOv8 + Norfair tracker | Map `Tracker.update` output → list of `Object` |
| Detectron2 | Same shape |
| OpenCV multi-tracker | Use the OpenCV bbox + a fixed `id` per tracker handle |
| Custom edge silicon | Whatever produces the bbox + ID; just map to `Object` |

Cross-frame identity matching is OPTIONAL — if the upstream
detector carries stable IDs (e.g. `"cup-A"`), AXIOM passes them
through. If IDs are placeholder numerics (`"0"`, `"1"`), AXIOM
re-matches by IoU + label equality.

### Signing chain — 6 namespaces

Each detector signs under its own namespace, derived from
`AXIOM_MASTER_KEY`:

```
AXIOM_MASTER_KEY
       ├── derive_key("axiom-video-objects-v1")     → ObjectTrackReport
       ├── derive_key("axiom-video-motion-v1")      → MotionReport
       ├── derive_key("axiom-video-impact-v1")      → ImpactReport
       ├── derive_key("axiom-video-temporal-v1")    → TemporalChainReport
       ├── derive_key("axiom-video-timekeeper-v1")  → TimeKeeperReport
       └── derive_key("axiom-video-color-v1")       → ColorReport
```

Tamper to any one report breaks only its own signature — the others
still verify. Granular failure surface, granular trust.

### TimeKeeper — rhythm analysis over the event stream

Conceptually parallel to `axiom_audio.tempo` (which finds BPM in
waveform envelopes), but applied to event streams. Algorithm is
tiny + pure-Python:

1. Sort events by time.
2. Compute inter-event intervals overall + per event-type.
3. **Rhythm score** = `1 - (std / mean)` clamped to [0, 1]. Perfectly
   periodic → 1.0; random → near 0.
4. **Silence**: any interval > `silence_threshold_s` (default 1.0s).
5. **Burst**: sliding window of `burst_window_s` (default 0.5s)
   containing ≥ `burst_min_events` (default 3).

Discretized into a `rhythm_class` field for downstream readability:

| Score | Class |
|---|---|
| ≥ 0.85 | `regular` |
| ≥ 0.60 | `semi_regular` |
| ≥ 0.30 | `irregular` |
| < 0.30 | `chaotic` |
| `n_events < 2` | `insufficient` |

Use cases:
- **Kid-AI:** does the toy's interaction cadence look natural or
  is the model spamming? `rhythm_class == "regular"` on contact
  events is fine; `"chaotic"` may indicate a bug.
- **Dashcam:** are deceleration events clustered or evenly spread?
  Burst detection flags potential crash sequences.
- **Smart-home:** doorbell-press events with refractory period —
  anything inside the burst window is spoof.
- **Sports:** regular foot-strike rhythm at 180 bpm = healthy gait.

### ColorWatcher — color as a point in HSV space

Per your framing: colors ARE just numbers in space. We partition
the HSV cylinder:

- **Hue** (angular, 0-360°) → 6 named regions:
  red (330-30°), orange (30-90°), green (90-150°), cyan (150-210°),
  blue (210-270°), magenta (270-330°)
- **Saturation + Value** layered for modifiers:
  - `S < 0.15` → `gray` / `black` / `white` (override hue)
  - `V < 0.20` → `dark_<hue>` prefix
  - `V > 0.80` + `S < 0.50` → `pale_<hue>` prefix

Total ≈ 18 distinct labels — enough to be useful, few enough to be
deterministic across test fixtures.

Input contract: scene-graph-agnostic. Customer's upstream object
detector populates `Object.extras["color"]` with a `(r, g, b)`
tuple in 0-255. AXIOM consumes that tuple. Pixel-sampling
ingester is Phase B.

**Color-shift events** fire whenever consecutive per-frame labels
for the same track differ. Useful for:
- Traffic-light transitions (green → orange → red)
- Brake-light state changes
- Blush detection (face track shifts pale_red → red)
- Bruise/wound detection (pale → dark_red)

Stable tracks (no shifts) have `stable=True` in the per-track
record — surface this to skip noise events in downstream UIs.

### Confidence rolls up — VideoAgent's payload

When the EventToken VideoAgent runs in real mode, it nests all
four sub-reports and exposes a `summary` block for headline reads.
The token's `video.confidence` is the mean of the four sub-report
confidences.

### Back-compat with the legacy stub

The pre-Phase-A VideoAgent stub accepted hand-coded dicts
(`object_motion`, `impact_point`, `fracture_pattern`, etc.). The
real agent preserves that contract: if `inputs["video"]` does NOT
contain a `scene_graph`, it falls back to the stub shape with
`payload["mode"] = "stub"`. Existing tests + the older event-token
contract keep working unchanged.

## Test scenarios

```bash
AXIOM_MASTER_KEY=<64-hex> python3 -m pytest tests/test_axiom_video.py -v
```

25 hermetic tests. Highlights:

- IoU helper + Object/Scene dataclasses
- Tracker handles upstream IDs vs numeric placeholders vs flicker filter
- Motion classifier hits all 5 non-static classes via parametrized run
- Impact detector fires on decel + contact; no false positives on static
- Temporal-chain ordering + appear-per-track contract
- All 4 namespaces are distinct and start with `axiom-video-`
- Tamper detection on any sub-report flips verify to False
- **Full harness gate test** — 14 scenes, motion/impacts/signatures all 100%
- VideoAgent real mode runs the full pipeline through the Coordinator
- VideoAgent stub mode still works for legacy callers

## House rules for support + sales

- **Don't say "AXIOM understands video."** It doesn't. It produces
  *signed structured detections* over an upstream detector's output.
  The understanding (semantics, language description, intent) is
  the customer's LLM job, just like with audio.
- **The scene-graph input is the architectural moat.** It means
  AXIOM works with ANY upstream detector — YOLO, Detectron,
  customer's edge silicon. Don't lock into one vision-model
  recommendation; sell the audit + composition layer.
- **Lead with the kid-toy use case for compliance buyers.** "The
  toy detected: reach → grip → cup tilt → fall — fully signed,
  no pixels stored." That's the differentiator for COPPA reviewers
  who can't accept "the AI said it saw the toy fall."
- **Phase A doesn't ship fracture-pattern classification.** That's
  Phase B. If a customer asks about `radial_scatter` etc., be
  honest: it's in the concept doc + the legacy stub shape; the
  real detector is on the runway.
- **Same compute footprint as audio.** Runs on the Orin Nano
  alongside the audio agents + `qwen2.5:1.5b`. The Sovereign Box
  story holds.

## Phase B runway (what we'd build next)

In rough priority order:

1. **Fracture-pattern classifier** — `radial_scatter` /
   `linear_break` / `crumple` patterns. Takes the impact event's
   moment-frames + a small CNN classifier. ~2-3 days of work +
   ~100 synthetic fixtures.
2. **Frame-ingestion adapter** — `axiom_video.ingest.frames_to_scene_graph`
   that accepts numpy arrays + an upstream detector (default to
   YOLO or OpenCV), produces a `SceneGraph`. ~2 days.
3. **Streaming windowed pipeline** — feed frames one at a time +
   produce rolling signed reports per window. ~3 days.
4. **Live-camera demo** — Orin Nano + USB camera + dustynv/yolov8
   container + AXIOM video → live signed event-token stream. ~1 day.
5. **`axiom_event_token.PhysicsAgent` integration** — combine
   AXIOM video's motion class + impact events with the physics
   plausibility rules (already in `_PHYSICS_RULES`) to flag
   physics-implausible event chains. ~1 day.

## Further reading

- [`axiom_report/templates/concept_video_topology.html`](../../axiom_report/templates/concept_video_topology.html) — original 351-line concept doc that shaped this build
- [`audio-phase-a.md`](audio-phase-a.md) — the analogous audio module (Phase A model)
- [`audio-agent-vs-llm.md`](audio-agent-vs-llm.md) — extended positioning framing applicable to video too
- [`tests/test_axiom_video.py`](../../tests/test_axiom_video.py) — locked-in contract (25 tests)
- [`scripts/video_harness.py`](../../scripts/video_harness.py) — 14 reference scenes + gate runner
