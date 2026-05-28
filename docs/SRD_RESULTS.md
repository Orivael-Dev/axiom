# SRD Honest Benchmark — Results

> **Status: placeholder.** This document is the template for the
> empirical write-up. Numbers in `«brackets»` get filled in from the
> Colab run of `notebooks/srd_benchmark.ipynb` once the sweep
> completes. Until then, treat every claim below as scaffolding.

## TL;DR

Three bullets (60 words max), filled after the run:

- **Did SRD beat existing llama.cpp K-quants at matched bits-per-weight on TinyLlama-1.1B WikiText-2?**
  `«yes / no / mixed»`
- **At its honest cost of ~13 bpw, SRD's perplexity is `«X.XX»`** —
  compared to Q6_K's `«Y.YY»` at 6.56 bpw. The pre-committed
  decision rule (SRD wins iff PPL_SRD < PPL_Q6_K by ≥0.05) says
  `«verdict»`.
- **The runtime α knob measurably affects quality** —
  α=0 PPL `«a»` vs α=1 PPL `«b»`. K-quants don't have this knob;
  whether the knob is *useful* is in §7.

## What SRD actually is

The May 2026 spec PDF calls this scheme "Stochastic Residual
Dithering." That name is misleading: there's nothing stochastic
about it, and there is no dither in the audio-engineering sense.
What the algorithm actually does is **deterministic residual
quantization** — the same family as AQLM (Egiazarian et al. 2024)
and QuIP# (Tseng et al. 2024) and the classical residual-k-means
literature going back to the 80s. We keep the SRD label because
that's the user's working name; everything below describes
deterministic residual quant.

The spec's §4 memory math claims SRD packs to ~39 % of FP16. That
claim ignores the per-block scale factors entirely. Counting
honestly — see §4 below — the true cost is ~13 bpw for group_size 64,
which is **~80 % of FP16** and lands *between* Q8_0 (8.5 bpw) and
unquantized FP16. The benchmark below therefore compares SRD to Q6_K
and Q8_0, not to Q4_K_M.

## Method

| | |
|---|---|
| Base model | TinyLlama-1.1B-Chat-v1.0 (revision `«hash»`) |
| Eval dataset | WikiText-2 raw v1, `test` split (sha256 `«fp[:16]»...`) |
| Sliding window | stride 512, context 2048 |
| Skip modules | `lm_head`, `embed_tokens` (default) |
| Group size | 64 |
| α sweep | {0.0, 0.5, 1.0} |
| Per-tensor variant | included as row 5 (mirrors spec §5 demo) |
| K-quant baselines | `«rerun_local | published_cite»` — see §11 if cited |
| Hardware | `«GPU»` |

Code: `axiom_quant.py` (kernel), `research/quant/quantize_model.py`
(model loader), `research/quant/bench_perplexity.py` (PPL sweep),
`research/quant/bench_llamacpp.py` (K-quant baseline).

## Honest bits-per-weight

For group size G, every weight stores:

| Component | Bits/weight |
|---|---|
| `W4` (4-bit base grid) | 4.0 |
| `D8` (8-bit residue grid) | 8.0 |
| `S4` (32-bit base scale, one per block) | 32 / G |
| `S8` (32-bit residue scale, one per block) | 32 / G |
| **Total** | **12 + 64 / G** |

For G = 64: **13.0 bpw**. For G = 128: **12.5 bpw**. The spec's
"39 % of FP16" figure — which would correspond to 6.24 bpw —
silently dropped both per-block scale terms. The benchmark uses
the 13.0 figure throughout.

Pinned in the unit test `tests/test_axiom_quant.py::test_bpw_group_64_is_13_0`.

## Results

| # | Config | bpw | PPL | Δ vs FP16 |
|---|---|---|---|---|
| 1 | FP16 baseline | 16.00 | `«»` | — |
| 2 | SRD α=0 (pure 4-bit, g=64, per-block) | 4.50 | `«»` | `«»` |
| 3 | SRD α=0.5, g=64, per-block | 13.00 | `«»` | `«»` |
| 4 | SRD α=1.0, g=64, per-block | 13.00 | `«»` | `«»` |
| 5 | SRD α=1.0, per-tensor (spec §5 demo) | `«bpw»` | `«»` | `«»` |
| 6 | Q4_K_M | 4.85 | `«»` | `«»` |
| 7 | Q5_K_M | 5.69 | `«»` | `«»` |
| 8 | Q6_K | 6.56 | `«»` | `«»` |
| 9 | Q8_0 | 8.50 | `«»` | `«»` |

## Plot

![SRD vs K-quants](srd_perplexity_vs_bpw.png)

K-quant Pareto frontier is the teal line; SRD operating points are
orange squares. The decision rule asks whether SRD's α=1 point at
~13 bpw lands meaningfully below where the K-quant curve extrapolates
to 13 bpw.

## α elasticity

The runtime mixing knob α is SRD's headline feature relative to
K-quants — no K-format lets you trade quality for *anything* at
inference time. Measured effect on TinyLlama:

- α=0 → PPL `«a»`
- α=0.5 → PPL `«mid»`
- α=1 → PPL `«b»`

The α=0 → α=1 swing is `«delta»` PPL points at no memory delta.
Whether that's useful depends on the deployment: a 13 bpw model
that can dial back to "almost-Q4_K_M quality but instant — no
re-quantization" might be valuable for batch-vs-interactive routing;
a model where α=0.5 already matches α=1 means the knob is just
overhead.

## Verdict

`«One paragraph, four sentences max. Pre-committed rule: SRD is
worth pursuing iff PPL_SRD@13bpw < PPL_Q6_K@6.56bpw by ≥0.05
absolute. Anything closer is not worth the ~2× memory of SRD vs
Q6_K.»`

## Recommended next step

Conditional on the verdict above:

- **If "pursue":** Define §2.2 noise-shaping filter properly,
  build a fused CUDA kernel so the bpw advantage actually becomes
  a memory advantage (currently it's fake-quantization only),
  retest on Llama-3-8B.
- **If "shelve":** Investigate AQLM-style 2-bit quantization
  instead — that's where the real memory wins live. SRD's 13 bpw
  was never going to win against Q4_K_M's 4.85 anyway.

## Prior art

- **AQLM** — Egiazarian et al., 2024. Additive Quantization for
  Language Models. 2-bit weight quant with vector codebooks;
  current SOTA at extreme low-bit. Closest cousin to SRD in spirit.
- **QuIP#** — Tseng et al., 2024. Incoherence-processed extreme
  quantization. Different approach (lattice codes + Hadamard
  rotation), similar bpw targets.
- **llama.cpp K-quants** — Gerganov et al. Per-block scale + min
  with mixed bit widths per layer. The deployed baseline; what
  every local inference user already runs.

## Reproducibility appendix

```bash
# Phase A — unit tests, no model download
pip install -r research/quant/requirements.txt
pytest tests/test_axiom_quant.py -v
# Expect: 26 passed

# Phase B — coherence smoke test (downloads TinyLlama ~2 GB)
python research/quant/quantize_model.py \
  --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
  --alpha 1.0 --prompt "Once upon a time, " --tokens 80
# Expect: coherent English at α=1, degraded but still English at α=0

# Phase C1 — SRD perplexity sweep (~10-15 min on a T4)
python -m research.quant.bench_perplexity \
  --output research/quant/results/srd_sweep.json

# Phase C2 — K-quant baseline (cite-only path, no binaries required)
python -m research.quant.bench_llamacpp \
  --output research/quant/results/kquant_sweep.json
# For apples-to-apples, add --rerun-locally + --llama-bin <dir> +
# --wikitext-file <path>; needs llama.cpp built locally.

# Phase E — plot + read the verdict
python -m research.quant.plot_results \
  --inputs research/quant/results/srd_sweep.json,research/quant/results/kquant_sweep.json \
  --output docs/srd_perplexity_vs_bpw.png
```

Env: `«python», torch «», transformers «», datasets «»`. GPU: `«»`.
Total wallclock: `«»`. Dataset fingerprint: `«fp[:16]»...`.
