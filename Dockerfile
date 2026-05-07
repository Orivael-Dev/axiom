FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

# Copy project metadata first (layer caching for deps)
COPY pyproject.toml README.md ./

# Copy source modules
COPY axiom_signing.py ./
COPY axiom_latent.py axiom_latent_v2.py axiom_semantic_observable.py ./
COPY axiom_redact.py axiom_agent.py axiom_conversation_graph.py ./
COPY axiom_constitutional/ ./axiom_constitutional/
COPY axiom_files/ ./axiom_files/
COPY sovereign/ ./sovereign/
COPY examples/axiom_guard_api.py ./examples/

# Install Python dependencies
# anthropic is optional — Guard API works in heuristic mode without it
RUN pip install --no-cache-dir . fastapi uvicorn anthropic

EXPOSE 8001

# AXIOM_MASTER_KEY must be provided at runtime:
#   docker run -e AXIOM_MASTER_KEY=<hex> -p 8001:8001 axiom-guard
CMD ["python", "examples/axiom_guard_api.py"]
