#!/usr/bin/env bash
# Create the T4 compute target the SRD job uses (azureml:t4-spot).
# Scale-to-zero (no cost when idle) + low-priority/Spot (cheapest).
#
#   RG=my-rg WS=my-workspace ./azure/srd_benchmark/setup_compute.sh
#
set -euo pipefail

RG="${RG:?set RG (resource group)}"
WS="${WS:?set WS (Azure ML workspace)}"
NAME="${NAME:-t4-spot}"                       # must match compute: in job.yml
SIZE="${SIZE:-Standard_NC4as_T4_v3}"          # 1x T4; use Standard_NC64as_T4_v3 for 4x
TIER="${TIER:-low_priority}"                  # low_priority = Spot; set 'dedicated' if no Spot quota

az extension add -n ml --upgrade -y >/dev/null

az ml compute create \
  --name "$NAME" \
  --type AmlCompute \
  --size "$SIZE" \
  --min-instances 0 \
  --max-instances 1 \
  --tier "$TIER" \
  --resource-group "$RG" \
  --workspace-name "$WS"

echo ""
echo "created compute '$NAME' ($SIZE, scale-to-zero, tier=$TIER)"
echo "now run:  az ml job create -f azure/srd_benchmark/job.yml -g $RG -w $WS"
echo ""
echo "If this failed on quota: request 'Standard NCASv3_T4 Family vCPUs' for your region"
echo "in Subscription → Usage + quotas (Spot quota is a separate line from on-demand)."
