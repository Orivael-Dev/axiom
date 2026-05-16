# Axiom Skill Pack Registry — packs.orivael.dev
#
# Read-only HTTP server that mirrors signed pack manifests.
# Separate image from the Firewall dashboard: different blast radius,
# different scaling.

FROM python:3.11-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir --target=/install \
    fastapi==0.136.* \
    uvicorn[standard]==0.32.*

FROM python:3.11-slim AS runtime

RUN groupadd -r axiom && useradd -r -g axiom -d /app -s /sbin/nologin axiom
WORKDIR /app

COPY --from=builder /install /usr/local/lib/python3.11/site-packages

# Source — needs axiom_signing (for derive_key) + axiom_firewall.skill_pack
# (the parse/verify logic is shared between dashboard and registry).
COPY axiom_signing.py            ./
COPY axiom_intent_classifier.py  ./
COPY axiom_firewall/             ./axiom_firewall/
COPY axiom_packs/                ./axiom_packs/

# Bake the first-party packs into the image. Production redeploys to
# add a new pack: image is the source of truth.
COPY packs/                      ./packs/

RUN chown -R axiom:axiom /app
ENV AXIOM_PACKS_DIR=/app/packs
USER axiom

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8005/healthz').status==200 else 1)"

ENV UVICORN_WORKERS=2
ENV UVICORN_HOST=0.0.0.0
ENV UVICORN_PORT=8005

CMD ["sh", "-c", "exec uvicorn axiom_packs.server:app --host $UVICORN_HOST --port $UVICORN_PORT --workers $UVICORN_WORKERS --proxy-headers --forwarded-allow-ips=*"]
