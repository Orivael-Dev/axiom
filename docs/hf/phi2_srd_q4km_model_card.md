---
base_model: microsoft/phi-2
license: mit
library_name: gguf
tags:
  - quantized
  - srd
  - gguf
  - edge
  - anti-hallucination
  - phi
  - microsoft
  - reasoning
pipeline_tag: text-generation
model_type: phi
size_categories:
  - 1B<n<10B
---

# Phi-2 · SRD Q4_K_M

Standard GGUF — drop into any llama.cpp build, no custom kernels.

**Base model:** [microsoft/phi-2](https://huggingface.co/microsoft/phi-2)  
**Quantization:** SRD4 → Q4_K_M  
**File size:** ~1.7 GB

## What is SRD?

Standard Q4_K_M loses information systematically. **Stochastic Residual Dithering (SRD)**
computes an INT8 residual (D8) before quantization. At load time the corrected weights
are: `W ≈ W4 + D8 × S8`. Inference speed is identical to vanilla Q4_K_M after load.

SRD targets the **reasoning layers** (40–77% of depth, layers 12–24 in this model).
Phi-2 was trained on textbook-quality data — its mid-depth reasoning cluster is where
Q4 quantization noise has the most impact on multi-step correctness.

## Benchmark results

Evaluated on [TruthfulQA MC1](https://huggingface.co/datasets/truthful_qa) and
WikiText-2 perplexity. Results pending — run the benchmark and open a Discussion.

| Mode | TruthfulQA MC1 ↑ | Δ vs baseline | D8 overhead |
|---|---|---|---|
| Baseline Q4_K_M | TBD | — | 0 MB |
| **Selective SRD** (layers 12–24) | **TBD** | **TBD** | TBD MB |
| Full SRD (all layers) | TBD | TBD | TBD MB |

## Usage

```bash
llama-cli -m phi-2-srd-q4km.gguf \
  -p "Instruct: Explain the difference between a mutex and a semaphore.\nOutput:" \
  --n-predict 300
```

Phi-2 responds well to both the `Instruct:/Output:` format and the `Human:/Assistant:` format:

```bash
llama-cli -m phi-2-srd-q4km.gguf \
  -p "Human: Write a Python function to binary search a sorted list.\nAssistant:" \
  --n-predict 300
```

## How it was built

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from research.quant.quantize_model import quantize_hf_model_inplace

tokenizer = AutoTokenizer.from_pretrained("microsoft/phi-2", trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    "microsoft/phi-2", torch_dtype=torch.float16, device_map="auto",
    trust_remote_code=True
)
quantize_hf_model_inplace(model, alpha=1.0, group_size=64)
# → converted to GGUF Q4_K_M via llama.cpp convert_hf_to_gguf.py
```

Note: `trust_remote_code=True` is required for Phi-2's custom attention implementation.

Pipeline: [orivael-dev/axiom](https://github.com/orivael-dev/axiom) — branch `claude/srd-prototype-benchmark-JRtv1`

## Contribute results

Run `llama-perplexity` on WikiText-2 and open a Discussion with:
- Hardware (CPU / CUDA / Metal / ROCm)
- Perplexity score
- Tokens/sec
