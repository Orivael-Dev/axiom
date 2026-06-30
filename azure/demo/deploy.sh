#!/usr/bin/env bash
# Deploy the Governed Agent demo to Azure Container Apps, with Azure OpenAI as the
# ungoverned-foil backend. Idempotent-ish: re-run to update.
#
#   ./azure/demo/deploy.sh
#
# Set these first (env or edit inline). Secrets are passed as Container Apps secrets.
set -euo pipefail

RG="${RG:-orivael-demo-rg}"
LOCATION="${LOCATION:-eastus}"
APP="${APP:-governed-demo}"
ENVNAME="${ENVNAME:-orivael-demo-env}"

# --- Backends -----------------------------------------------------------------
# Governed scenarios (Claude):
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"
# Ungoverned foil via Azure OpenAI (OpenAI-compatible). Point at your deployment:
#   AXIOM_OPEN_BASE_URL = https://<resource>.openai.azure.com/openai/deployments/<dep>
#   AXIOM_OPEN_MODEL    = <your deployment name>
AXIOM_OPEN_BASE_URL="${AXIOM_OPEN_BASE_URL:-}"
AXIOM_OPEN_API_KEY="${AXIOM_OPEN_API_KEY:-}"
AXIOM_OPEN_MODEL="${AXIOM_OPEN_MODEL:-gpt-4o-mini}"
# Stable signing key for the demo's HMAC manifests:
AXIOM_MASTER_KEY="${AXIOM_MASTER_KEY:?set AXIOM_MASTER_KEY (any strong secret)}"

az extension add -n containerapp --upgrade -y >/dev/null
az group create -n "$RG" -l "$LOCATION" -o none

# `containerapp up` builds the image from the Dockerfile and creates the env + app.
az containerapp up \
  --name "$APP" \
  --resource-group "$RG" \
  --environment "$ENVNAME" \
  --location "$LOCATION" \
  --source "$(git rev-parse --show-toplevel)" \
  --ingress external --target-port 8000 \
  --env-vars \
    AXIOM_FIREWALL_BETA_MODE=1 \
    AXIOM_OPEN_BASE_URL="$AXIOM_OPEN_BASE_URL" \
    AXIOM_OPEN_MODEL="$AXIOM_OPEN_MODEL"

# Secrets (kept out of env-vars listing).
az containerapp secret set -n "$APP" -g "$RG" --secrets \
  anthropic-key="$ANTHROPIC_API_KEY" \
  open-key="$AXIOM_OPEN_API_KEY" \
  master-key="$AXIOM_MASTER_KEY" -o none

az containerapp update -n "$APP" -g "$RG" --set-env-vars \
  ANTHROPIC_API_KEY=secretref:anthropic-key \
  AXIOM_OPEN_API_KEY=secretref:open-key \
  AXIOM_MASTER_KEY=secretref:master-key -o none

# Scale to zero when idle to conserve credits (0–2 replicas).
az containerapp update -n "$APP" -g "$RG" --min-replicas 0 --max-replicas 2 -o none

FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
echo ""
echo "Deployed: https://${FQDN}"
echo "Point demo.orivael.dev (CNAME) at it, or add a custom domain via 'az containerapp hostname add'."

# Note: the Dockerfile build context is the repo root; this uses Dockerfile at
# azure/demo/Dockerfile via 'az containerapp up' auto-detection. If detection picks
# the wrong Dockerfile, add:  --dockerfile azure/demo/Dockerfile
