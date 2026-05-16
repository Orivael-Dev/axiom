# Training manual — Skill Pack Registry

> Read-only HTTP service at `packs.orivael.dev`. Mirrors signed
> first-party Skill Pack manifests over a stable REST API so the
> Firewall (and any other consumer) can fetch them without filesystem
> access.

## What it is

A separate FastAPI app (`axiom_packs.server`) from the Firewall
dashboard. Three endpoints in v1:

- `GET /v1/packs` — index of all packs (metadata, no policy body)
- `GET /v1/packs/{name}` — full manifest of the latest version
- `GET /v1/packs/{name}/{version}` — full manifest of an exact version

Plus the standard `/healthz` + `/readyz`.

## Who it's for

- **Ops** running `packs.orivael.dev` (us, today).
- **Self-hosters** running a private internal registry.
- **Pack distributors** — third parties when publishing opens
  (Phase 2 week 7+).

## Why a separate service

- **Different blast radius.** A registry compromise affects pack
  discovery; it does NOT touch customer verdicts (signatures still
  catch it).
- **Different scaling shape.** Verdicts are constant-traffic; pack
  downloads are bursty + extremely cacheable behind CloudFront.
- **Different ops cadence.** Adding a pack = rebuild + ship the
  registry image. Firewall ships rarely.
- **Different security posture.** Registry is read-only and public;
  Firewall has tenant data.

## How it works

```
                  CloudFront (5-min cache on /v1/packs*)
                                │
                                ▼
                  ┌────────────────────────────────┐
                  │  ALB :443                       │
                  └──────────────┬─────────────────┘
                                 │ HTTP /8005
                                 ▼
                  ┌────────────────────────────────┐
                  │  axiom_packs.server (Fargate)  │
                  │                                 │
                  │  packs/<name>/pack.json        │
                  │           │                     │
                  │           ▼                     │
                  │  SkillPackManifest.parse()     │
                  │  verify_first_party()          │
                  │           │                     │
                  │  reject if NOT signed          │
                  │           │                     │
                  │           ▼                     │
                  │  return JSON                   │
                  └────────────────────────────────┘
```

Packs are baked into the Docker image at build time. **The image is
the source of truth for what's published.** No live database. No
admin UI. Adding a pack is a code change, a PR, a build, a deploy.

This deliberate constraint is a feature: it forces every pack release
through code review.

## Key concepts

### Image is the source of truth

```
$ ls packs/
code-review-base/  customer-support-base/  fdcpa/  gdpr-article-9/
hipaa-intake/  pci-dss/  coppa/  sec-rule-10b-5/
prompt-injection-strict/

$ docker build -f deploy/packs/Dockerfile -t orivael/axiom-packs:0.1.0 .
```

To publish a new pack:
1. Add `packs/<new-pack>/pack.json` to the repo
2. Sign it
3. Open a PR
4. After merge, CI builds the image
5. Operator deploys (or it auto-deploys via your GitOps pipeline)

There's no admin endpoint to add a pack at runtime. By design.

### Signature verification at SERVE time

Every request that loads a manifest from disk runs
`verify_first_party(manifest)` first. If it fails:
- The pack is silently OMITTED from the index
- A direct lookup returns 404

So even if a malicious image substitution slips in an unsigned pack,
the registry refuses to serve it.

### CloudFront fronting

`/v1/packs*` is cached for 5 minutes by CloudFront. Pack changes are
infrequent (weekly at most) so this halves the load on our origin
and gets pack downloads to the edge.

On a new pack release, invalidate the cache:

```bash
aws cloudfront create-invalidation \
    --distribution-id <id> --paths "/v1/packs*"
```

`/healthz` and `/readyz` are NOT cached.

### CORS open by default

The registry is meant to be browseable from anywhere. Default is
`Access-Control-Allow-Origin: *`. Lock down with
`AXIOM_PACKS_CORS_ORIGINS` if running an internal-only registry.

## Common workflows

### Workflow A: Add a new first-party pack

```bash
# 1. Add the pack to the repo
mkdir packs/my-new-pack/
$EDITOR packs/my-new-pack/pack.json

# 2. Sign with the master key
AXIOM_MASTER_KEY=<hex> python scripts/sign_packs.py packs/my-new-pack

# 3. Open a PR, get review, merge
# 4. CI builds + pushes the registry image
# 5. Deploy the new image
aws ecs update-service \
    --cluster axiom-packs --service axiom-packs \
    --force-new-deployment

# 6. Invalidate CloudFront so customers see it within 5 min
aws cloudfront create-invalidation \
    --distribution-id <id> --paths "/v1/packs*"
```

### Workflow B: Verify the registry is healthy

```bash
curl https://packs.orivael.dev/healthz
# {"status":"ok"}

curl https://packs.orivael.dev/readyz
# {"status":"ready","pack_count":9}

curl https://packs.orivael.dev/v1/packs | jq '.packs | length'
# 9
```

If `pack_count` drops from 9 to fewer than 9, a pack failed
signature verification (probably someone edited a file but forgot
to re-sign). Logs will show:

```
WARNING:    rejecting unsigned pack /app/packs/foo/pack.json
```

### Workflow C: Stand up a private registry

```bash
# Self-host with your own pack set
docker run -p 8005:8005 \
    -v /path/to/your/packs:/app/packs:ro \
    -e AXIOM_MASTER_KEY=<your-key> \
    orivael/axiom-packs:0.1.0

# Point a Firewall at it
docker run ... \
    -e AXIOM_FIREWALL_REGISTRY_URL=https://internal-registry.example.com \
    orivael/axiom-firewall:0.1.0
```

### Workflow D: Roll back a pack version

Pre-versioned layout means: keep BOTH `packs/<name>/pack.json` (the
latest) and `packs/<name>/<old-version>/pack.json` for any version
customers are still pinned to.

```bash
# Roll back the "latest" pointer
cp packs/fdcpa/0.1.0/pack.json packs/fdcpa/pack.json

# Bump the version field if you don't want to overwrite 0.2.0's slot
$EDITOR packs/fdcpa/pack.json  # set version: "0.1.1"
python scripts/sign_packs.py packs/fdcpa
```

Then redeploy + invalidate cache.

## Test scenarios

| # | Scenario | Expected |
|---|---|---|
| 1 | `GET /healthz` | 200 `{"status":"ok"}` |
| 2 | `GET /readyz` with packs dir | 200 `{"status":"ready","pack_count":N}` |
| 3 | `GET /readyz` without packs dir | 503 `{"status":"unready",...}` |
| 4 | `GET /v1/packs` empty registry | 200 `{"packs":[]}` |
| 5 | `GET /v1/packs` with 9 signed packs | 200 with all 9 in alphabetical order |
| 6 | `GET /v1/packs/fdcpa` | 200 with full manifest including policy |
| 7 | `GET /v1/packs/fdcpa/0.1.0` | 200, version matches |
| 8 | `GET /v1/packs/fdcpa/9.9.9` | 404 |
| 9 | `GET /v1/packs/does-not-exist` | 404 |
| 10 | Pack without `signature` field | NOT served (index skips it, 404 on direct lookup) |
| 11 | Pack signed with wrong key | NOT served |
| 12 | `GET /v1/packs/..%2Fetc` | 4xx (no filesystem escape) |
| 13 | `X-Request-ID: abc` echoed on response | yes |
| 14 | OPTIONS preflight from any origin | `Access-Control-Allow-Origin: *` |

All covered by `tests/test_axiom_packs_server.py` (15 tests).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/readyz` returns 503 "packs dir not found" | Image missing `packs/` or env var wrong | Check `AXIOM_PACKS_DIR`; rebuild image |
| `pack_count` lower than expected | One or more packs failing signature verification | Check logs: "rejecting unsigned pack ..."; re-sign with the right key |
| Pack works locally but missing in prod | Signed with dev master key, prod uses different key | Re-sign with `AXIOM_MASTER_KEY=<prod>` before pushing image |
| CloudFront serving stale pack | Cache hit | Invalidate `/v1/packs*` |
| ALB target unhealthy | `/healthz` failing | Exec into task, check `/app/packs/`, check disk space |

## Limitations / what's not here yet

- **Read-only.** No `POST /v1/packs`. Adding a pack requires a build.
  Phase 2 week 7+ introduces publishing.
- **No publisher namespaces.** All packs are flat `<name>`. Future:
  `@publisher/pack-name`.
- **No semver range resolution.** Customer asking for `fdcpa@>=0.1`
  must pick a concrete version. Future: latest-matching resolution.
- **No analytics.** No download counts surfaced today. CloudWatch
  request logs have the raw data; we'll surface it when growth
  warrants.

## Further reading

- Server source: `axiom_packs/server.py`
- Dockerfile: `deploy/packs/Dockerfile`
- Deployment guide: `deploy/packs/README.md`
- Client (the Firewall's consumer side): `axiom_firewall/registry_client.py`
