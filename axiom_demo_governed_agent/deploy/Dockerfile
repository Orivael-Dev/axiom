# Governed-agent live demo — container image.
#
# Build context is the REPO ROOT (the demo signs its verdicts with the repo's
# axiom_signing.py). Build directly:
#   docker build -f axiom_demo_governed_agent/deploy/Dockerfile -t orivael-demo .
# …or via compose (recommended — adds Caddy for automatic HTTPS):
#   docker compose -f axiom_demo_governed_agent/deploy/docker-compose.yml up -d --build
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip

# Dependencies first, for layer caching.
COPY axiom_demo_governed_agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The one repo-root module the demo uses to sign guard verdicts. It is stdlib-only
# and the demo has a fallback, but shipping it uses the real signing key.
COPY axiom_signing.py ./
# The demo package (server + UI).
COPY axiom_demo_governed_agent/ ./axiom_demo_governed_agent/

EXPOSE 8000

# Liveness: /api/meta returns 200 without starting a run.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/meta',timeout=4).status==200 else 1)"

# The demo keeps per-run state (SSE streams) in memory, so it MUST run as a single
# worker — multiple workers would split runs across processes and break the stream.
# ANTHROPIC_API_KEY must be supplied at runtime (see deploy/.env.example).
CMD ["python", "-m", "uvicorn", "axiom_demo_governed_agent.demo_server:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
