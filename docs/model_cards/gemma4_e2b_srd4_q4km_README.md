---
license: gemma
base_model: google/gemma-4-E2B-it
tags:
  - axiom
  - srd
  - gguf
  - quantized
  - moe
  - gemma
  - multimodal
  - orivael
language:
  - en
pipeline_tag: image-text-to-text
---

# Gemma 4 E2B-it — SRD-4 Q4_K_M GGUF

**Orivael SRD-4 quantization** of `google/gemma-4-E2B-it`, packed into a
HMAC-signed `.axm` container and extracted to GGUF Q4_K_M for llama.cpp /
LM Studio / PocketPal.

> **Patent pending — Orivael Inc.**  
> The SRD container format and signing protocol are the subject of pending
> patent claims (ORVL-023, ORVL-024).

---

## What is SRD?

**Stochastic Residual Dithering (SRD)** is a post-training quantization
technique developed by Orivael Inc. It applies calibrated noise dithering
to residual quantization error, recovering perplexity lost in aggressive
bit-width reduction — with no re-training or fine-tuning required.

Every weight shard is HMAC-SHA256 signed inside an `.axm` container,
providing cryptographic provenance: the fingerprint is a public commitment
to exactly these weights.

---

## Architecture — Mixture of Experts

Gemma 4 E2B is a **Mixture-of-Experts (MoE)** model:

| Property | Value |
|---|---|
| Total parameters | ~5B |
| Active parameters per token | ~2B (25%) |
| Experts | 8 total · 2 active per token |
| Hidden size | 2560 |
| Layers | 34 |
| Vocabulary | 262,144 tokens |
| Max context | 32,768 tokens |
| Attention | GQA |

SRD uses a **MoE-aware chunk split** (20 / 20 / 37 / 23 % of depth) rather
than the dense-model 40–77 % range, since expert routing layers are
distributed across the full depth.

---

## Compression

| Metric | Value |
|---|---|
| BF16 baseline | ~10.0 GB |
| SRD-4 container (.axm) | ~4.6 GB |
| **Q4_K_M GGUF (this file)** | **3.11 GB** |
| Compression vs BF16 | **69 %** |
| Bits per weight (avg) | ~4.85 bpw |
| HMAC governance | Yes — fingerprinted .axm |
| Re-training required | No |

---

## Files

| File | Size | Description |
|---|---|---|
| `gemma4_e2b_srd4_q4km.gguf` | 3.11 GB | GGUF Q4_K_M — llama.cpp / LM Studio / PocketPal |
| `gemma4_e2b_met_sidecar.json` | ~5 KB | MoE-aware MET slot map + RAM hydration policy |

---

## Usage

### llama.cpp

```bash
./llama-cli \
  -m gemma4_e2b_srd4_q4km.gguf \
  -p "<start_of_turn>user\nHello, how are you?<end_of_turn>\n<start_of_turn>model\n" \
  -n 200 --temp 0.7 -ngl 99
```

### LM Studio

Download and open `gemma4_e2b_srd4_q4km.gguf` directly in LM Studio.
Use the **Gemma Instruct** chat template.

### Python (llama-cpp-python)

```python
from llama_cpp import Llama

llm = Llama(
    model_path="gemma4_e2b_srd4_q4km.gguf",
    n_gpu_layers=-1,
    n_ctx=4096,
)
output = llm.create_chat_completion(messages=[
    {"role": "user", "content": "Hello, how are you?"}
])
print(output["choices"][0]["message"]["content"])
```

---

## Hardware Requirements

| Device | VRAM / RAM | Notes |
|---|---|---|
| RTX 3080 / 4070 (10 GB) | 10 GB VRAM | Full GPU offload (`-ngl 99`) |
| RTX 3070 (8 GB) | 8 GB VRAM | Full GPU offload |
| Apple M2/M3 (16 GB) | 16 GB unified | Full Metal offload |
| Jetson Orin Nano 8 GB | 8 GB | `-ngl 99`, fits comfortably |
| CPU only (16 GB RAM) | 16 GB | Slow but works |

---

## Axiom MET Sidecar

The included `gemma4_e2b_met_sidecar.json` provides **Memory-Efficient
Trajectory (MET)** hydration budgets for on-device inference engines that
support Axiom's intent-aware layer loading:

```json
{
  "moe": {
    "num_experts": 8,
    "experts_per_token": 2,
    "active_frac": 0.25,
    "chunk_note": "20/20/37/23 split — architecture-agnostic for MoE"
  },
  "hydration_policy": {
    "INFORM": ["early"],
    "HARM":   ["early", "factual", "reasoning", "governance"]
  }
}
```

---

## Benchmark

> **PPL benchmark in progress.**  
> WikiText-2 perplexity (stride 512, context 2048, 100 chunks) will be
> posted here once validated with a current llama.cpp build.
> An earlier run produced an invalid result due to a stale llama.cpp
> version at conversion time — that number is not published here.

| Metric | SRD Q4_K_M | Community Q4_K_M |
|---|---|---|
| bpw | ~4.85 | ~4.85 |
| Size | 3.11 GB | pending |
| WikiText-2 PPL | *pending re-run* | pending |
| HMAC governance | Yes | No |
| MoE-aware sidecar | Yes | No |

---

## License

Weights are derived from `google/gemma-4-E2B-it` and are subject to the
[Gemma Terms of Use](https://ai.google.dev/gemma/terms).  
The SRD quantization method and `.axm` container format are proprietary to
Orivael Inc. (patent pending ORVL-023, ORVL-024).

---

## Citation

```
@misc{orivael2026srd,
  title  = {Stochastic Residual Dithering (SRD): Post-Training Quantization
             with Cryptographic Provenance},
  author = {Orivael Inc.},
  year   = {2026},
  note   = {Patent pending ORVL-023, ORVL-024}
}
```
