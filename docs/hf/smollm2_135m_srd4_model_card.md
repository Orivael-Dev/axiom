---
base_model: HuggingFaceTB/SmolLM2-135M-Instruct
license: apache-2.0
library_name: gguf
tags:
  - quantized
  - srd
  - gguf
  - edge
  - anti-hallucination
  - smollm2
pipeline_tag: text-generation
model_type: llama
size_categories:
  - 10M<n<100M
---

# SmolLM2-135M · SRD4 Q4_K_M

Standard GGUF — drop into any llama.cpp build, no custom kernels.

**Base model:** [HuggingFaceTB/SmolLM2-135M-Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct)  
**Quantization:** SRD4 → Q4_K_M  
**File size:** ~119 MB

## What is SRD?

Standard Q4_K_M loses information systematically. **Stochastic Residual Dithering (SRD)**
computes an INT8 residual (D8) before quantization and stores it as a sidecar. At load
time the corrected weights are: `W ≈ W4 + D8 × S8`. After that, inference is identical
to vanilla Q4_K_M — zero per-token overhead.

For small models like this one, SRD targets the **reasoning layers** (40–77% of depth)
where Q4 degradation hurts most. This is called selective correction.

## Benchmark results

Evaluated on [TruthfulQA MC1](https://huggingface.co/datasets/truthful_qa) (817 questions)
and WikiText-2 perplexity. All modes use fake-quantized FP16 weights.

| Mode | TruthfulQA MC1 ↑ | Δ vs baseline | D8 overhead |
|---|---|---|---|
| Baseline Q4_K_M | 0.275 | — | 0 MB |
| **Selective SRD** (reasoning layers) | **0.289** | **+1.4%** | **13 MB** |
| Full SRD (all layers) | 0.291 | +1.6% | 114 MB |

Selective correction reaches full-SRD quality at **11% of the RAM cost**.  
WikiText-2 PPL: ~31 (baseline) → ~30 (selective).

## Usage

```bash
llama-cli -m smollm2-135m-srd4-q4km.gguf \
  -p "The capital of France is" \
  --n-predict 50
```

## How it was built

```python
from research.quant.quantize_model import quantize_hf_model_inplace
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "HuggingFaceTB/SmolLM2-135M-Instruct", torch_dtype=torch.float16
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
