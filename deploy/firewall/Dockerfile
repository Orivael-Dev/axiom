# Axiom Intent Firewall — production image
#
# Builds the dashboard + /v1/guard/check API as a single FastAPI app.
# Image is multi-stage for a small final layer; runs uvicorn as a
# non-root user.
#
# Build:
#   docker build -t orivael/axiom-firewall:latest \
#     -f deploy/firewall/Dockerfile .
#
# Run (free-tier-only, no Stripe):
#   docker run -p 8004:8004 \
#     -e AXIOM_MASTER_KEY="$(openssl rand -hex 32)" \
#     -e AXIOM_FIREWALL_SESSION_SECRET="$(openssl rand -hex 32)" \
#     -v $(pwd)/tenants:/data/tenants \
#     -e AXIOM_FIREWALL_TENANT_DIR=/data/tenants \
#     orivael/axiom-firewall:latest

# ── Build stage ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip

# Install pinned runtime deps from the lockfile. Hashes are verified —
# any swap mid-supply-chain fails the build. Refresh by running
# `pip-compile --generate-hashes deploy/firewall/requirements.in
# -o deploy/firewall/requirements.txt`. Dependabot will open a PR
# when a pinned dep ships a new patch (see .github/dependabot.yml).
COPY deploy/firewall/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir --target=/install --require-hashes \
    -r /build/requirements.txt

# ── Runtime stage ───────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Run as a non-root user.
RUN groupadd -r axiom && useradd -r -g axiom -d /app -s /sbin/nologin axiom

WORKDIR /app

# Bring in installed Python packages from builder.
COPY --from=builder /install /usr/local/lib/python3.11/site-packages

# Application source.
COPY axiom_firewall/        ./axiom_firewall/
COPY axiom_intent_classifier.py ./
COPY axiom_signing.py            ./
# Live docs source for the in-dashboard /help page. dashboard.py reads
# from BASE_DIR.parent / "docs" / "firewall", so this must land at
# /app/docs/firewall/*.md alongside the package.
#
# IMPORTANT: glob the top-level *.md only — do NOT recurse. The
# docs/firewall/internal/ subdirectory holds operator-only runbooks
# (launch playbook, billing, operations runbook) that must never
# ship inside a publicly-reachable container image.
COPY docs/firewall/*.md     ./docs/firewall/
# First-party skill packs. dashboard.py reads from
# BASE_DIR.parent / "packs" (i.e. /app/packs) unless
# AXIOM_FIREWALL_PACKS_DIR overrides. Signatures inside each
# pack.json must be produced with the same AXIOM_MASTER_KEY the
# container runs under — re-sign via scripts/sign_packs.py if the
# prod key changes.
COPY packs/                 ./packs/

# Tenant DB volume mount point.
RUN mkdir -p /data/tenants && chown -R axiom:axiom /data /app
ENV AXIOM_FIREWALL_TENANT_DIR=/data/tenants

USER axiom

EXPOSE 8004

# Liveness probe target; ALB / Kubernetes should hit /healthz on 8004.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8004/healthz').status==200 else 1)"

# Production uvicorn: 2 workers per CPU is the FastAPI default rec.
# UVICORN_WORKERS can override.
ENV UVICORN_WORKERS=2
ENV UVICORN_HOST=0.0.0.0
ENV UVICORN_PORT=8004

# `python -m uvicorn` is used (instead of bare `uvicorn`) because the
# build stage installs into `--target=/install` which copies packages
# but not entry-point scripts. Running the module directly avoids the
# need to also wire /install/bin into PATH.
CMD ["sh", "-c", "exec python -m uvicorn axiom_firewall.dashboard:app --host $UVICORN_HOST --port $UVICORN_PORT --workers $UVICORN_WORKERS --proxy-headers --forwarded-allow-ips=*"]
