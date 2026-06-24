#!/bin/bash
# AI Workbench post-build setup for Axiom Inference OS

set -e

cd /axiom

echo "==> Installing Axiom Python dependencies"
pip3 install --no-cache-dir requests fastapi "uvicorn[standard]" python-dotenv

echo "==> Generating AXIOM_MASTER_KEY if not set"
if [ -z "$AXIOM_MASTER_KEY" ]; then
    KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "export AXIOM_MASTER_KEY=$KEY" >> ~/.bashrc
    echo "    Generated key written to ~/.bashrc"
fi

echo "==> Starting trtllm-serve in background"
MODEL="${TRTLLM_MODEL:-meta/llama-3.1-8b-instruct}"
nohup trtllm-serve "$MODEL" --port 8000 > /var/axiom/trtllm.log 2>&1 &
echo "    trtllm-serve PID: $!"

echo "==> Waiting for TRT-LLM server to be ready"
for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/v2/health/ready > /dev/null 2>&1; then
        echo "    TRT-LLM ready after ${i}s"
        break
    fi
    sleep 2
done

echo "==> Axiom Inference OS ready"
echo "    Run:  python3 /axiom/deploy/nvidia/server.py"
echo "    API:  http://localhost:8080/v1/infer"
echo "    Docs: http://localhost:8080/docs"
