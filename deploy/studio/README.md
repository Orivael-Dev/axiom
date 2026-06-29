# AXIOM Studio deploy (`lab.orivael.dev`)

Runs the full Streamlit `ui.py` (8 playgrounds: Prompt Evolution, AXIOM DSL, Growth,
Exoskeleton, Audio, Dev Agent, Medical, Twitter) as a container on the Hetzner box,
reverse-proxied by Caddy on the `axiom-net` network. Prompt Evolution includes
**Experience-RAG prompt memory** (`axiom_constitutional/prompt_memory.py`).

## Prerequisites (on the box)
- Base image `orivael/axiom-research:local`.
- Docker network `axiom-net`.
- Env file `/opt/web_ui_build/web.env`:
  ```
  AXIOM_MASTER_KEY=<hex>
  NVIDIA_API_KEY=nvapi-...
  NIM_API_KEY=nvapi-...
  AXIOM_MODEL=meta/llama-3.3-70b-instruct
  ```

## Build
From a checkout containing the full `ui.py` **and** `axiom_constitutional/prompt_memory.py`
(this branch, or `main` after merge):
```bash
docker build -f deploy/studio/Dockerfile -t axiom-studio:local .
```

## Run (with persistent prompt memory + logs)
```bash
docker rm -f axiom-studio 2>/dev/null
docker run -d --name axiom-studio --restart unless-stopped --network axiom-net \
  --no-healthcheck --env-file /opt/web_ui_build/web.env \
  -e AXIOM_MODEL=meta/llama-3.3-70b-instruct \
  -e AXIOM_PROMPTS_DIR=/data/prompts -e AXIOM_LOGS_DIR=/data/logs \
  -v axiom-studio-data:/data \
  axiom-studio:local
```
- `--no-healthcheck` disables the base image's research-app healthcheck (wrong port for Streamlit).
- The **`axiom-studio-data` volume** + `AXIOM_PROMPTS_DIR`/`AXIOM_LOGS_DIR` keep prompt memory
  (`/data/prompts/_memory/prompt_memory.db`) and Growth logs across restarts, so the RAG
  memory **compounds** instead of resetting.

Verify:
```bash
docker exec axiom-studio python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=4).read())"
docker exec axiom-studio python3 -c "from axiom_constitutional import prompt_memory;print('remembered:',prompt_memory.stats())"
```

## Caddy block (`deploy/firewall/Caddyfile`)
```
lab.orivael.dev {
    encode gzip
    reverse_proxy axiom-studio:8501 {
        header_up X-Forwarded-Proto https
        header_up X-Accel-Buffering no
        flush_interval -1
        transport http { response_header_timeout 0s; read_timeout 0s }
    }
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }
    log { output stdout
          format json }
}
```
`flush_interval -1` + zero timeouts keep Streamlit's WebSocket (`/_stcore/stream`) alive.
Reload: `docker exec axiom-caddy caddy reload --config /etc/caddy/Caddyfile`.

## DNS
Namecheap A record `lab` → `178.156.205.89`.

## Notes
- All 8 tabs import cleanly; some playgrounds need their runtime backends/keys to fully
  function (Twitter API, medical container, audio input).
- **Prompt memory:** the Prompt Evolution sidebar has a "Prompt Memory (RAG)" toggle +
  min-score floor. It warm-starts the Worker from the best prompt of a similar past task
  and remembers every iteration — learning compounds across runs (persisted on the volume).
