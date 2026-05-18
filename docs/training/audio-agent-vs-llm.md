# Training manual — Audio Agent vs LLM audio learning

> Sales question of the week: *"Why not just use Whisper / GPT-4o
> audio / Deepgram?"* Short answer: **AXIOM audio doesn't compete
> with those.** It's the **signed provenance + selective-activation
> layer** over whatever LLM audio the customer already uses (or
> doesn't). They stack.

## What "traditional LLM audio learning" actually means

The space we're being compared to:

| Model | What it does | Cost | License |
|---|---|---|---|
| **Whisper** (OpenAI) | Speech-to-text + speaker-language ID | ~$0.006 / minute via API, free locally | MIT; CC-BY-NC for some derivatives |
| **GPT-4o audio** | Native multimodal — transcription + dialogue understanding + voice output | $0.06-0.30 / minute | proprietary, API-only |
| **AudioLM / Pengi / SALMONN** | Open-ended audio→text reasoning ("describe this scene") | research-grade, $$$ compute | mostly non-commercial |
| **Deepgram / AssemblyAI** | Production transcription + diarization + sentiment | ~$0.01-0.05 / minute | proprietary, API-only |

All of these are **single monolithic models** that do **understanding**.
You feed in audio bytes, you get text/structured-output back.
**You don't get a signed verdict, a deterministic output, or
component-level falsifiability.**

## The two-axis framing

```
                    UNDERSTANDING (LLM-shaped)
                            ▲
                            │
        Whisper, GPT-4o, ───┤
        Deepgram, SALMONN   │
                            │
   ◀────────────────────────┼──────────────────────▶
   MONOLITHIC               │              MODULAR
                            │
                            ├─── AXIOM audio agent
                            │     (material / voice /
                            │      VAD / tempo)
                            │
                            ▼
                    DETECTION (signed)
```

LLM audio lives in the top-left: monolithic + understanding-focused.
AXIOM audio lives in the bottom-right: modular + detection-focused.
**They're not in the same quadrant.** The right pitch is composition,
not substitution.

## What AXIOM audio actually is

Four independent agents, each with its own HMAC namespace:

| Agent | What it produces | Namespace | Tests |
|---|---|---|---:|
| `axiom_audio` (material classifier) | shatter / scattered_fragments / footsteps / glass_break / etc. with confidence | `axiom-audio-v1` | 10 |
| `axiom_voice` (voice fingerprint) | speaker identity, age-bracket (child / adult), gender (optional) | `axiom-voice-v1` | 12 |
| `axiom_vad` (voice-activity detection) | speaker-on / speaker-off intervals | `axiom-vad-v1` | 13 |
| `axiom_tempo` (cadence) | speaking-rate, stress markers, urgency indicators | `axiom-tempo-v1` | 15 |

Each emits a signed `*Report` that the `axiom_event_token`
Coordinator can selectively activate, sign-of-signs, and ship
as part of a larger EventToken alongside text / video / physics
verdicts.

Total: ~50 audio tests, gate-locked at latency p95, false-positive
rate, accuracy floor — see [`audio-phase-a.md`](audio-phase-a.md)
for the gate definitions.

## Where AXIOM wins outright

Ten advantages over LLM audio learning, in priority order for
support+sales conversations:

| # | Advantage | Why it matters |
|--:|---|---|
| 1 | **HMAC-signed per-property verdicts.** Every detector signs its result under a namespaced key derived from `AXIOM_MASTER_KEY`. Tamper-evident; cryptographically replay-able in court. | LLM transcripts have no signing. If a customer's regulator asks "prove this transcript wasn't edited," Whisper has no answer. AXIOM does. |
| 2 | **Selective activation.** Caller chooses which detectors fire. `Coordinator.compose(activate=("voice", "vad"))` runs only those two. | LLM audio is all-or-nothing — you pay the full inference cost even if you only need VAD. AXIOM is metered per agent. |
| 3 | **Deterministic.** Same audio bytes + same master key → byte-for-byte identical signed output. | LLM audio outputs drift across runs (temperature, sampling, model version). Auditors don't trust non-deterministic systems. |
| 4 | **Falsifiable at the agent level.** Each detector has a corpus + acceptance gates (latency p95, accuracy floor, FP rate). Failing a gate means the agent is genuinely broken. | LLM audio is benchmarked at the model level on aggregate datasets. "Whisper has X% WER on LibriSpeech" doesn't tell you whether it'll catch a specific compliance pattern. |
| 5 | **Modular replacement.** Swap the VAD detector without touching voice or tempo. Each is its own file, its own test suite, its own version. | LLM audio is monolithic — upgrade or don't. "Whisper-3 changed our pipeline" is a real customer pain. |
| 6 | **Tiny compute.** Runs on Orin Nano (8 GB unified memory, ~70 TOPS) alongside `qwen2.5:1.5b`. Even a Raspberry Pi 5 handles the detection-only workload. | Whisper-large needs a GPU. GPT-4o audio is API-only — no local option. Customers in regulated industries (healthcare, finance) need on-device. |
| 7 | **Privacy / sovereignty.** Audio bytes never leave the box. No "AI provider" sees them. | API audio (Deepgram, OpenAI, AssemblyAI) sends every byte to a third party. HIPAA / GDPR / FERPA conversations stall on that fact. |
| 8 | **Failure isolation.** Voice fingerprint outage doesn't break VAD. Tempo agent going down doesn't break material classification. | LLM audio degrades everywhere or nowhere. One model = one failure surface. |
| 9 | **Composes with the event-token Coordinator.** Audio agents are peers to text / video / physics. One signed `EventToken` wraps all of them with cross-modal governance. | LLM audio doesn't naturally compose — you'd need a glue layer to combine a Whisper transcript with a video-frame classifier with a physics constraint. |
| 10 | **Zero per-second cost at inference.** Local detectors are free to run forever. | Cloud LLM audio is metered. A 24/7 call-center monitoring deployment at 100 hours/day on Deepgram is $90-450/day; on AXIOM + Sovereign Box it's $0/day. |

## Where LLMs win outright (don't oversell)

Three places AXIOM audio is **NOT** the answer:

| LLM-audio strength | Why AXIOM doesn't compete |
|---|---|
| **Open-ended transcription.** "Convert this 30-min call into text." | AXIOM doesn't transcribe. The audio agents are detectors, not ASR. If a customer needs the words, point them at Whisper (local) or Deepgram (cloud) and have AXIOM run *alongside* it for the safety verdict. |
| **Dialogue understanding.** "Summarize the speaker's mood across this call." | AXIOM's tempo detector picks up stress *markers* but doesn't interpret. If the customer wants "mood across the call," that's an LLM job. |
| **Cross-language audio.** "Recognize speech in Hindi or Arabic." | AXIOM's voice fingerprint is language-agnostic but doesn't decode lexical content. Multi-language speech needs Whisper or equivalent. |

**Never claim AXIOM replaces Whisper for transcription.** It doesn't
try to. Customers who hear that pitch and then test transcription
will (correctly) think we're full of it.

## The composition pitch (the right pitch)

"You already use Whisper or you don't. Either way, AXIOM gives you
the four things Whisper doesn't:

1. A **signed audit trail** at the property level — voice was a
   child, not adult; speech rate was elevated; this is a
   scattered-fragments scene — each as its own verifiable verdict.
2. **Selective activation** so you pay only for the detectors a
   given request needs.
3. **Determinism** so legal can replay-verify any decision.
4. **Composition** with text / video / physics in one signed
   event token.

Whisper + AXIOM is a strictly better stack than Whisper alone."

## Vertical case studies

### Kid AI / AI toy company

**AXIOM does:** Voice fingerprint → age-bracket classification.
Tempo → urgency / distress markers. VAD → speaker-turn boundaries.
Material → scary-scene detection on ambient sound.

**LLM does:** Transcription via Whisper (local on-device) for
content review.

**Combined verdict:** EventToken with `voice` (age=child + signed),
`vad` (turn count), `tempo` (low stress + signed), plus the
transcript from Whisper attached as evidence. Toy company shows
this stack to a regulator and it's reproducible end-to-end.

### CallGuard / debt-collection compliance

**AXIOM does:** Material → background-scene classification (is
the caller in a place that suggests pressure tactics?). Voice →
speaker identity. Tempo → speech rate (high speech rate +
interruption rate = stress / aggression signal). VAD → talk-time
ratios (FDCPA prohibits agent dominating).

**LLM does:** Whisper transcribes the call. An LLM does the
FDCPA-violation flagging on the transcript.

**Combined:** AXIOM provides the **non-lexical evidence** (the
"how" of the call); LLM provides the lexical evidence (the "what").
Both signed, both replayable.

### Healthcare patient intake

**AXIOM does:** Voice fingerprint confirms patient identity
(continuity across calls). VAD detects pauses (consent-language
delivery). Tempo flags rushed disclosure (UDAAP / HIPAA risk).

**LLM does:** Transcription + summary of patient's stated symptoms.

**Combined:** HIPAA audit chain — *the patient who said "yes, I
consent" is the same patient on file, the consent was read at
normal speed, no pauses indicating coercion.* Three signed
detector reports + a transcript.

### Sovereign Box deployment

**Everything runs on the Orin Nano.** AXIOM detectors are ~100 MB
total working memory. `whisper.cpp` tiny model is ~75 MB. Combined
RAM footprint with `qwen2.5:1.5b` (for text reasoning) is ~4 GB —
comfortable on the 8 GB Nano. **Zero cloud calls. Zero
per-second cost. Full provenance chain.**

## House rules for support + sales

- **Never say "AXIOM beats Whisper at transcription."** It doesn't
  try. The customer will test this and we'll lose credibility.
- **Lead with signed verdicts, not accuracy.** The advantage is
  cryptographic provenance + selective activation, not
  raw-classifier performance. "What's your accuracy" is a Whisper
  question; "what's your audit chain" is an AXIOM question.
- **The stack pitch wins compliance buyers.** Don't position
  AXIOM as substitution — position as the audit + provenance
  layer that lets the customer USE Whisper safely. Bigger TAM,
  no head-to-head with $50B incumbents.
- **Sovereign Box is the compute story.** On the Nano, both
  AXIOM + tiny-Whisper run together at zero per-query cost. This
  is the procurement-meeting headline number for any 24/7
  monitoring use case.
- **Don't lead with "modular".** Modular is an engineering virtue;
  buyers don't care unless you make it concrete. The concrete is
  "you only pay for what you turn on" + "you can swap one detector
  without touching the others."
- **HMAC signature ≠ encryption.** Standard caveat (same as for
  Firewall + Research). The signature covers tamper-detection on
  the report, not confidentiality of the audio bytes. For
  confidentiality, the audio stays on-device — that's a separate
  property.

## Differentiator one-liner (for cold outbound)

> "Whisper tells you what was said. AXIOM tells you what was
> said, who said it, how stressed they were, and ships every
> claim with a signed audit chain you can replay in court."

## Further reading

- [`audio-phase-a.md`](audio-phase-a.md) — what's actually in `axiom_audio/`
- [`research-engine.md`](research-engine.md) — same signed-verdict pattern applied to research
- [`docs/FIREWALL_PHASE_STATUS.md`](../FIREWALL_PHASE_STATUS.md) — where the audio agents sit in Phase 4 (CallGuard adjacency)
- [`docs/GAME_PLAN.md`](../GAME_PLAN.md) §5 — CallGuard's product wedge
- [`tests/test_axiom_audio.py`](../../tests/test_axiom_audio.py) — material classifier contract
- [`tests/test_axiom_voice.py`](../../tests/test_axiom_voice.py) — voice fingerprint contract
- [`tests/test_axiom_vad.py`](../../tests/test_axiom_vad.py) — VAD contract
- [`tests/test_axiom_tempo.py`](../../tests/test_axiom_tempo.py) — tempo contract
