# Running `.axm` models — laptop / WSL2 troubleshooting

Field notes for running SRD real-packed `.axm` archives on a Windows laptop
with a discrete NVIDIA GPU via WSL2. Captured from a working bring-up on a
GTX 1660 Ti (Turing SM 7.5, 6 GB) — WSL2 Ubuntu, CUDA 13.3 / driver 610.47,
torch 2.6.0+cu124, 2026-06-02.

The headline result: the **GPU fp16 path runs end-to-end** on a discrete
laptop GPU — the exact path NvMap error 12 blocks on the Orin Nano's
unified memory. See `docs/SRD_ROADMAP.md` task 8 for the full numbers.

---

## Two ways to run

| | PyTorch `.axm` loader | GGUF via llama.cpp |
|---|---|---|
| Command | `axm run FILE --device cuda` | `axm extract` → `bench_llamacpp_infer` |
| Weights | dequant W4+D8 → fp16 (30 s CPU) | quantized on disk (no dequant) |
| RSS (1660 Ti) | 3959 MB | **543 MB** |
| VRAM (1660 Ti) | 2202 MB (fp16) | **884 MiB** (Q4_K_M) |
| Use it for | exact-fidelity check vs the packed weights | deployment / repeated benchmarking |

**Recommendation:** use the PyTorch loader once to confirm the archive is
intact and produces coherent output, then convert to GGUF and run everything
through llama.cpp. The GGUF path is 7.3× lighter on RAM and 2.4× on VRAM and
skips the 30 s CPU dequant on every launch.

```bash
# 1. verify + run once (PyTorch, exact packed weights)
python3 axm_cli.py run tinyllama_srd_7bpw_REAL.axm --device cuda \
    --tokens 80 --n-runs 3

# 2. convert to GGUF + benchmark (deployment path)
python3 axm_cli.py extract tinyllama_srd_7bpw_REAL.axm \
    --gguf-out tinyllama_q4km.gguf --llamacpp ~/llama.cpp --device cpu
python3 -m research.quant.bench_llamacpp_infer \
    --gguf tinyllama_q4km.gguf \
    --llama-cli ~/llama.cpp/build/bin/llama-cli \
    --ngl 99 --n-runs 3
```

> The repo loader (`research/quant/load_from_axm.py` →
> `srd_realpack.load_real_packed()`) already does the full dequant pipeline.
> `axiom_axm.py` is the container/verify layer only — it locates and
> integrity-checks `weights/`, it does not itself reconstruct weights. You do
> not need to hand-roll a ZIP reader; `axm run` wires both halves together.

---

## Pitfalls (and fixes)

### Environment / WSL2

- **`nvidia-smi: not found` but the file exists** — you're in the
  `docker-desktop` WSL distro, a minimal LinuxKit VM with no glibc loader.
  An existing glibc binary reports "not found" when its ELF interpreter is
  missing. **Fix:** install and use a real Ubuntu distro
  (`wsl --install -d Ubuntu` from PowerShell), and always open *Ubuntu*, not
  Docker Desktop. Check with `cat /etc/os-release`.

- **GPU passthrough lives at `/usr/lib/wsl/lib`** — driver-provided
  (`libcuda.so`, `nvidia-smi`). From a real Ubuntu distro it's auto-mounted;
  `nvidia-smi` works without extra setup. Do **not** install a Linux NVIDIA
  driver inside WSL — CUDA support ships in the *Windows* driver.

- **CUDA runtime / driver mismatch** — the cu-wheel must match the driver's
  CUDA runtime (top-right of `nvidia-smi`). CUDA 13.3 runtime needs Windows
  driver ≥ 610. Update the NVIDIA Windows driver if torch reports
  `cuda.is_available() == False` while `nvidia-smi` works.

- **9p filesystem slowness** — `mmap` of large `.pt` files over the Windows
  drive mount (`/mnt/c`, `I:`) takes minutes. **Copy the `.axm` to a native
  Linux path (`/tmp` or `~`) first.**

- **GPU VRAM zombie processes** — failed runs leave CUDA contexts pinning
  VRAM. `wsl --shutdown` (from PowerShell) clears them.

### CUDA / Turing build

- **`cospi`/`sinpi` exception-spec conflict** — older CUDA + new glibc
  (≥ 2.43) fails to compile/run on Turing. **Fix:** CUDA ≥ 13.3.

- **llama.cpp CUDA arch** — detect the SM version, then pass it to cmake:
  ```bash
  python3 -m research.quant.bench_llamacpp_infer --detect-arch
  # SM 7.5 → 75 (GTX 16xx / RTX 20), 8.9 → 89 (RTX 40), 10.0 → 100 (RTX 50)
  cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75
  cmake --build build --config Release -j$(nproc)
  ```

### Hand-rolled loaders (if you bypass `axm run`)

These bite only if you read the ZIP and load weights manually. The repo's
`load_real_packed` already handles them — listed here for completeness.

- **`load_state_dict(..., assign=True)` is mandatory** when loading into a
  freshly instantiated (non-meta or meta) model. Without it the assignment
  is a silent no-op and the model runs on random parameters.
- **Strip `tokenizer_class` from config** if instantiating
  `PreTrainedTokenizerFast(tokenizer_file=...)` directly. Using
  `AutoTokenizer.from_pretrained(weights_dir)` (what the repo does) avoids it.
- **Filter `token_type_ids`** before `model.generate()` — Llama rejects the
  key. Only appears if you call `PreTrainedTokenizerFast` directly;
  `AutoTokenizer` for Llama doesn't emit it.

---

## `.axm` format reference (for inspection)

`.axm` is a standard ZIP (`50 4B 03 04` / `PK` magic). Real-packed layout:

| Entry | Contents |
|---|---|
| `header.json` | AXM metadata + HMAC signature |
| `weights/srd_packed.pt` | per-layer packed dict (154 linear layers for TinyLlama) |
| `weights/srd_dense.pt` | embed_tokens, lm_head, layer norms (FP16) |
| `weights/srd_index.json` | quantized layer-name list + quant params |
| `weights/config.json` | `LlamaConfig` |
| `weights/tokenizer.json` | HF fast tokenizer |

Each packed linear weight (7 bpw at `top_k_pct=0.25`, `group_size=64`):

| Field | Type | Meaning |
|---|---|---|
| `w4_packed` | uint8 `(out, in/2)` | nibble-packed 4-bit base, 2 values/byte |
| `s4` | float32 `(out, in/G)` | per-block W4 scale |
| `d8_mask` | uint8 bitmask | 1 bit/weight — marks top-25% residue positions |
| `d8_vals` | int8 (sparse) | 8-bit residue at mask=1 positions |
| `s8` | float32 `(out, in/G)` | per-block D8 scale |

Effective bpw = 4 (W4) + 0.25·8 (sparse D8) + 2·(32/64) (scales) = **7 bpw**.
Dequant: `W = W4·S4 + alpha·(D8·S8)` → fp16, with `alpha=1.0` at inference.
This is implemented in `axiom_quant.srd_dequantize` /
`research.quant.srd_realpack.load_real_packed` — the reference, not a
re-implementation target.
