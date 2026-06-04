# ax-os-desktop — courier branch (transient)

Not part of Axiom. Temporary courier for the AX OS **Tauri desktop shell**.
Delete after transfer.

NEW — drops in a top-level `desktop/` directory:
- `desktop/src/` — React/Vite AX OS shell (api.ts, types.ts, App.tsx,
  components/PanelCard.tsx, styles.css). Talks to the AX OS local service
  (aui/server.py) at http://127.0.0.1:8800 — same contract as the Streamlit app.
- `desktop/src-tauri/` — Tauri 2 wrapper (window, CSP allowing localhost:8800,
  default capability, Cargo/build.rs/main.rs).
- `desktop/README.md` — run steps + a regenerate-the-wrapper fallback.

Scaffold authored without a Rust/Node toolchain (couldn't build here) — the
React frontend is solid; verify the Tauri wrapper with `npm run tauri dev`
locally. No Axiom imports (HTTP-only to the local service).
