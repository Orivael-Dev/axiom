---
base_model: Qwen/Qwen3-1.7B
license: apache-2.0
library_name: gguf
tags:
  - quantized
  - srd
  - gguf
  - edge
  - anti-hallucination
  - qwen
  - qwen3
  - reasoning
pipeline_tag: text-generation
model_type: qwen3
size_categories:
  - 1B<n<10B
---

# Qwen3-1.7B · SRD4 Q4_K_M

Standard GGUF — drop into any llama.cpp build, no custom kernels.

**Base model:** [Qwen/Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B)  
**Quantization:** SRD4 → Q4_K_M  
**File size:** ~1.2 GB

## What is SRD?

Standard Q4_K_M loses information systematically. **Stochastic Residual Dithering (SRD)**
computes an INT8 residual (D8) before quantization. At load time the corrected weights
are: `W ≈ W4 + D8 × S8`. Inference speed is identical to vanilla Q4_K_M after load.

SRD targets the **reasoning layers** (40–77% of depth, layers 11–21 of 28 in this model).
Qwen3 supports both thinking (reasoning) and non-thinking modes via `/think` and `/no_think`
tokens. SRD selective targets the reasoning chunk — this is the highest-leverage correction
range in thinking mode, where chain-of-thought coherence is most sensitive to quantization
error.

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

> **Note:** `/think` mode produces `<think>...</think>` traces before the answer — these
> are reasoning traces, not errors. `/no_think` gives direct answers without traces:
> faster but less accurate on complex tasks. Choose the mode that fits your latency and
> accuracy requirements.

**Thinking mode (recommended for complex tasks):**

```bash
llama-cli -m qwen3_1b7_q4km.gguf \
  -p "<|im_start|>user
/think What is the integral of x^2?<|im_end|>
<|im_start|>assistant" \
  --n-predict 400
```

**Non-thinking mode (faster, direct answers):**

```bash
llama-cli -m qwen3_1b7_q4km.gguf \
  -p "<|im_start|>user
/no_think What is the integral of x^2?<|im_end|>
<|im_start|>assistant" \
  --n-predict 200
```

## How it was built

```python
from research.quant.quantize_model import quantize_hf_model_inplace
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen3-1.7B", torch_dtype=torch.float16
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
