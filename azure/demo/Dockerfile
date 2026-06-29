# Governed Agent demo — container for Azure Container Apps.
# Build context = repo root:  docker build -f azure/demo/Dockerfile -t governed-demo .
FROM python:3.11-slim

WORKDIR /app

# Runtime deps for the demo server (FastAPI + the two model SDKs).
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" anthropic openai python-dotenv

# Only what the demo needs: the signing primitive + the demo package.
COPY axiom_signing.py /app/axiom_signing.py
COPY axiom_demo_governed_agent /app/axiom_demo_governed_agent

ENV PYTHONUNBUFFERED=1 \
    AXIOM_FIREWALL_BETA_MODE=1

EXPOSE 8000

# Serve the FastAPI app (module path; the package imports axiom_signing from /app).
CMD ["uvicorn", "axiom_demo_governed_agent.demo_server:app", \
     "--host", "0.0.0.0", "--port", "8000"]
