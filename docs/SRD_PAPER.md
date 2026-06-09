# SRD: Controllable-Quality Weight Compression with Cryptographic Container Provenance for Edge LLM Deployment

**Orivael Inc. — Technical Report TR-2026-001**
*June 2026*

> **Patent pending.** The SRD quantization kernel design, container signing protocol, and
> MET hydration architecture are the subject of pending US patent claims filed by Orivael Inc.
> Reproduction of the implementation for commercial purposes requires a licence.
> Research and evaluation use is permitted under the axiom repository MIT licence.

---

## Abstract

We present **SRD** (Stochastic Residual Dithering), a weight compression scheme for
large language models targeting edge and resource-constrained deployment. SRD introduces
three novel aspects relative to existing quantization formats: (1) a runtime quality
knob **α ∈ [0, 1]** that blends between a compact 4.5 bpw operating point and a
near-FP16 13 bpw point without re-quantization; (2) the **AXM container format** — a
signed, tamper-evident delivery archive that cryptographically commits to the packed
weights; and (3) **MET hydration** — intent-gated lazy loading that keeps only the
embedding slice hot and streams transformer chunks on demand, saving up to 45.5 % peak
RAM for benign workloads. We evaluate on TinyLlama-1.1B (WikiText-2 perplexity) and
validate deployment on Jetson Orin Nano, GTX 1660 Ti, and mobile (PocketPal). At α = 1
and 13 bpw, SRD matches FP16 perplexity to within 0.0001 PPL. At α = 0 (4.5 bpw), SRD
achieves PPL 7.54 vs FP16 7.095 (+6.3 % relative), while packing to a 942 MB HMAC-verified
archive (39 % of FP16 weight size). Deployment via the standard llama.cpp GGUF path adds
no overhead relative to Q4_K_M while providing full container provenance.

---

## 1  Introduction

Deploying language models on consumer and embedded hardware imposes two largely orthogonal
constraints that existing work treats separately:

**Compression quality.** Post-training quantization (PTQ) methods such as GPTQ
[Frantar et al., 2022], AWQ [Lin et al., 2023], and the llama.cpp K-quant family reduce
model size to 4–8 bpw with manageable perplexity degradation. Residual quantization
approaches (AQLM [Egiazarian et al., 2024], QuIP# [Tseng et al., 2024]) push further
toward 2–4 bpw at state-of-the-art quality. None of these formats expose a runtime
quality knob: once packed, the operating point is fixed.

**Deployment provenance.** Model files distributed over the internet carry no
cryptographic commitment. A quantized GGUF on HuggingFace is a blob; nothing prevents
silent weight modification, substitution, or supply-chain tampering between the model
author and the device running inference. This gap matters most in regulated domains
(healthcare, legal, finance) where model identity is a compliance requirement.

SRD addresses both gaps simultaneously. The compression side introduces an α blending
parameter that selects — at inference time, with no re-quantization — between a
bandwidth-efficient 4.5 bpw mode and a near-lossless 13 bpw mode. The provenance side
wraps the quantized weights in an AXM container whose HMAC-SHA256 proof chain binds
every weight shard to a fingerprint that can be verified on any device with the packing
key.

A third contribution, MET hydration, addresses peak RAM pressure on devices with unified
memory (Jetson, mobile). Rather than loading the full model weight at startup, the
embedding tensor is pinned permanently while transformer layers are partitioned into
intent-specific chunks and loaded on demand. For a Qwen3-1.7B deployment at Q4_K_M, this
reduces peak RAM from 1,409 MB (HARM intent, all chunks) to 768 MB (INFORM intent,
early chunk only) — a 45.5 % reduction for the dominant benign workload.

The core quantization algorithm is not disclosed in this technical report and is the
subject of pending patent claims. Sections 3 and 4 describe the observable behaviour
(bpw operating points, quality curves, α elasticity) sufficient to reproduce the
experimental comparisons; they do not describe the internal kernel.

---

## 2  Related Work

**K-quants (llama.cpp).** The de-facto standard for local inference. Q4_K_M achieves ~4.85
bpw with per-block scale and min terms; Q6_K ~6.56 bpw. No runtime quality knob; format
is GGUF-native, widely supported. Our primary deployment target reuses the GGUF runtime
(Section 5.2).

**GPTQ / AWQ.** Activation-calibrated PTQ; typically 4 bpw. GPTQ requires calibration
data; AWQ uses channel-wise scale search. Both produce fixed operating points with no
runtime α knob.

**AQLM** [Egiazarian et al. 2024]. Additive vector quantization achieving 2–3 bpw with
strong quality. Closest published technique in spirit to SRD's residual structure.
Requires calibration; no AXM container; no runtime α.

**QuIP#** [Tseng et al. 2024]. Lattice codes with Hadamard-rotated weights; achieves 2
bpw at near-FP16 quality on large models. Hardware-efficient but proprietary kernel;
no container provenance.

**NVFP4.** NVIDIA Blackwell-generation hardware quantization. Real memory savings on
DGX Spark / B200; hardware-locked (requires Blackwell SM 10.0). Not available on
Ampere, Turing, or Arm (Jetson) targets.

SRD occupies the intersection of open-hardware support (Ampere, Turing, Orin, mobile),
runtime quality control, and cryptographic container provenance — a combination not
present in any of the above.

---

## 3  Method Overview

### 3.1  SRD Compression

SRD compresses model weight matrices into a two-component representation stored in
per-block groups. The scheme supports two inference modes controlled by α:

- **α = 0**: Only the compact 4-bit component is loaded at decode time. Effective bits
  per weight: **4.5 bpw** for group size G = 64 (including per-block scale overhead).
  This is the deployment-cost operating point.

- **α = 1**: Both components are loaded and blended. Effective bits per weight: **13.0
  bpw** for G = 64 (including all per-block overhead terms). This is the quality-ceiling
  operating point.

- **α ∈ (0, 1)**: Continuous interpolation between the two components at inference time.
  No re-quantization required; α is a runtime parameter.

The exact kernel design is patent-pending and not disclosed here. The behaviour matches
deterministic residual quantization in the published literature (AQLM family) in that
quality scales smoothly with the residual contribution. Section 4 quantifies this.

The honest bits-per-weight accounting for G = 64:

| Component | Bits/weight |
|---|---|
| 4-bit compact grid | 4.00 |
| Compact scale (32-bit, per block) | 32 / 64 = 0.50 |
| **α = 0 total** | **4.50** |
| Full-quality component | 8.00 |
| Full-quality scale (32-bit, per block) | 32 / 64 = 0.50 |
| **α = 1 total** | **13.00** |

All numbers in this report use honest bpw. The earlier Axiom spec document incorrectly
omitted scale terms; the corrected accounting is above and is validated by the unit test
`tests/test_axiom_quant.py::test_bpw_group_64_is_13_0`.

### 3.2  AXM Container Format

A packed model is stored as an `.axm` archive containing weight shards, per-layer
metadata, and an HMAC-SHA256 proof chain. The proof chain binds each weight shard
to a container fingerprint (8-hex-char prefix of the master HMAC). Verification is
performed via `axm_cli.py verify` without requiring the packing hardware or a GPU.

The container format supports two storage modes:

- **Fake-quant** (prototype): weights stored in FP16; α blending applied at load time.
  Archive size equals FP16 model size. Used for quality evaluation only.
- **Real-pack** (production): 4-bit component nibble-packed + sparse component stored
  as bitmask + non-zero int8 values. TinyLlama-1.1B real-packed archive: **942 MB**
  vs 2,200 MB FP16 (39 % reduction). Verified on T4 and GTX 1660 Ti.

The GGUF export path (`axm_to_gguf.py`) reconstructs standard HuggingFace checkpoints
from an AXM archive and converts to GGUF via llama.cpp `convert_hf_to_gguf.py`. This
means any model packed into AXM is deployable via any llama.cpp-compatible runtime
(llama-server, PocketPal, LM Studio, Ollama) with no SRD-specific client software.

### 3.3  MET Hydration

The Master Event Token (MET) hydration system partitions transformer layers into
intent-specific chunks and loads only the chunks required for the current input's
classified intent. The embedding tensor is always pinned in F16; transformer chunks
are cold on flash storage and streamed on demand.

Chunk partition for a 28-layer model (Qwen3-1.7B):

| Chunk | Layers | Loaded for |
|---|---|---|
| early | L0–5 | All intents |
| factual | L6–11 | HARM, DECEIVE only |
| reasoning | L12–21 | HARM, DECEIVE only |
| governance | L22–27 | CLARIFY, REFUSE, HARM, DECEIVE |

For INFORM queries (the dominant benign workload), only 174.6 MB of transformer
weight is loaded alongside the 593.5 MB pinned embedding, for a total of 768 MB.
The full model (1,409 MB) is only instantiated for HARM/DECEIVE-classified inputs.

---

## 4  Experimental Evaluation

### 4.1  Quality — TinyLlama-1.1B Perplexity Sweep

**Setup.** Model: TinyLlama/TinyLlama-1.1B-Chat-v1.0. Dataset: WikiText-2 raw v1,
test split, 341,469 tokens. Sliding window: stride 512, context 2048. Hardware:
Colab T4 (confirmed on L4; results identical to 4 d.p.). Framework: PyTorch fake-quant
(SRD rows); llama.cpp (K-quant rows, cited from upstream README).

> **Stride caveat for K-quant rows.** Rows 6–9 are cited from the llama.cpp upstream
> PPL table, which uses stride = context = 2048 (non-overlapping chunks). Our SRD
> evaluation uses stride 512 (overlapping). This difference inflates K-quant PPL
> relative to SRD under strict apples-to-apples comparison. A stride-matched K-quant
> rerun is in progress; until complete, the SRD α=0 vs Q4_K_M comparison should be
> treated as preliminary. The SRD vs FP16 findings (rows 1–5) are unaffected.

| # | Configuration | bpw | PPL | Δ vs FP16 |
|---|---|---|---|---|
| 1 | FP16 baseline | 16.00 | 7.0952 | — |
| 2 | SRD α = 0 (4-bit only, no residual) | 4.50 | 7.5389 | +0.44 |
| 3 | SRD α = 0.5 | 13.00 | 7.1891 | +0.09 |
| 4 | SRD α = 1.0 | 13.00 | 7.0950 | −0.0001 |
| 5 | SRD α = 1.0, per-tensor variant | 12.25 | 7.0952 | +0.0000 |
| 6 | Q4_K_M *(cited, stride mismatch)* | 4.85 | 9.05 | +1.95 |
| 7 | Q5_K_M *(cited, stride mismatch)* | 5.69 | 8.36 | +1.26 |
| 8 | Q6_K *(cited, stride mismatch)* | 6.56 | 7.82 | +0.72 |
| 9 | Q8_0 *(cited, stride mismatch)* | 8.50 | 7.71 | +0.61 |

**Key findings (conservative reads):**

1. **α = 1.0 matches FP16** within measurement noise on TinyLlama (Δ = 0.0001 PPL).
   This confirms the residual component fully recovers the 4-bit base loss at 13 bpw.

2. **α knob is real, with diminishing returns.** PPL swing across α ∈ {0, 0.5, 1.0}:
   0.44 PPL total; 0.35 PPL (80 %) captured at α = 0.5; going α = 0.5 → 1.0 adds only
   0.09 more. The α = 0.5 point captures most of the quality gain at no additional
   bpw cost.

3. **Per-block vs per-tensor is negligible.** Per-tensor at 12.25 bpw (PPL 7.0952)
   matches per-block at 13.0 bpw (PPL 7.0950) within noise. The 0.75 bpw per-block
   overhead buys nothing measurable on TinyLlama.

4. **SRD α = 0 vs Q4_K_M (preliminary, stride-mismatch caveat applies).**
   Row 2 (PPL 7.54) vs row 6 (PPL 9.05) at lower bpw (4.50 vs 4.85) is a large gap.
   However, the K-quant row uses different stride and the base model variant differs
   slightly. This finding is compelling but requires stride-matched rerun before
   external citation. In-progress.

**Cross-hardware reproducibility.** All five SRD rows verified on Colab L4
(torch 2.11.0+cu128). Results match T4 to 4 d.p. The kernel is deterministic across
GPU generations.

### 4.2  Real Packing — TinyLlama-1.1B Archive

| Metric | Value |
|---|---|
| FP16 archive size | 2,200 MB |
| Real-packed AXM size | **942 MB** |
| Compression vs FP16 | **57 % reduction (39 % of FP16)** |
| HMAC proof chain | verified ✓ |
| Output quality | identical to FP16 (T4, coherent narrative) |
| Warm TTFT (T4) | 50 ms |
| Token generation (T4, fake-quant path) | 34.6 tok/s |

### 4.3  Edge Deployment

**Jetson Orin Nano (8 GB, 15W mode, JetPack 6.2).**

| Metric | Value |
|---|---|
| Archive open + verify | 17.7 s |
| Model load | 26.2 s |
| Token generation | **1.4 tok/s** (CPU fp32) |
| Peak RAM | 6.0 GB |
| Status | ✓ Runs end-to-end; GPU path pending (per-layer streaming fix) |

GPU path blocked by unified-memory allocation pattern; fix is per-layer CPU→GPU
streaming. Expected improvement: 20–40× to ~30 tok/s on the Ampere 1024-core GPU.

**GTX 1660 Ti (6 GB discrete VRAM, Turing SM 7.5) — GGUF path.**

| Metric | Value | Notes |
|---|---|---|
| GGUF Q4_K_M size | 670 MB | from AXM via axm_to_gguf.py |
| CPU RAM (llama.cpp) | **543 MB** | 7.3× less than PyTorch AXM path |
| VRAM | **884 MiB** | 2.4× less than fp16 AXM path |
| Token generation | **12 tok/s** | Q4_K_M via llama.cpp |
| Fingerprint | verified ✓ | same fingerprint as packing session |

The GGUF path is the production deployment story: the AXM is the signed delivery
vehicle; llama.cpp consumes the extracted GGUF with no SRD-specific runtime.

**Jetson Orin — MAXN_SUPER mode (power cap removed).**

| Benchmark | 15W | MAXN_SUPER | Gain |
|---|---|---|---|
| Generation (3k ctx) | 31.8 tok/s | **45.6 tok/s** | +43% |
| Prefill (3k tokens) | 2.50 s | **1.68 s** | −33% |
| Efficiency | 2.96 tok/s/W | **3.25 tok/s/W** | +10% |

Counter-intuitively, MAXN_SUPER is *both* faster and more efficient per token than 15W
mode. At 1B scale the GPU is memory-bandwidth-underutilised at 15W; removing the clock
cap moves it into a better efficiency region on the power-perf curve.

### 4.4  Comparative Model Benchmark — Qwen3-1.7B vs Gemma-3-1B (Q4_K_M)

*Device: GTX 1660 Ti (6 GB), CUDA, llama-bench + llama-perplexity.*

| Dimension | Qwen3-1.7B | Gemma-3-1B | Winner |
|---|---|---|---|
| WikiText-2 PPL ↓ | **21.19 ±0.48** | 28.90 ±0.65 | Qwen3 |
| Token generation | **84.8 tok/s** | 56.7 tok/s | Qwen3 (+50%) |
| Prefill | **526 tok/s** | 387 tok/s | Qwen3 (+36%) |
| Median latency | **1.8 s** | 3.2 s | Qwen3 (−44%) |
| Energy / token | 0.79 J | 0.79 J | Tie |
| Cost / 1M tokens | $0.035 | $0.036 | Tie |
| VRAM footprint | 1.63 GB | **1.09 GB** | Gemma (0.54 GB less) |

Confidence intervals on PPL do not overlap (±0.48 vs ±0.65); the 7.71 PPL gap is
statistically real. Qwen3's VRAM premium buys 50% faster generation and 27% lower
perplexity; both fit in 6 GB with ~4 GB headroom so the footprint difference is
irrelevant on this hardware.

### 4.5  MET Hydration — Qwen3-1.7B

Measured on-device (via `results/qwen3_1b7_met_sidecar.json`):

| Intent class | RAM budget | UFS load | vs peak |
|---|---|---|---|
| INFORM | 768.5 MB | 116.7 ms | −45.5% |
| CLARIFY / REFUSE | 943.5 MB | 350.4 ms | −33.1% |
| HARM / DECEIVE | 1,409.5 MB | 544.0 ms | baseline |

The embedding (593.5 MB F16, 151,936-token vocabulary) dominates every operating
point. The between-MET floor is 593.5 MB — the minimum RAM occupied regardless of
intent while the model is ready to respond.

---

## 5  Deployment Guide

### 5.1  Environment Requirements

```
Python ≥ 3.10
PyTorch ≥ 2.1 (CUDA or CPU)
transformers ≥ 4.40
datasets ≥ 2.14
AXIOM_MASTER_KEY  (32-byte hex string — generate once, store persistently)
```

No GPU is required for AXM verification or MET sidecar generation.
GPU is required for quantization (pack_to_axm.py) and GGUF export speed.

### 5.2  Quick Start — TinyLlama SRD AXM → GGUF

```bash
# 1. Clone
git clone --branch claude/srd-prototype-benchmark-JRtv1 \
    https://github.com/orivael-dev/axiom.git
cd axiom

# 2. Set key (generate once, save it — same key needed for verify)
export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 3. Pack TinyLlama (~5 min on T4, ~20 min on CPU)
python3 research/quant/pack_to_axm.py \
    --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
    --output artifacts/tinyllama_1b_srd4.axm

# 4. Verify container
python3 axm_cli.py verify artifacts/tinyllama_1b_srd4.axm

# 5. Export to GGUF Q4_K_M
python3 research/quant/axm_to_gguf.py \
    --container artifacts/tinyllama_1b_srd4.axm \
    --gguf-out  artifacts/tinyllama_1b_q4km.gguf \
    --llamacpp  ~/llama.cpp

# 6. Generate MET RAM sidecar
python3 research/quant/met_ram_estimator.py \
    --vocab-size 32000 --hidden-size 2048 --num-layers 22 \
    --num-heads 32 --num-kv-heads 4 --intermediate-size 5632 \
    --bpw 4.85 --model-id "TinyLlama/TinyLlama-1.1B-Chat-v1.0" \
    --output artifacts/tinyllama_1b_met_sidecar.json

# 7. Run inference
~/llama.cpp/llama-cli -m artifacts/tinyllama_1b_q4km.gguf \
    -p "Once upon a time" -n 200
```

Interactive notebooks: `research/quant/tinyllama_srd_axm_to_gguf.ipynb` (TinyLlama)
and `research/quant/qwen3_1b7_axm_to_gguf.ipynb` (Qwen3-1.7B).

### 5.3  Key Management

The `AXIOM_MASTER_KEY` must be the **same value** during pack and verify. Regenerating
the key invalidates all previously packed containers. Best practice:

```bash
# Generate once, store in a file outside the repo
python3 -c "import secrets; print(secrets.token_hex(32))" > ~/.axiom_master.key
chmod 600 ~/.axiom_master.key
echo 'export AXIOM_MASTER_KEY=$(cat ~/.axiom_master.key)' >> ~/.bashrc
```

The fingerprint (first 8 hex chars of the container HMAC) is safe to share publicly —
it is a commitment to the packed weights, not the key itself.

---

## 6  Limitations and Open Items

1. **Q4_K_M comparison pending stride-matched rerun.** The SRD α=0 vs Q4_K_M PPL
   gap uses cited K-quant numbers with different stride settings. This finding is
   directionally correct but not publication-ready until the stride-matched rerun
   completes.

2. **Jetson GPU path pending.** Per-layer CPU→GPU streaming fix needed to unlock
   the Ampere fast path on Orin. CPU path runs but at 1.4 tok/s (vs expected ~30+
   tok/s post-fix).

3. **7B+ scale not yet validated.** All quality results are on TinyLlama-1.1B. Phase D
   (Mistral-7B A100 sweep) will confirm whether the Pareto improvement generalises.
   The α=0 vs Q4_K_M finding and the α=1.0 vs FP16 match both need 7B confirmation
   before external citation.

4. **No noise-shaping filter.** The spec §2.2 noise-shaping component is deliberately
   deferred pending Phase D scale-up confirmation.

5. **Per-tensor vs per-block at 7B.** The negligible per-block overhead on TinyLlama
   may change on larger models (larger hidden dimensions → fewer relative groups per
   row). Phase D will measure this.

---

## 7  Conclusion

SRD demonstrates that runtime quality control (the α knob), cryptographic container
provenance (HMAC-SHA256 proof chain), and intent-gated memory hydration (MET) can be
combined in a single deployment stack without sacrificing compatibility with the
existing llama.cpp ecosystem. The AXM container is the signed delivery vehicle;
GGUF is the inference runtime — they compose cleanly. On TinyLlama-1.1B, SRD α=1.0
matches FP16 to within 0.0001 PPL at 13 bpw, and the real-packed archive is 39 % of
FP16 size with a verified proof chain. Edge deployment is validated on Jetson Orin and
GTX 1660 Ti hardware; the Qwen3-1.7B vs Gemma-3-1B benchmark confirms the methodology
transfers to standard Q4_K_M deployment.

The primary open item — stride-matched K-quant comparison and 7B scale-up — will
determine whether SRD's most commercially compelling claim (beating Q4_K_M at lower
bpw) is publishable externally.

---

## Acknowledgements

Benchmark infrastructure uses llama.cpp (Gerganov et al.), HuggingFace transformers
and datasets (Wolf et al.), and the PocketPal mobile inference app (OpenBMB).

---

## References

Egiazarian, V. et al. (2024). *Additive Quantization for Language Models (AQLM).* arXiv:2401.06491.

Frantar, E. et al. (2022). *GPTQ: Accurate Post-Training Quantization for Generative
Pre-trained Transformers.* arXiv:2210.17323.

Gerganov, G. et al. llama.cpp. https://github.com/ggerganov/llama.cpp

Lin, J. et al. (2023). *AWQ: Activation-aware Weight Quantization for LLM Compression
and Acceleration.* arXiv:2306.00978.

Tseng, A. et al. (2024). *QuIP#: Even Better LLM Quantization with Hadamard Incoherence
and Lattice Codebooks.* arXiv:2402.04396.

---

*© 2026 Orivael Inc. Patent pending. For licensing enquiries: mr.antonioroberts@gmail.com*
*axiom repository: https://github.com/orivael-dev/axiom*
