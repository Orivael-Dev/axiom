# AXIOM Re:Search Engine — beta-tester image
#
# Builds the FastAPI research server with the console HTML + signed
# event-token machinery + /help docs route. Designed for invited-beta
# distribution: Caddy basic-auth at the edge gates the URL itself;
# AXIOM_RESEARCH_TOKEN (if set) adds bearer-token defense-in-depth on
# /api/* paths.
#
# Build:
#   docker build -t orivael/axiom-research:latest \
#     -f deploy/research/Dockerfile .
#
# Run (development, no auth gate):
#   docker run -p 8765:8765 \
#     -e AXIOM_MASTER_KEY="$(openssl rand -hex 32)" \
#     -e AXIOM_BACKEND=local,deepseek \
#     -e DEEPSEEK_API_KEY="sk-..." \
#     orivael/axiom-research:latest

# ── Build stage ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip

# Runtime deps the research server actually needs. Markdown is
# required for /help rendering.
RUN pip install --no-cache-dir --target=/install \
    fastapi==0.136.* \
    uvicorn[standard]==0.32.* \
    pydantic==2.* \
    requests==2.* \
    markdown==3.10.* \
    openai==1.* \
    python-dotenv==1.*

# ── Runtime stage ───────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN groupadd -r axiom && useradd -r -g axiom -d /app -s /sbin/nologin axiom

WORKDIR /app

COPY --from=builder /install /usr/local/lib/python3.11/site-packages

# Application source — just what the research server transitively imports.
COPY axiom_research_server.py    ./
COPY axiom_research_retriever.py ./
COPY axiom_research/             ./axiom_research/
COPY axiom_event_token/          ./axiom_event_token/
COPY axiom_exoskeleton.py        ./
COPY axiom_exoskeleton_ledger.py ./
COPY axiom_exoskeleton_honesty.py ./
COPY axiom_medical_agent.py      ./
COPY axiom_medical_coordinator.py ./
COPY axiom_medical_container.py  ./
COPY axiom_medical_descriptor.py ./
COPY axiom_medical_governance.py ./
COPY axiom_medical_ledger.py     ./
COPY axiom_medical_safety.py     ./
COPY axiom_constitutional/       ./axiom_constitutional/
COPY axiom_intent_classifier.py  ./
COPY axiom_signing.py            ./
COPY axiom_axm.py                ./
COPY axiom_files/                ./axiom_files/
COPY examples/                   ./examples/

# Static assets — the console HTML and ledger viewer.
COPY web/                        ./web/

# Live docs source — /help reads this on every request, so updates
# need a container restart (or bind-mount the docs/ folder).
COPY docs/research_engine.md     ./docs/research_engine.md
COPY docs/firewall/              ./docs/firewall/

# Ledger volume mount point.
RUN mkdir -p /data && chown -R axiom:axiom /data /app
ENV HOME=/data

USER axiom

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health').status==200 else 1)"

ENV AXIOM_RESEARCH_HOST=0.0.0.0
ENV AXIOM_RESEARCH_PORT=8765
ENV UVICORN_WORKERS=2

CMD ["sh", "-c", "exec python -m uvicorn axiom_research_server:app --host $AXIOM_RESEARCH_HOST --port $AXIOM_RESEARCH_PORT --workers $UVICORN_WORKERS --proxy-headers --forwarded-allow-ips=*"]
