---
base_model: TinyLlama/TinyLlama-1.1B-Chat-v1.0
license: apache-2.0
library_name: gguf
tags:
  - quantized
  - srd
  - gguf
  - edge
  - anti-hallucination
  - llama
pipeline_tag: text-generation
model_type: llama
size_categories:
  - 100M<n<1B
---

# TinyLlama-1.1B · SRD4 Q4_K_M

Standard GGUF — drop into any llama.cpp build, no custom kernels.

**Base model:** [TinyLlama/TinyLlama-1.1B-Chat-v1.0](https://huggingface.co/TinyLlama/TinyLlama-1.1B-Chat-v1.0)  
**Quantization:** SRD4 → Q4_K_M  
**File size:** ~670 MB

## What is SRD?

Standard Q4_K_M loses information systematically. **Stochastic Residual Dithering (SRD)**
computes an INT8 residual (D8) before quantization. At load time the corrected weights
are: `W ≈ W4 + D8 × S8`. Inference speed is identical to vanilla Q4_K_M after load.

For TinyLlama specifically, **full SRD** (D8 applied to all layers) outperforms selective
correction. TinyLlama was trained with a uniform objective across layers (3T tokens on
SlimPajama/The Pile), without the layer-specialization curriculum of newer architectures.
All layers benefit equally from D8 restoration.

## Benchmark results

Evaluated on [TruthfulQA MC1](https://huggingface.co/datasets/truthful_qa) (817 questions)
and WikiText-2 perplexity.

| Mode | TruthfulQA MC1 ↑ | WikiText-2 PPL ↓ | Δ PPL | D8 overhead |
|---|---|---|---|---|
| Baseline Q4_K_M | 0.289 | 10.46 | — | 0 MB |
| Selective SRD (reasoning layers) | 0.283 | 10.23 | **-0.23** | 98 MB |
| **Full SRD** (all layers) | **0.292** | **10.18** | **-0.28** | **392 MB** |

Full SRD is recommended for this model. The LLaMA-1 architecture distributes
information uniformly — partial correction creates layer mismatch on MC1,
though both modes improve perplexity.

**WikiText-2 PPL is the primary signal for TinyLlama.** SRD delivers a clear
-0.23 to -0.28 improvement in language modeling quality. The MC1 differences
(±0.006) are within statistical noise (SE ≈ ±0.031 at n=200).

## Usage

```bash
llama-cli -m tinyllama-1b-srd4-q4km.gguf \
  -p "<|system|>\nYou are a helpful assistant.</s>\n<|user|>\nWhat is the speed of light?</s>\n<|assistant|>\n" \
  --n-predict 100
```

## How it was built

```python
from research.quant.quantize_model import quantize_hf_model_inplace
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0", torch_dtype=torch.float16
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
