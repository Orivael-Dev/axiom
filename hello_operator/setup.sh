#!/usr/bin/env bash
#
# Hello Operator — one-command setup
# ===================================
# Installs deps, generates a signing key, writes config, and prints the run command.
# Safe to re-run: it never overwrites an existing key or config.
#
#   ./hello_operator/setup.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENV_FILE="$ROOT/.env"
CONFIG_FILE="$HERE/config.json"

echo ""
echo "  Hello Operator — setup"
echo "  ======================================================"

# ── 1. Python check ──────────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
  echo "  ✗ python3 not found. Install Python 3.9+ and re-run." >&2
  exit 1
fi
PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
echo "  ✓ Python $PYV"

# ── 2. Dependencies ──────────────────────────────────────────────────────────
echo "  • Installing dependencies (fastapi, uvicorn, anthropic, httpx, python-dotenv)…"
python3 -m pip install --quiet --upgrade \
  fastapi "uvicorn[standard]" anthropic httpx python-dotenv pydantic 2>&1 | tail -1 || true
echo "  ✓ Dependencies ready"

# ── 3. Signing key ───────────────────────────────────────────────────────────
touch "$ENV_FILE"
if grep -q '^AXIOM_MASTER_KEY=' "$ENV_FILE" 2>/dev/null; then
  echo "  ✓ AXIOM_MASTER_KEY already set in .env (kept)"
else
  KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  echo "AXIOM_MASTER_KEY=$KEY" >> "$ENV_FILE"
  echo "  ✓ Generated AXIOM_MASTER_KEY → .env"
fi

# Optional: prompt to add Anthropic key if missing
if ! grep -q '^ANTHROPIC_API_KEY=' "$ENV_FILE" 2>/dev/null; then
  echo "  • ANTHROPIC_API_KEY not set — operator will run in STUB mode (no live model)."
  echo "    Add it to .env for live answers:  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env"
fi

# ── 4. Config ────────────────────────────────────────────────────────────────
if [ -f "$CONFIG_FILE" ]; then
  echo "  ✓ config.json already present (kept)"
else
  cp "$HERE/config.example.json" "$CONFIG_FILE"
  echo "  ✓ Wrote config.json (edit it to set policy, system prompt, webhook)"
fi

# ── 5. Done ──────────────────────────────────────────────────────────────────
echo ""
echo "  ✓ Setup complete."
echo ""
echo "  Run it:"
echo "      python3 hello_operator/server.py"
echo ""
echo "  Then open:  http://localhost:8800"
echo ""
echo "  Notifications: edit hello_operator/config.json → notifications.webhook.url"
echo "                 to forward blocks/flags to Slack or Discord."
echo "  ======================================================"
echo ""
