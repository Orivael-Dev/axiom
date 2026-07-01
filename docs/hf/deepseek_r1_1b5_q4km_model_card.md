---
base_model: deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B
license: mit
library_name: gguf
tags:
  - quantized
  - srd
  - gguf
  - edge
  - anti-hallucination
  - deepseek
  - reasoning
pipeline_tag: text-generation
model_type: qwen2
size_categories:
  - 1B<n<10B
---

# DeepSeek-R1-Distill-1.5B · SRD4 Q4_K_M

Standard GGUF — drop into any llama.cpp build, no custom kernels.

**Base model:** [deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B)  
**Quantization:** SRD4 → Q4_K_M  
**File size:** ~1.1 GB

## What is SRD?

Standard Q4_K_M loses information systematically. **Stochastic Residual Dithering (SRD)**
computes an INT8 residual (D8) before quantization. At load time the corrected weights
are: `W ≈ W4 + D8 × S8`. Inference speed is identical to vanilla Q4_K_M after load.

SRD targets the **reasoning layers** (40–77% of depth, layers 11–21 of 28 in this model).
This is a distilled reasoning model — the chain-of-thought reasoning layers are the
highest-leverage SRD target. Selective correction focuses D8 on the depth range where
multi-step inference coherence is encoded.

## Benchmark results

Evaluated on [TruthfulQA MC1](https://huggingface.co/datasets/truthful_qa) (817 questions)
and WikiText-2 perplexity.

Built 2025-06-08 — benchmarks pending.

| Mode | TruthfulQA MC1 ↑ | Δ vs baseline | D8 overhead |
|---|---|---|---|
| Baseline Q4_K_M | TBD | — | 0 MB |
| **Selective SRD** (reasoning layers) | **TBD** | **TBD** | TBD |
| Full SRD (all layers) | TBD | TBD | TBD |

WikiText-2 PPL: TBD

## Usage

> **Note:** DeepSeek-R1 outputs `<think>...</think>` blocks before the final answer.
> These are reasoning traces, not errors — they are part of the model's chain-of-thought
> and can be stripped or displayed depending on your application.

```bash
llama-cli -m deepseek_r1_1b5_q4km.gguf \
  -p "<|User|>What is 15% of 840?<|Assistant|><think>" \
  --n-predict 400
```

## How it was built

```python
from research.quant.quantize_model import quantize_hf_model_inplace
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B", torch_dtype=torch.float16
)
quantize_hf_model_inplace(model, alpha=1.0, group_size=64)
# → converted to GGUF Q4_K_M via llama.cpp convert_hf_to_gguf.py
```

Pipeline: [orivael-dev/axiom](https://github.com/orivael-dev/axiom) — branch `claude/srd-prototype-benchmark-JRtv1`

## Contribute results

Run `llama-perplexity` on WikiText-2 and open a Discussion with:
- Hardware (CPU / CUDA / Metal / ROCm)
- Perplexity score
- Tokens/sec
