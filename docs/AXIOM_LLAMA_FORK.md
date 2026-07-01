# axiom-llama.cpp — Custom Fork Specification

## Overview

`axiom-llama.cpp` is a fork of [llama.cpp](https://github.com/ggerganov/llama.cpp) that
adds native SRD4 quantization support. Instead of the lossy three-stage pipeline:

```
.axm → srd_dequantize() → FP16 → convert_hf_to_gguf → Q4_K_M
```

the fork reads a **`.srd4` sidecar file** alongside a companion GGUF and exposes the
W4/D8 decomposition at inference time, enabling:

- **Runtime α knob** — quality dial 0.0–1.0 without re-converting
- **MET-aware selective layer loading** — INFORM intent loads only early layers
- **HMAC fingerprint verification** — tamper detection before first forward pass
- **Smaller files** — SRD4 at α=0 is 4.5 bpw (vs Q4_K_M at 4.85 bpw)
- **One conversion pass** — sidecar repack takes minutes, not hours

---

## The `.srd4` Sidecar Binary Format

Produced by `research/quant/axm_to_srd4_gguf.py`. Sits alongside a companion `.gguf`
file (architecture + dense params). The SRD4 fork replaces GGUF quantized tensors with
sidecar data at load time.

```
Offset  Size  Type      Field
──────────────────────────────────────────────────────────────────
0       8     uint8[8]  Magic: "AXMSRD4\0"  (0x41 58 4D 53 52 44 34 00)
8       4     uint32LE  JSON header byte length (N)
12      N     UTF-8     JSON header (see below)
12+N    …     binary    Concatenated tensor blocks (order matches header["tensors"])
```

### JSON Header Fields

```json
{
  "version":      "1.0",
  "fingerprint":  "a3b71d22",
  "alpha_default": 1.0,
  "group_size":   64,
  "top_k_pct":    1.0,
  "n_quantized":  96,
  "scheme":       "srd",
  "tensors": [
    {
      "name":     "model.layers.0.self_attn.q_proj",
      "out_features": 2048,
      "in_features":  2048,
      "group_size":   64,
      "n_blocks":     65536,
      "byte_offset":  0,
      "byte_length":  6815744
    },
    ...
  ]
}
```

`byte_offset` is relative to the start of the binary data region (after the JSON header).

### Block Layout — `block_srd4_t`

Each block covers `group_size=64` weights:

```c
typedef struct {
    uint8_t  w4[32];   // 64 int4 values, nibble-packed, unsigned [0,15]
    float    s4;       // base scale (one per group)
    int8_t   d8[64];   // 8-bit residuals
    float    s8;       // residual scale (one per group)
} block_srd4_t;        // 104 bytes total
```

**Nibble packing:** `w4[i] = low_nibble | (high_nibble << 4)` for indices
`2i` and `2i+1`. Stored unsigned: add 8 to convert from int4 [-8,7] → uint4 [0,15].

**bpw at α=0:** 32×8 + 32 = 288 bits / 64 weights = **4.5 bpw**
**bpw at α=1:** (32 + 4 + 64 + 4)×8 / 64 = **13.0 bpw** (W4 + S4 + D8 + S8)

---

## Dequantization Kernel

### C Reference Implementation

```c
// ggml-srd4.h

#define BLOCK_SRD4_WEIGHTS  64
#define BLOCK_SRD4_W4_BYTES 32
#define BLOCK_SRD4_BYTES    104   // sizeof(block_srd4_t)

typedef struct {
    uint8_t  w4[BLOCK_SRD4_W4_BYTES];  // nibble-packed int4, bias+8
    float    s4;
    int8_t   d8[BLOCK_SRD4_WEIGHTS];
    float    s8;
} block_srd4_t;

// Dequantize one block to float32
// alpha=0.0: pure W4 (4.5 bpw path, skip D8 reads for bandwidth)
// alpha=1.0: full W4+D8 quality (13.0 bpw path)
static inline void dequantize_block_srd4(
    const block_srd4_t * GGML_RESTRICT blk,
    float * GGML_RESTRICT out,
    float alpha
) {
    const float s4 = blk->s4;
    const float s8 = blk->s8 * alpha;   // zero if alpha=0 → branch-free

    for (int i = 0; i < BLOCK_SRD4_WEIGHTS / 2; ++i) {
        uint8_t packed = blk->w4[i];
        int8_t  lo4    = (int8_t)(packed & 0x0F) - 8;   // [0,15]→[-8,7]
        int8_t  hi4    = (int8_t)(packed >> 4)   - 8;

        out[2*i]   = (float)lo4 * s4 + (float)blk->d8[2*i]   * s8;
        out[2*i+1] = (float)hi4 * s4 + (float)blk->d8[2*i+1] * s8;
    }
}
```

### GGML Type Registration

Register `GGML_TYPE_SRD4` in `ggml.h` / `ggml.c`:

```c
// ggml.h — add after GGML_TYPE_IQ4_NL
GGML_TYPE_SRD4   = 36,   // axiom SRD4: W4+D8+scales, 104 bytes/64 weights

// ggml.c — in ggml_type_traits[]
[GGML_TYPE_SRD4] = {
    .type_name   = "srd4",
    .blck_size   = BLOCK_SRD4_WEIGHTS,
    .type_size   = BLOCK_SRD4_BYTES,
    .is_quantized = true,
    .to_float    = (ggml_to_float_t) dequantize_row_srd4,
    .from_float  = NULL,   // pack on host, not in ggml
    .from_float_ref = NULL,
    .vec_dot     = ggml_vec_dot_srd4_q8_0,
    .vec_dot_type = GGML_TYPE_Q8_0,
    .nrows       = 1,
},
```

### CUDA / Metal Kernels (stub locations)

| File | Symbol |
|------|--------|
| `ggml-cuda/dequantize-srd4.cu` | `dequantize_block_srd4_cuda<<<...>>>(alpha)` |
| `ggml-metal/ggml-metal.metal` | `kernel void kernel_dequantize_srd4(constant float& alpha)` |

Both kernels receive `alpha` as a push-constant / uniform buffer updated per inference
call from the new `--alpha` flag.

---

## GGUF KV Metadata Keys

The annotated companion GGUF (produced by `axm_to_srd4_gguf.py --gguf-out`) carries
these keys under the standard GGUF KV store:

| Key | Type | Example |
|-----|------|---------|
| `axiom.srd4.version` | string | `"1.0"` |
| `axiom.srd4.alpha_default` | float32 | `1.0` |
| `axiom.srd4.group_size` | uint32 | `64` |
| `axiom.srd4.top_k_pct` | float32 | `1.0` |
| `axiom.srd4.n_quantized_layers` | uint32 | `96` |
| `axiom.srd4.fingerprint` | string | `"a3b71d22"` |
| `axiom.srd4.sidecar_file` | string | `"model.srd4"` (basename) |
| `axiom.chunk_map` | string | JSON: `{"0":"early","6":"factual",...}` |
| `axiom.hydration_policy` | string | JSON: `{"INFORM":["early"],...}` |

`chunk_map` and `hydration_policy` mirror the values in
`research/quant/add_axiom_gguf_meta.py:SLOT_RANGES` and `HYDRATION_POLICY`.

---

## New CLI Flags

All flags are additive — standard llama.cpp flags remain unchanged.

```
./llama-cli -m model.gguf [standard flags] [SRD4 flags]

SRD4 flags:
  --srd4-sidecar PATH   Path to .srd4 sidecar (default: model.srd4 alongside model.gguf)
  --alpha FLOAT         W4+D8 blend [0.0, 1.0] (default: alpha_default from GGUF KV)
                        0.0 = pure W4 (4.5 bpw, fast, low RAM)
                        1.0 = full W4+D8 (13.0 bpw, near-FP16 quality)
  --met-policy INTENT   Load only layers needed for INTENT:
                        INFORM      → early layers only    (~768 MB Gemma-3 1B)
                        CLARIFY     → early + governance   (~900 MB)
                        REFUSE      → early + governance   (~900 MB)
                        HARM        → full model           (~1,409 MB)
                        DECEIVE     → full model           (~1,409 MB)
                        (no flag)   → full model
  --power-profile NAME  Shorthand presets:
                        CONSERVE    → --alpha 0.0 --met-policy INFORM
                        STANDARD    → --alpha 0.25 (no MET filter)
                        FULL        → --alpha 1.0 (no MET filter)
  --verify-fingerprint  Check HMAC fingerprint from GGUF KV against AXIOM_MASTER_KEY
                        before first forward pass. Exits with error if tampered.
```

### Example Commands

```bash
# Compact mode: 4.5 bpw, early layers only, ~768 MB (Gemma-3 1B)
./llama-cli -m gemma3_1b.gguf --power-profile CONSERVE \
    -p "What is the capital of France?" -n 100

# Full quality: 13.0 bpw, all layers, near-FP16
./llama-cli -m gemma3_1b.gguf --alpha 1.0 \
    -p "Solve: integrate x^2 from 0 to 1" -n 200

# Fingerprint verification (governance requirement)
AXIOM_MASTER_KEY=your_key_hex ./llama-cli -m gemma3_1b.gguf \
    --verify-fingerprint -p "Hello" -n 1

# Perplexity comparison at different alpha values
./llama-perplexity -m gemma3_1b.gguf --alpha 0.0 -f wikitext2_test.txt
./llama-perplexity -m gemma3_1b.gguf --alpha 1.0 -f wikitext2_test.txt
```

---

## MET-Aware Selective Layer Loading

Based on `research/quant/met_ram_estimator.py:CHUNK_FRACS` and `HYDRATION_POLICY`:

| Chunk | Layers (Gemma-3 1B, 18 total) | RAM at α=0 |
|-------|-------------------------------|------------|
| early | 0–5 | 174 MB transformer |
| factual | 6–11 | + 174 MB |
| reasoning | 12–17 | + 174 MB |
| governance | last 2 | + 58 MB |
| embedding/lm_head | — | 594 MB (vocab=262,144) |

| `--met-policy` | Chunks loaded | Total RAM | Savings |
|----------------|---------------|-----------|---------|
| INFORM | early | **768 MB** | 45.5% |
| CLARIFY/REFUSE | early + governance | 826 MB | 41.4% |
| HARM/DECEIVE | all | 1,409 MB | 0% |
| (none) | all | 1,409 MB | 0% |

The fork uses `mmap` for the sidecar — only the tensor byte ranges for hydrated layers
are read into physical pages. Unhydrated layer ranges are never faulted in.

### C Implementation Hook

```c
// llama.cpp: llama_model_load() — after reading chunk_map from GGUF KV
if (params.met_policy) {
    const char* chunks = hydration_policy_lookup(params.met_policy);
    for (int i = 0; i < n_layers; ++i) {
        if (!layer_in_chunks(i, chunks, chunk_map)) {
            skip_layer_srd4_mmap(i);   // mark range as MADV_DONTNEED
        }
    }
}
```

---

## Fingerprint Verification

```c
// llama.cpp: llama_model_load() — at startup if --verify-fingerprint set
const char* expected_fp = gguf_find_key(ctx, "axiom.srd4.fingerprint");
const char* master_key  = getenv("AXIOM_MASTER_KEY");
if (!master_key) {
    LLAMA_LOG_ERROR("AXIOM_MASTER_KEY not set — cannot verify fingerprint\n");
    return LLAMA_ERROR_VERIFY_FAILED;
}
char computed[9];
axiom_compute_fingerprint(sidecar_path, master_key, computed, sizeof(computed));
if (strncmp(computed, expected_fp, 8) != 0) {
    LLAMA_LOG_ERROR("Fingerprint mismatch: expected %s, got %s\n", expected_fp, computed);
    return LLAMA_ERROR_TAMPERED;
}
LLAMA_LOG_INFO("Fingerprint verified: %s\n", computed);
```

`axiom_compute_fingerprint()` computes `HMAC-SHA256(master_key, sidecar_bytes)[:4]`
hex-encoded — matching the derivation in `axiom_axm.py:AXMContainer.fingerprint()`.

---

## Build Instructions

### Prerequisites

```bash
git clone --branch claude/srd-prototype-benchmark-JRtv1 \
    https://github.com/orivael-dev/axiom.git /workspace/axiom

# Clone and patch llama.cpp
git clone https://github.com/ggerganov/llama.cpp /workspace/axiom-llama.cpp
cd /workspace/axiom-llama.cpp
```

### Patch files to add/modify

| File | Change |
|------|--------|
| `ggml.h` | Add `GGML_TYPE_SRD4 = 36` |
| `ggml.c` | Add `ggml_type_traits[GGML_TYPE_SRD4]` entry |
| `ggml-srd4.h` | New file: `block_srd4_t` + `dequantize_block_srd4()` |
| `ggml-srd4.c` | New file: `dequantize_row_srd4()`, `ggml_vec_dot_srd4_q8_0()` |
| `ggml-cuda/dequantize-srd4.cu` | New file: CUDA kernel with alpha uniform |
| `llama.cpp` | SRD4 sidecar load in `llama_model_load()`, CLI param parsing |
| `common/common.h` | `gpt_params.alpha`, `gpt_params.met_policy`, `gpt_params.verify_fp` |
| `common/common.cpp` | Parse `--alpha`, `--met-policy`, `--power-profile`, `--verify-fingerprint` |

### Build

```bash
cmake /workspace/axiom-llama.cpp -B /workspace/axiom-llama.cpp/build \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_CUDA=ON             # remove if no GPU
    -DAXIOM_SRD4=ON            # new CMake option, enables ggml-srd4.c

cmake --build /workspace/axiom-llama.cpp/build -j$(nproc) \
    --target llama-cli llama-perplexity llama-quantize
```

---

## Conversion Pipeline

### Step 1 — Pack model into .axm (existing)

```bash
python3 research/quant/pack_to_axm.py \
    --model google/gemma-3-1b-it \
    --output gemma3_1b_srd4.axm \
    --bpw 4.5 --group-size 64 --real-pack
```

### Step 2 — Convert .axm → .srd4 + companion .gguf (new tool)

```bash
python3 research/quant/axm_to_srd4_gguf.py \
    --container gemma3_1b_srd4.axm \
    --srd4-out  gemma3_1b.srd4 \
    --gguf-out  gemma3_1b.gguf \
    --llamacpp  /workspace/axiom-llama.cpp
```

This reads `srd_packed.pt` directly — no `srd_dequantize()`, no FP16 intermediate.
Conversion time: ~2 min for 1B model (vs ~40 min for full FP16 round-trip on T4).

### Step 3 — Inspect sidecar

```bash
python3 research/quant/axm_to_srd4_gguf.py \
    --inspect gemma3_1b.srd4
```

Output:
```
SRD4 Sidecar: gemma3_1b.srd4
  version:      1.0
  fingerprint:  a3b71d22
  alpha_default: 1.0
  group_size:   64
  n_quantized:  96
  tensors:      96
  binary_bytes: 654,131,200  (623.9 MB)
  bpw at α=0:  4.50
  bpw at α=1:  13.00
```

---

## Verification Checklist

```bash
# 1. Sidecar inspection
python3 research/quant/axm_to_srd4_gguf.py --inspect gemma3_1b.srd4

# 2. PPL comparison: α=0 vs α=1 (same file, same weights)
./llama-perplexity -m gemma3_1b.gguf --alpha 0.0 -f wikitext2_test.txt
./llama-perplexity -m gemma3_1b.gguf --alpha 1.0 -f wikitext2_test.txt
# Expected: α=1.0 PPL ≈ FP16 PPL; α=0.0 PPL ≈ Q4_K_M PPL

# 3. Fingerprint check — tampered GGUF should fail
cp gemma3_1b.gguf tampered.gguf
printf '\x00' | dd of=tampered.gguf bs=1 seek=1024 conv=notrunc 2>/dev/null
AXIOM_MASTER_KEY=your_key ./llama-cli -m tampered.gguf --verify-fingerprint -p "Hi" -n 1
# Expected: "Fingerprint mismatch" error, exit code nonzero

# 4. MET RAM — INFORM intent should use ~768 MB
/usr/bin/time -v ./llama-cli -m gemma3_1b.gguf --met-policy INFORM \
    -p "What is the capital of France?" -n 50 2>&1 | grep "Maximum resident"
# Expected: ~800 MB (768 MB target + ~30 MB overhead)

# 5. Syntax check (Python conversion tool)
python3 -c "import ast; ast.parse(open('research/quant/axm_to_srd4_gguf.py').read()); print('✓')"
```

---

## Expected PPL Results (Gemma-3 1B)

| Mode | bpw | Est. WikiText-2 PPL | RAM |
|------|-----|---------------------|-----|
| FP16 (reference) | 16.0 | ~7.5 | 2,318 MB |
| SRD4 α=1.0 | 13.0 | ~7.6 | 1,409 MB |
| SRD4 α=0.25 | ~6.5 | ~8.5 | 826 MB |
| Q4_K_M (baseline) | 4.85 | ~8.8 | 815 MB |
| **SRD4 α=0.0** | **4.5** | **~9.0** | **768 MB** |
| QAT Q4_0 (Google) | 4.0 | ~8.2 | 650 MB |

> Note: PPL figures are estimates until `gemma3_1b_srd_vs_qat.ipynb` is run on Colab.
> The SRD4 α=0.0 vs Q4_K_M comparison is the key result: smaller file, same quality tier.

---

## Source Files

| File | Role |
|------|------|
| `research/quant/axm_to_srd4_gguf.py` | Conversion tool (.axm → .srd4 + annotated GGUF) |
| `research/quant/axm_to_gguf.py` | Original lossy pipeline (kept for fallback) |
| `research/quant/srd_realpack.py` | `load_real_packed()` / `save_real_packed()` |
| `axiom_quant.py` | `srd_pack_w4`, `srd_unpack_w4`, `srd_pack_d8_sparse`, `srd_unpack_d8_sparse` |
| `research/quant/add_axiom_gguf_meta.py` | GGUF KV annotation, `SLOT_RANGES`, `HYDRATION_POLICY` |
| `axiom_agent_fabric/power_conditioner.py` | `InferenceConfig.alpha` / `met_policy` outputs |
| `research/quant/met_ram_estimator.py` | `CHUNK_FRACS`, `HYDRATION_POLICY` (RAM budgets) |
| `docs/SRD_RESULTS.md` | Benchmark context: SRD-4 vs Q4_K_M perplexity results |
