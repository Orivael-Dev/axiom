# Fine-tuning the 135M extractor

Out of the box, the SRD-quantized SmolLM2-135M is *structurally* fixable but has poor
*recall*: with grammar-constrained decoding it emits valid schema-only JSON, but it
grabs the wrong fields and mangles clinical values. Two complementary levers close the
gap — and governance holds regardless of model quality.

| Problem | Fix | Where |
|---|---|---|
| invented keys, repetition loops, invalid JSON | **grammar-constrained decoding** | already on in `LlamaCppBackend` (`json_schema`) |
| low recall, wrong fields, mangled values | **schema fine-tune (SFT)** | this directory |

## 1. Generate SFT data (NIM teacher → grounded pairs)
```bash
export NVIDIA_API_KEY=nvapi-...          # or NIM_API_KEY
python finetune/gen_sft_data.py --count 500 --out finetune/data/medical_extraction_sft.jsonl
```
The teacher (`meta/llama-3.3-70b-instruct`) writes fully-synthetic records + their
verbatim extraction; only **grounded** field values survive. Output is chat-format
JSONL whose system/user prompts are byte-identical to inference
(`backends.extract_system_prompt`), so train and test distributions match. A small
committed sample lives at `data/medical_extraction_sft.sample.jsonl`.

> The extractor is trained to pull **all** present fields (identifiers included).
> De-identification / minimum-necessary is the governance layer's job, not the model's.

### One-click training on a GPU (RunPod)
`train_runpod.ipynb` runs the whole SFT on a cloud GPU pod: installs deps, loads the
dataset (upload or clone), fine-tunes `SmolLM2-135M-Instruct`, evals a sample
extraction, and exports GGUF (f16 + q8_0). Open it in a RunPod Jupyter pod, set the
config cell, Run All. A 135M full fine-tune finishes in minutes on a 3090/4090/A40.

## 2. SFT with TRL (135M full fine-tune fits on one consumer GPU)
```python
from datasets import load_dataset
from trl import SFTTrainer, SFTConfig
from transformers import AutoModelForCausalLM, AutoTokenizer

base = "HuggingFaceTB/SmolLM2-135M-Instruct"
tok = AutoTokenizer.from_pretrained(base)
model = AutoModelForCausalLM.from_pretrained(base)
ds = load_dataset("json", data_files="finetune/data/medical_extraction_sft.jsonl", split="train")

SFTTrainer(
    model=model, processing_class=tok, train_dataset=ds,
    args=SFTConfig(output_dir="out/smollm2-135m-medextract", num_train_epochs=3,
                   per_device_train_batch_size=8, learning_rate=2e-5, bf16=True,
                   packing=True, max_length=2048),
).train()
model.save_pretrained("out/smollm2-135m-medextract"); tok.save_pretrained("out/smollm2-135m-medextract")
```
TRL applies the tokenizer chat template to the `messages` field automatically.

## 3. Quantize to GGUF and serve
```bash
python llama.cpp/convert_hf_to_gguf.py out/smollm2-135m-medextract --outfile medextract-f16.gguf
llama.cpp/llama-quantize medextract-f16.gguf medextract-q4km.gguf Q4_K_M     # (or apply SRD)
llama.cpp/llama-server -m medextract-q4km.gguf -c 2048 --port 8080 --no-webui
```

## 4. Run under governance (grammar on)
```bash
python run_demo.py --local        # LlamaCppBackend constrains output to the schema
```
Same governance pipeline, now with a model that actually recalls the authorized
fields. Re-measure: authorized-field recall ↑, identifier-leak rate stays 0
(governance), fabrication-block rate ↓ (model stops mangling values).
