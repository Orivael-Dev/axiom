# AXIOM Guard API — serves `axiom_guard_api:app` on :8001.
#
# This is the guard/latent/sovereign HTTP surface behind Caddy's `@guardapi`
# routes on firewall.orivael.dev; the Orivael consoles (osconsole, governance,
# checkmyllm) proxy their guard calls here. The running image had historically
# been hand-built with no committed recipe — this file makes it reproducible.
#
# Build from the REPO ROOT (axiom_guard_api.py + the axiom_* modules it imports
# live there):
#   docker build -f deploy/guard-api/Dockerfile -t orivael/axiom-guard-api:local .
#
# Run (via deploy/firewall/docker-compose.yml `guard-api` service, which supplies
# AXIOM_MASTER_KEY + NIM_* env and the axiom-net network).
#
# Pins match the versions in the live image as of 2026-06-30 (Python 3.12).
FROM python:3.12-slim

WORKDIR /app

# axiom_guard_api only needs the web stack + LLM SDKs; every axiom_* dependency
# is pure-Python and comes in via the COPY below.
RUN pip install --no-cache-dir \
    "fastapi==0.138.0" \
    "uvicorn[standard]==0.49.0" \
    "pydantic==2.13.4" \
    "anthropic==0.111.0" \
    "python-dotenv==1.2.2" \
    "openai==2.43.0"

# axiom_guard_api imports a broad slice of the axiom_* modules (guard, latent,
# sovereign, redact, intent, signing, …), so copy the repo. .dockerignore keeps
# .git and heavy build artifacts out of the image.
COPY . /app/

EXPOSE 8001

CMD ["uvicorn", "axiom_guard_api:app", "--host", "0.0.0.0", "--port", "8001"]
