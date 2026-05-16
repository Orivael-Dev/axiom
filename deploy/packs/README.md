# Deploying the Skill Pack Registry

The registry is a separate FastAPI service from the Firewall
dashboard. It serves signed first-party packs over HTTP at
`packs.orivael.dev`.

## Why separate

- **Different blast radius.** A registry compromise affects pack
  discovery, not customer verdicts.
- **Different scaling.** Pack downloads are infrequent and cacheable
  behind CloudFront; verdict traffic is constant.
- **Different ops cadence.** Adding a new pack is a fast image rebuild;
  Firewall changes are far rarer.

## Quick local run

```bash
docker build -t orivael/axiom-packs:local \
    -f deploy/packs/Dockerfile .

docker run -p 8005:8005 orivael/axiom-packs:local

# Verify
curl http://localhost:8005/v1/packs | jq
curl http://localhost:8005/v1/packs/fdcpa | jq
```

## Production deploy (AWS)

Mirrors the Firewall pattern: ECR image, ECS Fargate, ALB, ACM cert,
Route53 record for `packs.orivael.dev`. The CloudFormation template
isn't pre-written for the registry (yet) because it's substantially
simpler — no Secrets Manager, no EFS, no metered billing. A two-task
service behind an ALB with the cert is enough.

Recommended setup:
- Front with CloudFront. Cache `/v1/packs*` for 5 minutes — pack
  releases are infrequent and cache invalidation is one-shot per
  release.
- One ECS service, 2 tasks, `t3.micro`-equivalent (256 CPU / 512 MB).

## Adding a new pack to the registry

1. Drop the directory under `packs/<name>/pack.json`.
2. Sign with `AXIOM_MASTER_KEY=<prod-key> python scripts/sign_packs.py`.
3. Rebuild + push the registry image.
4. Force a new ECS deployment.

CloudFront invalidation:

```bash
aws cloudfront create-invalidation \
    --distribution-id <id> --paths "/v1/packs*"
```

## API surface (v1)

| Endpoint | Cacheable | Description |
|---|---|---|
| `GET /v1/packs` | 5 min | Index of all packs (metadata only, no policy bodies) |
| `GET /v1/packs/{name}` | 5 min | Full manifest of the latest version |
| `GET /v1/packs/{name}/{version}` | forever | Full manifest of an exact version |
| `GET /healthz` | no | Liveness |
| `GET /readyz` | no | Readiness (verifies `packs/` exists) |

Every response includes `X-Request-ID` for tracing.

CORS is open (`*`) by default — anyone can browse the registry.
Restrict with `AXIOM_PACKS_CORS_ORIGINS` if you want to lock it down.

## Future (Phase 2 weeks 7–8)

- **Publishing API** (`POST /v1/packs`) — currently the registry is
  read-only. Third-party publishing requires:
  - Publisher key issuance (AWS KMS)
  - Per-publisher namespace (`@publisher/pack-name`)
  - Pack-review workflow
- **Pack pinning** — let tenants pin to `fdcpa@>=0.1,<0.2` semver
  ranges and get automatic updates within the range.
- **Smithery.ai discovery** — list the MCP server + registry on
  Smithery for one-click developer adoption.
