# Azure build & benchmark kit

Two credit-funded tracks, kept separate so they don't compete for the same dollar:

| Track | Goal | ~Budget | Where |
|---|---|---|---|
| **SRD benchmark sweep** | Hard WikiText-2 perplexity numbers — SRD-4 vs Q4_K_M at matched bpw, on real A100s | ~$4,000 | `srd_benchmark/` |
| **Always-on demo host** | A stable `demo.orivael.dev` clients can hit, running the Governed Agent on Azure | ~$1,000 | `demo/` |

The governance runtime itself is stdlib-light and runs anywhere — the credits are for
**GPU benchmarking** (numbers no competitor has) and **hosting** (a URL to send a
prospect), not the runtime.

## Track 1 — SRD benchmark sweep (`srd_benchmark/`)

Runs the existing `research/quant/bench_*.py` scripts across a model × bpw matrix on a
Tesla **T4** node and collects a results table.

> **No A100 in the subscription? T4 is fine.** Perplexity is computed from the model's
> logits — it's *identical* on any GPU. The A100 only buys speed and room for bigger
> models. A T4 (16 GB) holds every model you'd quantize here, and is ~7× cheaper, so the
> credits go much further. The published "SRD-4 beats Q4_K_M at matched bpw" claim does
> not change — only the wall-clock does.

- **SKU:** `NC4as_T4_v3` (1× T4 16 GB) ≈ **$0.53/hr** → **$4,000 ≈ 7,500 T4-hours.**
  Want 4-way parallelism: `NC64as_T4_v3` (4× T4) ≈ $4.35/hr (run 4 model jobs at once —
  T4s don't pool VRAM).
- **Limits:** 16 GB VRAM → eval models ≤ ~7B fp16 or ≤ ~13B quantized; 70B won't fit one
  T4 even in 4-bit. Turing (cc 7.5): use `attn_implementation="sdpa"`/`eager` (no FlashAttn-2).
- **Create the GPU target** (scale-to-zero, Spot), then **launch**:

  ```bash
  RG=my-rg WS=my-workspace ./srd_benchmark/setup_compute.sh   # makes azureml:t4-spot
  az ml job create -f srd_benchmark/job.yml -g my-rg -w my-workspace
  ```
  First time, you may need to request **`Standard NCASv3_T4 Family vCPUs`** quota for your
  region (Subscription → Usage + quotas; Spot quota is a separate line). No Spot quota?
  set `TIER=dedicated` in the setup script.
- **Output:** `outputs/srd_sweep_results.{json,md}` — perplexity per (model, scheme, bpw).

## Track 2 — Always-on demo host (`demo/`)

Containerizes the Governed Agent demo and deploys it to Azure Container Apps, with
**Azure OpenAI** as the ungoverned-foil backend (so the whole demo runs on credits).

- **Cost:** Container Apps scale-to-low + Azure OpenAI usage ≈ $50–100/mo → a year on ~$1k.
- **Deploy:** `./demo/deploy.sh` (az CLI; builds from `demo/Dockerfile`).
- **Backends:** `ANTHROPIC_API_KEY` drives the governed Claude scenarios; the ungoverned
  scenario is pointed at Azure OpenAI via `AXIOM_OPEN_BASE_URL` / `AXIOM_OPEN_API_KEY` /
  `AXIOM_OPEN_MODEL` (env-overridable, default NIM — no code change needed).

## Prereqs

```bash
az login
az account set --subscription "<your-subscription>"
# Track 1 also needs: az extension add -n ml ; an Azure ML workspace + A100 Spot quota
# Track 2 also needs: az extension add -n containerapp ; an Azure OpenAI resource + deployment
```

> Spend note: A100 Spot nodes can be evicted — the sweep writes per-model results
> incrementally so an eviction loses at most the in-flight model, not the run.
