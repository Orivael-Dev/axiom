# AX OS — desktop shell (Tauri 2 + React)

The "OS window" front-end for AX OS. A Tauri 2 desktop app whose React/Vite
UI renders the adaptive `WorkspacePlan` — it talks to the **AX OS local
service** (`aui/server.py`) over `http://127.0.0.1:8800`, exactly like the
Streamlit shell. Same data, native window.

> ⚠️ Authored without a local Rust/Node toolchain — treat `src-tauri/` as a
> best-effort Tauri 2.x scaffold. The React app under `src/` is the
> substantive part. If the Tauri toolchain version drifts, regenerate the
> Rust wrapper (see "If the Tauri scaffold won't build" below) and keep
> `src/` as-is.

## Prerequisites
- Node 18+ and Rust (stable) + the platform's Tauri deps — see
  https://tauri.app/start/prerequisites/
- The AX OS local service running (see below)

## Run (two terminals)
```bash
# terminal 1 — the AX OS local service (from the ax-os repo root)
pip install -r requirements-aui.txt
AX_OS_REPO=. python -m aui.server          # serves http://127.0.0.1:8800

# terminal 2 — the desktop shell (from desktop/)
npm install
npm run tauri dev                          # opens the AX OS window
```
The window: type a goal → **Open workspace** → adaptive panels render
(filled with your files/tools/branch/authorized-agents), plus the signed
audit trail. A refused goal shows the intent-gate banner.

Point at a non-default service with `VITE_AX_OS_API` (e.g.
`VITE_AX_OS_API=http://127.0.0.1:9000 npm run tauri dev`).

## Layout
```
src/
  api.ts            typed client for the AX OS service (/assemble /audit /health)
  types.ts          WorkspacePlan / Panel / AuditTrail shapes (mirror the service)
  App.tsx           the shell: intent input → panel grid → audit trail
  components/PanelCard.tsx
  styles.css
src-tauri/          Tauri 2 wrapper (window, CSP allowing localhost:8800, capability)
```

## Bundling
`bundle.active` is `false` so `npm run tauri dev` runs without icon assets.
To build installers: `npm run tauri icon path/to/logo.png` (generates the
`src-tauri/icons/*`), flip `bundle.active` to `true`, then `npm run tauri build`.

## If the Tauri scaffold won't build
Tauri 2 config/Rust evolves; if `npm run tauri dev` errors on the wrapper,
regenerate it and drop this `src/` back in:
```bash
npm create tauri-app@latest   # choose React + TS; or `npm run tauri init` in place
# then copy this src/ over the generated frontend and set
# build.devUrl=http://localhost:1420, build.frontendDist=../dist in tauri.conf.json
```

## Boundary
The desktop shell only calls the AX OS local service over HTTP — no Axiom
imports, no Axiom source. The trust layer stays behind the service + bridge.
