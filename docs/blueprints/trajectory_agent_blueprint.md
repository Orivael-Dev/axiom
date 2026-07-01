# AXIOM Low-Power Edge Trajectory Agent — Blueprint

> Source: `Axiom_Trajectory_Agent_Blueprint.pdf` (Orivael AXIOM Framework —
> Private Architecture Draft). Text extracted and preserved here so the
> originating idea and the reuse analysis live in the repo. Prototype lives in
> `axiom_edge_trajectory.py` with governance spec
> `axiom_files/research/edge_trajectory.axiom`.

## Multi-Event Tokenization & Power-Conditioned Trajectory Prediction

Deployment architecture for an ultra-lightweight, resource-constrained Axiom
Trajectory Agent for real-time fitness tracking and sensor telemetry. When edge
execution environments are governed by hardware-level power conditioning loops
(automated power managers scaling down voltage tiers on embedded systems or AI
acceleration boxes), traditional multi-layer attention architectures break down
due to compute bottlenecks. This system circumvents latency inflation and
hardware throttling by translating continuous multi-sensor feeds into highly
compact, vectorized Trajectory Tokens handled via fast, static projection
matrix pipelines.

### 1. Bare-Bones Trajectory Token Schema

Raw time-series metrics from wearables (PPG, accelerometer, galvanic skin
response) and hardware context flags are compressed into structured unified
array objects rather than verbose JSON blobs, so concurrent biometric spikes
are evaluated in a single clock-cycle sequence pass.

- **Temporal Anchor (`t`)** — epoch timestamp mapping the token block into
  regional historical context.
- **State Vector (`v`)** — fixed-dimension metric array
  `[HeartRate, HRV_ms, Active_METs, Motion_State_ID]`.
- **Power Topology Context (`p`)** — system-level metrics
  `[Current_Watt_Draw, Power_Agent_Throttle_Flag]`.

```json
{ "t": 1718113200, "v": [68, 54, 1.2, 1], "p": [12, 0] }
```

### 2. Power-Aware Adaptive Predictor Architecture

When the host power-conditioning agent triggers hardware throttling (standard
performance tiers → low-power edge modes), the agent shrinks its sliding
attention/history window `W` to bypass excessive stack memory usage and executes
low-overhead linear projections over pre-compiled matrix paths.

```
Prediction Score Vector:  S = V_flattened · W_projection
y_pred = argmax( Σ_i v_i × ω_ij )
```

Where `v_i` are elements of the active history window and `ω_ij` the weights of
the optimized ~1K-parameter projection model.

**Power Management Optimization Rule:** when `Power_Agent_Throttle_Flag == 1`,
the execution loop bypasses deep feature search and uses zero-overhead array
structures allocated once during bootstrap. Memory fragmentation is avoided by
keeping allocation bounds static.

### 3. Edge Hardware Execution Topography

```
+-----------------------------------------------------------+
| Multi-Sensor Hardware (PPG, Accelerometer, METs)          |
+-----------------------------+-----------------------------+
              | [High-Frequency Raw Stream]
              v
+-----------------------------------------------------------+
| Local Serialization Layer (flat state arrays: v, p)       |
+-----------------------------+-----------------------------+
              | [Compact Trajectory Tokens]
              v
+-----------------------------------------------------------+
| Edge Trajectory Predictor (low-power matrix projection;   |
| window-adaptive loop)                                     |
+-----------------------------+-----------------------------+
              | [Predictive Classification]
              v
+-----------------------------------------------------------+
| Power Conditioning Supervisor (scales frequency, throttle)|
+-----------------------------------------------------------+
```

| Hardware State | Window `W` | Compute Profile | Telemetry Frequency |
|---|---|---|---|
| Unthrottled (High Performance) | 5 steps | Full-window dot-product | 50–100 Hz continuous |
| Throttled (Power Restrained) | 3 steps | Zero-padded truncated projection | Sub-sampled |

### 4. Predictive Personalization & Drift Calibration

To avoid resource-heavy backprop on edge hardware, personalization runs via
continuous spatial drift monitoring against a pre-cached baseline distribution.
Computing spatial centroids across the running history window detects creeping
metabolic or physiological shifts (e.g. elevated cardiac recovery intervals
intersecting stable MET output) without a high-wattage analytical cloud backend.

```python
def evaluate_trajectory_drift(current_trajectory, baseline_distribution):
    current_centroid = np.mean(current_trajectory, axis=0)
    baseline_centroid = baseline_distribution["mean"]
    distance = np.linalg.norm(current_centroid - baseline_centroid)
    if distance > baseline_distribution["threshold"]:
        return "TRAJECTORY_DRIFT_DETECTED"
    return "STABLE_PATHWAY"
```

---

## How the blueprint maps onto existing Axiom code

| Blueprint concept | Existing footprint reused | File |
|---|---|---|
| `Power_Agent_Throttle_Flag`, watt draw | `PowerState.thermal_ok` / `is_backup` / `battery_pct`; `PowerSensor.read()` | `axiom_agent_fabric/power_conditioner.py` |
| Window shrink on throttle (5→3) | Profile-driven `context_window` reduction (`PROFILE_CONFIGS`) | `axiom_agent_fabric/power_conditioner.py` |
| Drift via centroid + norm vs threshold | `ManifoldChecker` drift logic; `DRIFT_THRESHOLD = 0.10` | `axiom_latent_v2.py`, `axiom_cas_orchestrator.py` |
| HMAC-signed token / result | `derive_key(ns)` + canonical-JSON `_sign` | `axiom_signing.py`, `axiom_retrospect.py` |
| CANNOT_MUTATE config | module-freeze via `sys.modules[__name__].__class__` | `axiom_retrospect.py` |
| `.axiom` ↔ `.py` pairing + supply-chain hash | `register_agent_hash` / `verify_agent_hash` | `axiom_files/parser.py` |

**New in this prototype:** the compact `{t, v, p}` token schema, the
power-adaptive sliding-window predictor, and the static projection-matrix
classifier.

### Deliberate deviations from the PDF (for the governance story)

1. **Deterministic projection matrix.** The PDF uses `np.random.randn(20, 3)`.
   The prototype seeds the matrix from a fixed value (`random.Random`) so
   predictions are reproducible and the matrix can be pinned and hashed —
   required for signed, auditable verdicts.
2. **Pure Python, no numpy.** Flatten / pad / dot / argmax / mean / norm are
   implemented directly. This is faithful to the "bare-bones,
   static-allocation, edge" thesis and matches this repo's no-heavy-deps audio
   modules (numpy/scipy are not guaranteed on target edge devices).

### Governance note — biometric data

The state vector carries heart rate and HRV, which are sensitive health
signals. The spec puts `biometric_data_policy` in `CANNOT_MUTATE` and routes
biometric input through `SensitiveDataGate` / `BiometricDataGate`: raw vectors
stay inside the bounded sliding window and never persist, transmit off-device,
or feed training. Only signed prediction verdicts and drift status cross the
agent boundary.
