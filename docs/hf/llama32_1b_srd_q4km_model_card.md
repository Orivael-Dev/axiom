---
base_model: meta-llama/Llama-3.2-1B-Instruct
license: llama3.2
library_name: gguf
tags:
  - quantized
  - srd
  - gguf
  - edge
  - anti-hallucination
  - llama
  - meta
pipeline_tag: text-generation
model_type: llama
size_categories:
  - 100M<n<1B
---

# Llama-3.2-1B-Instruct · SRD Q4_K_M

Standard GGUF — drop into any llama.cpp build, no custom kernels.

**Base model:** [meta-llama/Llama-3.2-1B-Instruct](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct)  
**Quantization:** SRD4 → Q4_K_M  
**File size:** ~770 MB

## What is SRD?

Standard Q4_K_M loses information systematically. **Stochastic Residual Dithering (SRD)**
computes an INT8 residual (D8) before quantization. At load time the corrected weights
are: `W ≈ W4 + D8 × S8`. Inference speed is identical to vanilla Q4_K_M after load.

SRD targets the **reasoning layers** (40–77% of depth, layers 6–12 of 16 in this model).
LlamaForCausalLM architecture with uniform training — selective SRD targets the mid-depth
layers where instruction-following coherence lives. Selective correction focuses D8 on
this range rather than applying overhead to well-calibrated factual and output layers.

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

> **Note:** The base model is gated on HuggingFace — re-downloading base weights requires
> Meta license acceptance. The GGUF here is the quantized artifact only; no base weights
> are redistributed.

```bash
llama-cli -m llama-3-2-1b-instruct_srd_q4_k_m.gguf \
  -p "<|begin_of_text|><|start_header_id|>user<|end_header_id|>

Your question here<|eot_id|><|start_header_id|>assistant<|end_header_id|>" \
  --n-predict 200
```

## How it was built

```python
from research.quant.quantize_model import quantize_hf_model_inplace
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "meta-llama/Llama-3.2-1B-Instruct", torch_dtype=torch.float16
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
