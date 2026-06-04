# SRD Governance Container — Guide for Organizations

## The problem

Enterprise AI deployments have no standard for model provenance. A deployed model
can be silently swapped — different weights, different fine-tune, different
quantization — with no way for the receiving organization to detect the change.

Git history proves who wrote the code, not what weights are running. A SHA-256
hash of a GGUF file proves the file hasn't changed, but it doesn't prove *what
quantization was applied*, *which base model was used*, or *whether the original
packer endorsed this exact artifact*. When a vendor delivers a fine-tuned model,
there is no chain of custody.

---

## The SRD governance container

An `.axm` file is a signed ZIP archive containing model weights, a YAML header,
and a proof ledger. Every sub-module is covered by a keyed HMAC-SHA256 entry
derived from the organization's `AXIOM_MASTER_KEY`. The container's **fingerprint**
is a deterministic, public 8-character commitment derived from the header
signature — shareable without exposing the key.

Packing is deterministic: **same weights + same key = same fingerprint**.

---

## What the .axm proves

| Claim | How it's enforced |
|-------|-------------------|
| Exact quantization | `bpw`, `scheme`, `group_size`, `top_k_pct` are signed payload fields, not a mutable sidecar |
| Weight integrity | Weights manifest lists SHA-256 of every tensor file; manifest is covered by a proof entry |
| Tamper detection | Any modification to weights, header, or proof entries breaks the HMAC chain; `verify.py` reports the failing layer |
| Chain of custody | Fingerprint published to HuggingFace is the same commitment the packer held at signing time |

---

## 3-step workflow for any organization

**Step 1 — Fine-tune on your domain data**

Use any standard fine-tuning pipeline (QLoRA, full fine-tune, etc.) against your
proprietary domain corpus. The output is a standard HuggingFace model checkpoint.

**Step 2 — Pack into a governance container**

```bash
python3 research/quant/run_srd4_local.py \
    --model /path/to/your-finetuned-model \
    --output-dir /workspace/governance_output \
    --llamacpp /workspace/llama.cpp \
    --quant Q4_K_M
```

Output: `model_srd4.axm` (signed container) + `model_srd4_q4km.gguf` (inference artifact).

**Step 3 — Publish**

```bash
python3 research/quant/push_srd_to_hub.py \
    --axm        /workspace/governance_output/model_srd4.axm \
    --gguf       /workspace/governance_output/model_srd4_q4km.gguf \
    --pack-stats /workspace/governance_output/results/pack_stats.json \
    --repo-id    your-org/your-model-srd4-axm \
    --base-model your-org/your-finetuned-model \
    --domain     healthcare
```

The push script verifies the `.axm` before upload, renders a governance model card
with the fingerprint, and uploads four files: `.axm`, `.gguf`, `verify.py`, `README.md`.

---

## Domain examples

| Domain | Governance need | What .axm proves |
|--------|----------------|------------------|
| Healthcare | HIPAA audit trail — which model generated each output | Exact weights signed at approval time; logs can reference the fingerprint |
| Legal | Chain of custody for AI-assisted drafts and summaries | Fingerprint ties each output to a specific, auditable model version |
| Finance | SR 11-7 model risk management; change control documentation | bpw, scheme, and base model are part of the signed payload, not editable metadata |
| Defense | CMMC supply chain integrity; air-gapped deployment | Offline verification — `verify.py` clones toolkit once, then runs without network |
| Education | FERPA; curriculum provenance for student-facing content | Fingerprint embeds in session logs alongside generated content |
| Manufacturing | ISO 9001 process traceability for AI quality decisions | Model becomes a versioned, traceable artifact in the QMS |
| Code | Software supply chain security; SBOM analogy for AI components | Signed model bill of materials — proves what went in and that nothing changed |

---

## FAQ

**Who holds the master key?**

The packing organization. The key is never embedded in the container or published
to HuggingFace. The fingerprint is public; the key is the private audit credential.
Organizations should store their `AXIOM_MASTER_KEY` in their secrets management
system (Vault, AWS Secrets Manager, etc.) alongside the deployment key for the
model.

**How is this different from model hashing?**

A hash proves a file hasn't changed. The AXM HMAC chain proves that every
sub-module was signed by a specific key at a specific time, and that the
quantization metadata (bpw, scheme) is part of the signed payload — not a mutable
sidecar that can be edited after signing. It also proves *who* signed it
(whoever holds the master key), not just that the file is intact.

**Can I use my own fine-tuned model?**

Yes. The pipeline accepts any HuggingFace model ID or local checkpoint path.
The `.axm` format is model-agnostic — it works with Mistral, Qwen, LLaMA,
or any HuggingFace-compatible model.

**Does verification require HuggingFace access after download?**

No. `verify.py` clones the axiom toolkit once to `/tmp/axiom`, then runs
entirely offline against the local `.axm` file. Subsequent runs use the cached
toolkit. Suitable for air-gapped environments once the toolkit is cloned.

**What happens if the model is tampered with?**

`verify.py` exits non-zero and prints `VERIFICATION FAILED`. If the fingerprint
doesn't match the expected value (e.g. re-packed with a different key), the script
catches this as a separate case and reports the mismatch. In both cases the
deployed model should be quarantined and the incident investigated.

**How do I distribute the master key to verification teams?**

Treat it like a code-signing key — distribute out-of-band via your organization's
secrets management system, not via the HuggingFace repo or alongside the model
files. The fingerprint (public) goes in audit logs; the key stays in the vault.

---

## Quick start

```bash
# Prerequisites (RunPod A100 or local GPU)
git clone --depth 1 \
    --branch claude/srd-prototype-benchmark-JRtv1 \
    https://github.com/orivael-dev/axiom.git /workspace/axiom
pip install -r /workspace/axiom/research/quant/requirements.txt

# Generate AXIOM_MASTER_KEY for your organization (keep this secret)
export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo $AXIOM_MASTER_KEY   # save this to your secrets manager

# Pack your model
python3 /workspace/axiom/research/quant/run_srd4_local.py \
    --model  your-org/your-model \
    --output-dir /workspace/out \
    --llamacpp   /workspace/llama.cpp

# Publish governance container
HF_TOKEN=hf_... python3 /workspace/axiom/research/quant/push_srd_to_hub.py \
    --axm        /workspace/out/your-model_srd4.axm \
    --gguf       /workspace/out/your-model_srd4_q4km.gguf \
    --pack-stats /workspace/out/results/pack_stats.json \
    --repo-id    your-org/your-model-srd4-axm \
    --base-model your-org/your-model \
    --domain     general
```

---

## Cost reference (RunPod)

| GPU | Pack time | Total pipeline | Estimated cost |
|-----|-----------|---------------|----------------|
| A100 40 GB | ~22 min | ~40 min | ~$1.10 |
| A10G 24 GB | ~35 min | ~55 min | ~$0.55 |

---

*See also:* [`docs/SRD_RESULTS.md`](SRD_RESULTS.md) — benchmark data |
[`docs/MISTRAL_COLAB_TO_NANO.md`](MISTRAL_COLAB_TO_NANO.md) — Colab → Orin Nano guide
