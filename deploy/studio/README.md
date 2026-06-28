# AXIOM Studio deploy (`lab.orivael.dev`)

Runs the full Streamlit `ui.py` (all 8 playgrounds: Prompt Evolution, AXIOM DSL,
Growth, Exoskeleton, Audio, Dev Agent, Medical, Twitter) as a container on the
Hetzner box, reverse-proxied by Caddy on the `axiom-net` network.

## Prerequisites (on the box)
- Base image `orivael/axiom-research:local` present (`docker images | grep axiom-research`).
- Docker network `axiom-net` (the one Caddy resolves upstreams on).
- An env file with the keys, e.g. `/opt/web_ui_build/web.env`:
  ```
  AXIOM_MASTER_KEY=<hex>
  NVIDIA_API_KEY=nvapi-...
  NIM_API_KEY=nvapi-...
  AXIOM_MODEL=meta/llama-3.3-70b-instruct
  ```

## Build
From a checkout containing the **full** `ui.py` (branch `claude/ui-full-playgrounds`,
or `main` after merge):
```bash
docker build -f deploy/studio/Dockerfile -t axiom-studio:local .
```

## Run
```bash
docker rm -f axiom-studio 2>/dev/null
docker run -d --name axiom-studio --restart unless-stopped --network axiom-net \
  --no-healthcheck --env-file /opt/web_ui_build/web.env \
  axiom-studio:local
```
`--no-healthcheck` disables the base image's research-app healthcheck (wrong port for
Streamlit). The Dockerfile `CMD` already starts Streamlit on `:8501`.

Verify: `docker exec axiom-studio python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=4).read())"`

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
Namecheap A record `lab` → `178.156.205.89` (the `*.orivael.dev` wildcard points off-box,
so each subdomain needs its own A record).

## Notes
- All 8 tabs import cleanly; some playgrounds need their runtime backends/keys to fully
  function (Twitter API, medical container, audio input). Imports succeed regardless.
- This replaced the earlier FastAPI `web_ui.py` deploy at `lab.orivael.dev` — to make the
  online version identical to the device's Streamlit `ui.py`.
