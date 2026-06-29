# AXIOM Studio deploy (`lab.orivael.dev`)

Runs the full Streamlit `ui.py` (8 playgrounds: Prompt Evolution, AXIOM DSL, Growth,
Exoskeleton, Audio, Dev Agent, Medical, Twitter) as a container on the Hetzner box,
reverse-proxied by Caddy on the `axiom-net` network. Prompt Evolution includes
**Experience-RAG prompt memory** (`axiom_constitutional/prompt_memory.py`) and
**Knowledge-RAG** (`axiom_constitutional/knowledge_rag.py`).

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
From a checkout with the full `ui.py` + `axiom_constitutional/{prompt_memory,knowledge_rag}.py`
(`main`). **Use `--no-cache`** so a changed pip line (e.g. adding `rich`) actually installs:
```bash
docker build --no-cache -f deploy/studio/Dockerfile -t axiom-studio:local .
```

## Run (with persistent prompt memory + logs)
```bash
docker rm -f axiom-studio 2>/dev/null
docker run -d --name axiom-studio --restart unless-stopped --network axiom-net \
  --no-healthcheck --env-file /opt/web_ui_build/web.env \
  -e AXIOM_MODEL=meta/llama-3.3-70b-instruct \
  -e AXIOM_BACKEND=nim \
  -e AXIOM_PROMPTS_DIR=/persist/prompts -e AXIOM_LOGS_DIR=/persist/logs \
  -v axiom-studio-persist:/persist \
  axiom-studio:local
```
- `--no-healthcheck` disables the base image's research-app healthcheck (wrong port for Streamlit).
- **`AXIOM_BACKEND=nim`** — without it the UI defaults to `local,nim` and tries Ollama on
  `localhost:11434` (not in the container), so the rubric's first model call fails to connect.
  (`AXIOM_BACKEND=nim` is also in `web.env` as a backstop.)
- **Mount persistence at `/persist`, NOT `/data`.** The base image sets `HOME=/data` and pip
  installs packages under `/data/.local/...`; a volume mounted at `/data` would mask those
  packages with a stale copy (this is what caused `ModuleNotFoundError: rich`). Keep the
  persistence volume off `/data`.
- `AXIOM_PROMPTS_DIR`/`AXIOM_LOGS_DIR` put the RAG prompt-memory db
  (`/persist/prompts/_memory/prompt_memory.db`) + Growth logs on the volume, so memory
  **compounds** across restarts. The Knowledge-RAG index rebuilds from the in-image corpus
  on first use (also cached under `/persist/prompts/_knowledge`).

Verify:
```bash
docker exec axiom-studio python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=4).read())"
docker exec axiom-studio python3 -c "import rich, importlib.metadata as m; from axiom_constitutional import prompt_memory, knowledge_rag; print('rich', m.version('rich'), '| remembered:', prompt_memory.stats())"
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
- **Prompt memory (Experience RAG):** sidebar "Prompt Memory (RAG)" toggle + min-score floor.
  Warm-starts the Worker from the best prompt of a similar past task; remembers every iteration.
- **Knowledge RAG:** sidebar "Knowledge RAG (AXIOM docs)" toggle. Retrieves AXIOM spec/docs/
  `.axiom` examples and grounds the Worker — so it can answer "how to build an AXIOM agent".

## Gotchas (learned the hard way)
1. **`rich` is required** (core dep of `evolution.py`/`meta_evolution.py`) — in the pip line.
2. **Build with `--no-cache`** when changing the pip line, or the dep won't actually install.
3. **Persistence volume must NOT mount `/data`** — it masks `/data/.local` site-packages.
4. **`AXIOM_BACKEND=nim`** must be set, or Prompt Evolution tries a non-existent local Ollama.
5. **`/persist` must be writable by uid 999 (`axiom`).** The container runs as non-root; a fresh
   named volume mounts root-owned, so the app hits `PermissionError` on `LOGS_DIR.mkdir` /
   prompt-memory writes. The Dockerfile `chown`s `/persist` to 999 so a fresh volume inherits it;
   for an already-created root-owned volume: `docker exec -u 0 axiom-studio chown -R 999:999 /persist`.
