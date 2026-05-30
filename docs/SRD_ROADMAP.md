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

## Phase E3 candidates (after E2)

Once E2 produces a validated Pareto improvement, these are natural next steps
within the LLM quality track:

1. **Mixed-bitrate base** — use the layer sensitivity ranking from
   `bench_layer_sensitivity.py` to assign 3-bit base to low-sensitivity layers
   and 5-bit to high-sensitivity ones. Target: match E2 PPL at lower bpw.

2. **Larger model validation** — run the sparse sweep on Mistral-7B (A100
   required) to confirm the Pareto improvement generalizes beyond TinyLlama.

3. **Group size sensitivity** — sweep `group_size ∈ {32, 64, 128, 256}` at
   fixed `top_k_pct=0.25` to understand the scale-overhead vs quality trade-off
   at the most interesting operating point (~7 bpw).
