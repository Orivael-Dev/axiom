---
type: org
---

# srd-lab

Research lab exploring **Stochastic Residual Dithering (SRD)** — an experimental
quantization pre-processing technique for making small language models run faster
and cheaper on edge hardware without sacrificing output quality.

## What we're working on

Standard 4-bit quantization loses information systematically. SRD applies a
structured residual decomposition before quantization, recovering perplexity
that would otherwise be lost — at zero extra inference cost.

The open question: **does it actually work across architectures?**
That's what this lab is here to find out, with community help.

## Models

| Model | Architecture | Quantization | Size |
|---|---|---|---|
| [benchmark-collection](https://huggingface.co/srd-lab/benchmark-collection) | Gemma 3 / SmolLM2 / DeepSeek-R1 / Qwen3 | SRD4 → Q4_K_M | 119 MB – 1.0 GB |

All models are standard GGUF — drop into any llama.cpp build, no custom kernels.

## How to contribute

1. Download a model from the [benchmark collection](https://huggingface.co/srd-lab/benchmark-collection)
2. Run `llama-perplexity` on WikiText-2 and compare against the Q4_K_M baseline
3. Open a Discussion with your results — hardware, perplexity, tokens/sec

All hardware welcome: CPU, CUDA, Metal, ROCm.

## Built on

- [Axiom Framework](https://github.com/orivael-dev/axiom) — the research infrastructure behind SRD
- [llama.cpp](https://github.com/ggerganov/llama.cpp) — inference runtime
- Residual quantization family: AQLM, QuIP#, residual k-means
