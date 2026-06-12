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

All models are standard GGUF — drop into any llama.cpp build, no custom kernels.

| Model | Base | Size | TruthfulQA MC1 | Δ vs Q4_K_M |
|---|---|---|---|---|
| [SmolLM2-135M-SRD4](https://huggingface.co/srd-lab/smollm2-135m-srd4) | SmolLM2-135M-Instruct | 119 MB | 0.289 | +1.4% |
| [Qwen2.5-Coder-0.5B-SRD4](https://huggingface.co/srd-lab/qwen25-coder-0p5b-srd4) | Qwen2.5-Coder-0.5B-Instruct | 360 MB | 0.307 | +0.4% |
| [Gemma3-1B-SRD4](https://huggingface.co/srd-lab/gemma3-1b-srd4) | Gemma3-1B-IT | 670 MB | 0.318 | +1.9% |
| [TinyLlama-1.1B-SRD4](https://huggingface.co/srd-lab/tinyllama-1b-srd4) | TinyLlama-1.1B-Chat | 670 MB | 0.292 | +0.3% |

Evaluated on TruthfulQA MC1 (817 questions). Higher = less hallucination.

## Key finding

Selective D8 correction — applied only to the reasoning layers (40–77% of model
depth) — matches or exceeds full-model correction at 8–11% of the memory overhead.
For Gemma3-1B, selective outperforms full correction by +1.1%.

## How to contribute

1. Download any model above
2. Run `llama-perplexity` on WikiText-2 and compare against the Q4_K_M baseline
3. Open a Discussion on the model repo with your results — hardware, perplexity, tokens/sec

All hardware welcome: CPU, CUDA, Metal, ROCm.

## Built on

- [Axiom Framework](https://github.com/orivael-dev/axiom) — the research infrastructure behind SRD
- [llama.cpp](https://github.com/ggerganov/llama.cpp) — inference runtime
- Residual quantization family: AQLM, QuIP#, residual k-means
