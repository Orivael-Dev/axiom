# research/ — Script Reference

Quick guide to every Python file and notebook in this directory.
**The most important section for building notebooks is [MET Metadata — What Adds It and When](#met-metadata--what-adds-it-and-when).**

---

## MET Metadata — What Adds It and When

MET (Master Event Token) metadata describes how a GGUF is split into hydration slots
(embedding, early, factual, reasoning, governance) for on-device inference. It lets
Android apps like PocketPal pre-load only the chunks needed for the current intent.

| Script / Notebook | MET Added? | How |
|---|---|---|
| `colab_gemma4_12b_srd4_pipeline.ipynb` | ✅ **Automatic** | Cell 6 writes `.axiom_meta.json` sidecar |
| `colab_gemma4_31b_axiom_finetune_srd.ipynb` | ✅ **Automatic** | Cell 9 writes `.axiom_meta.json` sidecar |
| `add_axiom_gguf_meta.py` | ✅ **Manual run** | `python3 add_axiom_gguf_meta.py --gguf model.gguf` |
| `pack_qwen_gdrive_to_axm.ipynb` | ❌ **Not yet** | Needs a MET cell added (see below) |
| `run_srd4_local.py` | ❌ No | Run `add_axiom_gguf_meta.py` separately after |
| `colab_mistral_srd4_pipeline.py` | ❌ No | Run `add_axiom_gguf_meta.py` separately after |
| `pack_to_axm.py` | ❌ No | Packs weights only; no MET layer info |
| `pack_gguf_to_axm.py` | ❌ No | Wraps GGUF in .axm; no MET layer info |
| All `simulation/` scripts | ❌ No | Simulate MET behavior; don't touch real files |
| `tag_met_slots.py` | ⬛ Training data only | Tags JSONL for training QRF, not model files |

### The full MET flow

```
1. tag_met_slots.py          ← tag training JSONL with slot annotations (one-time setup)
        ↓
2. hydration_sim.py          ← simulate / validate slot layout and QRF accuracy (no files written)
        ↓
3. [pack pipeline]           ← produce a .gguf from SRD or GGUF source
        ↓
4. add_axiom_gguf_meta.py    ← write .axiom_meta.json sidecar (slots, chunks, fingerprint)
                               --annotate flag also bakes axiom.* KV keys into a new GGUF copy
        ↓
5. push_srd_to_hub.py        ← upload .axm + .gguf + verify.py + model card to HF Hub
```

Steps 3+4 are combined automatically in the Gemma 4 notebooks (Cells 6/9).
**If you are building the Qwen GDrive notebook, add a MET cell after the GGUF is produced
(copy the Cell 6 pattern from `colab_gemma4_12b_srd4_pipeline.ipynb`).**

---

## research/quant/ — Packing, Quantization & Benchmarks

### Main pipelines

| File | Purpose | MET |
|---|---|---|
| `colab_mistral_srd4_pipeline.py` | Mistral-7B SRD-4 → .axm → GGUF Q4_K_M — Colab cells (A100/T4) | ❌ |
| `colab_gemma4_12b_srd4_pipeline.ipynb` | Gemma 4 12B SRD-4 → .axm → GGUF → MET sidecar — full Colab notebook | ✅ Cell 6 |
| `colab_gemma4_31b_axiom_finetune_srd.ipynb` | Gemma 4 31B fine-tune on Axiom data → SRD → MET sidecar | ✅ Cell 9 |
| `pack_qwen_gdrive_to_axm.ipynb` | Download Qwen GGUF from Google Drive → pack to signed .axm | ❌ needs MET cell |
| `run_srd4_local.py` | Full SRD pipeline CLI — RunPod / local GPU / no Colab needed | ❌ |
| `add_axiom_gguf_meta.py` | Write `.axiom_meta.json` MET sidecar; optional annotated GGUF copy | ✅ **is the tool** |
| `push_srd_to_hub.py` | Upload .axm + GGUF + verify.py + model card to HuggingFace Hub | ❌ |

### Packing

| File | Purpose |
|---|---|
| `pack_to_axm.py` | Pack any HF model (FP16 or SRD-quantized) → signed .axm archive |
| `pack_gguf_to_axm.py` / `.ipynb` | Wrap an existing GGUF in a signed .axm (no re-quantization) |
| `pack_local_safetensors.ipynb` | Pack local safetensors checkpoint → .axm |
| `pack_vision_to_axm.py` | Pack vision/multimodal models → .axm |
| `pack_fleet.py` | Batch-pack all models in a fleet manifest |
| `srd_realpack.py` | Real SRD pack — writes packed-int4 on disk (not FP16 fake-quant) |
| `axm_to_gguf.py` | Convert a verified .axm → GGUF for llama.cpp |
| `load_from_axm.py` | Load a model back from .axm into memory |
| `quantize_model.py` | Standalone quantization helper |

### Benchmarks

| File | Purpose |
|---|---|
| `bench_perplexity.py` | Sliding-window WikiText-2 PPL for SRD configs (rows 1–5 of results table) |
| `bench_llamacpp.py` | K-quant PPL via llama-perplexity (rows 6–9 of results table) |
| `bench_llamacpp_infer.py` | TTFT + tok/s benchmark for llama.cpp inference |
| `bench_layer_sensitivity.py` | Per-layer quantization sensitivity sweep |
| `bench_mistral_kv.py` | KV cache benchmark for Mistral |
| `bench_orin_mistral7b.py` | Jetson Orin hardware benchmark for Mistral-7B |
| `bench_laptop_srd_vs_q4.py` | Laptop CPU comparison: SRD vs Q4_K_M |
| `ab_compare.py` | A/B: pack FP16 vs SRD, compare output side-by-side |
| `colab_benchmark.py` | General-purpose Colab benchmark runner |
| `colab_mistral_kv_bench.py` | Mistral KV-cache benchmark (Colab cells) |
| `plot_results.py` | Plot PPL / throughput tables from bench JSON output |

### Utilities

| File | Purpose |
|---|---|
| `trajectory_filter.py` | Split text into phrase segments; used by MET encoder and simulate scripts |
| `simulate_axm.py` | Simulate pack → load → inference without downloading real models |
| `simulate_kv_context.py` | Simulate KV cache reuse across context windows |
| `colab_realpack_validate.py` | Validate real-pack output matches expected quantization |
| `llama32_1b_jetson_benchmark.ipynb` | Llama 3.2 1B benchmark on Jetson hardware |

---

## research/simulation/ — MET & QRF Simulators

These scripts **simulate** MET behavior and compute statistics. They do **not** modify
any GGUF or .axm files. Run them to validate slot layouts and QRF accuracy before
committing to a real pack run.

| File | Purpose |
|---|---|
| `hydration_sim.py` | **Main MET simulator** — 6 phases: slot layout, RAM budget, UFS latency, QRF accuracy (Phase 6 uses tagged training data). CLI: `--workload`, `--storage` |
| `met_retro_sim.py` | MET encoder → state transition engine → signed ledger → retrospective report end-to-end |
| `met_full_loop.py` | Closes feedback wire: retrospective results update QRF predictor for next cycle |
| `reverse_qrf_sim.py` | Forward-prediction arm: QRF fires in idle gap, pre-hydrates before next MET arrives |
| `qrf_offload_sim.py` | QRF-driven offload / core-spawn — models pre-wake signal timing for edge CPUs |
| `drone_met_arch.py` | Drone deployment simulation: ground-encode / air-execute vs hybrid scenarios |

### Running the main simulator

```bash
AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
    python3 research/simulation/hydration_sim.py
```

Phase 6 (QRF accuracy) requires tagged training data. Run `tag_met_slots.py` first if
the tagged files don't exist yet.

---

## research/finetune/ — Fine-tuning Pipelines

| File | Purpose | MET |
|---|---|---|
| `tag_met_slots.py` | Tag training JSONL with `axiom_met_slot`, `axiom_met_triggers`, `axiom_hydration_intent` fields — **run this before `hydration_sim.py` Phase 6**. Input: any axiom `*.jsonl`. Output: `*_tagged.jsonl` | ⬛ training data |
| `gen_axiom_dataset.py` | Generate ~5 000 metric-targeted ChatML examples across 10 categories (verdict, tamper, revocation, CLI, AXIOM_BLOCK adapter, etc.) | ❌ |
| `eval_axiom_metrics.py` | 8-metric evaluator — test JSON validity, verdict accuracy, tamper detection, revocation, refusal, no-fake-signatures, CLI accuracy against any model | ❌ |
| `colab_axiom_finetune.py` | Qwen 2.5-Coder-1.5B QLoRA fine-tune → 8-metric eval → merge → push to HF | ❌ |
| `colab_gpt_cve_axiom_pipeline.py` | Fine-tune on CVE records + Axiom constitution for security-focused assistant | ❌ |
| `push_to_hub.py` | Upload merged model + LoRA adapter + GGUF + model card to HuggingFace Hub | ❌ |

### Tagging workflow

```bash
# Tag all training files (one-time, or re-run after adding new examples)
python3 research/finetune/tag_met_slots.py \
    --input axiom_behavioral_training.jsonl \
    --output axiom_behavioral_training_tagged.jsonl

python3 research/finetune/tag_met_slots.py \
    --input autotrain_data/axiom_metric_targeted.jsonl \
    --output autotrain_data/axiom_metric_targeted_tagged.jsonl

# Then run simulator (Phase 6 will find the tagged files automatically)
python3 research/simulation/hydration_sim.py
```

---

## research/exploitbench/ — Security Evaluation

| File | Purpose |
|---|---|
| `run_exploitbench.py` | Run adversarial exploit benchmark suite against a model endpoint |
| `compare_models.py` | Compare model performance on exploit / refusal tasks |

---

## research/demo/ — Edge Demos

| File | Purpose | MET |
|---|---|---|
| `smollm_edge_demo.py` / `.ipynb` | SmolLM2-135M: SRD → .axm → GGUF → MET sidecar → PocketPal deploy. Includes `adb push` commands for Android | ✅ built-in |

---

## Building a new Qwen + Google Drive notebook

The existing `pack_qwen_gdrive_to_axm.ipynb` handles:
- Download GGUF from Google Drive link
- Pack to signed .axm
- Verify HMAC chain
- Download/summary

**It does not add MET metadata.** To add MET, insert a cell after the GGUF is produced
that mirrors Cell 6 in `colab_gemma4_12b_srd4_pipeline.ipynb`:

```python
# MET metadata cell — paste after GGUF is written
import json, math
from pathlib import Path

# Read real arch from model config
cfg_path = MODEL_DIR / "config.json"
cfg      = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
HIDDEN   = cfg.get("hidden_size", 2048)
VOCAB    = cfg.get("vocab_size", 151936)    # Qwen 2.5 default
N_LAYERS = cfg.get("num_hidden_layers", 28)

# Embedding slot (always pinned F16, never swapped)
EMB_MB = round(VOCAB * HIDDEN * 2 / 1024**2, 1)

# Split transformer layers across 4 slots
FRACS = {"early": 0.20, "factual": 0.20, "reasoning": 0.37, "governance": 0.23}
BYTES_PER_LAYER = HIDDEN * HIDDEN * 4 * 2 / 1024**2  # rough W4 estimate
slots, lo = {}, 0
for name, frac in FRACS.items():
    count = max(1, round(N_LAYERS * frac))
    hi    = min(lo + count - 1, N_LAYERS - 1)
    slots[name] = {"layers": [lo, hi], "mb": round(BYTES_PER_LAYER * count, 1)}
    lo = hi + 1

meta = {
    "model": MODEL_ID,
    "embedding_slot": {"mb": EMB_MB, "dtype": "F16", "always_loaded": True},
    "transformer_chunks": slots,
    "chunk_map": {str(i): name for name, s in slots.items()
                  for i in range(s["layers"][0], s["layers"][1]+1)},
    "hydration_policy": {
        "INFORM":  ["early", "factual"],
        "CLARIFY": ["early", "factual", "reasoning"],
        "REFUSE":  ["early", "governance"],
        "HARM":    ["early", "governance"],
    },
    "storage_speed_mbs": 1500,
    "fingerprint": "run axm_cli.py verify to get real fingerprint",
}

sidecar = GGUF_PATH.with_suffix(".axiom_meta.json")
sidecar.write_text(json.dumps(meta, indent=2))
print(f"MET sidecar: {sidecar}")
print(f"  Embedding pinned: {EMB_MB} MB  |  Layers: {N_LAYERS}")
for name, s in slots.items():
    print(f"  {name:12s}  L{s['layers'][0]:02d}-{s['layers'][1]:02d}  {s['mb']:.0f} MB")
```

Replace `MODEL_DIR`, `MODEL_ID`, and `GGUF_PATH` with the variable names from your notebook.

---

## Quick commands

```bash
# Add MET sidecar to any existing GGUF
python3 research/quant/add_axiom_gguf_meta.py --gguf model.gguf

# Add MET + bake axiom.* KV keys into a new GGUF copy
python3 research/quant/add_axiom_gguf_meta.py --gguf model.gguf --annotate

# Tag training data for QRF training / Phase 6 sim
python3 research/finetune/tag_met_slots.py --input axiom_behavioral_training.jsonl

# Run full hydration simulation
python3 research/simulation/hydration_sim.py

# Pack any HF model → .axm on a local/RunPod GPU
python3 research/quant/run_srd4_local.py --model mistralai/Mistral-7B-Instruct-v0.3 \
    --output-dir /workspace/out --llamacpp /workspace/llama.cpp

# Push signed container to HuggingFace
python3 research/quant/push_srd_to_hub.py \
    --axm model.axm --gguf model.gguf \
    --pack-stats pack_stats.json \
    --repo-id orivael/my-model --base-model mistralai/Mistral-7B-Instruct-v0.3
```
