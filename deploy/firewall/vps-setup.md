# Single-VPS deploy walkthrough

> Quickest path to a public `https://firewall.orivael.dev`.
> ~30 minutes once DNS propagates. ~$5-12/mo recurring.
> For production-grade HA see the AWS Fargate path in `README.md`.

## What you get

- One Linux box running the Firewall behind Caddy
- Auto-TLS via Let's Encrypt
- Tenant SQLite files in a Docker volume on the host
- Free-tier-only by default (Stripe billing wires in optionally)

## Prerequisites

- A registered domain (this guide assumes `orivael.dev`)
- DNS control — Cloudflare, Route 53, Namecheap, etc.
- A VPS provider account (Hetzner, DigitalOcean, Vultr, Linode all work)
- Your laptop with `ssh`, `curl`, `openssl`

## Steps

### 1. Provision a VPS

Any of these — pick one. CPU/RAM is way more than soft-launch needs;
the bottleneck is bandwidth.

| Provider | Recommended size | $/mo |
|---|---|---|
| Hetzner Cloud | CX21 (2 vCPU / 4 GB / 40 GB) | ~$6 |
| DigitalOcean | Basic 2 GB droplet | $12 |
| Vultr | Cloud Compute 2 GB | $12 |

Use **Ubuntu 24.04 LTS**. Capture the root password / SSH key.

### 2. Point DNS

```
A    firewall.orivael.dev    <YOUR_VPS_IP>    TTL 300
```

Optional (for the docs site later):
```
A    docs.orivael.dev        <YOUR_VPS_IP>    TTL 300
```

Wait 2-5 minutes, verify:

```bash
dig +short firewall.orivael.dev
# Should print your VPS IP.
```

### 3. Install Docker

```bash
ssh root@<VPS_IP>
apt update && apt install -y docker.io docker-compose-plugin git python3 python3-pip

# Open firewall (if the OS-level one is on).
ufw allow 22 80 443 && ufw --force enable || true
```

If your VPS provider has a SEPARATE cloud firewall (DigitalOcean,
Hetzner, AWS Security Groups), open 80 + 443 + 22 there too.

### 4. Clone the repo + generate secrets

```bash
cd /opt
git clone https://github.com/Orivael-Dev/axiom.git
cd axiom

# Two independent 32-byte secrets.
openssl rand -hex 32          # → AXIOM_MASTER_KEY
openssl rand -hex 32          # → AXIOM_FIREWALL_SESSION_SECRET
```

**Save AXIOM_MASTER_KEY in a password manager.** Losing it means
every verdict you've ever signed becomes unverifiable.

### 5. Configure environment

```bash
cp deploy/firewall/.env.example deploy/firewall/.env
$EDITOR deploy/firewall/.env
```

Fill in the required fields. Stripe-related vars stay empty for now:

```bash
AXIOM_MASTER_KEY=<first hex string>
AXIOM_FIREWALL_SESSION_SECRET=<second hex string>
AXIOM_FIREWALL_PUBLIC_URL=https://firewall.orivael.dev
AXIOM_FIREWALL_TENANT_DIR=/data/tenants
FIREWALL_HOST=firewall.orivael.dev

# Free-tier only — leave blank:
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_INDIE=
STRIPE_PRICE_TEAM=
STRIPE_METER_INDIE=
STRIPE_METER_TEAM=
```

### 6. Re-sign the first-party packs with YOUR key

The packs in `packs/` are signed with the repo's test key. Your deploy
uses a different `AXIOM_MASTER_KEY`, so the signatures won't verify.
Re-sign:

```bash
AXIOM_MASTER_KEY=<your_master_key> python3 scripts/sign_packs.py
# 9 packs signed.
```

**Critical** — if you skip this, every `/dashboard/packs/install`
returns 400 "invalid signature".

### 7. Bring up the stack

```bash
cd deploy/firewall
docker compose --profile tls up -d --build
```

What runs:
- `firewall` container — FastAPI app on internal port 8004
- `caddy` container — reverse proxy on host ports 80 + 443

Caddy auto-provisions a Let's Encrypt cert on first request to the
domain. First request takes ~30 seconds while ACME runs; cached
afterwards.

Watch the first startup:

```bash
docker compose --profile tls logs -f

# You should see:
#   axiom-firewall starting
#     brand:           orivael.dev
#     tenant dir:      /data/tenants
#     billing enabled: False
#   certificate obtained successfully
```

### 8. Verify

```bash
# From your laptop:
curl https://firewall.orivael.dev/healthz
# {"status":"ok"}

curl https://firewall.orivael.dev/readyz
# {"status":"ready"}

# Sign up flow:
open https://firewall.orivael.dev
```

Create an account, create a key, hit `/v1/guard/check` once. Done.

## Post-launch

### Daily backups

The tenant SQLite files live in the `firewall-data` Docker volume.
Losing this volume = losing all customer data. Cron a backup off-box:

```bash
# On the VPS, /etc/cron.daily/axiom-backup
#!/bin/bash
docker run --rm -v firewall-data:/data alpine \
    tar czf - /data \
    | ssh backup@<another-host> "cat > /backups/axiom-$(date +%F).tar.gz"
chmod +x /etc/cron.daily/axiom-backup
```

Or push to S3/B2 with `rclone copy`. Verify the restore path quarterly.

### Stripe billing (when you're ready)

```bash
# Test mode first — sanity check the product setup.
STRIPE_SECRET_KEY=sk_test_... python3 scripts/stripe_setup.py

# Then live mode.
STRIPE_SECRET_KEY=sk_live_... python3 scripts/stripe_setup.py
# Copy the printed env-var lines into deploy/firewall/.env.

# Create webhook at https://dashboard.stripe.com/webhooks
#   URL:    https://firewall.orivael.dev/billing/webhook
#   Events: customer.subscription.created, updated, deleted
# Copy the signing secret into STRIPE_WEBHOOK_SECRET in .env.

# Reload.
cd deploy/firewall
docker compose --profile tls up -d
```

### Status monitoring

UptimeRobot's free tier pings `/healthz` every 5 minutes and emails
on failure. Or set up CloudWatch / Better Stack / your-preferred tool.

The Firewall already logs structured to stdout — `docker compose logs`
or pipe to a log aggregator if you have one.

### Updates

```bash
cd /opt/axiom
git pull
cd deploy/firewall
docker compose --profile tls up -d --build
```

Schema migrations are forward-additive (only `ALTER TABLE ADD COLUMN`)
so rolling back is safe through Phase 3.

## When to migrate to AWS

The single-VPS path holds up to ~10K paid users. Migrate when:

- A single tenant exceeds 100M decision events (Phase 1 Decision §3 trigger)
- You need HA across availability zones
- An enterprise customer asks for an SLA you can't deliver on one box
- You need HIPAA-BAA-eligible infrastructure (AWS BAA only applies to
  HIPAA-eligible services)

Migration path: tar the `firewall-data` volume, restore to EFS in
AWS, deploy via `deploy/firewall/cloudformation.yaml`. Tenant SQLite
files port directly with no schema changes.

## Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Cert pending forever | DNS not propagated, OR port 80 blocked | `curl http://firewall.orivael.dev/` — should see Caddy redirect |
| 502 Bad Gateway | Firewall container not running | `docker compose ps` then `docker compose logs firewall` |
| Pack install: invalid signature | Skipped step 6 | Re-sign with your master key |
| Browser shows "Not Secure" | First request still in ACME exchange | Wait 30s, hard reload |
| Container restarts every minute | `AXIOM_MASTER_KEY` empty in `.env` | Check `docker compose logs firewall` |
| Quota counter wrong | Time zone confusion — quota is UTC-aligned | Check the host's clock (`timedatectl`) |

## Cost ceiling

| Item | Monthly cost |
|---|---|
| VPS (Hetzner CX21) | $6 |
| Domain (orivael.dev annual / 12) | ~$2 |
| Stripe per-call fees | 2.9% + $0.30 per charge, no overhead per call |
| Let's Encrypt cert | $0 |
| **Total at zero customers** | **~$8/mo** |

The path scales economically — at $49/mo Indie tier, your first paying
customer covers infrastructure for six months.
