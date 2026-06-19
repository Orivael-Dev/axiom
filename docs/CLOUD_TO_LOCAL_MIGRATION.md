# Cloud-to-Local AI Migration — Team Playbook

A step-by-step guide for Orivael team members running a client migration from
cloud AI (OpenAI, Anthropic, Azure OpenAI, Vertex AI) to a governed local
deployment using the Axiom/Orivael toolchain.

---

## Why this is a business opportunity

| Pain point clients feel | What we offer |
|---|---|
| API bills scaling with usage | Zero per-token cost after hardware |
| Data leaving the building (HIPAA, GDPR, SOC 2) | Inference stays on-premise |
| Vendor lock-in and surprise rate limits | Portable `.axm` containers, model-agnostic |
| No audit trail on AI decisions | Orivael Governance Console — every decision signed |
| Compliance gap (EU AI Act, OWASP) | Built-in compliance checker and domain packs |

**Target clients first:** healthcare practices, law firms, financial advisors,
and any SMB paying >$500/month on OpenAI that handles sensitive customer data.

---

## Migration phases at a glance

```
Phase 1 — Audit (1–2 days)
  Understand what the client is using AI for and what it costs.

Phase 2 — Model selection (1 day)
  Match each use case to a local model that fits their hardware.

Phase 3 — Pack & convert (2–4 hours in Colab)
  SRD-4 quantize → .axm container → GGUF ready for llama.cpp.

Phase 4 — Deploy & test (1 day)
  Install on client hardware, smoke test, validate outputs.

Phase 5 — Governance console (half day)
  Wire up Orivael dashboard, compliance packs, domain policy.

Phase 6 — Handoff (half day)
  Train the client's team, hand over runbook, set a 30-day check-in.
```

---

## Phase 1 — Client audit

Ask the client to pull their last 3 months of API invoices. You need:

- **Which models** they call (GPT-4o, Claude 3.5, Gemini 1.5 Pro, etc.)
- **Monthly spend** per model
- **Use cases** — customer support, document summarisation, code review, data extraction, etc.
- **Average prompt/completion token sizes** (ask for API logs or dashboard export)
- **Data sensitivity** — does the prompt or completion ever contain PII, PHI, or confidential business data?
- **Hardware on-site** — workstation GPU, NAS, server room, or none

Fill in the [Client Audit Sheet] (use `docs/PRODUCT_TEMPLATE.md` as a starting point) before moving to Phase 2.

### Typical findings

Most SMBs are using GPT-4o for tasks that a 3–7B local model handles at 85–95%
quality: summarisation, classification, simple Q&A, form extraction, email draft.
Heavy reasoning tasks (complex legal analysis, multi-step coding) may need a
12B+ model or a hybrid approach where only sensitive queries stay local.

---

## Phase 2 — Model selection

Match use case to hardware tier:

| Client hardware | RAM | Fits | Good for |
|---|---|---|---|
| Phone / tablet (Android/iOS) | 6–12 GB | SmolLM2-135M, Qwen3-1.7B | Mobile agent, edge classifier |
| Laptop / workstation (no GPU) | 16–32 GB | Gemma3-1B, Mistral-7B (CPU) | Internal tools, low-traffic chat |
| Workstation with GPU (8–16 GB VRAM) | 16 GB+ | Gemma 4 12B, Mistral-7B | Customer support, doc processing |
| Local server (A10 / 3090 / 4090) | 24 GB+ | Gemma 4 12B–27B, Qwen2.5-14B | High-traffic, multi-user |
| On-prem server (A100 / H100) | 40–80 GB | Llama-3-70B, Gemma 4 27B+ | Enterprise, high quality |

**Rule of thumb:** model file size (GB) × 1.2 = minimum system RAM needed for
CPU inference. For GPU inference, model file must fit in VRAM.

For most SMB migrations, **Gemma 4 12B Q4_K_M** (7.5 GB file) or
**Mistral-7B Q4_K_M** (4 GB file) covers 80% of use cases.

---

## Phase 3 — Pack and convert

Use our Colab notebooks — no local GPU required. Each notebook handles one
model end-to-end: download → SRD-4 pack → `.axm` → GGUF + MET sidecar.

### Available notebooks

| Model | Notebook | VRAM needed | Time (A100) |
|---|---|---|---|
| Gemma 4 12B | `research/quant/colab_gemma4_12b_srd4_pipeline.ipynb` | 40 GB | 35–60 min |
| Gemma 4 E2B | `research/quant/gemma4_e2b_srd4_pipeline.ipynb` | 8 GB | 15 min |
| Mistral 7B | `research/quant/colab_mistral_srd4_pipeline.py` | 15 GB | 25–40 min |
| Qwen3 1.7B | `research/quant/qwen3_1b7_axm_to_gguf.ipynb` | 8 GB | 10 min |
| TinyLlama 1B | `axiom_tinyllama_finetune.ipynb` | 8 GB | 10 min |

### What each notebook produces

```
<model>_srd4.axm          — signed container (fingerprinted, tamper-evident)
<model>_q4km.gguf         — GGUF Q4_K_M, runs on any llama.cpp build
<model>_q4km.axiom_meta.json  — MET slot map (memory hydration sidecar)
```

### Step-by-step (Gemma 4 12B as example)

1. Open [Google Colab](https://colab.research.google.com), set runtime to **A100**
2. Upload or open `research/quant/colab_gemma4_12b_srd4_pipeline.ipynb`
3. **Cell 1** — GPU check, clone repo, build llama.cpp (~15 min)
4. **Cell 2** — HuggingFace login, download model (~10–15 min)
   - Client needs HF account with model terms accepted
5. **Cell 3** — SRD-4 pack → `.axm` (~20–40 min)
6. **Cell 4** — Verify `.axm` proof ledger (~10 s)
7. **Cell 5** — Extract → GGUF Q4_K_M (~15–25 min)
8. **Cell 6** — Write MET sidecar (~10 s)
9. **Cell 7** — Smoke test, download files

### Colab cost

A100 40 GB on Colab Pro+ = ~$1–2 for the full pipeline. One-time per model.

### No custom llama.cpp fork needed

The notebooks use standard upstream `github.com/ggerganov/llama.cpp`, built
fresh in each session. The output GGUF runs on any llama.cpp-compatible
runtime (Ollama, LM Studio, PocketPal, llama-server).

---

## Phase 4 — Deploy to client hardware

### Option A — Ollama (easiest, recommended for non-technical clients)

```bash
# On the client's machine:
curl -fsSL https://ollama.com/install.sh | sh

# Import the GGUF we produced:
ollama create client-model -f Modelfile
# where Modelfile contains:
#   FROM ./gemma4_12b_q4km.gguf

# Test:
ollama run client-model "Summarise this: ..."
```

Ollama exposes an OpenAI-compatible API on `localhost:11434`, so existing
client code that calls OpenAI can switch with a one-line URL change.

### Option B — llama-server (more control, same GGUF)

```bash
./llama-server -m gemma4_12b_q4km.gguf --port 8080 -ngl 99
```

### Option C — Phone / tablet (MET hydration, edge agents)

For small models (SmolLM2-135M, Qwen3-1.7B):
```bash
adb push model_q4km.gguf /storage/emulated/0/models/
adb push model_q4km.axiom_meta.json /storage/emulated/0/models/
```
Open in [PocketPal AI](https://github.com/a-ghorbani/pocketpal-ai) or our
Axiom edge agent. The `.axiom_meta.json` sidecar tells the app which
transformer chunks to load per intent — saves 40–60% peak RAM.

### Option D — RunPod CPU pod (conversion only, no ongoing cost)

Use this if the client has no GPU for conversion and does not want Colab:
- Deploy a 4 vCPU / 16 GB RAM Ubuntu pod ($0.16/hr)
- `pip install torch --index-url https://download.pytorch.org/whl/cpu gguf numpy`
- Run `axm_to_srd4_gguf.py` (zero VRAM — all CPU)
- Pull outputs, terminate pod

### Validate the deployment

```bash
# Quick sanity check — should respond in < 5 s on GPU, < 60 s on CPU:
curl http://localhost:11434/api/generate -d '{
  "model": "client-model",
  "prompt": "Reply with the word READY only.",
  "stream": false
}'
```

Run a 20-question sample from the client's real workload and compare
outputs to their current cloud model. Document any quality gaps before handoff.

---

## Phase 5 — Governance console

Every deployment should include the Orivael Governance Console so the client
has visibility into their AI usage.

### Start the dashboard

```bash
# Clone the repo on the client's server:
git clone https://github.com/orivael-dev/axiom.git
cd axiom

export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
# Save this key — it signs every audit decision. Losing it breaks the ledger.
echo $AXIOM_MASTER_KEY > /etc/axiom/master.key  # or use their secrets manager

uvicorn axiom_firewall.dashboard:app --host 0.0.0.0 --port 8004
```

### What to configure per client

| Page | What to set | Why |
|---|---|---|
| **Dashboard** | Verify model-in-use pill shows their local model | Confirms Ollama is being read |
| **Compliance** | Set active domain (healthcare / finance / legal) | Triggers correct pack requirements |
| **Packs** | Install domain compliance pack | e.g. `hipaa-intake` for healthcare |
| **Policy** | Add any business-specific block patterns | Client's custom rules |
| **CMR Agent** | Configure routing rules and escalation threshold | If replacing a customer-facing bot |

### Compliance packs by industry

| Industry | Required packs |
|---|---|
| Healthcare | `hipaa-intake`, `gdpr-article-9` |
| Finance / fintech | `pci-dss`, `gdpr-article-9` |
| Legal | `gdpr-article-9` |
| Government | `gdpr-article-9` |
| General / retail | None required (still recommended) |

Install via `/dashboard/packs` → search → Install. The compliance checker at
`/dashboard/compliance` will turn green when all required packs are present.

---

## Phase 6 — Client handoff

Deliver three things:

**1. Runbook (1-page doc per client)**
- How to restart Ollama / llama-server if it crashes
- Dashboard URL and login
- Who to call if outputs seem wrong (escalation path)
- AXIOM_MASTER_KEY storage location

**2. Cost comparison report**
Fill in actual numbers from the audit (Phase 1):
```
Before:  $X/month to [OpenAI/Anthropic]
After:   $Y/month hardware amortised + $0 per token
Payback: X months
Annual saving: $Z
```

**3. 30-day check-in scheduled**
Review the audit ledger for any anomalies, check compliance dashboard,
confirm the client's team is comfortable with the runbook.

---

## FAQ for client conversations

**"Will the local model be as good as GPT-4o?"**
For summarisation, classification, email draft, and form extraction — yes,
within 85–95% quality at 7B+. For complex multi-step reasoning or
cutting-edge coding, there may be a gap. We benchmark their actual workload
before making promises.

**"What if we need the cloud for some tasks?"**
Hybrid is fine. Sensitive or high-volume tasks go local; anything that
genuinely needs frontier capability can stay on the cloud. The Orivael
router can split by intent class automatically.

**"Who manages model updates?"**
We re-run the Colab pipeline when a new model version ships (takes 1–2
hours). The client keeps their existing hardware. We recommend a quarterly
model refresh cycle.

**"What hardware do we need to buy?"**
For a 5–20 user office: a refurbished workstation with an RTX 3090 (24 GB
VRAM) handles Gemma 4 12B at 15–25 tokens/sec — enough for most chat
workloads. Budget $800–1,500. ROI at $500/month cloud spend = < 3 months.

**"Is this compliant with HIPAA / GDPR?"**
Local inference means PHI never leaves the network. Combined with our
signed audit ledger and `hipaa-intake` pack, this satisfies the core
technical safeguards. The client still owns their BAA obligations — we
provide the technical controls, not legal advice.

**"Do you need a master key to run the conversion tools?"**
No. The GGUF conversion scripts (`add_axiom_gguf_meta.py`,
`axm_to_srd4_gguf.py`, `met_ram_estimator.py`) run without any Axiom
keys — they only need `pip install gguf numpy torch`. The master key is
only required to run the governance dashboard and sign audit decisions.

---

## Quick reference — key files

| File | What it does |
|---|---|
| `research/quant/colab_gemma4_12b_srd4_pipeline.ipynb` | Full Gemma 4 12B Colab pipeline |
| `research/quant/colab_mistral_srd4_pipeline.py` | Mistral 7B Colab pipeline |
| `research/quant/axm_to_srd4_gguf.py` | Convert .axm → .srd4 + GGUF (CPU, no master key) |
| `research/quant/add_axiom_gguf_meta.py` | Add/patch axiom.* KV keys in a GGUF |
| `research/quant/met_ram_estimator.py` | Estimate and update MET RAM sidecar |
| `axm_cli.py` | Pack, verify, inspect .axm containers |
| `axiom_firewall/dashboard.py` | Orivael Governance Console (uvicorn) |
| `docs/AXM_LAPTOP_INFERENCE.md` | Laptop/desktop inference setup |
| `docs/MISTRAL_COLAB_TO_NANO.md` | Mistral → Jetson Nano deployment |
| `docs/EDGE_RAG_DEPLOYMENT_GUIDE.md` | RAG on edge devices |
