# Freeze the two binaries and stage them as Tauri sidecars (Windows).
# Run from the ax-os repo root. Requires Python, PyInstaller, Rust, and
# $env:AXIOM_REPO pointing at a clone of Axiom main.
$ErrorActionPreference = "Stop"
if (-not $env:AXIOM_REPO) { throw "set `$env:AXIOM_REPO to your Axiom main checkout" }

python -m pip install --quiet pyinstaller

Write-Host "> freezing axiom-mcp"
pyinstaller --clean -y packaging/axiom-mcp.spec
Write-Host "> freezing ax-os-service"
pyinstaller --clean -y packaging/ax-os-service.spec

$triple = (rustc -Vv | Select-String 'host: ').ToString().Replace('host: ','').Trim()
New-Item -ItemType Directory -Force -Path desktop/src-tauri/bin | Out-Null
Copy-Item "dist/axiom-mcp.exe"     "desktop/src-tauri/bin/axiom-mcp-$triple.exe"
Copy-Item "dist/ax-os-service.exe" "desktop/src-tauri/bin/ax-os-service-$triple.exe"

Write-Host "OK staged sidecars for $triple in desktop/src-tauri/bin/"
Write-Host "  next:  cd desktop; npm run tauri build"
