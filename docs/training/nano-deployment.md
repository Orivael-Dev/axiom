# Training manual — Jetson Orin Nano deployment

> The hardware reference platform for **Sovereign Box** SKU
> conversations. 8 GB unified memory, NVIDIA Ampere GPU, ARM64
> running JetPack 6 (Ubuntu 22.04 base). Runs the full AXIOM stack
> — Firewall, Research Engine, Dev-Agent Coder, kid-audit toolchain
> — locally with `qwen2.5:1.5b` via dustynv/ollama.

## Why this hardware

| Property | Orin Nano | Equivalent cloud |
|---|---|---|
| Unified memory | 8 GB shared CPU+GPU | n/a |
| Ampere GPU | 32 Tensor Cores @ ~70 TOPS (INT8) | A10G partial |
| Power | 7-15 W under load | dedicated rack |
| Cost (hardware) | ~$500 | n/a |
| Cost per generation (qwen2.5:1.5b) | **~$0** | ~$0.001-0.003 (hosted) |
| Network | Tailscale-reachable, LAN-reachable, or air-gapped | always-online |

The on-device LLM is the Sovereign Box's headline cost number. A
toy company doing 10K audit re-runs/month pays $0 on the Nano vs
$30-90 on hosted Haiku. Over a 36-month deal that's >$1K saved
per box.

## Reference stack on the Nano

| Layer | What runs |
|---|---|
| OS | Ubuntu 22.04 (JetPack 6) |
| Container runtime | Docker + `nvidia-container-toolkit` |
| LLM | `dustynv/ollama:r36.4.0` — CUDA-accelerated Ollama, model storage on NVMe |
| Model | `qwen2.5:1.5b` (~1 GB on disk, ~1.5 GB working) — fits comfortably with browser+GNOME open |
| Storage | 465 GB NVMe at `/mnt/nvme` — models, swap, logs |
| Swap | 16 GB swapfile at `/mnt/nvme/swapfile` + JetPack's default 3.7 GB zram |
| Reach | Tailscale tailnet IP for laptop-driven workflows |
| Software | The AXIOM repo on the working branch |

## Common workflows

### Workflow A: First-time Orin setup

```bash
# 1. Confirm JetPack version + L4T tag
cat /etc/nv_tegra_release | head -1     # R36 → tag r36.4.0

# 2. Install Docker + nvidia runtime
sudo apt-get update
sudo apt-get install -y docker.io nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
sudo usermod -aG docker $USER && newgrp docker

# 3. Move pulled Ollama models to NVMe (avoids eMMC wear)
mkdir -p /mnt/nvme/ollama
docker pull dustynv/ollama:r36.4.0
docker run -d \
  --runtime nvidia --network host --restart unless-stopped \
  --memory-swap -1 --shm-size 2g \
  -e OLLAMA_KEEP_ALIVE=30s \
  -e OLLAMA_MAX_LOADED_MODELS=1 \
  -e OLLAMA_NUM_PARALLEL=1 \
  -v /mnt/nvme/ollama:/root/.ollama \
  --name ollama \
  dustynv/ollama:r36.4.0

docker exec -it ollama ollama pull qwen2.5:1.5b

# 4. 16 GB swap on NVMe
sudo fallocate -l 16G /mnt/nvme/swapfile
sudo chmod 600 /mnt/nvme/swapfile
sudo mkswap /mnt/nvme/swapfile
sudo swapon /mnt/nvme/swapfile
echo '/mnt/nvme/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl --system

# 5. AXIOM repo + env
cd ~ && git clone https://github.com/Orivael-Dev/axiom.git
cd axiom
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: AXIOM_MASTER_KEY, OLLAMA_URL=http://localhost:11434,
# OLLAMA_MODEL=qwen2.5:1.5b
```

Then push the env to `~/.bashrc` so it survives reboots — see
the env-var section in [`dev-agent-coder.md`](dev-agent-coder.md).

### Workflow B: Confirm the GPU is being used

```bash
# In one terminal — should hover near 0% when idle
sudo tegrastats

# In another — fire a generation
curl -s http://localhost:11434/api/generate -d '{
  "model":"qwen2.5:1.5b","prompt":"say hi","stream":false,
  "options":{"num_ctx":1024,"num_predict":16}
}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('response',''))"

# GR3D_FREQ in tegrastats should spike >50% during the generation.
# If it stays 0%, the nvidia runtime isn't engaged — re-check
# `docker info | grep -i runtime`.
```

### Workflow C: Drive the Orin from a laptop

```bash
# On the laptop (~/.bashrc OR ~/.zshrc):
export AXIOM_MASTER_KEY="<same hex as the Orin>"
export OLLAMA_URL="http://orin.tailnet.ts.net:11434"
export OLLAMA_MODEL="qwen2.5:1.5b"
export AXIOM_RESEARCH_BACKEND="ollama"
export AXIOM_CODER_BACKEND="ollama"
```

Any AXIOM tool the laptop runs hits the Orin's Ollama transparently.
No code changes — the env vars + Tailscale + same master key are
the whole config.

### Workflow D: The Guard API + console from a phone

```bash
# On the Orin:
python3 -m uvicorn axiom_guard_api:app --host 0.0.0.0 --port 8001
```

Then on your phone (any browser):
```
http://<orin-ip>:8001/console
```

Same-origin: no file:// pain, no CORS, no Downloads-folder
shenanigans. The Research tab POSTs to `/research/run` which
internally uses `localhost:11434` — your phone never talks to
Ollama directly. localStorage persists settings across refreshes.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker: command not found` | curl-script Docker not installed | `sudo apt-get install -y docker.io nvidia-container-toolkit` |
| Container restart-loop, `docker exec` says "Restarting" | OOM-killed on startup OR wrong L4T tag | `docker logs ollama --tail 100`; check `dmesg \| grep -i oom`; verify tag matches `/etc/nv_tegra_release` |
| `GR3D_FREQ` stays 0% during generation | nvidia runtime not engaged | `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`; confirm via `docker info \| grep -i runtime` shows `nvidia` |
| OOM mid-generation | qwen2.5:1.5b + browser + GNOME exceeds 8 GB | Drop to `qwen2.5:1.5b` (~1.5 GB) or `phi3:mini`; shut GNOME with `sudo systemctl set-default multi-user.target`; cap `num_ctx` to 1024 |
| Connection refused on `:11434` | Container down | `docker ps --filter name=ollama`, `docker start ollama` |
| Console can't reach Orin | UFW firewall on | `sudo ufw allow 8001/tcp` |
| Different IP every reboot | DHCP lease change | Use Tailscale tailnet name (`orin.tailnet.ts.net`) instead of IP |
| Models lost after `docker rm ollama` | `~/.ollama` was the default, not NVMe | Always bind-mount `-v /mnt/nvme/ollama:/root/.ollama`; if lost, `ollama pull qwen2.5:1.5b` re-downloads |

## What the Nano can run (concurrent)

| Service | RAM (idle) | RAM (active) |
|---|---:|---:|
| dustynv/ollama with qwen2.5:1.5b loaded | ~200 MB | ~1.5-2 GB during generation |
| `axiom_guard_api` (FastAPI + uvicorn) | ~150 MB | ~300 MB under load |
| `axiom_packs.server` (registry) | ~120 MB | ~200 MB |
| GNOME + Firefox with 1 tab open | ~1.5 GB | ~2.5 GB |
| Total comfortable | ~2-3 GB | ~5 GB |
| Available headroom (8 GB Nano) | 5-6 GB | 3 GB + swap |

Sovereign-Box headless config (no GNOME, no browser) gets ~6 GB
back — plenty of room for a 7B model if a customer wants the
upgrade (qwen2.5:7b lives at ~5 GB working).

## Hardware ladder for Sovereign Box conversations

| Tier | Hardware | Model | Use case |
|---|---|---|---|
| Lite | Orin Nano 8GB | qwen2.5:1.5b | dev agent + research; kid-audit |
| Standard | Orin Nano Super 8GB | qwen2.5:3b | same + faster, larger context |
| Pro | Orin AGX 64GB | qwen2.5:14b or llama3.3-70b-instruct (quantized) | Firewall + research at production capacity |

All three run the same AXIOM repo, the same code paths. Customers
upgrade hardware without changing config — just the model name.

## House rules for support + sales

- **The Sovereign Box pitch is privacy + economics together.** Don't
  lead with privacy alone (cloud LLMs have HIPAA endpoints now).
  Lead with the dual: "your data never leaves AND you pay zero per
  query." The cost angle wins the procurement meeting.
- **dustynv/ollama, not curl-script Ollama.** The native curl install
  on Jetson runs CPU-only. The dustynv image uses CUDA. Performance
  delta is ~10x. Memory headroom is much better. **Never
  recommend the curl install on Jetson.**
- **NVMe is mandatory, not optional.** eMMC has ~10-100 TB
  write-endurance; daily model + log writes burn through it.
  Customer hardware spec should call out an NVMe SSD as a
  prerequisite.
- **Tailscale is the right reach plan.** LAN works for office
  deployments; Tailscale handles roaming + WFH + NAT without
  punching the customer's firewall. Don't sell port-forwarding.

## Further reading

- [`docs/NANO_DEV_AGENT.md`](../NANO_DEV_AGENT.md) — public deployment doc
- [`dev-agent-coder.md`](dev-agent-coder.md) — the coder that runs on the Nano
- [`research-engine.md`](research-engine.md) — research engine, also Ollama-backed
- [`SETUP.txt`](../../SETUP.txt) — entry-point quickstart
- [Jetson AI Lab — jetson-containers](https://github.com/dusty-nv/jetson-containers) — where `dustynv/ollama` comes from
