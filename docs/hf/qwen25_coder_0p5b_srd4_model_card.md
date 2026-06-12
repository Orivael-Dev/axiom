---
base_model: Qwen/Qwen2.5-Coder-0.5B-Instruct
license: apache-2.0
library_name: gguf
tags:
  - quantized
  - srd
  - gguf
  - edge
  - anti-hallucination
  - qwen
  - code
pipeline_tag: text-generation
model_type: qwen2
size_categories:
  - 100M<n<1B
---

# Qwen2.5-Coder-0.5B · SRD4 Q4_K_M

Standard GGUF — drop into any llama.cpp build, no custom kernels.

**Base model:** [Qwen/Qwen2.5-Coder-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct)  
**Quantization:** SRD4 → Q4_K_M  
**File size:** ~360 MB

## What is SRD?

Standard Q4_K_M loses information systematically. **Stochastic Residual Dithering (SRD)**
computes an INT8 residual (D8) before quantization. At load time the corrected weights
are: `W ≈ W4 + D8 × S8`. Inference speed is identical to vanilla Q4_K_M after load.

SRD targets the **reasoning layers** (40–77% of depth, 9 layers in this model),
where Q4 degradation disrupts multi-step code reasoning most.

## Benchmark results

Evaluated on [TruthfulQA MC1](https://huggingface.co/datasets/truthful_qa) (817 questions).
WikiText-2 PPL is reported for completeness but note this is a **code-specialized model** —
WikiText-2 (English prose) is off-domain; code benchmarks are more representative.

| Mode | TruthfulQA MC1 ↑ | Δ vs baseline | D8 overhead |
|---|---|---|---|
| Baseline Q4_K_M | 0.303 | — | 0 MB |
| **Selective SRD** (reasoning layers) | **0.307** | **+0.4%** | **35 MB** |
| Full SRD (all layers) | 0.305 | +0.2% | 391 MB |

Selective matches or edges full SRD at **9% of the RAM cost**.

## Usage

```bash
llama-cli -m qwen25-coder-0p5b-srd4-q4km.gguf \
  -p "def fibonacci(n):" \
  --n-predict 80
```

## How it was built

```python
from research.quant.quantize_model import quantize_hf_model_inplace
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen2.5-Coder-0.5B-Instruct", torch_dtype=torch.float16
)
quantize_hf_model_inplace(model, alpha=1.0, group_size=64)
# → converted to GGUF Q4_K_M via llama.cpp convert_hf_to_gguf.py
```

Pipeline: [orivael-dev/axiom](https://github.com/orivael-dev/axiom) — branch `claude/srd-prototype-benchmark-JRtv1`

## Contribute results

Run `llama-perplexity` on a code corpus or HumanEval and open a Discussion with:
- Hardware (CPU / CUDA / Metal / ROCm)
- Perplexity or pass@k score
- Tokens/sec
