---
base_model: google/gemma-3-1b-it
license: gemma
library_name: gguf
tags:
  - quantized
  - srd
  - gguf
  - edge
  - anti-hallucination
  - gemma
pipeline_tag: text-generation
model_type: gemma3
size_categories:
  - 100M<n<1B
---

# Gemma3-1B · SRD4 Q4_K_M

Standard GGUF — drop into any llama.cpp build, no custom kernels.

**Base model:** [google/gemma-3-1b-it](https://huggingface.co/google/gemma-3-1b-it)  
**Quantization:** SRD4 → Q4_K_M  
**File size:** ~670 MB

## What is SRD?

Standard Q4_K_M loses information systematically. **Stochastic Residual Dithering (SRD)**
computes an INT8 residual (D8) before quantization. At load time the corrected weights
are: `W ≈ W4 + D8 × S8`. Inference speed is identical to vanilla Q4_K_M after load.

SRD targets the **reasoning layers** (40–77% of depth, 7 layers in this model).
Gemma3's architecture shows the clearest layer specialization of the models tested —
selective correction outperforms applying D8 to all layers.

## Benchmark results

Evaluated on [TruthfulQA MC1](https://huggingface.co/datasets/truthful_qa) (817 questions)
and WikiText-2 perplexity.

| Mode | TruthfulQA MC1 ↑ | Δ vs baseline | D8 overhead |
|---|---|---|---|
| Baseline Q4_K_M | 0.299 | — | 0 MB |
| **Selective SRD** (reasoning layers) | **0.318** | **+1.9%** | **49 MB** |
| Full SRD (all layers) | 0.307 | +0.8% | 641 MB |

**Selective SRD outperforms full SRD by +1.1%** at 8% of the RAM cost.  
Applying D8 correction beyond the reasoning chunk adds noise rather than signal —
the factual and output layers were already well-calibrated at Q4.

WikiText-2 PPL: ~33 (baseline) → ~31 (selective).

## Usage

```bash
llama-cli -m gemma3-1b-srd4-q4km.gguf \
  -p "<start_of_turn>user\nExplain quantum entanglement simply.<end_of_turn>\n<start_of_turn>model\n" \
  --n-predict 200
```

## How it was built

```python
from research.quant.quantize_model import quantize_hf_model_inplace
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "google/gemma-3-1b-it", torch_dtype=torch.float16
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
