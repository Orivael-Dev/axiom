# ANF Token Economics

How sparse activation, 3D latent tokens, and reverse-QRF collapse change the
Chinchilla math for AXIOM-shaped models.

This note exists so a skeptical reader can trace every number in it back to a
file and line on `main`. The headline scaling claim is deliberately
conservative; the broader theoretical envelope is sketched but not credited.

---

## 1. Baseline — from Chinchilla (2022) to modern over-training (2024–2026)

The Chinchilla scaling result (Hoffmann et al., 2022, arXiv:2203.15556)
said compute-optimal training for a dense transformer wants roughly
**20 training tokens per parameter**. A 1B-parameter model wants ~20B
tokens. That ratio framed the field for about two years.

**The field has since moved past it.** Modern frontier practice
deliberately *over-trains* small dense models far past the
compute-optimal point:

| Model              | Params | Tokens | Tokens/param |
|---                 |---     |---     |---           |
| Chinchilla optimum | —      | —      | **20**       |
| Llama 2 70B        | 70 B   | 2 T    | 29           |
| Llama 3.1 405B     | 405 B  | 15 T   | 37           |
| NVFP4 12B (2025)   | 12 B   | 10 T   | **833**      |
| Llama 3 8B         | 8 B    | 15 T   | **1,875**    |
| Phi-3 mini         | 3.8 B  | 3.3 T  | 868          |

**Why the over-training?** Training compute is paid once. Inference
compute repeats forever. If you'll serve a model billions of times,
every fraction of a percentage point of accuracy you can squeeze out
at training time pays back across the full deployed lifetime. The
break-even point is around 10–100 B inference tokens served — easily
exceeded by any production model. So the modern strategy is: pick a
size convenient for inference, then train it at 200–2000 tokens per
parameter until the data saturates.

**Two assumptions still sit underneath every variant of the ratio:**

1. **Every forward pass touches every parameter.** Each token's gradient
   flows through the whole network, so per-token compute is proportional
   to total parameter count.
2. **A token is one categorical ID drawn from a vocabulary.** The
   embedding is high-dimensional, but the *input stream* is a flat 1D
   sequence; each position carries one unit of "what was said next."

Both assumptions are violated in different ways by AXIOM's architecture.
The rest of this note walks through how, and what that does to the
ratio. Where Sections 6 and 8 cite an absolute token budget, they use
the modern over-trained range (200–2000 tokens/param) rather than the
2022 Chinchilla baseline.

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

## 5. Reverse QRF collapse — synthetic-trajectory generator

This section describes the reverse-QRF module shipped on this branch
(`axiom_qrf_reverse.py`, commit `a9b2fa7`). The forward direction
existed already in `axiom_qrf.py`; reverse-QRF was added to close the
loop and produce signed synthetic trajectories from observed
`(prompt, answer)` pairs.

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

### 5.2 What reverse QRF does

Given an **observed `(prompt, answer)` pair**, `ReverseQRFEngine.collapse`
(`axiom_qrf_reverse.py:114-198`) produces a signed superposition of
trajectory hypotheses consistent with that answer under the existing
forward model.

The actual algorithm shipped on this branch:

```python
def collapse(self, prompt: str, observed_answer: str) -> ReverseQRFResult:
    # 1. Forward QRF gives the weighted branch superposition for the prompt.
    forward = self._forward.forecast(prompt)

    # 2. Encode the observed answer (trace-only — no multiplex needed).
    observed_intents = self._encode_trace(observed_answer)["intent_vector"]

    # 3. Score each forward branch against the observation.
    for branch in forward.branches:
        branch_trace = self._encode_trace(branch["response"])
        intent_alignment = jaccard(observed_intents,
                                   branch_trace["intent_vector"])
        quality          = branch["metrics"]["overall"]
        compatibility    = intent_alignment * quality
        constitutional   = checker.compute_distance(
                              branch_trace["confidence"],
                              rival_present=True, fields_clean=True)
        score            = branch["probability_weight"] * compatibility

        # 4. Accept if score clears tau AND trajectory stays on-manifold.
        if score >= tau and constitutional >= MIN_TRAJECTORY_DISTANCE:
            accepted.append(hypothesis(...))

    return ReverseQRFResult(hypotheses=sorted(accepted, by=score, desc),
                            hmac_signature=...)
```

Two design choices in the shipped version that differ from the original
proposal:

- **Intent Jaccard instead of forced replay.** The original sketch
  re-ran `LatentEngine.run(prompt, forced_mid_chain=branch)` to
  generate each candidate trajectory. The shipped version instead
  re-encodes each branch's response text via the trace-only phase and
  scores by Jaccard similarity on intent label sets. Same conceptual
  purpose (does this branch's reasoning align with the observed
  answer?), but doesn't require modifying `LatentEngine` to accept a
  forced branch parameter.
- **Two-gate acceptance.** A trajectory must clear both `tau`
  (compatibility threshold, default 0.10) AND
  `MIN_TRAJECTORY_DISTANCE` (constitutional gate, 0.05). The second
  gate keeps synthetic trajectories from drifting off the
  CANNOT_MUTATE manifold even if the first gate passes.

Each accepted hypothesis carries `(forward_weight, intent_alignment,
branch_quality, compatibility, constitutional_distance, score)` — the
multi-dimensional training signal §4 described, signed under
`derive_key(b"axiom-qrf-reverse-v1")`.

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

### 5.4 Synthetic-data multiplier — and the natural-data ceiling

| Today                              | With reverse QRF                                 |
|------------------------------------|--------------------------------------------------|
| 1 `(prompt, answer)` → 1 record    | 1 `(prompt, answer)` → N trajectories            |
| N = 1                              | N = 3–8 (the domain branch count from §5.1)      |
| Each record is a token sequence    | Each trajectory is 3-stage × 3-axis (§4)         |

Conservative multiplier: **~3-5× more training units per observation**,
each ~3× denser than a flat token.

**Why this isn't a "nice to have."** §1's modern over-training
practice (200–2000 tokens/param) runs into a hard ceiling: there is
not enough high-quality natural text on the internet to keep scaling.
"Scaling Laws for Quantized LLMs with 100T Training Tokens" (Kumar et
al., 2024, arXiv:2411.17691) shows diminishing returns kick in around
**~1000 tokens per parameter** for typical web-quality data — past
that, the model starts memorizing rather than generalizing. NVFP4 12B
stopped at 10T tokens (833 tokens/param) for exactly this reason: 12T
would have hit saturation, 50T would have wasted compute.

A 12B AXIOM model trained at the same modern ratio would need ~10T
training trajectories. Natural sources cap out well below that
(Common Crawl filtered ~1–3T, all of arXiv ~10B, all of Wikipedia
~4B). The gap has to be filled by synthetic generation. Reverse-QRF
collapse is exactly that generator: take a smaller corpus of
high-quality `(prompt, answer)` pairs and produce 3–8× more
trajectories per pair, each carrying intent + manifold metadata at
the granularity of `axiom_latent_v2.TrajectorySample`. The
constitutional gate (`MIN_TRAJECTORY_DISTANCE = 0.05`) and acceptance
threshold (`τ = 0.10`) keep the synthetic data within bounds the
forward QRF would have produced anyway, so it doesn't drift the
distribution off-manifold.

The gap is the point. Reverse-QRF turns a `~3T natural-token` corpus
into a `~10–25T trajectory-sample` corpus that's genuinely different
from a naive epoching pass — each trajectory carries different
intent geometry, different stage scaling, different rival branches —
without leaving the constitutional manifold.

### 5.5 What was built

Shipped on this branch in commit `a9b2fa7`:

| Artifact | Purpose |
|---|---|
| `axiom_qrf_reverse.py` | `ReverseQRFEngine`, `ReverseQRFResult`, `TrajectoryHypothesis`. TRUST_LEVEL 3, frozen module, HMAC-signed under `derive_key(b"axiom-qrf-reverse-v1")`. |
| `axiom_files/core/axiom_qrf_reverse.axiom` | TRUST_LEVEL 3 constitutional spec with HUMAN_REVIEW gates on `tau_threshold_change` and `branch_pool_change`. |
| `tests/test_axiom_qrf_reverse.py` | 9 tests (3 BLOCKED + 3 PASSED + 3 INVARIANTS) — all pass. Covers domain rejection, tau range validation, TRUST_LEVEL/ISOLATION immutability, score sorting, tau filtering, branch conservation, and HMAC integrity. |
| `examples/reverse_qrf_demo.py` | End-to-end demo across financial / medical / security domains, exercising all three regimes (full acceptance, partial constitutional filtering, full rejection). |

CLI: `python3 -m axiom_qrf_reverse "<prompt>" "<observed_answer>"
--domain medical --tau 0.10`.

Run the demo:

```bash
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
python3 examples/reverse_qrf_demo.py
```

---

## 6. Conservative scaling claim

Three independent mechanisms, conservatively credited:

| Mechanism                            | Conservative gain                            |
|--------------------------------------|----------------------------------------------|
| ANF sparse activation (§2)           | ~5× more parameters per compute budget       |
| Latent 3D-trajectory tokens (§4)     | ~3× signal density per training sample       |
| Reverse-QRF collapse (§5, shipped)   | ~3-5× training samples per observation       |

The 2-3× headline parameter-scaling claim sits comfortably inside the
sparse-compute multiplier alone (§2). Sections 4 and 5 are treated as
**architectural headroom**, not as headline claims, because:

- §4 requires changing the training input format (trajectories instead
  of tokens), which is a non-trivial pipeline change.
- §5 is shipped as a module but has not yet been used to generate a
  training corpus at scale — the multiplier is on paper until that
  corpus exists.

---

## 7. What's NOT claimed

- AXIOM does not train faster than Chinchilla *on the same dense
  architecture*. The headroom comes from architecture changes — sparse
  cores, modular delegates, structured trajectory inputs — not a better
  training recipe applied to a vanilla transformer.
- Reverse-QRF is shipped as a module but has not been validated at
  corpus scale. The ~3-5× synthetic-data multiplier in §5.4 is the
  designed gain per observation; the realized gain on a large run is
  unmeasured.
- Synthetic-data augmentation from RedAgent/BlueAgent (`axiom_red_agent.py`,
  `axiom_blue_agent.py`) is not credited in the headline number. Its real
  contribution depends on how much novel signal the synthetic data carries,
  which varies by domain.
- "3D tokens" is shorthand for the existing trajectory representation,
  not a new patent. The ingredients are in `axiom_latent.py`,
  `axiom_latent_v2.py`, and `axiom_intent_classifier.py` today.

---

## 8. Worked example — a 1B-parameter target

Modern over-trained baseline (per §1, picking the middle of the 200–2000
tokens/param range):

- 1B params × ~1000 tokens/param = **~1T training tokens**.
- 2022 Chinchilla baseline would have said 20B; the field has moved on.

With ANF sparse activation only (§2):

- Same ~1T training tokens.
- ~5× compute headroom → **2-3B parameters** trainable in the same
  wall-clock and compute budget.

With reverse-QRF synthetic generation layered on (§5, now built — see
`axiom_qrf_reverse.py`):

- 2-3B parameters trained on ~1T observed tokens, augmented to
  **~3-5T reverse-collapsed trajectory units**, each carrying ~3× the
  signal density of a flat token (§4).
- This pushes past the natural-data saturation ceiling identified in §5.4
  (~1000 tokens/param for typical web-quality data) by replacing scarce
  natural tokens with constitutional-bounded synthetic trajectories.

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

---

## 10. External validation — third-party benchmarks

This document's headline claim (sparse activation gives ~5× parameter
headroom at the same compute budget) is grounded in well-published
external work. The other multipliers (latent 3D trajectories, reverse-
QRF synthetic generation) are AXIOM-specific and not yet externally
benchmarked.

### 10.1 Anchors for the §1–§3 claims

- **Chinchilla** (Hoffmann et al., 2022, "Training Compute-Optimal
  Large Language Models", arXiv:2203.15556) — source of the
  20-tokens-per-parameter rule.
- **Switch Transformer** (Fedus et al., 2021, "Switch Transformers:
  Scaling to Trillion Parameter Models with Simple and Efficient
  Sparsity", arXiv:2101.03961) — establishes that activation-ratio
  improvements translate roughly to compute-per-token savings, which is
  exactly the mechanism §2 invokes for ANF.
- **Mixtral 8x7B** (Jiang et al., 2024) — concrete production MoE
  with 12.9B active / 46.7B total parameters (~28% activation ratio).
  Direct precedent for the "fewer active parameters per token" line.
- **DeepSeek-V3** (DeepSeek-AI, 2024) — 37B active / 671B total
  (~5.5% activation), plus published multi-token prediction (MTP)
  numbers relevant to §4's signal-density argument.

These four references cover the §1–§3 claims end-to-end. The §4
latent-trajectory argument and §5 reverse-QRF proposal are
AXIOM-specific and have no direct external precedent yet.

### 10.2 Quality benchmarks AXIOM already runs

These don't measure token economics but they do validate that the
architecture produces working capability:

| Benchmark    | Runner                              | Tests |
|---           |---                                  |---    |
| HumanEval    | `examples/axiom_humaneval_run.py`   | code synthesis (pass@1) |
| ARC          | `examples/axiom_arc_run.py`         | science reasoning |
| TruthfulQA   | `examples/truthfulqa_run.py`        | hallucination rate |
| AGIEval      | `examples/axiom_agi_eval.py`        | exam-style multi-domain |

All four run baseline-vs-AXIOM A/B and report accuracy deltas.

### 10.3 The token-economics gap

None of the four runners above tracks tokens. They report pass@1 or
accuracy, not tokens-per-correct-answer. That's the missing
measurement for this document's central claim.

The cheapest way to close that gap is to extend
`examples/axiom_humaneval_run.py` to record `usage.input_tokens` and
`usage.output_tokens` from each Anthropic API response and report
**tokens-per-correct-answer** for baseline vs AXIOM modes. The runner
is already two-mode A/B, so the only addition is a token counter on
the existing API loop. Result: a directly-published external metric
(pass@1) paired with a derived token-economics metric on the same
problem set.

### 10.4 What would NOT be cheap

- **DataComp-LM (DCLM)** or **Cerebras-GPT replication** — these
  measure tokens-to-quality directly, but require actually training
  a model. Not feasible without a training pipeline.
- **MLPerf Training/Inference** — formal industry submission process;
  high overhead.
- **HELM** (Stanford) — broad evaluation, but each scenario requires
  significant integration work.

For a first external token-economics data point, §10.3's HumanEval
extension is the right slice.
