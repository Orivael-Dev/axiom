# Deploying the Axiom Intent Firewall

The Firewall is one FastAPI application. It needs a Python runtime,
persistent storage for the per-tenant SQLite files, two secrets
(`AXIOM_MASTER_KEY`, `AXIOM_FIREWALL_SESSION_SECRET`), and — optionally —
Stripe credentials for self-serve billing.

## Quick deploy paths

| Target | Time | Use when |
|---|---|---|
| [Docker (local)](#docker-local) | 5 min | You want to try it on your laptop. |
| [Docker (single host)](#docker-single-host) | 30 min | One VPS, you control the cert. |
| [AWS ECS Fargate](#aws-ecs-fargate-recommended) | 1 day | Production. HA across AZs, managed certs, autoscaling. |

---

## Docker (local)

```bash
# From the repo root
docker build -t orivael/axiom-firewall:local \
    -f deploy/firewall/Dockerfile .

docker run -p 8004:8004 \
    -e AXIOM_MASTER_KEY="$(openssl rand -hex 32)" \
    -e AXIOM_FIREWALL_SESSION_SECRET="$(openssl rand -hex 32)" \
    -v $(pwd)/tenants:/data/tenants \
    orivael/axiom-firewall:local
```

Open <http://localhost:8004>. Sign up, create a key, you're done.

---

## Docker (single host)

For a public-facing deploy on one VPS with TLS via Let's Encrypt:

```bash
# 1. Point firewall.example.com at the host's IP via your DNS provider.

# 2. From the repo root:
cd deploy/firewall
cp .env.example .env
# Fill in AXIOM_MASTER_KEY + AXIOM_FIREWALL_SESSION_SECRET
# Set AXIOM_FIREWALL_PUBLIC_URL=https://firewall.example.com
# Set FIREWALL_HOST=firewall.example.com (for Caddy)

# 3. Start the stack:
docker compose --profile tls up -d
```

Caddy auto-provisions the cert. First request takes ~30s while ACME
runs. Subsequent requests are fast.

To enable Stripe self-serve billing, run [`scripts/stripe_setup.py`](#stripe-setup)
and set the printed env vars in `.env`.

---

## AWS ECS Fargate (recommended)

Full IaC stack: ECS Fargate behind an ALB, ACM cert, Route53 record,
EFS for tenant DBs, Secrets Manager, CloudWatch logs.

### Prerequisites

- A VPC with at least 2 public + 2 private subnets across AZs.
- A Route53 hosted zone for the parent domain.
- An ACM cert covering `firewall.<your domain>` (must be in
  `us-east-1` if using the default region).
- An ECR repository named `axiom-firewall`.

### Build + push the image

```bash
aws ecr get-login-password --region us-east-1 | \
    docker login --username AWS --password-stdin \
    "$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com"

docker build -t axiom-firewall:v0.1.0 \
    -f deploy/firewall/Dockerfile .

docker tag axiom-firewall:v0.1.0 \
    "$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/axiom-firewall:v0.1.0"

docker push "$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/axiom-firewall:v0.1.0"
```

### Deploy the CloudFormation stack

```bash
aws cloudformation deploy \
    --template-file deploy/firewall/cloudformation.yaml \
    --stack-name axiom-firewall-prod \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        DomainName=firewall.example.com \
        HostedZoneId=Z1234567890ABC \
        CertificateArn=arn:aws:acm:us-east-1:$AWS_ACCOUNT_ID:certificate/abcd1234 \
        ImageUri=$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/axiom-firewall:v0.1.0 \
        VpcId=vpc-abcdef \
        PublicSubnetIds=subnet-pub1,subnet-pub2 \
        PrivateSubnetIds=subnet-priv1,subnet-priv2 \
        DesiredCount=2
```

The stack auto-generates `AXIOM_MASTER_KEY` and `AXIOM_FIREWALL_SESSION_SECRET`
into Secrets Manager. Stripe-related secrets are created but empty —
populate them via the console (or `aws secretsmanager put-secret-value`)
after running the Stripe setup script.

### Stripe setup

```bash
# Use test mode first to dry-run.
STRIPE_SECRET_KEY=sk_test_... python scripts/stripe_setup.py
# Copy the printed env-var lines into Secrets Manager.

# Then re-run with live keys when ready to charge:
STRIPE_SECRET_KEY=sk_live_... python scripts/stripe_setup.py
```

After that, create a webhook endpoint at
<https://dashboard.stripe.com/webhooks> pointing at
`https://firewall.example.com/billing/webhook`, subscribed to:

- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Copy the webhook signing secret (`whsec_...`) into
`axiom-firewall/stripe-webhook-secret` in Secrets Manager.

### Verify the deploy

```bash
curl https://firewall.example.com/healthz
# {"status": "ok"}

curl https://firewall.example.com/readyz
# {"status": "ready"}
```

Sign up at `https://firewall.example.com/signup`, create an API key,
and run one `/v1/guard/check` call.

### Roll a new version

```bash
# Push the new image with a new tag, then:
aws ecs update-service \
    --cluster axiom-firewall \
    --service axiom-firewall \
    --force-new-deployment

# Or update the stack with a new ImageUri to track the rollout in CFN.
aws cloudformation deploy \
    --stack-name axiom-firewall-prod \
    --template-file deploy/firewall/cloudformation.yaml \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides ImageUri=...new-tag
```

ECS does a rolling deployment with `MinimumHealthyPercent=100`, so
there's never a moment where 0 healthy tasks are serving traffic.

---

## Health endpoints

| Endpoint | Purpose | Status semantics |
|---|---|---|
| `/healthz` | Liveness | Always 200 if the process is up. ALB and Kubernetes use this for restart decisions. |
| `/readyz` | Readiness | 200 when the registry DB is writable; 503 if not. ALB uses this to pull a task out of rotation. |

---

## Observability

- **Logs:** stdout, JSON-friendly via uvicorn. CloudWatch ingests
  automatically via `awslogs` driver. Request IDs in
  `X-Request-ID` header.
- **Metrics:** Prometheus `/metrics` endpoint planned for Phase 2.
  Until then, CloudWatch Container Insights gives CPU/memory/network.
- **Tracing:** Send `X-Request-ID` from upstream; the Firewall echoes
  it on every response so you can correlate.

---

## Backups

Tenant SQLite files live on EFS (Fargate path) or your mount point
(Docker path). Recommended cadence:

```bash
# Daily, via cron or AWS Backup
tar czf "tenants-$(date +%Y%m%d).tar.gz" \
    -C /data tenants
aws s3 cp "tenants-$(date +%Y%m%d).tar.gz" \
    s3://axiom-firewall-backups/
```

The CloudFormation stack does not yet provision automatic backups —
add an `AWS::Backup::BackupPlan` referencing the EFS FileSystem when
you're ready.

---

## Rollback

If a deploy goes bad:

```bash
# Roll back via ECS:
aws ecs update-service \
    --cluster axiom-firewall \
    --service axiom-firewall \
    --task-definition axiom-firewall:PREVIOUS_REVISION

# Or via CloudFormation:
aws cloudformation rollback-stack \
    --stack-name axiom-firewall-prod
```

The tenant DB schema is forward-additive (migrations are `ALTER TABLE
... ADD COLUMN`) so a backward rollback won't drop columns. If a future
release ever requires a destructive migration, the release notes will
flag it.
