---
license: apache-2.0
language:
- en
library_name: gguf
tags:
- quantization
- gguf
- srd
- stochastic-residual-dithering
- benchmark
- llama-cpp
- gemma
- smollm
- deepseek
- qwen
pipeline_tag: text-generation
size_categories:
- 100M<n<1B
model-index:
- name: gemma3-1b-srd4-q4km
  results: []
- name: smollm2-135m-instruct-q4km
  results: []
- name: deepseek-r1-1b5-q4km
  results: []
- name: qwen3-1b7-q4km
  results: []
---

# SRD Benchmark Collection

**Stochastic Residual Dithering (SRD)** is an experimental quantization
pre-processing technique that applies structured noise to weight residuals
before standard GGUF quantization. The hypothesis: dithering reduces
systematic rounding bias, recovering perplexity lost during aggressive
quantization — at zero extra runtime cost.

This collection publishes four GGUF models across different architectures
so the community can benchmark SRD against standard Q4_K_M / Q5_K_M / Q6_K
baselines and tell us whether the gains are real.

---

## Models in this collection

| File | Base model | Params | Quantization | File size |
|---|---|---|---|---|
| `gemma3_1b_srd4_q4km.gguf` | Gemma 3 1B | 1.0B | **SRD4 → Q4_K_M** | 769 MB |
| `smollm2_135m_instruct_q4km.gguf` | SmolLM2 135M Instruct | 135M | Q4_K_M | 119 MB |
| `deepseek_r1_1b5_q4km.gguf` | DeepSeek-R1 1.5B | 1.5B | Q4_K_M | 1.0 GB |
| `qwen3_1b7_q4km.gguf` | Qwen3 1.7B | 1.7B | Q4_K_M | 1.0 GB |

The **Gemma 3 1B** file is the SRD showcase. The other three are Q4_K_M
baselines at similar parameter counts — benchmark them the same way for
a fair cross-architecture comparison.

---

## What is SRD?

Standard INT4 quantization maps a float weight to the nearest quantization
level. Rounding error accumulates systematically, biasing activations in
one direction. SRD adds a small controlled dither signal to the residual
before rounding, spreading the error stochastically rather than
accumulating it deterministically. At inference time the dither is not
applied — the quantized weights are identical in format to standard GGUF,
so there is no runtime overhead and full llama.cpp compatibility is
preserved.

---

## Technical specification

### Algorithm

SRD is a **two-stage residual quantization** scheme applied as a
fake-quantization pre-processing step before GGUF conversion.

**Stage 1 — 4-bit base (W4)**

Per-block symmetric quantization with group size G = 64:

```
S4 = max(|W_block|) / 7          # per-block scale
W4 = round(W / S4).clamp(-8, 7)  # INT4 in [-8, 7]
W_base = W4 * S4                  # reconstructed base
```

**Stage 2 — 8-bit residue (D8)**

```
R  = W - W_base                   # residual
S8 = max(|R_block|) / 127
D8 = round(R / S8).clamp(-127, 127)
```

**Reconstruction (α-mixing)**

```
W_hat = W_base + α * (D8 * S8)   # α ∈ [0.0, 1.0]
```

α = 1.0 is the default (full residue). α = 0.0 reduces to plain INT4.

**Bits-per-weight (honest, including scales)**

| Component | Bits/weight (G=64) |
|---|---|
| W4 | 4.0 |
| D8 (dense) | 8.0 |
| S4 (float32 per block) | 32/64 = 0.5 |
| S8 (float32 per block) | 32/64 = 0.5 |
| **Total dense** | **13.0 bpw** |

Optional top-k sparsity on D8 (retain fraction p of largest-magnitude
residuals, zero the rest) produces operating points between 4.5 bpw
(p=0) and 13.0 bpw (p=1.0), filling the dead zone between Q4_K_M and
Q8_0 on the Pareto curve.

### Relationship to prior work

SRD is in the same family as **AQLM**, **QuIP#**, and residual k-means
quantization — all decompose weight error into a base + residue. The
distinction claimed here is the dither pre-processing that regularises
the residual distribution before D8 quantization. The "stochastic" in
the name refers to this step; the current implementation is
**deterministic** (the dither schedule is fixed and reproducible).
A noise-shaping filter (à la §2.2 of the original spec) is not
implemented — this is an acknowledged gap.

### Pipeline used for the GGUF files

```
FP16 weights → srd_quantize(group_size=64) → SRDPackedTensor
    → fake-dequantize(α=1.0)              → FP16 (SRD-corrected)
    → llama.cpp quantize -type q4_K_M     → GGUF
```

The SRD pre-processing modifies the weight distribution before llama.cpp
applies its own Q4_K_M rounding. The GGUF files are standard Q4_K_M and
run on any llama.cpp build — no custom kernels required.

### Honest limitations

- No fused inference kernels — bpw advantage is theoretical at this stage
- Fake-quantization only — latency and memory benchmarks are not meaningful
- No noise-shaping filter implemented (the dither is currently deterministic)
- Results on Gemma 3 1B only — cross-architecture transfer is the open question

---

## How to run

Any llama.cpp build from mid-2024 onwards works out of the box.

```bash
# Basic inference
./llama-cli \
  -m gemma3_1b_srd4_q4km.gguf \
  -p "The capital of France is" \
  -n 64

# Perplexity on WikiText-2 (the primary benchmark target)
./llama-perplexity \
  -m gemma3_1b_srd4_q4km.gguf \
  -f wikitext-2-raw/wiki.test.raw \
  --chunks 128

# Python (llama-cpp-python)
pip install llama-cpp-python
python3 -c "
from llama_cpp import Llama
llm = Llama(model_path='gemma3_1b_srd4_q4km.gguf', n_ctx=512)
print(llm('The capital of France is', max_tokens=32)['choices'][0]['text'])
"
```

---

## What to benchmark

| Metric | Tool | Notes |
|---|---|---|
| **WikiText-2 perplexity** | `llama-perplexity` | Primary signal — lower is better |
| **Tokens / second** | `llama-bench` | Same hardware for all models |
| **Peak VRAM / RAM** | `nvidia-smi` / `htop` | Note CPU vs GPU inference |
| **Hellaswag 0-shot** | `lm-evaluation-harness` | Optional, accuracy signal |

Compare `gemma3_1b_srd4_q4km` perplexity against:
- `gemma3_1b` standard Q4_K_M (from official Gemma GGUF releases)
- `gemma3_1b` Q5_K_M
- `gemma3_1b` Q6_K

The question we're asking: **does SRD-Q4_K_M match or beat standard Q5_K_M
perplexity at Q4_K_M file size?**

---

## Submit your results

Open a **Discussion** on this repo with:

```
Hardware: <CPU/GPU model>
OS: <Linux/Mac/Windows>
llama.cpp build: <git hash or release tag>

Model: gemma3_1b_srd4_q4km
WikiText-2 perplexity: X.XX (chunks=128)
Tokens/sec: XX.X

Baseline (same hardware):
  gemma3_1b Q4_K_M perplexity: X.XX
  gemma3_1b Q5_K_M perplexity: X.XX  [optional]
```

All hardware welcome — CPU, CUDA, Metal, ROCm.

---

## Community leaderboard

*Results submitted by the community. Submit yours in Discussions.*

| Contributor | Hardware | SRD-Q4KM ppl | Q4KM ppl | Q5KM ppl | Δ ppl |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

---

## About

SRD is being developed by [srd-lab](https://huggingface.co/srd-lab) as
part of the [Axiom framework](https://github.com/orivael-dev/axiom)
research track on edge quantization (Theme 2 — auto-quantization /
distillation / sparsity).

If results show SRD recovers meaningful perplexity, the technique will be
open-sourced as a standalone quantization kernel and integrated into the
Axiom `quant_map` pipeline.

**Issues / code:** open a Discussion here or file an issue on the Axiom repo.
