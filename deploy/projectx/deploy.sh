#!/usr/bin/env bash
# Build + deploy the projectx React dashboard to projectx.orivael.dev
# Usage:  bash deploy/projectx/deploy.sh
set -euo pipefail

HOST="${PROJECTX_HOST:-root@178.156.205.89}"
REMOTE_DIR="/opt/sites/projectx"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../sites/projectx" && pwd)"

cd "$SRC_DIR"
echo "==> building in $SRC_DIR"
npm install
npm run build

echo "==> uploading dist/ to $HOST:$REMOTE_DIR"
ssh "$HOST" "mkdir -p $REMOTE_DIR && rm -rf $REMOTE_DIR/assets"   # clear old hashed assets
scp -r dist/index.html dist/assets "$HOST:$REMOTE_DIR/"

echo "==> done — https://projectx.orivael.dev (static file_server, no Caddy reload needed)"
