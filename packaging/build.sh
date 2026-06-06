#!/usr/bin/env bash
# Freeze the two binaries and stage them as Tauri sidecars. Run from the
# ax-os repo root. Requires: Python, PyInstaller, Rust (for the triple), and
# AXIOM_REPO pointing at a clone of Axiom main.
set -euo pipefail
: "${AXIOM_REPO:?set AXIOM_REPO to your Axiom main checkout}"

python -m pip install --quiet pyinstaller

echo "› freezing axiom-mcp"
pyinstaller --clean -y packaging/axiom-mcp.spec
echo "› freezing ax-os-service"
pyinstaller --clean -y packaging/ax-os-service.spec

TRIPLE="$(rustc -Vv | sed -n 's/host: //p')"
mkdir -p desktop/src-tauri/bin
cp "dist/axiom-mcp"     "desktop/src-tauri/bin/axiom-mcp-${TRIPLE}"
cp "dist/ax-os-service" "desktop/src-tauri/bin/ax-os-service-${TRIPLE}"

echo "✓ staged sidecars for ${TRIPLE} in desktop/src-tauri/bin/"
echo "  next:  cd desktop && npm run tauri build"
