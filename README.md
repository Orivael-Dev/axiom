# ax-os-sidecar — courier branch (transient)

Not part of Axiom. One-click desktop packaging: freeze the service + Axiom
into binaries and auto-start them as Tauri sidecars. Delete after transfer.

UPDATE (overwrite):
- `bridge/client.py` — bridge honours AX_OS_MCP_BIN (spawn a frozen Axiom
  sidecar binary instead of `python -m axiom_mcp_server`). Env-only, no
  Axiom import — boundary intact.

NEW:
- `tests/test_bridge_command.py` — command-resolution (pure, no server).
- `packaging/service_main.py` — frozen service entry: finds the sibling
  `axiom-mcp` binary, defaults ledgers + a per-install signing key to the
  user-data dir.
- `packaging/{axiom-mcp,ax-os-service}.spec` — PyInstaller specs.
- `packaging/build.sh` / `build.ps1` — freeze both + stage as Tauri sidecars.
- `packaging/PACKAGING.md` — full flow + the exact Tauri snippets to add
  (tauri.conf externalBin, Cargo tauri-plugin-shell, capability, main.rs).
  Kept as snippets so your working Tauri build isn't clobbered.

Authored without a PyInstaller/Rust toolchain — the Python side is solid
(tests pass); the Tauri sidecar wiring is documented to apply + verify.
Fallback: the frozen `ax-os-service` runs standalone (Python-free) too.
