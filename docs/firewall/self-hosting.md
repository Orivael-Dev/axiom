# Self-hosting

The Axiom Intent Firewall runs as a single FastAPI app. You can host
it anywhere you can run Python 3.11+ or a Docker container.

This guide is the short version. For the prod-grade AWS deploy, see
[`deploy/firewall/README.md`](../../deploy/firewall/README.md).

## Minimum runtime

- Python 3.11+ (3.10 works but isn't tested)
- 256 MB RAM, 1 vCPU per replica
- A persistent volume for tenant SQLite files
- A way to terminate TLS (Caddy, nginx, Traefik, ALB, Cloudflare
  Tunnel — your choice)

## Run with Docker

```bash
docker build -t orivael/axiom-firewall:local \
    -f deploy/firewall/Dockerfile .

docker run -d --name firewall \
    -p 8004:8004 \
    -e AXIOM_MASTER_KEY="$(openssl rand -hex 32)" \
    -e AXIOM_FIREWALL_SESSION_SECRET="$(openssl rand -hex 32)" \
    -v /var/lib/axiom-firewall:/data/tenants \
    --restart unless-stopped \
    orivael/axiom-firewall:local
```

Hit <http://localhost:8004/healthz> to verify.

## Run with docker-compose + Caddy (auto TLS)

See `deploy/firewall/docker-compose.yml` and `deploy/firewall/Caddyfile`.

```bash
cd deploy/firewall
cp .env.example .env
# fill in AXIOM_MASTER_KEY, AXIOM_FIREWALL_SESSION_SECRET, FIREWALL_HOST
docker compose --profile tls up -d
```

## Run from source

```bash
git clone https://github.com/Orivael-Dev/axiom
cd axiom
pip install fastapi uvicorn jinja2 python-multipart itsdangerous

export AXIOM_MASTER_KEY=$(openssl rand -hex 32)
export AXIOM_FIREWALL_SESSION_SECRET=$(openssl rand -hex 32)

uvicorn axiom_firewall.dashboard:app \
    --host 0.0.0.0 --port 8004 \
    --workers 2 --proxy-headers --forwarded-allow-ips='*'
```

## Required environment

| Variable | Required | Purpose |
|---|---|---|
| `AXIOM_MASTER_KEY` | yes | HMAC root for signing verdicts. 64 hex chars. |
| `AXIOM_FIREWALL_SESSION_SECRET` | yes | Cookie signing key. 64+ chars. |
| `AXIOM_FIREWALL_TENANT_DIR` | no | Where to keep tenant SQLite files. Default `tenants`. Mount this to a persistent volume. |
| `AXIOM_FIREWALL_PUBLIC_URL` | no | The URL the dashboard is served at. Used for Stripe redirects. |
| `AXIOM_FIREWALL_CORS_ORIGINS` | no | Comma-separated origins permitted to call `/v1/guard/check` from a browser. Empty = server-side only. |

## Optional: enable Stripe billing

Run [`scripts/stripe_setup.py`](#) with your test- or live-mode secret
key. It prints the env vars to set:

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...   # from the webhook endpoint you create
STRIPE_PRICE_INDIE=price_...
STRIPE_PRICE_TEAM=price_...
STRIPE_METER_INDIE=axiom_firewall_indie
STRIPE_METER_TEAM=axiom_firewall_team
```

If you leave Stripe unset, the Firewall runs in free-tier-only mode:
all paid-tier routes return 503, free tier signup + verdict path work.

## Behind a reverse proxy

The Firewall trusts `X-Forwarded-*` headers when started with
`--proxy-headers --forwarded-allow-ips='*'`. Pass through:

| Header | What it's used for |
|---|---|
| `X-Forwarded-For` | Client IP (for per-IP signup rate limit). |
| `X-Forwarded-Proto` | Required so redirects use `https://`. |
| `X-Forwarded-Host` | Optional; only used for log output. |

Restrict `--forwarded-allow-ips` to your proxy's CIDR when the upstream
is on a known network.

## Health endpoints

| Path | Purpose | Code |
|---|---|---|
| `/healthz` | Liveness — process is up | 200 |
| `/readyz`  | Readiness — DB writable    | 200 / 503 |

## Backups

Tenant SQLite files are self-contained. Back up `$AXIOM_FIREWALL_TENANT_DIR`
on the cadence your data-retention policy requires:

```bash
tar czf /backups/axiom-firewall-$(date +%Y%m%d).tar.gz \
    -C $AXIOM_FIREWALL_TENANT_DIR .
```

Restoring is a `tar xzf` into a new mount on a fresh deploy.

## Upgrades

The dashboard performs `ALTER TABLE ... ADD COLUMN` migrations on
startup automatically. Forward-only — no destructive migrations are
planned through Phase 3. Read the release notes before upgrading
across a major release.
