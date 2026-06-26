# Domain Agent — `automotive_kia_us.axiom`

A constitutional **domain agent** for KIA US vehicles: diagnostics, step-by-step
repair guidance, in-car audio event classification, and voice-command routing.
It is **offline-first** — DTC lookups, repair specs, torque values, and audio
classification all work with no network. Network is used only for OTA TSB updates,
live recall status, and dealer inventory.

> Written in the AXIOM language. For the constructs used here (`AGENT`, `CONCEPT`,
> `CONSTRAINT`, `WHEN`, `DELEGATES`, `CANNOT_MUTATE`, `TRUST_LEVEL`, `HUMAN_REVIEW`),
> see [`AXIOM_SPEC.md`](../../AXIOM_SPEC.md) — §2.6 covers the `CONCEPT` construct.

## Scope
KIA US lineup, 2016–2026 (all US trims): Telluride, Sedona (2016–2021),
Carnival (2022–2026), Sorento, Sportage, Niro, EV6, EV9, K5, Forte, Rio, Stinger.
Anything outside this model/date range returns `OUT_OF_SCOPE`.

## What it does (CONCEPTs)
| Concept | Triggers on | Result |
|---|---|---|
| `EngineAudioClassifier` | engine knock / misfire / belt squeal | `AUDIO_EVENT_ENGINE` (→ critical on scattered decay) |
| `BrakeAudioClassifier` | brake squeal / grinding | `AUDIO_EVENT_BRAKE` (→ critical on sustained metal grind) |
| `CabinAlertClassifier` | chimes / HVAC / cabin alerts | `AUDIO_EVENT_CABIN` |
| `VoiceCommandGate` | driver voice input | `VOICE_COMMAND_ROUTED` (VAD ≥ 0.70) or `VOICE_COMMAND_UNCLEAR` |
| `OfflineCache` | no/poor network | serves DTC, torque, repair data from local KV cache; flags `offline=true` |
| `RepairGuruContext` | repeat repair/diagnosis queries | injects prior-turn context via ConstitutionalRetrospect |

Diagnostic verdicts: `DIAGNOSTIC_CLEAR`, `DIAGNOSTIC_ALERT`, `DIAGNOSTIC_CRITICAL`,
plus `REPAIR_GUIDANCE`.

## Governance
- **Safety-critical rule:** any brake / airbag / powertrain / steering event emits
  `DIAGNOSTIC_CRITICAL` and is never downgraded, regardless of audio confidence.
- **No fabrication:** never emits unknown DTC codes or part numbers; DTC results are
  labeled `CONFIRMED` or `INFERRED`. Never claims a safety system works without
  diagnostic confirmation.
- **Injection resilience:** `bypass`/`ignore`/`override` input routes to Sandbox
  (`HighRiskInput`); VIN/owner-data input routes to `SensitiveDataGate`; voice
  content is never logged (only `intent_class` is retained).
- **Immutable fields** (`CANNOT_MUTATE`): agent, goal, version, trust_level,
  model scope, offline-first rule, safety-critical rule, data-retention policy,
  training prohibition, sensitive-data gate. `TRUST_LEVEL 1`.
- **Human review** (24h timeout, blocks on timeout) required for changes to trust
  level, model scope, the safety-critical rule, or security.

## Status
Standalone domain agent — **not yet registered in
[`domain_index.json`](./domain_index.json)** (which currently lists government,
finance, healthcare), so it has no recorded benchmark suite or validation status.
