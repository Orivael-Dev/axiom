# SRD Research Roadmap — Future Domains & Follow-ons

This document maps ideas that surfaced during the SRD prototype phases to their
appropriate domain agents or engineering tracks. Items here are deliberately
**not implemented** — they either require results from Phase E2 first, belong to
a different product domain, or need hardware/kernel infrastructure that doesn't
exist yet.

## Phase E2 is the prerequisite

The sparse-residual and layer-selective experiments (Phase E2) must complete
before anything below is worth touching. The Pareto curve needs at least one
operating point in the 6–11 bpw range beating K-quants before SRD graduates
from "prototype" to "buildable."

---

## Deferred ideas and their right contexts

| Idea | Right context | Gate |
|------|--------------|------|
| **Fused Triton / CUDA sparse kernel** — real memory savings, not fake-quant | Inference-engineering track (separate from quality research) | After E2 Pareto curve is complete; one kernel per operating point |
| **NVIDIA 2:4 structured sparsity** — hardware-accelerated D8 sparsity on Ampere+ | Same inference-engineering track | Requires fixed 50% sparsity; may need `top_k_pct=0.50` as the design target from E2 |
| **Speculative decoding with 4-bit base as drafter** | Speculative-decoding track, not SRD-specific | Needs real inference kernel first; the fake-quant base generates too slowly to measure |
| **Adaptive α controller** — battery/latency/entropy-gated alpha at runtime | Axiom runtime — wire into `axiom_event_token/router.py` via `RouterPolicy.score()` (Theme 3) | After kernels exist; alpha switching at token-level needs sub-ms overhead |
| **Learned residual adapters** — replace D8 with a small trained network | Follow-on after sparse residuals are validated; natural paper candidate | Needs E2 baseline; training loop via LoRA-style adapter |
| **Mixed-bitrate base** — 3-bit W4 for insensitive layers, 5-bit for sensitive | Phase E3 using the sensitivity map from `bench_layer_sensitivity.py` | Requires E2 layer sensitivity data to decide which layers tolerate 3-bit |
| **Diffusion / DiT domain SRD** — quantize UNet / transformer DiT weights | Video-generation agent; needs FID/CLIP metric, not PPL | Entirely different evaluation pipeline; start fresh |
| **Edge audio DSP** — neural amp modeler, mastering plugin | Edge/audio agent; latency ≤2 ms budget; SNR metric | Independent domain; no shared eval infrastructure with LLM track |
| **Multimodal / vision encoder SRD** | AXM format extension (add per-modality `quant_map` entry) | After LLM track is stable; encoder sensitivity profiles differ significantly |
| **CXL memory pooling** | Defer until a CXL-aware backend ships in vLLM / tgi (see Theme 4 in CLAUDE.md) | No action until upstream hardware exists |

---

## Phase E3 — Real packing (active)

### Motivation

**NVFP4 on DGX Spark (May 2026):** NVIDIA released NVFP4 quantization for
Blackwell-gen hardware (B100/B200/DGX Spark). Community models (e.g.
Step-3.7-Flash NVFP4) already show real 4× memory savings, but are
**hardware-locked to Blackwell** — requires 2× DGX Sparks for large models
and is unavailable on A10G, T4, or edge devices.

**Jetson Orin Nano target:** The Orin Nano (8 GB unified memory, Ampere GPU,
1024 CUDA cores) is an ideal SRD deployment target. TinyLlama at SRD 7 bpw
real-packed (~918 MB) fits with headroom for KV cache. FP16 (~2.2 GB) also
fits, but leaves no headroom for context. Quantization matters on this device.

**E2 quality proof complete:** The Colab A/B run (`ab_compare.py`) confirmed
that TinyLlama-1.1B at SRD 7 bpw produces coherent output matching FP16
quality. The remaining gap is storage — the `.axm` archive is still FP16-sized
(1.5 GB actual vs 918 MB theoretical at 7 bpw). Phase E3 closes that gap.

### E3 tasks

1. ✅ **W4 bit-packing** — `srd_pack_w4()` / `srd_unpack_w4()` in
   `axiom_quant.py` pack 2 int4 values into 1 uint8 byte (bit-exact
   round-trip, halves W4 storage).

2. ✅ **Sparse D8 storage** — `srd_pack_d8_sparse()` /
   `srd_unpack_d8_sparse()` store a 1-bit-per-element bitmask + tightly
   packed non-zero int8 values. At `top_k_pct=0.25`, D8 drops to ~0.375
   bytes/element.

3. ✅ **`pack_to_axm.py --real-pack`** — `research/quant/srd_realpack.py`
   `save_real_packed()` writes `srd_packed.pt` (W4 nibble + sparse-D8) +
   `srd_dense.pt` (FP16 embeddings/norms/lm_head) + `srd_index.json`.
   `quant_map["packed"]=true`. Archive is genuinely ~half FP16.

4. ✅ **`load_from_axm.py` unpack on load** — detects `packed: true` (or
   the on-disk `srd_index.json`) and calls `load_real_packed()`:
   meta-init from config → load dense → unpack each layer to FP16 → assign.

5. ⬜ **Orin Nano benchmark** — run `axm run` on the Orin Nano with the
   real-packed TinyLlama archive. Target metrics: load time, TTFT, tok/s,
   peak RSS. Compare FP16 vs SRD 7 bpw real-packed side-by-side.

   *E3 real-pack validated on Colab T4 (2026-05-31):* 942 MB archive
   (vs 1535 MB FP16 fake-quant, **39% smaller**), `packed=true`, proofs
   verified, output identical to FP16, warm TTFT 50 ms, 34.6 tok/s. The
   942 MB matches the ~918 MB estimate (gap = FP16 dense params + zip).
   Remaining: re-run the same `axm run` on the actual Orin Nano hardware
   and capture peak RSS to confirm the 8 GB fit with KV-cache headroom.

   > **⚠️ Key-portability requirement:** The `.axm` archive is signed with
   > `AXIOM_MASTER_KEY`. The Orin Nano must use the **same key** that was
   > set when packing — otherwise `axm run` fails proof verification.
   > The Colab validation cell generates an ephemeral random key
   > (`secrets.token_hex(32)`) that is lost when the session ends. Before
   > running the Orin Nano benchmark, either:
   > - Set a persistent `AXIOM_MASTER_KEY` (store in `.env` or your shell
   >   profile) **before** packing in Colab, then `export` that same key on
   >   the Orin Nano, or
   > - Re-pack the model directly on the Orin Nano (avoids key transport).

6. ⬜ **NVIDIA 2:4 structured sparsity path** — once `top_k_pct=0.50` is
   validated in E2, wire the D8 mask into `torch.nn.utils.prune` 2:4 format
   so Ampere sparse Tensor Cores can accelerate the residual matmul directly.

### E3 vs NVFP4 positioning

| | NVFP4 | SRD E3 |
|---|---|---|
| Hardware | Blackwell only | Any CUDA (Ampere, T4, Orin) |
| Format | FP4 base, no residual | W4 + sparse D8 residual |
| Quality | ~4 bpw, PPL TBD | 7 bpw, quality proven |
| Edge (Orin Nano) | ✗ | ✓ |
| Open format (.axm) | ✗ | ✓ |

---

## Other deferred candidates (post-E3)

1. **Mixed-bitrate base** — use the layer sensitivity ranking from
   `bench_layer_sensitivity.py` to assign 3-bit base to low-sensitivity layers
   and 5-bit to high-sensitivity ones. Target: match E2 PPL at lower bpw.

2. **Larger model validation** — run the sparse sweep on Mistral-7B (A100
   required) to confirm the Pareto improvement generalizes beyond TinyLlama.

3. **Group size sensitivity** — sweep `group_size ∈ {32, 64, 128, 256}` at
   fixed `top_k_pct=0.25` to understand the scale-overhead vs quality trade-off
   at the most interesting operating point (~7 bpw).
