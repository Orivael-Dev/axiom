# Mistral-7B: Colab A100 → Orin Nano End-to-End Guide

Compress Mistral-7B FP16 on a Colab A100 GPU, sign it as an `.axm` container,
extract a GGUF Q4_K_M, then run it on an Orin Nano with NVMe M.2 storage.

---

## Overview

```
[Colab A100]                          [Orin Nano]
  Mistral-7B FP16 (13.5 GB)            mistral_srd4_q4km.gguf (~4.07 GB)
    ↓  axm pack --srd4                  stored on NVMe M.2 (/mnt/nvme/)
  mistral_srd4.axm  (signed)
    ↓  axm verify
    ↓  axm extract → GGUF Q4_K_M
    ↓  cell6_download()
  <download to laptop>
    ↓  scp to Orin Nano
                                        bench_orin_mistral7b.py --nvme
```

Expected results on Orin Nano 8 GB:

| Mode | ngl | Context | tok/s |
|------|-----|---------|-------|
| A — full GPU | 32 | ~5.8K tokens | ~5–7 |
| B — partial offload | 22 | ~11K tokens | ~4–6 |
| SpectralQuant (optional) | 32 | ~39K tokens | ~5–6 |

---

## Part 1 — Colab A100 Setup

### 1.1 Runtime selection

1. Open a new Colab notebook
2. **Runtime → Change runtime type → A100 GPU** (requires Colab Pro/Pro+)
   - T4 (15 GB) also works but pack time doubles and requires `device_map=auto`
   - Standard T4 + 12.7 GB RAM is too tight and will likely OOM during pack

### 1.2 Environment variable

The `.axm` signing uses `AXIOM_MASTER_KEY`. Cell 1 generates a random session key
automatically. If you want reproducible fingerprints across sessions, set a fixed key:

```python
import os
os.environ["AXIOM_MASTER_KEY"] = "your-64-hex-char-key-here"
```

Set this **before** Cell 1 if you need a fixed key. Otherwise let Cell 1 generate one.

---

## Part 2 — Colab Cells (run in order)

The pipeline lives in `research/quant/colab_mistral_srd4_pipeline.py`.
Create 7 separate code cells and paste each block below.

### Cell 1 — GPU check + clone repo (~30 s)

```python
import subprocess, sys
subprocess.run(["git", "clone", "--depth", "1",
    "--branch", "claude/srd-prototype-benchmark-JRtv1",
    "https://github.com/orivael-dev/axiom.git", "/content/axiom"], check=True)
sys.path.insert(0, "/content/axiom")
from research.quant.colab_mistral_srd4_pipeline import *
cell1_setup()
```

**Expected output:**
```
GPU:  A100-SXM4-40GB  40.0 GB VRAM  SM 8.0
RAM:  83.5 GB system
  ✓ Memory looks sufficient for Mistral-7B pack
AXIOM_MASTER_KEY set (random, session-only)
✓ Ready.  Repo: /content/axiom
```

### Cell 2 — Pack: Mistral-7B FP16 → SRD-4 .axm (~20–30 min)

```python
cell2_pack()
```

**What happens:**
- Downloads `mistralai/Mistral-7B-Instruct-v0.3` from HuggingFace (~13.5 GB)
- Applies SRD-4 quantization: W4 base only (`top_k_pct=0`), ~4.5 bpw
- Signs every weight tensor with HMAC-SHA256 (three-tier signing)
- Outputs `mistral_srd4.axm` + `results/mistral_pack.json`

**Expected output (at end):**
```
✓ Packed in 24.3 min
  .axm size : 4.52 GB
  bpw       : 4.5
  fingerprint: a3f9...
```

> On T4: device_map=auto splits GPU + CPU RAM. Pack time ~45 min.
> On T4 + <20 GB RAM: may OOM. Upgrade to High-RAM runtime.

### Cell 3 — Verify: check every HMAC proof (~10 s)

```python
cell3_verify()
```

**Expected output:**
```json
{
  "verified": true,
  "proofs_checked": 288,
  "fingerprint": "a3f9..."
}
✓ Verified  (288 proofs)
```

This confirms the `.axm` was not tampered with before you proceed to extract.

### Cell 4a — Build llama.cpp with CUDA (~3–5 min)

```python
cell4a_build_llamacpp()
```

Auto-detects the GPU's SM architecture and builds `llama-cli` + `llama-quantize`.
Skips silently if already built from a previous run.

**Expected output:**
```
Building llama.cpp for A100-SXM4-40GB SM 8.0...
✓ llama-cli ready
```

### Cell 4b — Extract: .axm → FP16 → GGUF Q4_K_M (~15 min)

```python
cell4b_extract()
```

**Steps performed internally:**
1. Reconstructs FP16 weights from SRD-4 W4 base
2. Saves temp HuggingFace checkpoint
3. `convert_hf_to_gguf.py` → F16 GGUF
4. `llama-quantize` → Q4_K_M GGUF (~4.07 GB)

**Expected output:**
```
✓ Extracted in 14.2 min
  GGUF size   : 4.07 GB
  quant type  : Q4_K_M
  fingerprint : a3f9...
```

### Cell 5 — Quick generation test on GPU (~30 s)  *(optional)*

```python
cell5_smoke_test()
```

Validates the GGUF produces coherent output before download. **Core pipeline ends at
Cell 4b** — skip this cell for production runs where output quality is verified
separately. To skip programmatically: `os.environ["SKIP_SMOKE_TEST"] = "1"`.

Expected: ~50–80 tok/s on A100.

### Cell 6 — Download files

```python
cell6_download()
```

Downloads four files to your browser:

| File | Size | Description |
|------|------|-------------|
| `mistral_srd4.axm` | ~4.52 GB | Signed weight container |
| `mistral_srd4_q4km.gguf` | ~4.07 GB | Ready-to-run GGUF |
| `mistral_pack.json` | ~5 KB | Pack stats + fingerprint |
| `mistral_extract.json` | ~2 KB | Extract stats |

> Total download: ~8.6 GB. Use a fast connection or only download the GGUF
> if you don't need the signed `.axm` for provenance verification.

---

## Part 2b — Running on RunPod (recommended for business use)

For repeated or automated compression runs, RunPod is more reliable than Colab:
no 12-hour timeout, persistent storage volumes, SSH access, and ~$3/run on an A100.

### Setup (one time)

1. Create a RunPod pod: **A100 SXM 40 GB**, attach a **50 GB network volume**
   at `/workspace`.
2. SSH in and install deps:

```bash
pip install transformers accelerate psutil torch --index-url https://download.pytorch.org/whl/cu121
git clone --depth 1 \
    --branch claude/srd-prototype-benchmark-JRtv1 \
    https://github.com/orivael-dev/axiom.git /workspace/axiom
pip install -r /workspace/axiom/research/quant/requirements.txt
```

### Run the pipeline

```bash
cd /workspace/axiom

# Pack your model (HF ID or local path)
python3 research/quant/run_srd4_local.py \
    --model mistralai/Mistral-7B-Instruct-v0.3 \
    --output-dir /workspace/srd_output \
    --llamacpp /workspace/llama.cpp \
    --quant Q4_K_M

# Or with your own fine-tuned model
python3 research/quant/run_srd4_local.py \
    --model /workspace/my_finetuned_model \
    --output-dir /workspace/srd_output \
    --llamacpp /workspace/llama.cpp
```

Output files land in `--output-dir`:

| File | Description |
|------|-------------|
| `model_srd4.axm` | Signed .axm container |
| `model_srd4_q4km.gguf` | GGUF Q4_K_M for llama.cpp |
| `pack_stats.json` | Timing, bpw, fingerprint |
| `extract_stats.json` | GGUF size, verification |

Add `--smoke-test` to run a 64-token generation check at the end.
Add `--bench` to run the KV simulation benchmark after extraction.
Use `--skip-extract` to stop after `.axm` (if you only need the signed container).

### Cost estimate

| GPU | Pack time | Extract time | Total | Cost |
|-----|-----------|-------------|-------|------|
| A100 40 GB | ~22 min | ~15 min | ~40 min | ~$1.10 |
| A10G 24 GB | ~35 min | ~18 min | ~55 min | ~$0.55 |

---

## Part 3 — Transfer to Orin Nano

### Option A — Direct download on Orin Nano (fastest)

SSH into the Orin Nano and download the pre-built GGUF directly from HuggingFace:

```bash
# On Orin Nano
pip install huggingface_hub
huggingface-cli download bartowski/Mistral-7B-Instruct-v0.3-GGUF \
    Mistral-7B-Instruct-v0.3-Q4_K_M.gguf \
    --local-dir /mnt/nvme/models/
```

This skips the Colab download and transfers the same Q4_K_M file (~4.07 GB).
Use this if you don't need the Axiom-signed `.axm` provenance on the Nano.

### Option B — scp from your laptop (after Colab download)

```bash
# On your laptop, after downloading from Colab
scp ~/Downloads/mistral_srd4_q4km.gguf orin:/mnt/nvme/models/

# If hostname doesn't resolve, use the IP directly
scp ~/Downloads/mistral_srd4_q4km.gguf user@192.168.x.x:/mnt/nvme/models/
```

### Verify NVMe mount on Orin Nano

```bash
# Confirm NVMe is mounted and check available space
lsblk -d -o name,rota,tran,size
# Look for: nvme0n1  0  nvme  <size>

df -h /mnt/nvme/
# Should show ~250 GB total for a 250 GB M.2 drive

# If not mounted yet:
sudo mkdir -p /mnt/nvme
sudo mount /dev/nvme0n1p1 /mnt/nvme   # adjust partition as needed
# Add to /etc/fstab for persistence
```

**Expected lsblk output:**
```
NAME     ROTA TRAN  SIZE
mmcblk0     1       29.1G   ← microSD (slow, ~90 MB/s)
nvme0n1     0 nvme  232.9G  ← your NVMe M.2 (fast, ~3 GB/s)
```

---

## Part 4 — Build llama.cpp on Orin Nano

Orin Nano uses SM 8.7 (Ampere). Build once, reuse for all future benchmarks.

```bash
# Install build dependencies (first time only)
sudo apt-get install -y cmake build-essential

# Clone llama.cpp
git clone --depth 1 https://github.com/ggerganov/llama.cpp.git ~/llama.cpp

# Configure for SM 8.7 (Orin Nano / AGX Orin)
cmake -B ~/llama.cpp/build -S ~/llama.cpp \
    -DGGML_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES=87 \
    -DCMAKE_BUILD_TYPE=Release

# Build (use all 6 cores on Orin Nano)
cmake --build ~/llama.cpp/build -j6 -t llama-cli llama-quantize

# Verify
~/llama.cpp/build/bin/llama-cli --version
```

Build time: ~10–15 min on Orin Nano.

---

## Part 4b — OOM Prevention on Orin Nano

The Orin Nano has **5.5 GB usable unified memory**. With the 4.07 GB model loaded,
~1.4 GB remains. Memory is consumed by:

| Consumer | Size |
|----------|------|
| Model weights (Q4_K_M) | 4.07 GB |
| KV cache (f16, 8K ctx) | ~1.0 GB |
| Activation tensors | ~150–200 MB |
| System / driver overhead | ~150 MB |
| **Total at 8K ctx** | **~5.5 GB** ← right at the limit |

Without any protection, a long prompt or a second process eating RAM will OOM-kill
llama.cpp mid-generation. Four layers prevent this.

---

### Layer 1 — KV cache quantization (biggest win, zero setup)

llama.cpp can quantize the K and V cache tensors without retraining. This is the
single highest-leverage change — halves or quarters KV memory with minimal quality
impact at normal context lengths:

```bash
# Q8_0 — halves KV memory (~64 KB/token), nearly lossless up to ~20K ctx
~/llama.cpp/build/bin/llama-cli \
    -m /mnt/nvme/models/mistral_srd4_q4km.gguf \
    -ngl 32 -c 16000 \
    -ctk q8_0 -ctv q8_0 \
    --flash-attn \
    --prompt "Your prompt here"

# Q4_0 — quarters KV memory (~32 KB/token), slight quality drop on very long ctx
~/llama.cpp/build/bin/llama-cli \
    -m /mnt/nvme/models/mistral_srd4_q4km.gguf \
    -ngl 32 -c 32000 \
    -ctk q4_0 -ctv q4_0 \
    --flash-attn \
    --prompt "Your prompt here"
```

**Context budget with 1.4 GB headroom:**

| KV type | Per token | Max safe ctx | Quality impact |
|---------|-----------|-------------|----------------|
| `f16` (default) | 128 KB | ~8K | none |
| `q8_0` | 64 KB | ~16K | negligible |
| `q4_0` | 32 KB | ~32K | minor on long ctx |
| `q4_K_M` | ~28 KB | ~37K | minor on long ctx |

---

### Layer 2 — Flash attention (free memory, no quality cost)

`--flash-attn` computes attention in blocks instead of materialising the full
N×N attention matrix. Saves ~200–400 MB of peak activation memory — always
enable it:

```bash
# Add to every llama-cli call
--flash-attn
```

---

### Layer 3 — NVMe swap (overflow safety net)

If DRAM fills unexpectedly, NVMe swap (3 GB/s) prevents a hard OOM kill.
**Never use microSD for swap** (90 MB/s — a 4 GB page-out takes 45 s).

```bash
# One-time setup on Orin Nano
sudo fallocate -l 8G /mnt/nvme/swapfile
sudo chmod 600 /mnt/nvme/swapfile
sudo mkswap /mnt/nvme/swapfile
sudo swapon /mnt/nvme/swapfile

# Persist across reboots
echo '/mnt/nvme/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

With 8 GB NVMe swap, the effective memory ceiling is 13.5 GB. Generation will slow
if it pages, but it won't crash.

---

### Layer 4 — Auto-select safe launch script

`research/quant/orin_safe_launch.sh` reads `/proc/meminfo` and picks the highest
config that fits in available RAM without manual tuning:

```bash
# Basic usage
bash ~/axiom/research/quant/orin_safe_launch.sh \
    /mnt/nvme/models/mistral_srd4_q4km.gguf \
    ~/llama.cpp/build/bin/llama-cli \
    "Explain edge AI in one paragraph:"
```

**Example output:**
```
=== Orin Safe Launch ===
  Free RAM:    5340 MB
  Model:       4200 MB
  Headroom:    1140 MB
  Config:      A-q8  (ngl=32  q8-KV  ctx=16K)
```

The script tries configs from most capable to most conservative and always
enables `--flash-attn`. If headroom is below 200 MB it warns and drops to a
minimal 2K context config rather than crashing.

**Config tiers:**

| Headroom | ngl | KV type | ctx | Label |
|----------|-----|---------|-----|-------|
| ≥ 1400 MB | 32 | f16 | 8K | A-full |
| ≥ 900 MB | 32 | q8_0 | 16K | A-q8 |
| ≥ 500 MB | 32 | q8_0 | 8K | A-q8-safe |
| ≥ 200 MB | 22 | q4_0 | 4K | B-q4 |
| < 200 MB | 16 | q4_0 | 2K | minimal |

---

## Part 5 — Benchmark on Orin Nano

### 5.1 Clone the axiom repo (if not already present)

```bash
git clone --depth 1 https://github.com/orivael-dev/axiom.git ~/axiom
cd ~/axiom
```

### 5.2 Run the full benchmark

```bash
python3 -m research.quant.bench_orin_mistral7b \
    --llamacpp ~/llama.cpp/build/bin \
    --gguf /mnt/nvme/models/mistral_srd4_q4km.gguf \
    --nvme \
    --stats-json ~/axiom/results/orin_mistral_bench.json
```

The `--nvme` flag enables mmap (safe on NVMe, faster load) and measures
storage bandwidth from llama.cpp's `load_time` log line.

### 5.3 What to expect

The benchmark runs four configurations:

| Label | ngl | Context | What it tests |
|-------|-----|---------|---------------|
| A: full GPU 1K | 32 | 1,024 | Baseline — all layers on GPU |
| A: full GPU 4K | 32 | 4,096 | Near-limit context for 5.5 GB unified memory |
| B: partial 4K | 22 | 4,096 | CPU offload — 10 layers on CPU |
| B: partial 8K | 22 | 8,192 | 2× context, near-zero slowdown on unified memory |

**Example output:**

```
==================================================================
Mistral-7B on Orin Nano — modes that 'shouldn't work'
==================================================================
Model:   /mnt/nvme/models/mistral_srd4_q4km.gguf
Storage: NVMe M.2 (mmap enabled)
Size:    4.07 GB
Theoretical KV bytes/token: 131,072 B (128 KB)

  Running A: full GPU      ctx=1,024  ngl=32  (predicted max 5,857)
    tok/s: 6.21 tok/s  KV: 128.0 MiB  load=1.34s (3.03 GB/s)
    simulation error: 0.0%  (✓ PASS)

  Running A: full GPU 4K   ctx=4,096  ngl=32  (predicted max 5,857)
    tok/s: 5.98 tok/s  KV: 512.0 MiB  load=1.31s (3.10 GB/s)
    simulation error: 0.0%  (✓ PASS)

  Running B: partial (ngl=22) 4K  ctx=4,096  ngl=22  (predicted max 11,066)
    tok/s: 5.54 tok/s  KV: 512.0 MiB  load=1.38s (2.95 GB/s)
    simulation error: 0.0%  (✓ PASS)

  Running B: partial (ngl=22) 8K  ctx=8,192  ngl=22  (predicted max 11,066)
    tok/s: 5.41 tok/s  KV: 1024.0 MiB  load=1.35s (3.01 GB/s)
    simulation error: 0.0%  (✓ PASS)

SUMMARY
...
  Unified memory verdict:
    Full GPU (ngl=32) avg:   6.10 tok/s
    Partial  (ngl=22) avg:   5.48 tok/s
    Slowdown from offload:   10.2%
    ✓ Unified memory: CPU offload costs < 20% — nearly free context doubling

  NVMe storage bandwidth (implied by load_time):
    Measured:   3.02 GB/s
    microSD:    ~0.09 GB/s  (34× faster)
    eMMC:       ~0.30 GB/s  (10× faster)
    ✓ NVMe confirmed — cold load is no longer the bottleneck
```

### 5.4 Interpreting the results

**Unified memory verdict:** On a discrete 6 GB GPU, offloading 10 layers to CPU
would cost 50–80% tok/s due to PCIe bandwidth. On Orin Nano's unified memory there
is no physical data copy — the "CPU" and "GPU" views share the same DRAM pool.
The <20% slowdown shows you can double context almost for free.

**NVMe verdict:** ~3 GB/s vs ~90 MB/s on microSD means a 4 GB model loads in
~1.3 s instead of ~45 s. This makes Orin Nano viable for cold-start deployments.

**Simulation accuracy:** The theoretical formula
`n_layers × 2 × n_kv_heads × head_dim × 2 bytes = 131,072 B/token` should match
llama.cpp's `kv self size` log line within 5% (`✓ PASS`).

---

## Part 6 — Optional: SpectralQuant KV Compression

SpectralQuant compresses KV cache tensors 6.62× while matching or slightly
exceeding baseline decode speed on Mistral-7B.

```bash
pip install spectralquant
```

Context gain on Orin Nano (5.5 GB unified memory, Q4_K_M weights):

| Without SpectralQuant | With SpectralQuant (6.62×) |
|-----------------------|---------------------------|
| Mode A: ~5.8K tokens  | Mode A: ~38K tokens |
| Mode B: ~11K tokens   | Mode B: ~73K tokens |

### Using SpectralQuant in Python inference

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from spectralquant import compress_cache  # pip install spectralquant

model = AutoModelForCausalLM.from_pretrained(
    "mistralai/Mistral-7B-Instruct-v0.3",
    torch_dtype="float16",
)
tokenizer = AutoTokenizer.from_pretrained("mistralai/Mistral-7B-Instruct-v0.3")

# Use the sq_validated preset (6.55× compression, fully validated on Mistral-7B)
cache = compress_cache(preset="sq_validated")  # or "sq_edge" for max compression

inputs = tokenizer("Your long prompt here...", return_tensors="pt")
outputs = model.generate(**inputs, past_key_values=cache, max_new_tokens=200)
```

### SpectralQuant KV block isolation

The `KVBlockKey.kv_compression` field in the Axiom framework isolates cached
states by compression mode. A block cached without SpectralQuant will not be
mistakenly reused for a SpectralQuant session:

```python
from axiom_event_token import KVBlockKey

key_none = KVBlockKey.from_token_ids(token_ids, model_id="mistral-7b",
    axm_fingerprint="...", kv_compression="none")
key_sq   = KVBlockKey.from_token_ids(token_ids, model_id="mistral-7b",
    axm_fingerprint="...", kv_compression="sq_validated")

# key_none.hex() != key_sq.hex() — different block_ids, no cache collision
```

---

## Part 7 — Quick Reference

### File locations (Orin Nano)

| Item | Path |
|------|------|
| Model GGUF | `/mnt/nvme/models/mistral_srd4_q4km.gguf` |
| llama-cli | `~/llama.cpp/build/bin/llama-cli` |
| Benchmark script | `~/axiom/research/quant/bench_orin_mistral7b.py` |
| Benchmark results | `~/axiom/results/orin_mistral_bench.json` |

### Key commands

```bash
# Full benchmark with NVMe
python3 -m research.quant.bench_orin_mistral7b \
    --llamacpp ~/llama.cpp/build/bin \
    --gguf /mnt/nvme/models/mistral_srd4_q4km.gguf \
    --nvme

# Skip 8K test (if memory is very tight)
python3 -m research.quant.bench_orin_mistral7b \
    --llamacpp ~/llama.cpp/build/bin \
    --gguf /mnt/nvme/models/mistral_srd4_q4km.gguf \
    --nvme --skip-8k

# Quick test — full GPU, 512 token context
~/llama.cpp/build/bin/llama-cli \
    -m /mnt/nvme/models/mistral_srd4_q4km.gguf \
    --ngl 99 --ctx-size 512 --n-predict 64 \
    --prompt "Explain edge AI in one paragraph:"

# Partial offload — double context, minimal speed penalty
~/llama.cpp/build/bin/llama-cli \
    -m /mnt/nvme/models/mistral_srd4_q4km.gguf \
    --ngl 22 --ctx-size 8192 --n-predict 64 \
    --prompt "Explain edge AI in one paragraph:"
```

### Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| OOM during pack (Colab) | T4 + insufficient RAM | Use A100 or High-RAM T4 |
| GGUF loads in 45+ seconds | Model on microSD | Move to NVMe, use `--nvme` flag |
| GGUF loads slowly on NVMe | mmap disabled | Make sure you're passing `--nvme` flag |
| tok/s near 0 | All layers on CPU (`--ngl 0`) | Increase `--ngl` to at least 16 |
| Verify failure on `.axm` | `AXIOM_MASTER_KEY` changed | Keep same key between pack/verify |
| cmake CUDA error | Wrong SM arch | Orin Nano is always SM 8.7 |
| `llama-cli: not found` | Wrong bin path | Check `~/llama.cpp/build/bin/` |
