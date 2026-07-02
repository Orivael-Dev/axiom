# Deploying the governed-agent demo (Docker + Caddy, auto-HTTPS)

Brings the "type your own scenario" demo live on a fresh Azure VM (or any Linux host
with Docker) behind automatic HTTPS. Caddy terminates TLS and reverse-proxies to the
demo; the demo container never binds a public port.

## Prerequisites

1. **A Linux VM with Docker + the compose plugin.**
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
2. **DNS**: an `A` record for your demo domain (e.g. `demo.orivael.dev`) pointing at the
   VM's public IP. Caddy can't issue a certificate until this resolves.
3. **Open ports 80 and 443 inbound.** On Azure this is a Network Security Group rule:
   ```bash
   az network nsg rule create -g <rg> --nsg-name <nsg> -n allow-web \
     --priority 1000 --access Allow --protocol Tcp --direction Inbound \
     --destination-port-ranges 80 443 --source-address-prefixes '*'
   ```
   Port 80 is required for the ACME HTTP challenge, not just a redirect.
4. **An Anthropic API key** — the demo calls Claude to reason live.

## Bring it up

```bash
git clone https://github.com/Orivael-Dev/axiom.git
cd axiom/axiom_demo_governed_agent/deploy
cp .env.example .env
# edit .env: ANTHROPIC_API_KEY, DEMO_DOMAIN, ACME_EMAIL  (NVIDIA_API_KEY optional)
docker compose up -d --build
```

First start takes a few seconds while Caddy fetches the certificate. Then open
`https://<DEMO_DOMAIN>/`.

## Verify (do this before you post the launch link)

```bash
# from anywhere — runs 3 scenarios through the live model + guard and checks verdicts
python3 ../../scripts/smoke_demo.py --base https://<DEMO_DOMAIN>
```
A healthy run prints `3/3 scenarios passed`. If you see
`run errored — ANTHROPIC_API_KEY not set`, the key isn't reaching the container — fix
`.env` and `docker compose up -d` again. That missing key is the #1 launch failure.

## Operate

```bash
docker compose logs -f demo         # live app logs
docker compose logs -f caddy        # TLS / cert issuance
docker compose restart demo         # after changing .env
docker compose pull && docker compose up -d --build   # update to a new commit
docker compose down                 # stop everything
```

## Notes

- **Single worker, by design.** The demo holds each run's SSE state in memory, so the
  container runs one uvicorn worker. To serve more concurrent sessions, scale out with
  more VMs behind a load balancer using sticky sessions — not more workers on one host.
- **Tools are simulated.** No real emails/commands/transfers execute; the guard rules on
  the *attempt*. Safe to hand a public audience.
- **Costs.** Every custom run makes live Claude calls. For an unauthenticated public demo,
  watch your Anthropic spend and consider a rate limit / usage cap in front of it.
