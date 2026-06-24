# Axiom Inference OS — NVIDIA NGC container
#
# Base: CUDA 12.4 runtime (compatible with TRT-LLM 0.12+)
# The TRT-LLM inference server runs as a sidecar (see docker-compose.yml);
# this image contains only the Axiom control plane.
#
# Build:
#   docker build -f deploy/nvidia/Dockerfile \
#     -t nvcr.io/<your-org>/axiom-inference-os:latest .
#
# Push to NGC:
#   docker login nvcr.io
#   docker push nvcr.io/<your-org>/axiom-inference-os:latest

FROM nvcr.io/nvidia/cuda:12.4.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.11 python3.11-dev python3-pip ca-certificates && \
    ln -sf python3.11 /usr/bin/python3 && \
    pip3 install --no-cache-dir --upgrade pip && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /axiom

# Install Python deps (minimal — core stack is stdlib + requests)
COPY deploy/nvidia/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy Axiom source modules
COPY axiom_signing.py \
     axiom_intent_classifier.py \
     axiom_latent.py axiom_latent_v2.py \
     axiom_semantic_observable.py \
     axiom_memory_engine.py \
     axiom_semantic_cosmos.py \
     axiom_research_retriever.py \
     axiom_prefix_cache.py \
     axiom_delta_memory.py \
     axiom_multiresolution_memory.py \
     axiom_output_shaper.py \
     axiom_inference_os.py \
     axiom_audit_ledger.py \
     axiom_exoskeleton_ledger.py \
     axiom_blt_bench.py \
     ./

COPY axiom_event_token/ ./axiom_event_token/
COPY axiom_firewall/   ./axiom_firewall/
COPY packs/            ./packs/

# Inference OS HTTP server
COPY deploy/nvidia/server.py ./server.py

# Data + audit directories
RUN mkdir -p /var/axiom/audit /var/axiom/memory /var/axiom/delta

ENV AXIOM_MASTER_KEY="" \
    AXIOM_BACKEND="trtllm" \
    TRTLLM_URL="http://trtllm:8000/v1" \
    TRTLLM_MODEL="meta/llama-3.1-8b-instruct" \
    AXIOM_AUDIT_PATH="/var/axiom/audit/ledger.jsonl" \
    AXIOM_DELTA_MEMORY_PATH="/var/axiom/delta/delta_memory.jsonl" \
    AXIOM_PORT="8080"

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["python3", "server.py"]
