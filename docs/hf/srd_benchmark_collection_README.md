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

| File | Base model | Quantization | Size |
|---|---|---|---|
| `gemma3_1b_srd4_q4km.gguf` | Gemma 3 1B | **SRD4 → Q4_K_M** | 769 MB |
| `smollm2_135m_instruct_q4km.gguf` | SmolLM2 135M Instruct | Q4_K_M | 119 MB |
| `deepseek_r1_1b5_q4km.gguf` | DeepSeek-R1 1.5B | Q4_K_M | 1.0 GB |
| `qwen3_1b7_q4km.gguf` | Qwen3 1.7B | Q4_K_M | 1.0 GB |

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
