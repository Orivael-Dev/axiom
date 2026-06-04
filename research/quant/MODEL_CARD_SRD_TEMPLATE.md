---
language: en
license: mit
base_model: {{BASE_MODEL}}
tags:
  - srd4-governance
  - axm-container
  - edge-inference
  - {{DOMAIN}}
{{DOMAIN_TAGS_BLOCK}}  - orivael
  - llama-cpp-compatible
  - gguf
library_name: gguf
pipeline_tag: text-generation
---

# {{REPO_ID}}

A cryptographically signed SRD-4 governance container for `{{BASE_MODEL}}`.

This is **not just a compressed model** — it is a tamper-evident, auditable model
package. The `.axm` file provides HMAC-signed proof of exactly what was packed and
at what quantization. The `.gguf` file is the ready-to-run inference artifact.

---

## What the .axm gives you

- **Fingerprint** `{{FINGERPRINT}}` — a public commitment derived from
  HMAC-SHA256 over the signed container header. Shareable without exposing the key.
- **HMAC proof chain** — `{{PROOFS_COUNT}}` signed proof entries covering every
  sub-module (header, delegates, trajectories, weights manifest).
- **Tamper detection** — any modification to weights, header, or proof entries
  breaks the verification chain and flags the exact location of the change.
- **Quantization provenance** — bpw, scheme (`srd`), group size, and `top_k_pct`
  are part of the signed payload, not a mutable sidecar.

---

## Verification

```bash
# Requires AXIOM_MASTER_KEY from the publishing organization
export AXIOM_MASTER_KEY="<your-org-key>"

# Download the .axm file, then:
python verify.py
# → VERIFIED  fingerprint={{FINGERPRINT}}  proofs={{PROOFS_COUNT}}
```

The `AXIOM_MASTER_KEY` is your organization's custody token. It is never embedded
in the container or published to HuggingFace — the fingerprint above is the public
commitment; the key is the private audit credential.

---

## Inference (llama.cpp)

```bash
# Download the GGUF and run
~/llama.cpp/build/bin/llama-cli \
    -m {{GGUF_FILENAME}} \
    --ngl 32 \
    --ctx-size 8192 \
    --flash-attn \
    --prompt "Your prompt here" \
    --n-predict 256
```

For Jetson Orin Nano (5.5 GB unified memory) use the safe-launch script:

```bash
bash orin_safe_launch.sh {{GGUF_FILENAME}} ~/llama.cpp/build/bin/llama-cli "Your prompt"
```

---

## Pack metadata

| Field | Value |
|-------|-------|
| Fingerprint | `{{FINGERPRINT}}` |
| Base model | `{{BASE_MODEL}}` |
| Quantization | SRD-4 (W4 base, ~{{BPW}} bpw) |
| Proofs checked | {{PROOFS_COUNT}} |
| .axm size | {{AXM_SIZE_GB}} GB |
| GGUF size | {{GGUF_SIZE_GB}} |
| Pack time | {{PACK_TIME_MIN}} min |
| Packed | {{TIMESTAMP}} |

---

## Domain: {{DOMAIN}}

{{DOMAIN_NOTES}}

---

## How organizations use this

Any organization can pack their own fine-tuned model into a governance container:

**Step 1 — Fine-tune on your domain data** using any standard pipeline
(QLoRA, full fine-tune, etc.).

**Step 2 — Pack into a governance container:**

```bash
python3 research/quant/run_srd4_local.py \
    --model /path/to/your-finetuned-model \
    --output-dir /workspace/governance_output \
    --llamacpp /workspace/llama.cpp
```

**Step 3 — Publish the governance container:**

```bash
python3 research/quant/push_srd_to_hub.py \
    --axm    /workspace/governance_output/model_srd4.axm \
    --gguf   /workspace/governance_output/model_srd4_q4km.gguf \
    --pack-stats /workspace/governance_output/results/pack_stats.json \
    --repo-id    your-org/your-model-srd4-axm \
    --base-model your-org/your-finetuned-model \
    --domain healthcare
```

---

## SRD benchmark context

SRD-4 at 4.5 bpw outperforms standard Q4_K_M at 4.85 bpw by **1.51 perplexity
points** on TinyLlama-1.1B (WikiText-2, 341K tokens). At matched bpw, SRD-4
achieves lower perplexity than any standard k-quant tier. See
[`docs/SRD_RESULTS.md`](https://github.com/orivael-dev/axiom/blob/claude/srd-prototype-benchmark-JRtv1/docs/SRD_RESULTS.md)
for the full benchmark table.

---

## Files in this repo

| File | Description |
|------|-------------|
| `{{AXM_FILENAME}}` | SRD-4 governance container — signed source of truth |
| `{{GGUF_FILENAME}}` | GGUF Q4_K_M — ready for llama.cpp inference |
| `verify.py` | Standalone tamper-check script (requires `AXIOM_MASTER_KEY`) |
| `README.md` | This model card |

---

## License

MIT — see [axiom repo](https://github.com/orivael-dev/axiom) for full terms.
