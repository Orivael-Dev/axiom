# ANF Token Economics

How sparse activation, 3D latent tokens, and reverse-QRF collapse change the
Chinchilla math for AXIOM-shaped models.

This note exists so a skeptical reader can trace every number in it back to a
file and line on `main`. The headline scaling claim is deliberately
conservative; the broader theoretical envelope is sketched but not credited.

---

## 1. Baseline — the dense transformer Chinchilla rule

The Chinchilla scaling result says compute-optimal training for a dense
transformer wants roughly **20 training tokens per parameter**. A 1B-parameter
model wants ~20B tokens.

Two assumptions sit underneath that ratio:

1. **Every forward pass touches every parameter.** Each token's gradient flows
   through the whole network, so per-token compute is proportional to total
   parameter count.
2. **A token is one categorical ID drawn from a vocabulary.** The embedding is
   high-dimensional, but the *input stream* is a flat 1D sequence; each
   position carries one unit of "what was said next."

Both assumptions are violated in different ways by AXIOM's architecture. The
rest of this note walks through how, and what that does to the ratio.

---

## 2. ANF sparse activation — compute headroom, not token headroom

The AXIOM Neural Fabric (ANF) is a 100-core sparse reasoning substrate.

- `axiom_anf_emulator.py:103` — `TOTAL_CORES = 100`
- `axiom_anf_emulator.py:20`  — `VECTOR_DIM = 32` (each core carries a 32D latent)
- `axiom_anf_emulator.py:22-25` — per-intent activation table:

  | Intent       | Cores active |
  |--------------|--------------|
  | `INFORM`     | 20%          |
  | `REQUEST`    | 25%          |
  | `EXPLORE`    | 30%          |
  | `MANIPULATE` | 15%          |
  | `DECEIVE`    | 10%          |
  | `HARM`       |  5%          |

The simulator's reference intent distribution
(`examples/anf_cost_sim.py:51-58`) weights INFORM 40 / REQUEST 20 / EXPLORE
15 / MANIPULATE 10 / DECEIVE 10 / HARM 5 across 100 sampled inferences. The
weighted-average activation is:

```
(40·0.20 + 20·0.25 + 15·0.30 + 10·0.15 + 10·0.10 + 5·0.05) / 100
= (8.0 + 5.0 + 4.5 + 1.5 + 1.0 + 0.25) / 100
= 20.25 / 100
= 20.25% of cores active per inference
```

Per-token compute drops by roughly a factor of `100/20.25 ≈ 4.9×`. The total
*token budget* doesn't drop — each core still needs sufficient supervision to
learn its specialization, and Chinchilla coverage applies per-core. What
changes is that the same compute budget now fits **~5× more parameters**.

This is the Mixture-of-Experts shape: Switch Transformer with activation
ratio `1/N` gives roughly `N×` compute headroom for the same wall-clock
training time. ANF's `100/20.25 ≈ 4.9×` lands in the same regime.

> **Bottom line, Section 2 alone:** ~5× more parameters at the same compute
> budget, with the token budget unchanged from the dense Chinchilla baseline.

---

## 3. AXM SkillDelegates — modular factorization

`axiom_axm.py:152-158` defines `SkillDelegate` as a routed expert that gates
on `intent_classes: Tuple` — each delegate trains on a disjoint subset of
intents. The total parameter count is the sum of the delegate parameter
counts; the total training token requirement is **at most** the sum of each
delegate's per-class Chinchilla allotment.

Because the intent buckets are disjoint, real-world token streams divide
across delegates rather than being replicated. Adding delegates increases
total system parameters roughly linearly without increasing the per-delegate
token requirement.

This effect is structurally similar to Section 2 (sparse-expert routing) and
is not added as a separate multiplier in Section 6 — it's the same compute
headroom, viewed at a different layer of the stack.

---

## 4. Latent reasoning — AXIOM already produces 3D tokens

The intuition behind "tokens are flat" is correct for raw text. AXIOM's
latent stack already encodes each reasoning step as a structured trajectory.
`axiom_latent_v2.py:51-67` defines `TrajectorySample`:

```
stage:                   str         # PREFLIGHT / MID_CHAIN / FINAL_SYNTHESIS
intent_vector:           List[float] # agent intent embedding at this stage
token_cost:              int         # tokens consumed up to this stage
latency_ms:              float       # wall-clock at capture
constitutional_distance: float       # distance from CANNOT_MUTATE boundary [0,1]
```

`axiom_latent.py:816-924` produces three of these per reasoning pass:

| Stage              | intent_vector scale | distance check after          |
|--------------------|---------------------|-------------------------------|
| `preflight`        | 0.5 × base          | no rival yet                  |
| `mid_chain`        | 0.8 × base          | multiplex branch selected     |
| `final_synthesis`  | 1.0 × base          | full distance check, monotone |

Each sample carries **three orthogonal axes** of supervisable signal:

1. **Semantic** — the `intent_vector` (continuous embedding).
2. **Geometric** — the `constitutional_distance` (scalar in `[0,1]`,
   `axiom_latent_v2.py:298-311`, computed as
   `min(d_floor, d_ceiling, d_rival, d_fields)`).
3. **Temporal** — the `stage` (categorical, 3 values, with monotonicity
   enforced by `MonotonicGate` at `axiom_latent_v2.py:482-606`).

A flat token carries one of these (just position-in-sequence). A trajectory
sample carries all three. When training input is the *trajectory* rather
than the raw token stream, each training unit carries roughly 3× the
supervisable signal of a categorical token.

This isn't a new patent claim — the ingredients (`IntentClassifier`, the
3-stage trajectory, the constitutional-distance scalar) are already in the
code. What's "new" is treating the trajectory as the *training unit*
instead of the supervision signal.

`axiom_training_to_axm.py:12-17` already partially does this: ~500 flat
records plus ~200 ChatML messages collapse to ~27 trajectory blocks, one
per intent-class cluster. The trajectory blocks are denser units than the
source records; the corpus shrinks ~25× without losing the intent and
action-sequence signal.

> **Bottom line, Section 4:** trajectories carry ~3× the signal density of
> raw tokens. The effective tokens-per-parameter ratio drops from 20:1
> toward roughly **7-trajectories-per-parameter**.

---

## 5. Reverse QRF collapse — a designed-but-unbuilt synthetic generator

This section is a **design proposal**, not implemented code. The forward
direction exists; the reverse direction does not.

### 5.1 What forward QRF does today

`axiom_qrf.py:90-223` implements `QRFEngine`. The flow:

1. Run `LatentEngine.run(prompt, trajectory=True)` to produce the latent
   trajectory and the multiplex's `all_branches[]` (up to 8 candidate
   reasoning paths).
2. Trim to the domain-specific count (`axiom_qrf.py:39-46`):

   | Domain          | Branches |
   |-----------------|----------|
   | `medical`       | 8        |
   | `financial`     | 6        |
   | `security`      | 6        |
   | `supply_chain`  | 4        |
   | `hr`            | 4        |

3. Convert each branch's score to a probability weight via
   `w_i = score_i / Σ score_j`, sort descending.
4. Identify "killed" branches (`score == 0`) and label the result band as
   `HIGH` (top weight ≥ 0.50) / `MODERATE` / `LOW` / `UNCERTAIN`.
5. Sign the result with HMAC and return.

**Forward QRF does not collapse the superposition.** It returns all
weighted branches; the caller picks the top.

### 5.2 What reverse QRF would do

Given an **observed `(prompt, answer)` pair**, reverse QRF would produce
the superposition of latent trajectories `T_1, T_2, …, T_N` consistent
with that answer under the existing forward model.

Algorithm sketch:

```
def reverse_collapse(prompt, observed_answer, domain) -> List[Trajectory]:
    forward = QRFEngine(domain).run(prompt)
    candidates = []
    for branch in forward.weighted_branches:
        # Replay LatentEngine with this branch forced as the mid_chain pick.
        T = latent_engine.run(prompt, forced_mid_chain=branch)
        # Score the trajectory against the observed answer.
        compatibility = manifold_distance(T.final_state, observed_answer)
        score = branch.forward_weight * compatibility
        if score >= TAU:  # default TAU = 0.10 — matches L1_WARNING
            candidates.append(SignedTrajectory(T, score))
    return candidates
```

Each accepted candidate is a `TrajectorySample × 3` (one per stage)
carrying intent, distance, and stage — already in the
`axiom_latent_v2.py:51-67` shape.

### 5.3 Why it's called "reverse collapse"

Forward QRF is the wavefunction-analog direction: many weighted branches
collapse to one observed answer when the caller picks the top weight.
Reverse QRF runs the same probability machinery the other way — one
observed answer becomes a *signed* superposition of branches consistent
with it.

The forward step is `Σ w_i · branch_i → answer`. The reverse step
recovers the `{(w_i, branch_i)}` set that would have produced the
observed answer with non-trivial weight. The math is the same; the
direction of inference is inverted.

### 5.4 Synthetic-data multiplier

| Today                              | With reverse QRF                                 |
|------------------------------------|--------------------------------------------------|
| 1 `(prompt, answer)` → 1 record    | 1 `(prompt, answer)` → N trajectories            |
| N = 1                              | N = 3–8 (the domain branch count from §5.1)      |
| Each record is a token sequence    | Each trajectory is 3-stage × 3-axis (§4)         |

Conservative multiplier: **~3-5× more training units per observation**,
each ~3× denser than a flat token.

### 5.5 What would be needed to build it

Out of scope for this note. A future slice would need:

- `axiom_qrf_reverse.py` — `ReverseQRFEngine.collapse(prompt, answer, domain)`
- `axiom_files/core/axiom_qrf_reverse.axiom` — TRUST_LEVEL 3,
  HUMAN_REVIEW on `tau_threshold_change` and `branch_pool_change`
- HMAC signing under a fresh key:
  `derive_key(b"axiom-qrf-reverse-v1")` (the four-layer pattern used
  elsewhere in the stack)
- Tests pinning the round-trip invariant: forward-collapse of any
  reverse-generated trajectory must reproduce the original `(prompt, answer)`
  with probability ≥ τ
- A demo: `examples/reverse_qrf_demo.py`

The writeup in this section is the spec that module would be built from.

---

## 6. Conservative scaling claim

Three independent mechanisms, conservatively credited:

| Mechanism                            | Conservative gain                            |
|--------------------------------------|----------------------------------------------|
| ANF sparse activation (§2)           | ~5× more parameters per compute budget       |
| Latent 3D-trajectory tokens (§4)     | ~3× signal density per training sample       |
| Reverse-QRF collapse (§5, proposed)  | ~3-5× training samples per observation       |

The 2-3× headline parameter-scaling claim sits comfortably inside the
sparse-compute multiplier alone (§2). Sections 4 and 5 are treated as
**architectural headroom**, not as headline claims, because:

- §4 requires changing the training input format (trajectories instead
  of tokens), which is a non-trivial pipeline change.
- §5 is an unbuilt proposal.

---

## 7. What's NOT claimed

- AXIOM does not train faster than Chinchilla *on the same dense
  architecture*. The headroom comes from architecture changes — sparse
  cores, modular delegates, structured trajectory inputs — not a better
  training recipe applied to a vanilla transformer.
- Reverse-QRF is a design proposal. Section 5 is careful to say "would
  require" rather than "does."
- Synthetic-data augmentation from RedAgent/BlueAgent (`axiom_red_agent.py`,
  `axiom_blue_agent.py`) is not credited in the headline number. Its real
  contribution depends on how much novel signal the synthetic data carries,
  which varies by domain.
- "3D tokens" is shorthand for the existing trajectory representation,
  not a new patent. The ingredients are in `axiom_latent.py`,
  `axiom_latent_v2.py`, and `axiom_intent_classifier.py` today.

---

## 8. Worked example — a 1B-parameter target

Dense Chinchilla baseline:

- 1B params × 20 = **20B training tokens**.

With ANF sparse activation only (§2):

- Same 20B training tokens.
- ~5× compute headroom → **2-3B parameters** trainable in the same
  wall-clock and compute budget.

With reverse-QRF synthetic generation layered on (§5, if built):

- 2-3B parameters trained on ~20B observed tokens, augmented to
  **~60-100B reverse-collapsed trajectory units**, each carrying ~3× the
  signal density of a flat token (§4).

The 2-3B parameter figure is the conservative line. The trajectory-density
and reverse-collapse multipliers are *bonus headroom*, not part of the
headline claim.

---

## 9. Verification

Every constant cited above can be checked against the live code on `main`.
Run these from the repo root:

```bash
# §2 — TOTAL_CORES, VECTOR_DIM, CORE_ACTIVATION table
grep -n 'TOTAL_CORES\|VECTOR_DIM\|CORE_ACTIVATION' axiom_anf_emulator.py | head -5

# §2 — intent mix and per-token compute proxy
grep -A 8 'INTENT_MIX' examples/anf_cost_sim.py

# §3 — SkillDelegate intent_classes
grep -n 'intent_classes' axiom_axm.py | head -3

# §4 — TrajectorySample structure
grep -A 5 'class TrajectorySample' axiom_latent_v2.py

# §4 — constitutional_distance formula
sed -n '298,311p' axiom_latent_v2.py

# §5 — DOMAIN_BRANCH_COUNTS
sed -n '39,46p' axiom_qrf.py
```

Plus one runtime check that the activation ratio actually computes to
the cited 20.25%:

```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
python3 examples/anf_cost_sim.py -n 1000 | grep avg_cores_active
# Expected: avg_cores_active ≈ 20.25 / 100 cores
```

If any number in this document contradicts the live code, the code is
the source of truth and the document should be corrected.
