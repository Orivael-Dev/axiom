# Axiom MCP HTTP server — production image
#
# Wraps axiom_mcp_server.py with an HTTP/SSE transport so Claude Code
# on the web and mobile can connect to it as a remote MCP server.
#
# Build:
#   docker build -t orivael/axiom-mcp:latest -f deploy/mcp/Dockerfile .
#
# Run:
#   docker run -p 8006:8006 \
#     -e AXIOM_MASTER_KEY="$(openssl rand -hex 32)" \
#     -e AXIOM_MCP_TOKEN="$(openssl rand -hex 24)" \
#     orivael/axiom-mcp:latest

# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir --target=/install \
    fastapi==0.115.0 \
    uvicorn[standard]==0.30.6 \
    httpx==0.27.2 openai anthropic

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN groupadd -r axiom && useradd -r -g axiom -d /app -s /sbin/nologin axiom
WORKDIR /app

COPY --from=builder /install /usr/local/lib/python3.11/site-packages

# MCP server + HTTP wrapper + dependencies
COPY axiom_mcp_server.py        ./
COPY axiom_mcp_http.py          ./
COPY axiom_signing.py           ./
COPY axiom_intent_classifier.py ./
COPY axiom_latent_v2.py         ./
COPY axiom_qrf.py               ./
COPY axiom_spec_linter.py       ./
COPY axiom_research_pipeline.py ./
COPY axiom_constitutional/ ./axiom_constitutional/
COPY axiom_certify.py ./
COPY axiom_cbv.py ./
COPY axiom_mkb.py ./
COPY axiom_redact.py ./
COPY axiom_latent.py ./
COPY axiom_semantic_observable.py ./
COPY axiom_conversation_graph.py ./
COPY axiom_cmaa.py ./
COPY axiom_memory_engine.py ./
COPY axiom_language.py ./
COPY axiom_marketplace.py ./
COPY axiom_sovereign_phone.py ./
COPY axiom_cpi.py ./
COPY axiom_intent_gate.py ./
COPY axiom_firewall/ ./axiom_firewall/
COPY axiom_files/               ./axiom_files/
COPY packs/                     ./packs/

RUN mkdir -p /data && chown -R axiom:axiom /app /data
USER axiom

EXPOSE 8006

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8006/healthz').status==200 else 1)"

ENV UVICORN_WORKERS=1
CMD ["sh", "-c", "exec python -m uvicorn axiom_mcp_http:app --host 0.0.0.0 --port 8006 --workers $UVICORN_WORKERS --proxy-headers --forwarded-allow-ips=*"]
