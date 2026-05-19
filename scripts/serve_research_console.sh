#!/usr/bin/env bash
# Launch the AXIOM Re:Search Engine locally.
#
# Browser → http://127.0.0.1:8765
#
# Env you may want to set:
#   AXIOM_MASTER_KEY        (required) 32-byte hex secret
#   OLLAMA_URL              local nano backend URL (default 127.0.0.1:11434)
#   OLLAMA_MODEL            default llama3.2:3b
#   NVIDIA_NIM_API_KEY      activates NIM backend when paired with AXIOM_BACKEND=nim
#   AXIOM_RESEARCH_TOKEN    enables bearer auth on /api/research and /api/ledger
#   AXIOM_RESEARCH_HOST     default 127.0.0.1 — change ONLY when you want LAN exposure
#   AXIOM_RESEARCH_PORT     default 8765
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "${AXIOM_MASTER_KEY:-}" ]; then
  echo "AXIOM_MASTER_KEY not set — generating an ephemeral one for this session."
  echo "(Set AXIOM_MASTER_KEY explicitly to persist signed-token verifiability"
  echo " across restarts.)"
  export AXIOM_MASTER_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
fi

exec python3 -m axiom_research_server "$@"
