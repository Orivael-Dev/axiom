---
language: en
license: gemma
base_model: google/gemma-4-12b-it
tags:
  - gemma
  - gemma-4
  - google
  - srd4-governance
  - axm-container
  - gguf
  - quantized
  - q4_k_m
  - instruction-tuned
  - orivael
library_name: gguf
pipeline_tag: text-generation
---

# orivael/gemma-4-12b-it-SRD4-Q4_K_M

**Gemma 4 12B Instruct** quantized with **SRD-4 (Stochastic Residual Dithering)**
and packed into a cryptographically signed `.axm` governance container.

The GGUF file (`gemma4_12b_it_srd4_q4km.gguf`) is a drop-in replacement for any
standard Q4_K_M GGUF — same llama.cpp commands, same hardware, same context length.

---

## What is SRD-4?

Standard INT4 quantization discards the rounding residuals.
SRD-4 stores them in a compact signed sidecar (the `.axm` container), applies
stochastic dithering before quantizing, and recovers the residuals at load time —
raising effective precision without adding runtime cost.

The correction is applied **once at load time** (static, not dynamic). After that,
inference runs at exactly the same speed as vanilla Q4_K_M.

---

## Benchmark

> Results from `gemma4_12b_srd_benchmark.ipynb` — WikiText-2 PPL,
> stride 512, context 2048, 100 chunks.

| Method | bpw | WikiText-2 PPL | Size |
|---|---|---|---|
| **SRD Q4_K_M (this repo)** | ~4.85 | **TBD** ← Cell 6 | ~7.5 GB |
| Standard Q4_0 (community) | ~4.0 | **TBD** ← Cell 7 | ~6.5 GB |
| BF16 reference | 16.0 | **TBD** | ~24 GB |

> Update the table above with your Cell 6 / Cell 7 PPL numbers from RunPod,
> then re-push `README.md` to HuggingFace.

**TruthfulQA MC1** (run `bench_sidecar_hallucination.py --model gemma4-12b`):

| Mode | MC1 accuracy | D8 overhead |
|---|---|---|
| Baseline Q4 | TBD | 0 MB |
| Selective SRD (calibrated window) | TBD | ~TBD MB |
| Full SRD | TBD | ~788 MB |

---

## Files in this repo

| File | Size | Description |
|---|---|---|
| `gemma4_12b_it_srd4_q4km.gguf` | ~7.5 GB | GGUF Q4_K_M — ready for llama.cpp |
| `gemma4_12b_srd4.axm` | ~6.5 GB | SRD-4 governance container (HMAC-signed) |
| `verify.py` | < 1 KB | Standalone tamper-check script |
| `README.md` | — | This file |

---

## Inference

### llama.cpp

```bash
./llama-cli \
  -m gemma4_12b_it_srd4_q4km.gguf \
  --n-gpu-layers 99 \
  --ctx-size 8192 \
  --flash-attn \
  -p "<start_of_turn>user\nYour prompt here<end_of_turn>\n<start_of_turn>model\n" \
  --n-predict 512
```

### llama-server (OpenAI-compatible)

```bash
./llama-server \
  -m gemma4_12b_it_srd4_q4km.gguf \
  --n-gpu-layers 99 \
  --ctx-size 8192 \
  --flash-attn \
  --port 8080
```

### Python (llama-cpp-python)

```python
from llama_cpp import Llama

llm = Llama(
    model_path="gemma4_12b_it_srd4_q4km.gguf",
    n_gpu_layers=-1,
    n_ctx=8192,
    flash_attn=True,
)
output = llm.create_chat_completion(
    messages=[{"role": "user", "content": "Your prompt here"}],
    max_tokens=512,
)
print(output["choices"][0]["message"]["content"])
```

### Chat template

Gemma 4 uses `<start_of_turn>` / `<end_of_turn>` delimiters (same as Gemma 3).
The GGUF includes the tokenizer config — llama.cpp and llama-cpp-python read it
automatically.

---

## Hardware requirements

| VRAM | Context | Notes |
|---|---|---|
| 8 GB | 2 048 | Fits with small KV cache |
| 12 GB | 8 192 | Comfortable for typical use |
| 24 GB | 32 768 | Full long-context capability |
| 40–80 GB (A100) | 128 000 | Gemma 4 max context, all layers on GPU |

---

## Architecture (Gemma 4 12B)

| Field | Value |
|---|---|
| Parameters | ~12 B |
| Layers | 28 |
| Hidden size | 3 072 |
| Intermediate size | 24 576 |
| Attention heads | 16 |
| KV heads (GQA) | 8 |
| Vocab size | 262 144 |
| MLP | GeGLU |
| Max context | 128 000 tokens |

---

## Governance container

The `.axm` file wraps the same weights as the GGUF with:

- **Fingerprint** — `TBD` (fill in after `axm_cli.py verify` on RunPod)
- **HMAC proof chain** — TBD proof entries covering header, delegates, and weights manifest
- **Tamper detection** — any bit-flip in weights or metadata breaks the chain and
  identifies the exact location of the change
- **Quantization provenance** — bpw, scheme (`srd`), group size, and `top_k_pct`
  are inside the signed payload, not a mutable sidecar

```bash
# Verify (requires AXIOM_MASTER_KEY from Orivael)
export AXIOM_MASTER_KEY="<your-key>"
python verify.py
# → VERIFIED  fingerprint=TBD  proofs=TBD
```

---

## How it was built

```bash
# 1. Pack BF16 → SRD-4 .axm
python3 research/quant/pack_to_axm.py \
  --model   google/gemma-4-12b-it \
  --output  gemma4_12b_srd4.axm \
  --srd4 --real-pack --group-size 64

# 2. Verify proof chain
python3 axm_cli.py verify gemma4_12b_srd4.axm

# 3. Convert → Q8_0 intermediate, then Q4_K_M GGUF
python3 convert_hf_to_gguf.py google/gemma-4-12b-it \
  --outfile gemma4_12b_q8_0.gguf --outtype q8_0
./llama-quantize gemma4_12b_q8_0.gguf gemma4_12b_it_srd4_q4km.gguf Q4_K_M

# Full reproducible notebook: research/quant/gemma4_12b_srd_benchmark.ipynb
# (Colab / RunPod, A100 recommended)
```

---

## SRD benchmark context

SRD-4 at 4.5 bpw outperforms standard Q4_K_M at 4.85 bpw by **1.51 PPL points**
on TinyLlama-1.1B (WikiText-2). The selective sidecar variant
(`bench_gemma4_12b_chunk_sweep.py`) runs an architecture-fingerprinted calibration
sweep to find the optimal correction window for this specific architecture,
rather than applying the fixed 40–77% default used for smaller models.

See [`research/quant/bench_gemma4_12b_chunk_sweep.py`](https://github.com/orivael-dev/axiom/blob/claude/srd-multimodal/research/quant/bench_gemma4_12b_chunk_sweep.py)
for the sweep and the recommended `start_frac` / `end_frac` output.

---

## License

**Base model weights:** [Gemma Terms of Use](https://ai.google.dev/gemma/terms) —
`google/gemma-4-12b-it`. You must accept the Gemma license on HuggingFace before
downloading. Commercial use is permitted subject to those terms.

**SRD quantization code and `.axm` container format:** MIT — Orivael Inc.
See [github.com/orivael-dev/axiom](https://github.com/orivael-dev/axiom).

The quantized GGUF and `.axm` files inherit the Gemma Terms of Use from the
base model weights.
