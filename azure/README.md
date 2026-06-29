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
single A100 80GB Spot node and collects a results table.

- **SKU:** `Standard_NC24ads_A100_v4` (1× A100 80GB), **Spot** (~$1/hr vs ~$3.7 on-demand).
  $4,000 of Spot ≈ thousands of A100-hours — a large sweep with headroom.
- **Launch:** `az ml job create -f srd_benchmark/job.yml` (after `az ml workspace` is set).
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
